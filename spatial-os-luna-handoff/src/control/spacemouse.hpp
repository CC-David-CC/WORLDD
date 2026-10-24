#pragma once
#include "control/adapters.hpp"
#include <functional>
#include <mutex>

namespace spatial::control {
// One native libspnav connection per human input process. No inference runs here.
class SpaceMouseSource final {
public:
    using Emit = std::function<void(const v1::DeviceEvent&)>;
    explicit SpaceMouseSource(Emit emit);
    ~SpaceMouseSource();
    SpaceMouseSource(const SpaceMouseSource&) = delete;
    SpaceMouseSource& operator=(const SpaceMouseSource&) = delete;
    // Explicit activation. Absence of a daemon leaves other controls usable.
    bool open();
    void pump(DeviceAdapters::Clock::time_point now);
    void close() noexcept;
    int descriptor() const;
    bool connected() const noexcept { return active_; }
private:
    v1::DeviceEvent event();
    void activate();
    void disconnect();
    Emit emit_;
    std::unique_lock<std::mutex> library_owner_;
    bool opened_ = false, active_ = false;
    std::uint64_t generation_ = 0, sequence_ = 0;
    DeviceAdapters::Clock::time_point heartbeat_{};
};
}
