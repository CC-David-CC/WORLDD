#pragma once
#include "control/adapters.hpp"
#include <X11/Xlib.h>
#include <functional>
#include <memory>
#include <vector>

namespace spatial::control {
struct PhysicalSource {
    std::filesystem::path device_node;
    v1::DeviceKind kind;
};
// Runs on the compositor X thread. Emit must enqueue without blocking on worldd.
// Only configured kernel device nodes attached to the client's core seat qualify.
// Actor seats and XTEST devices do not inherit physical input authority.
class PhysicalCapture final {
public:
    using Emit = std::function<void(const v1::DeviceEvent&)>;
    PhysicalCapture(Display* display, Window output, std::vector<PhysicalSource> sources, Emit emit);
    ~PhysicalCapture();
    PhysicalCapture(const PhysicalCapture&) = delete;
    PhysicalCapture& operator=(const PhysicalCapture&) = delete;
    bool handle(XEvent& event);
    void pump(DeviceAdapters::Clock::time_point now);
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
