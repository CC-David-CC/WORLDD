#pragma once
#include "core/protocol.hpp"
#include "x11/input.hpp"
#include <map>
namespace spatial {
class XSurfaces;
// Runs only on the compositor X thread. worldd owns actor authorization.
class XInputRouter {
public:
    XInputRouter(Display* display, NativeInput& input, XSurfaces& surfaces);
    wire::NativeActorInput execute(const wire::RendererInputCommand& command);
    void snapshot(wire::NativeSnapshot& snapshot);
    // Read an existing seat. Never create devices or acquire application control.
    native_input::Reply native_seat(const std::string& actor);
    // Erase each seat only after the server confirms its closure.
    void close_all();
private:
    struct Seat {
        std::uint64_t id = 0;
        wire::SurfaceHandle focus;
        std::map<unsigned, std::string> keys;
    };
    wire::NativeActorInput state(const std::string& actor, Seat& seat, const native_input::Reply& reply);
    native_input::Reply checked(native_input::Request request);
    Display* display_;
    NativeInput& input_;
    XSurfaces& surfaces_;
    std::map<std::string, Seat> seats_;
};
}
