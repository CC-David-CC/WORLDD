// Thin diagnostic transport. The production bridge and X server do all routing.
#include "x11/input.hpp"
#include "core/protocol.hpp"
#include <cstdlib>
#include <iostream>
#include <poll.h>
#include <string_view>
#include <unistd.h>
int main(int argc, char** argv) {
    try {
        const char* name = std::getenv("SPATIAL_TEST_DISPLAY");
        if (!name || !*name) throw std::runtime_error("Dedicated test display required");
        Display* display = XOpenDisplay(name);
        if (!display) throw std::runtime_error("Cannot open test display");
        const bool owner = argc == 2 && std::string_view(argv[1]) == "--owner";
        if (owner) XSelectInput(display, DefaultRootWindow(display), SubstructureRedirectMask | SubstructureNotifyMask);
        XSync(display, False);
        spatial::NativeInput bridge(display);
        spatial::native_input::Reply ready; ready.set_protocol_version(1);
        spatial::write_frame(std::cout, ready);
        while (true) {
            while (XPending(display)) {
                XEvent event; XNextEvent(display, &event);
                if (owner && event.type == MapRequest) XMapWindow(display, event.xmaprequest.window);
                if (owner && event.type == ConfigureRequest) {
                    const auto& c = event.xconfigurerequest;
                    XWindowChanges changes{c.x, c.y, c.width, c.height, c.border_width, c.above, c.detail};
                    XConfigureWindow(display, c.window, c.value_mask, &changes);
                }
            }
            XFlush(display);
            pollfd descriptors[]{{STDIN_FILENO, POLLIN, 0}, {ConnectionNumber(display), POLLIN, 0}};
            if (poll(descriptors, 2, -1) < 0) throw std::runtime_error("Bridge poll failed");
            if (descriptors[0].revents & (POLLIN | POLLHUP)) {
                std::string bytes;
                if (!spatial::read_frame(std::cin, bytes)) break;
                spatial::native_input::Request request;
                if (!request.ParseFromString(bytes)) throw std::runtime_error("Bad input request");
                spatial::write_frame(std::cout, bridge.exchange(request));
            }
        }
        XCloseDisplay(display);
        return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 2; }
}
