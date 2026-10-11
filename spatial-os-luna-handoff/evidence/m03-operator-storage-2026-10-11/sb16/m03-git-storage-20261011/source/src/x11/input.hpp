#pragma once
#include "input.pb.h"
#include <X11/Xlib.h>
namespace spatial {
namespace native_input = input::v1;
// The caller owns this Display and serializes all X access on its X thread.
class NativeInput {
public:
    explicit NativeInput(Display* display);
    native_input::Reply exchange(const native_input::Request& request);
    native_input::Reply call(native_input::Request request);
private:
    Display* display_;
    int opcode_ = 0;
    std::uint64_t sequence_ = 0;
};
}
