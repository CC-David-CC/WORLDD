#pragma once
#include "input.pb.h"
#include <X11/Xlib.h>
#include <memory>
namespace spatial {
class NativeChannel;
namespace native_input = input::v1;
// The caller owns this Display and serializes all X access on its X thread.
class NativeInput {
public:
    explicit NativeInput(Display* display);
    // Consumes sealed world-scoped configuration. Disconnects fail without terminating graphics.
    explicit NativeInput(int sealed_config_fd);
    ~NativeInput();
    NativeInput(const NativeInput&) = delete;
    NativeInput& operator=(const NativeInput&) = delete;
    native_input::Reply exchange(const native_input::Request& request);
    native_input::Reply call(native_input::Request request);
    const std::string& server_id() const;
private:
    Display* display_ = nullptr;
    std::unique_ptr<NativeChannel> scoped_;
    int opcode_ = 0;
    std::uint64_t sequence_ = 0;
};
}
