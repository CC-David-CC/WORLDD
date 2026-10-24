#include "control/physical_capture.hpp"
#include "control/physical_source.hpp"
#include "control/physical_query.hpp"
#include <X11/XKBlib.h>
#include <X11/Xatom.h>
#include <X11/extensions/XInput2.h>
#include <algorithm>
#include <array>
#include <map>
#include <limits>
#include <set>
#include <stdexcept>
#include <sys/stat.h>

namespace spatial::control {
namespace {
void need(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
struct Devices {
    XIDeviceInfo* data;
    int count = 0;
    Devices(Display* display, int id) : data(XIQueryDevice(display, id, &count)) { need(data, "Cannot inspect input devices"); }
    ~Devices() { XIFreeDeviceInfo(data); }
};
}
struct PhysicalCapture::Impl {
    struct Source {
        v1::DeviceKind kind;
        physical_detail::KernelIdentity identity;
        std::uint64_t generation, sequence;
    };
    Display* display;
    Window output;
    Emit emit;
    std::vector<PhysicalSource> selected;
    std::map<int, Source> sources;
    std::map<int, std::uint64_t> generations;
    int opcode = 0, error_base = 0, pointer = 0, keyboard = 0;
    Atom device_node_property = None;
    bool pointer_grabbed = false, keyboard_grabbed = false;
    DeviceAdapters::Clock::time_point heartbeat{};
    Impl(Display* d, Window o, std::vector<PhysicalSource> s, Emit e)
        : display(d), output(o), emit(std::move(e)), selected(std::move(s)) {}
    v1::DeviceEvent event(int id) {
        auto& source = sources.at(id);
        need(source.sequence < std::numeric_limits<std::uint64_t>::max(), "Captured source sequence exhausted");
        v1::DeviceEvent result; result.set_protocol_version(1); result.set_source_id("xi-"+std::to_string(id));
        result.set_source_generation(source.generation); result.set_event_sequence(++source.sequence); return result;
    }
    void disconnect(int id) {
        auto value = event(id); value.mutable_disconnected(); emit(value); sources.erase(id);
    }
    void refresh() {
        std::map<std::filesystem::path, std::pair<v1::DeviceKind, physical_detail::KernelIdentity>> configured;
        std::set<dev_t> physical_devices;
        for (const auto& selection : selected) {
            const auto identity = physical_detail::kernel_identity(selection.device_node, selection.kind);
            if (!identity) continue; // Unplugged by-id paths may resolve again after the next hierarchy event.
            need(physical_devices.insert(identity->device).second &&
                 configured.emplace(identity->node, std::make_pair(selection.kind, *identity)).second,
                 "Configured input sources now alias one kernel device");
        }
        Devices devices(display, XIAllDevices);
        std::map<int, std::pair<v1::DeviceKind, physical_detail::KernelIdentity>> current;
        std::set<dev_t> attached_devices;
        for (int i = 0; i < devices.count; ++i) {
            const auto& device = devices.data[i];
            const bool mouse = device.use == XISlavePointer && device.attachment == pointer;
            const bool keys = device.use == XISlaveKeyboard && device.attachment == keyboard;
            if (!device.enabled || (!mouse && !keys)) continue;
            const auto node = physical_detail::device_node(display, device.deviceid, device_node_property, opcode, error_base);
            if (node.empty()) continue;
            const auto selection = configured.find(node);
            if (selection != configured.end() && ((mouse && selection->second.first == v1::MOUSE) ||
                                                 (keys && selection->second.first == v1::KEYBOARD))) {
                need(attached_devices.insert(selection->second.second.device).second,
                     "Several X input sources claim one configured kernel device");
                current.emplace(device.deviceid, selection->second);
            }
        }
        std::vector<int> removed;
        for (const auto& [id, source] : sources)
            if (!current.contains(id) || current.at(id).first != source.kind || current.at(id).second != source.identity)
                removed.push_back(id);
        for (int id : removed) disconnect(id);
        for (const auto& [id, selection] : current) if (!sources.contains(id)) {
            need(generations[id] < std::numeric_limits<std::uint64_t>::max(), "Captured source generation exhausted");
            sources.emplace(id, Source{selection.first, selection.second, ++generations[id], 0});
            auto value = event(id); value.mutable_connected()->set_kind(selection.first); emit(value);
        }
    }
    ~Impl() {
        if (!display) return;
        // Host shutdown must close the actor channel before releasing this capture.
        while (!sources.empty()) {
            const int id = sources.begin()->first;
            try { disconnect(id); } catch (...) { sources.erase(id); }
        }
        if (keyboard_grabbed) XIUngrabDevice(display, keyboard, CurrentTime);
        if (pointer_grabbed) XIUngrabDevice(display, pointer, CurrentTime);
        XFlush(display);
    }
};
PhysicalCapture::PhysicalCapture(Display* display, Window output, std::vector<PhysicalSource> sources, Emit emit)
    : impl_(std::make_unique<Impl>(display, output, std::move(sources), std::move(emit))) {
    auto& r = *impl_;
    need(display && output != None && bool(r.emit) && !r.selected.empty() && r.selected.size() <= 32,
         "Physical capture requires an explicit output and trusted device selection");
    int event = 0, error = 0, major = 2, minor = 1;
    need(XQueryExtension(display, "XInputExtension", &r.opcode, &event, &error) &&
         XIQueryVersion(display, &major, &minor) == Success && (major > 2 || (major == 2 && minor >= 1)), "XInput 2.1 required");
    r.error_base = error;
    r.device_node_property = XInternAtom(display, "Device Node", True);
    std::set<dev_t> nodes;
    for (const auto& source : r.selected) {
        need(source.kind == v1::KEYBOARD || source.kind == v1::MOUSE, "Unsupported physical device kind");
        const auto identity = physical_detail::kernel_identity(source.device_node, source.kind);
        need(identity && nodes.insert(identity->device).second,
             "Physical source must identify one distinct matching kernel input device");
    }
    need(XIGetClientPointer(display, None, &r.pointer), "Cannot identify the physical master pointer");
    Devices master(display, r.pointer);
    need(master.count == 1 && master.data[0].enabled && master.data[0].use == XIMasterPointer,
         "Client pointer is not a live master pointer");
    r.keyboard = master.data[0].attachment;
    XWindowAttributes window{};
    need(XGetWindowAttributes(display, output, &window) && window.map_state == IsViewable &&
         window.width == DisplayWidth(display, DefaultScreen(display)) &&
         window.height == DisplayHeight(display, DefaultScreen(display)), "Physical capture requires a live full output");
    r.refresh();
    need(std::any_of(r.sources.begin(), r.sources.end(), [](const auto& x) { return x.second.kind == v1::KEYBOARD; }) &&
         std::any_of(r.sources.begin(), r.sources.end(), [](const auto& x) { return x.second.kind == v1::MOUSE; }),
         "Configured physical keyboard and mouse are not attached to the core seat");
    std::array<unsigned char, XIMaskLen(XI_LASTEVENT)> pointer_mask{}, keyboard_mask{}, raw_mask{}, hierarchy_mask{};
    XISetMask(pointer_mask.data(), XI_ButtonPress); XISetMask(pointer_mask.data(), XI_ButtonRelease);
    XISetMask(keyboard_mask.data(), XI_KeyPress); XISetMask(keyboard_mask.data(), XI_KeyRelease);
    XIEventMask pm{r.pointer, static_cast<int>(pointer_mask.size()), pointer_mask.data()};
    XIEventMask km{r.keyboard, static_cast<int>(keyboard_mask.size()), keyboard_mask.data()};
    need(XIGrabDevice(display, r.pointer, output, CurrentTime, None, XIGrabModeAsync, XIGrabModeAsync, False, &pm) == XIGrabSuccess,
         "Physical pointer capture refused"); r.pointer_grabbed = true;
    need(XIGrabDevice(display, r.keyboard, output, CurrentTime, None, XIGrabModeAsync, XIGrabModeAsync, False, &km) == XIGrabSuccess,
         "Physical keyboard capture refused"); r.keyboard_grabbed = true;
    XISetFocus(display, r.keyboard, output, CurrentTime);
    XISetMask(raw_mask.data(), XI_RawMotion); XISetMask(hierarchy_mask.data(), XI_HierarchyChanged);
    XIEventMask masks[]{{r.pointer, static_cast<int>(raw_mask.size()), raw_mask.data()},
                        {XIAllDevices, static_cast<int>(hierarchy_mask.size()), hierarchy_mask.data()}};
    need(XISelectEvents(display, DefaultRootWindow(display), masks, 2) == Success, "Cannot select captured device events");
    XSync(display, False);
}
PhysicalCapture::~PhysicalCapture() = default;
bool PhysicalCapture::handle(XEvent& event) {
    auto& r = *impl_;
    if (event.type != GenericEvent || event.xcookie.extension != r.opcode || event.xcookie.send_event) return false;
    if (!XGetEventData(r.display, &event.xcookie)) return false;
    struct Cookie { Display* display; XGenericEventCookie* event; ~Cookie() { XFreeEventData(display, event); } } cookie{r.display, &event.xcookie};
    if (event.xcookie.evtype == XI_HierarchyChanged) {
        const auto& hierarchy = *static_cast<const XIHierarchyEvent*>(event.xcookie.data);
        for (int i = 0; i < hierarchy.num_info; ++i) {
            const auto& item = hierarchy.info[i];
            if ((item.flags & (XISlaveRemoved | XIDeviceDisabled | XISlaveDetached)) && r.sources.contains(item.deviceid))
                r.disconnect(item.deviceid);
        }
        r.refresh(); return true;
    }
    if (event.xcookie.evtype == XI_RawMotion) {
        const auto& raw = *static_cast<const XIRawEvent*>(event.xcookie.data);
        if (raw.deviceid != r.pointer || !r.sources.contains(raw.sourceid) || r.sources.at(raw.sourceid).kind != v1::MOUSE) return true;
        auto value = r.event(raw.sourceid); auto* motion = value.mutable_motion();
        unsigned index = 0;
        for (int axis = 0; axis < raw.valuators.mask_len * 8; ++axis) if (XIMaskIsSet(raw.valuators.mask, axis)) {
            if (axis == 0) motion->set_dx(raw.raw_values[index]);
            else if (axis == 1) motion->set_dy(raw.raw_values[index]);
            ++index;
        }
        r.emit(value); return true;
    }
    if (event.xcookie.evtype != XI_KeyPress && event.xcookie.evtype != XI_KeyRelease &&
        event.xcookie.evtype != XI_ButtonPress && event.xcookie.evtype != XI_ButtonRelease) return false;
    const auto& input = *static_cast<const XIDeviceEvent*>(event.xcookie.data);
    if (!r.sources.contains(input.sourceid)) return true;
    const auto kind = r.sources.at(input.sourceid).kind;
    auto value = r.event(input.sourceid);
    if (event.xcookie.evtype == XI_KeyPress || event.xcookie.evtype == XI_KeyRelease) {
        if (kind != v1::KEYBOARD || input.deviceid != r.keyboard) return true;
        const auto symbol = XkbKeycodeToKeysym(r.display, static_cast<KeyCode>(input.detail), 0, 0);
        const char* name = XKeysymToString(symbol); if (!name) return true;
        value.mutable_key()->set_keysym(name); value.mutable_key()->set_pressed(event.xcookie.evtype == XI_KeyPress);
        value.mutable_key()->set_repeat((input.flags & XIKeyRepeat) != 0);
    } else {
        if (kind != v1::MOUSE || input.deviceid != r.pointer) return true;
        if (input.detail == 4 || input.detail == 5) {
            if (event.xcookie.evtype != XI_ButtonPress) return true;
            value.mutable_scroll()->set_steps_y(input.detail == 4 ? 1 : -1);
        } else {
            if (input.detail < 1 || input.detail > 3) return true;
            value.mutable_button()->set_button(static_cast<unsigned>(input.detail));
            value.mutable_button()->set_pressed(event.xcookie.evtype == XI_ButtonPress);
        }
    }
    r.emit(value); return true;
}
void PhysicalCapture::pump(DeviceAdapters::Clock::time_point now) {
    auto& r = *impl_;
    need(now >= r.heartbeat, "Physical capture clock moved backwards");
    if (r.heartbeat != DeviceAdapters::Clock::time_point{} && now - r.heartbeat < std::chrono::milliseconds(100)) return;
    for (const auto& [id, source] : r.sources) {
        (void)source; auto event = r.event(id); event.mutable_heartbeat(); r.emit(event);
    }
    r.heartbeat = now;
}
}
