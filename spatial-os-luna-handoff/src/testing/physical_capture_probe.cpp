#include "control/physical_capture.hpp"
#include "control/physical_source.hpp"
#include "control/physical_query.hpp"
#include "platform/compositor_config.hpp"
#include "x11/compositor_connection.hpp"
#include <X11/Xatom.h>
#include <X11/extensions/XInput2.h>
#include <algorithm>
#include <cstring>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>

namespace {
using namespace spatial;
using namespace spatial::control;
void check(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
unsigned forwarded = 0;
int forwarded_code = 0;
int previous_error(Display*, XErrorEvent* event) { ++forwarded; forwarded_code = event->error_code; return 0; }
std::size_t descriptor_count() {
    return static_cast<std::size_t>(std::distance(std::filesystem::directory_iterator("/proc/self/fd"),
                                                std::filesystem::directory_iterator{}));
}
void kernel_checks() {
    for (const auto kind : {v1::KEYBOARD, v1::MOUSE}) {
        check(!physical_detail::kernel_identity("/dev/null", kind), "Non-input character device was accepted");
        check(!physical_detail::kernel_identity("/dev/zero", kind), "Second non-input device was accepted");
        check(!physical_detail::kernel_identity("relative-node", kind), "Relative input source was accepted");
    }
    check(!physical_detail::kernel_identity("/dev/null", v1::SPACEMOUSE), "Unexpected device kind was accepted");
    std::cout << "PASS reject_non_evdev\n";
    std::optional<physical_detail::KernelIdentity> keyboard, mouse;
    for (const auto& entry : std::filesystem::directory_iterator("/dev/input")) {
        if (!keyboard) keyboard = physical_detail::kernel_identity(entry.path(), v1::KEYBOARD);
        if (!mouse) mouse = physical_detail::kernel_identity(entry.path(), v1::MOUSE);
    }
    check(keyboard && mouse && keyboard->device != mouse->device, "Real distinct keyboard and relative mouse fixtures are required");
    check(!physical_detail::kernel_identity(keyboard->node, v1::MOUSE) &&
          !physical_detail::kernel_identity(mouse->node, v1::KEYBOARD), "Device class mismatch was accepted");
    struct stat node{};
    check(stat(keyboard->node.c_str(), &node) == 0 && S_ISCHR(node.st_mode) && node.st_rdev == keyboard->device &&
          node.st_ino == keyboard->inode, "Keyboard identity differs from the actual kernel node");
    std::cout << "FIXTURE keyboard=" << keyboard->node << " mouse=" << mouse->node
              << " evdev_read_access=" << (access(keyboard->node.c_str(), R_OK) == 0) << '\n';
    std::cout << "PASS kernel_keyboard_mouse\n";
    char name[] = "/tmp/spatial-physical-source-XXXXXX";
    check(mkdtemp(name), "Cannot create private fixture directory");
    struct Directory { std::filesystem::path path; ~Directory() { std::filesystem::remove_all(path); } } directory{name};
    const auto link = directory.path / "by-id";
    std::filesystem::create_symlink(keyboard->node, link);
    check(physical_detail::kernel_identity(link, v1::KEYBOARD) == keyboard, "Configured symlink lost its kernel identity");
    std::filesystem::remove(link);
    check(!physical_detail::kernel_identity(link, v1::KEYBOARD), "Removed source did not disappear");
    std::filesystem::create_symlink(mouse->node, link);
    check(!physical_detail::kernel_identity(link, v1::KEYBOARD) &&
          physical_detail::kernel_identity(link, v1::MOUSE) == mouse, "Reconnect kept the previous symlink target");
    std::filesystem::remove(link);
    std::filesystem::create_symlink(keyboard->node, link);
    check(physical_detail::kernel_identity(link, v1::KEYBOARD) == keyboard, "Fresh source did not recover");
    const auto regular = directory.path / "ordinary-file";
    std::ofstream(regular) << "fixture";
    check(!physical_detail::kernel_identity(regular, v1::KEYBOARD), "Ordinary file was accepted");
    std::cout << "PASS by_id_resolve_reconnect\n";
    const auto before = descriptor_count();
    for (unsigned i = 0; i < 100; ++i) {
        check(physical_detail::kernel_identity(link, v1::KEYBOARD).has_value(), "Repeated live identity failed");
        check(!physical_detail::kernel_identity("/dev/null", v1::KEYBOARD), "Repeated invalid identity succeeded");
    }
    check(descriptor_count() == before, "Kernel source inspection leaked a descriptor");
    std::cout << "PASS kernel_fd_lifetime\n";
}
void native_checks(int descriptor) {
    CompositorConfig config(descriptor);
    std::unique_ptr<Display, decltype(&XCloseDisplay)> display(open_compositor_display(config.display(), config.value()), XCloseDisplay);
    config.consume();
    XSetErrorHandler(previous_error);
    int opcode = 0, event = 0, error = 0, major = 2, minor = 1;
    check(XQueryExtension(display.get(), "XInputExtension", &opcode, &event, &error) &&
          XIQueryVersion(display.get(), &major, &minor) == Success, "Native XI2 query is unavailable");
    int pointer = 0;
    check(XIGetClientPointer(display.get(), None, &pointer), "Native master pointer is unavailable");
    const auto property = XInternAtom(display.get(), "Device Node", False);
    check(physical_detail::device_node(display.get(), 65535, property, opcode, error).empty() && forwarded == 0,
          "Removed XI device poisoned the previous error handler");
    std::cout << "PASS removed_device_query\n";
    bool rejected = false;
    try { (void)physical_detail::device_node(display.get(), pointer, 0xffffffffUL, opcode, error); }
    catch (const std::runtime_error&) { rejected = true; }
    check(rejected && forwarded == 1 && forwarded_code == BadAtom, "Unrelated property error was hidden");
    std::cout << "PASS unrelated_error_forwarded\n";
    forwarded = 0;
    XDestroyWindow(display.get(), None); // An earlier error must reach the original handler before the guarded query.
    check(physical_detail::device_node(display.get(), 65535, property, opcode, error).empty() &&
          forwarded == 1 && forwarded_code == BadWindow, "Earlier X error was swallowed by the unplug guard");
    std::cout << "PASS prior_error_preserved\n";
    forwarded = 0;
    std::string valid = "/dev/null";
    XIChangeProperty(display.get(), pointer, property, XA_STRING, 8, PropModeReplace,
                     reinterpret_cast<unsigned char*>(valid.data()), static_cast<int>(valid.size()));
    check(physical_detail::device_node(display.get(), pointer, property, opcode, error) == valid,
          "Valid property bytes were not decoded");
    std::string bad = valid; bad.append(2, '\0');
    XIChangeProperty(display.get(), pointer, property, XA_STRING, 8, PropModeReplace,
                     reinterpret_cast<unsigned char*>(bad.data()), static_cast<int>(bad.size()));
    check(physical_detail::device_node(display.get(), pointer, property, opcode, error).empty(), "Embedded property NUL was accepted");
    bad.assign(1030, 'a'); bad[0] = '/';
    XIChangeProperty(display.get(), pointer, property, XA_STRING, 8, PropModeReplace,
                     reinterpret_cast<unsigned char*>(bad.data()), static_cast<int>(bad.size()));
    check(physical_detail::device_node(display.get(), pointer, property, opcode, error).empty(), "Truncated property was accepted");
    XIDeleteProperty(display.get(), pointer, property);
    XSync(display.get(), False);
    check(forwarded == 0, "Bounded property checks generated unrelated X errors");
    std::cout << "PASS bounded_property_payload\n";
    const auto window = XCreateSimpleWindow(display.get(), DefaultRootWindow(display.get()), 0, 0,
        static_cast<unsigned>(DisplayWidth(display.get(), DefaultScreen(display.get()))),
        static_cast<unsigned>(DisplayHeight(display.get(), DefaultScreen(display.get()))), 0, 0, 0);
    XMapWindow(display.get(), window); XSync(display.get(), False);
    unsigned emitted = 0;
    rejected = false;
    try { PhysicalCapture capture(display.get(), window, {{"/dev/null", v1::KEYBOARD}, {"/dev/zero", v1::MOUSE}},
                                  [&](const auto&) { ++emitted; }); }
    catch (const std::runtime_error& failure) {
        rejected = std::string(failure.what()).find("kernel input device") != std::string::npos;
    }
    check(rejected && emitted == 0, "Fake kernel sources reached capture side effects");
    unsigned char mask[XIMaskLen(XI_LASTEVENT)]{};
    XISetMask(mask, XI_ButtonPress);
    XIEventMask selected{pointer, sizeof(mask), mask};
    check(XIGrabDevice(display.get(), pointer, window, CurrentTime, None, XIGrabModeAsync, XIGrabModeAsync, False, &selected) == XIGrabSuccess,
          "Rejected capture retained a pointer grab or native core capture is unavailable");
    XIUngrabDevice(display.get(), pointer, CurrentTime);
    XDestroyWindow(display.get(), window); XSync(display.get(), False);
    check(forwarded == 0, "Rejected capture generated unrelated X errors");
    std::cout << "PASS invalid_sources_before_capture\n";
}
}
int main(int argc, char** argv) {
    try {
        check(argc == 2, "Expected --kernel or one sealed native configuration descriptor");
        if (std::strcmp(argv[1], "--kernel") == 0) kernel_checks();
        else native_checks(std::stoi(argv[1]));
        return 0;
    } catch (const std::exception& error) { std::cerr << "FAIL " << error.what() << '\n'; return 1; }
}
