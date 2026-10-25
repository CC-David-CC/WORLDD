#pragma once
#include "control/actor_control.hpp"
#include "control/profiles.hpp"
#include "core/geometry.hpp"
#include <array>
#include <chrono>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <set>

namespace spatial::control {
// Thread-safe latest movement state. Capture ingestion never performs actor I/O.
class MotionIntent final {
public:
    using Clock = std::chrono::steady_clock;
    void configure(const v1::ControlProfiles& profiles, bool enabled, Clock::time_point now);
    bool observe(const v1::DeviceEvent& event, Clock::time_point captured, Clock::time_point now);
    std::optional<core::v1::ActorInputRequest> next(Clock::time_point now);
    void clear();
    void clear_motion(Clock::time_point now);
private:
    struct Source {
        v1::DeviceKind kind = v1::DEVICE_UNSPECIFIED;
        std::uint64_t generation = 0, sequence = 0;
        Clock::time_point heartbeat{}, axes_time{};
        std::set<std::string> keys;
        std::array<double, 6> axes{};
        bool has_axes = false;
    };
    mutable std::mutex mutex_;
    v1::ControlProfiles profiles_;
    std::map<std::string, Source> sources_;
    std::map<std::string, std::uint64_t> retired_generations_;
    bool enabled_ = false, last_active_ = false;
    Clock::time_point fresh_after_ = Clock::time_point::min();
    Clock::time_point last_refresh_ = Clock::time_point::min();
};

// Human device adapters only translate gestures. Scripted agents call the same
// ActorControl::propose boundary directly with existing semantic actions.
class DeviceAdapters final {
public:
    using Clock = std::chrono::steady_clock;
    using ClockNow = std::function<Clock::time_point()>;
    explicit DeviceAdapters(ActorControl& actor, v1::ControlProfiles profiles = default_profiles(), ClockNow clock = Clock::now);
    ~DeviceAdapters();
    bool event(const v1::DeviceEvent& event, Clock::time_point now);
    bool event(const v1::DeviceEvent& event, Clock::time_point captured, Clock::time_point now);
    void tick(Clock::time_point now);
    void cancel();
    // Cache-only reset after a checked world cancellation acknowledgement.
    void acknowledged_cancellation();
    void set_profiles(const v1::ControlProfiles& profiles);
    void select_profile(const std::string& id);
    void set_menu(bool open);
    void set_viewport(std::uint32_t width, std::uint32_t height, double vertical_fov);
    const v1::ControlProfiles& profiles() const noexcept { return profiles_; }
    v1::ControlStatus status() const;
    std::array<double, 6> last_raw_axes() const noexcept { return last_raw_; }
    std::shared_ptr<MotionIntent> motion_intent() const noexcept { return motion_intent_; }

private:
    struct Source {
        v1::DeviceKind kind;
        std::uint64_t generation, sequence;
        Clock::time_point heartbeat, axes_time;
        std::set<std::string> keys;
        std::set<unsigned> buttons;
        std::set<std::string> physical_keys;
        // Only modifiers pressed after the latest cancellation may join a tap.
        std::set<std::string> fresh_modifiers;
        std::set<unsigned> physical_buttons;
        std::array<double, 6> axes{};
        bool has_axes = false;
    };
    core::v1::ActorActionReply send(core::v1::ActorActionRequest request);
    v1::BindingAction binding(const std::string& key) const;
    void key(Source& source, const v1::DeviceKey& key);
    core::v1::AimReply aim();
    bool application_target(const core::v1::AimReply& target);
    void pointer(Source& source, const v1::DeviceButton& button);
    void motion(const v1::DeviceMotion& motion);
    void clear_state();
    bool key_held(const std::string& key) const;
    bool button_held(unsigned button) const;
    void switch_mode(bool navigating);
    ActorControl& actor_;
    std::shared_ptr<MotionIntent> motion_intent_ = std::make_shared<MotionIntent>();
    ClockNow clock_;
    Clock::time_point fresh_after_ = Clock::time_point::min();
    std::optional<Clock::time_point> event_capture_;
    v1::ControlProfiles profiles_;
    std::map<std::string, Source> sources_;
    std::map<std::string, std::uint64_t> retired_generations_;
    bool navigating_ = true, menu_ = false, axes_sent_ = false;
    bool click_enters_ = false;
    std::uint32_t width_ = 512, height_ = 512;
    double fov_ = 1.5707963267948966, cursor_x_ = 256, cursor_y_ = 256;
    std::array<double, 6> last_raw_{};
};
}
