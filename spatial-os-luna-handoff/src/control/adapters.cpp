#include "control/adapters.hpp"
#include "core/protocol.hpp"
#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace spatial::control {
namespace {
void need(bool value, const char* message) { if (!value) throw std::invalid_argument(message); }
constexpr auto source_timeout = std::chrono::milliseconds(350);
constexpr auto axis_timeout = std::chrono::milliseconds(250);
constexpr auto event_max_age = std::chrono::milliseconds(250);
constexpr std::size_t maximum_held_keys = 256;
struct ExpiredGesture {};
bool known_kind(v1::DeviceKind kind) { return kind == v1::KEYBOARD || kind == v1::MOUSE || kind == v1::SPACEMOUSE; }
bool modifier_key(const std::string& key) {
    static const std::set<std::string> modifiers{
        "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
        "Meta_L", "Meta_R", "Super_L", "Super_R", "Hyper_L", "Hyper_R",
        "ISO_Level3_Shift", "Mode_switch"};
    return modifiers.contains(key);
}
}
void MotionIntent::configure(const v1::ControlProfiles& profiles, bool enabled, Clock::time_point now) {
    validate_profiles(profiles);
    std::lock_guard lock(mutex_);
    profiles_ = profiles;
    if (enabled_ != enabled) {
        for (auto& [id, source] : sources_) {
            (void)id; source.keys.clear(); source.axes.fill(0); source.has_axes = false;
        }
        fresh_after_ = now;
    }
    enabled_ = enabled;
    fresh_after_ = now;
}
void MotionIntent::clear() {
    std::lock_guard lock(mutex_);
    sources_.clear(); retired_generations_.clear(); last_active_ = false; enabled_ = false;
    fresh_after_ = Clock::now(); last_refresh_ = Clock::time_point::min();
}
void MotionIntent::clear_motion(Clock::time_point now) {
    std::lock_guard lock(mutex_);
    for (auto& [id, source] : sources_) {
        (void)id; source.keys.clear(); source.axes.fill(0); source.has_axes = false;
    }
    fresh_after_ = now;
}
bool MotionIntent::observe(const v1::DeviceEvent& event, Clock::time_point captured, Clock::time_point now) {
    try {
        validate_message(event);
        if (event.protocol_version() != 1 || event.source_id().empty() || event.source_id().size() > 80 ||
            event.source_id().find('\0') != std::string::npos || !event.source_generation() ||
            !event.event_sequence() || event.event_case() == v1::DeviceEvent::EVENT_NOT_SET || captured > now)
            return false;
        std::lock_guard lock(mutex_);
        auto found = sources_.find(event.source_id());
        if (event.has_connected()) {
            const auto retired = retired_generations_.find(event.source_id());
            if (!known_kind(event.connected().kind()) || found != sources_.end() || sources_.size() >= 64 ||
                (retired == retired_generations_.end() && sources_.size() + retired_generations_.size() >= 64) ||
                (retired != retired_generations_.end() && event.source_generation() <= retired->second)) return false;
            Source source; source.kind = event.connected().kind(); source.generation = event.source_generation();
            source.sequence = event.event_sequence(); source.heartbeat = captured; source.axes_time = captured;
            sources_.emplace(event.source_id(), std::move(source));
            return true;
        }
        if (found == sources_.end() || found->second.generation != event.source_generation() ||
            event.event_sequence() <= found->second.sequence || captured < found->second.heartbeat) return false;
        auto& source = found->second;
        source.sequence = event.event_sequence(); source.heartbeat = captured;
        if (event.has_disconnected()) {
            if (!retired_generations_.contains(event.source_id()) && retired_generations_.size() >= 64) return false;
            retired_generations_[event.source_id()] = source.generation;
            sources_.erase(found);
            for (auto& [id, current] : sources_) {
                (void)id; current.keys.clear(); current.axes.fill(0); current.has_axes = false;
            }
            fresh_after_ = now;
            return true;
        }
        if (captured < fresh_after_ || now - captured >= event_max_age) {
            source.keys.clear(); source.axes.fill(0); source.has_axes = false; fresh_after_ = now;
            return true;
        }
        if (event.has_heartbeat()) return true;
        if (event.has_key()) {
            if (source.kind != v1::KEYBOARD || event.key().keysym().empty() ||
                event.key().keysym().size() > 64 || event.key().keysym().find('\0') != std::string::npos) return false;
            if (!event.key().repeat()) {
                if (event.key().pressed()) {
                    if (!source.keys.contains(event.key().keysym()) && source.keys.size() >= maximum_held_keys) return false;
                    source.keys.insert(event.key().keysym());
                }
                else source.keys.erase(event.key().keysym());
            }
        } else if (event.has_axes()) {
            if (source.kind != v1::SPACEMOUSE || event.axes().raw_size() != 6) return false;
            const auto& profile = active_profile(profiles_);
            source.axes.fill(0); source.has_axes = profile.spacemouse_calibrated();
            if (source.has_axes) for (unsigned i = 0; i < 6; ++i) {
                const auto& calibration = profile.spacemouse_axes(static_cast<int>(i));
                const auto index = static_cast<unsigned>(std::abs(calibration.raw_axis()) - 1);
                const double raw = event.axes().raw(static_cast<int>(index));
                if (!std::isfinite(raw) || std::abs(raw) > 1e9) return false;
                source.axes[i] = std::clamp((raw - calibration.center()) / calibration.full_scale() *
                    (calibration.raw_axis() < 0 ? -1 : 1), -1., 1.);
            }
            source.axes_time = captured;
        } else if (event.has_motion()) {
            if (source.kind != v1::MOUSE) return false;
        } else if (event.has_button()) {
            if (source.kind != v1::MOUSE && source.kind != v1::SPACEMOUSE) return false;
        } else if (event.has_scroll()) {
            if (source.kind != v1::MOUSE || event.scroll().steps_y() < -64 || event.scroll().steps_y() > 64) return false;
        }
        return true;
    } catch (...) { return false; }
}
std::optional<core::v1::ActorInputRequest> MotionIntent::next(Clock::time_point now) {
    constexpr auto refresh_interval = std::chrono::milliseconds(80);
    std::lock_guard lock(mutex_);
    if (!enabled_ || (last_refresh_ != Clock::time_point::min() && now - last_refresh_ < refresh_interval))
        return std::nullopt;
    const bool source_lost = std::any_of(sources_.begin(), sources_.end(), [&](const auto& item) {
        return now < item.second.heartbeat || now - item.second.heartbeat >= source_timeout;
    });
    if (source_lost) for (auto& [id, source] : sources_) {
        (void)id; source.keys.clear(); source.axes.fill(0); source.has_axes = false;
    }
    std::array<double, 6> axes{};
    const auto& profile = active_profile(profiles_);
    for (const auto& [id, source] : sources_) {
        (void)id;
        for (const auto& key : source.keys) switch ([&] {
            for (const auto& binding : profile.bindings()) if (binding.keysym() == key) return binding.action();
            return v1::BINDING_UNSPECIFIED;
        }()) {
        case v1::FORWARD: axes[2] -= 1; break; case v1::BACKWARD: axes[2] += 1; break;
        case v1::LEFT: axes[0] -= 1; break; case v1::RIGHT: axes[0] += 1; break;
        case v1::UP: axes[1] += 1; break; case v1::DOWN: axes[1] -= 1; break;
        case v1::ROLL_LEFT: if (profile.movement() == v1::FREE_FLIGHT) axes[5] += 1; break;
        case v1::ROLL_RIGHT: if (profile.movement() == v1::FREE_FLIGHT) axes[5] -= 1; break;
        default: break;
        }
    }
    std::array<double, 6> spacemouse{};
    for (const auto& [id, source] : sources_) {
        (void)id;
        if (source.has_axes && now >= source.axes_time && now - source.axes_time < axis_timeout)
            for (unsigned i = 0; i < 6; ++i) spacemouse[i] += source.axes[i];
    }
    const bool level_keyboard = profile.movement() == v1::LEVEL_MOVEMENT &&
        spacemouse[0] == 0 && spacemouse[1] == 0 && spacemouse[2] == 0;
    if (profile.movement() == v1::LEVEL_MOVEMENT && !level_keyboard)
        for (unsigned i = 0; i < 3; ++i) axes[i] = 0;
    for (unsigned i = 0; i < 6; ++i) axes[i] = std::clamp(axes[i] + spacemouse[i], -1., 1.);
    const bool active = std::any_of(axes.begin(), axes.end(), [](double value) { return value != 0; });
    if (!active && !last_active_) return std::nullopt;
    core::v1::ActorInputRequest result;
    for (const auto value : axes) result.add_axes(value);
    result.set_translation_frame(level_keyboard ? core::v1::LEVEL_TRANSLATION : core::v1::BODY_TRANSLATION);
    last_active_ = active; last_refresh_ = now;
    return result;
}

DeviceAdapters::DeviceAdapters(ActorControl& actor, v1::ControlProfiles profiles, ClockNow clock)
    : actor_(actor), clock_(std::move(clock)), profiles_(std::move(profiles)) {
    need(bool(clock_), "Device adapters require a monotonic clock");
    validate_profiles(profiles_);
    navigating_ = active_profile(profiles_).interaction() != v1::HOLD_TO_NAVIGATE;
    motion_intent_->configure(profiles_, navigating_, clock_());
}
DeviceAdapters::~DeviceAdapters() { motion_intent_->clear(); }
core::v1::ActorActionReply DeviceAdapters::send(core::v1::ActorActionRequest request) {
    if (event_capture_) {
        const auto now = clock_();
        if (now < *event_capture_ || now - *event_capture_ >= event_max_age) {
            cancel();
            throw ExpiredGesture{};
        }
    }
    try { return actor_.propose(actor_.context().generation, request); }
    catch (const ActorInputCancelled&) { acknowledged_cancellation(); throw; }
}
void DeviceAdapters::clear_state() {
    for (auto& [id, source] : sources_) {
        (void)id; source.keys.clear(); source.buttons.clear(); source.fresh_modifiers.clear();
        source.axes.fill(0); source.has_axes = false;
    }
    axes_sent_ = false; click_enters_ = false;
    motion_intent_->clear_motion(clock_());
}
void DeviceAdapters::acknowledged_cancellation() {
    clear_state(); navigating_ = true; fresh_after_ = clock_();
    motion_intent_->configure(profiles_, navigating_, fresh_after_);
}
void DeviceAdapters::cancel() {
    clear_state(); navigating_ = true;
    actor_.cancel(actor_.context().generation);
    fresh_after_ = clock_();
    motion_intent_->configure(profiles_, navigating_, fresh_after_);
}
void DeviceAdapters::switch_mode(bool navigating) {
    if (navigating_ == navigating) return;
    cancel();
    navigating_ = navigating;
    motion_intent_->configure(profiles_, navigating_ && !menu_, clock_());
}
void DeviceAdapters::set_menu(bool open) {
    if (menu_ == open) return;
    cancel(); menu_ = open;
    motion_intent_->configure(profiles_, navigating_ && !menu_, clock_());
}
void DeviceAdapters::set_profiles(const v1::ControlProfiles& profiles) {
    validate_profiles(profiles); // Invalid edits leave both configuration and input untouched.
    auto replacement = profiles;
    cancel();
    profiles_.Swap(&replacement);
    navigating_ = active_profile(profiles_).interaction() != v1::HOLD_TO_NAVIGATE;
    motion_intent_->configure(profiles_, navigating_ && !menu_, clock_());
}
void DeviceAdapters::select_profile(const std::string& id) {
    auto replacement = profiles_; replacement.set_active_profile(id); set_profiles(replacement);
}
void DeviceAdapters::set_viewport(std::uint32_t width, std::uint32_t height, double vertical_fov) {
    need(width > 0 && width <= 16384 && height > 0 && height <= 16384 &&
         std::isfinite(vertical_fov) && vertical_fov > 0 && vertical_fov < std::acos(-1.0), "Invalid control viewport");
    if (width != width_ || height != height_ || vertical_fov != fov_) {
        cancel(); width_ = width; height_ = height; fov_ = vertical_fov;
        cursor_x_ = width / 2.; cursor_y_ = height / 2.;
    }
}
v1::BindingAction DeviceAdapters::binding(const std::string& key) const {
    for (const auto& b : active_profile(profiles_).bindings()) if (b.keysym() == key) return b.action();
    return v1::BINDING_UNSPECIFIED;
}
bool DeviceAdapters::key_held(const std::string& key) const {
    return std::any_of(sources_.begin(), sources_.end(), [&](const auto& item) { return item.second.keys.contains(key); });
}
bool DeviceAdapters::button_held(unsigned button) const {
    return std::any_of(sources_.begin(), sources_.end(), [&](const auto& item) { return item.second.buttons.contains(button); });
}
void DeviceAdapters::key(Source& source, const v1::DeviceKey& event) {
    need(!event.keysym().empty() && event.keysym().size() <= 64 && event.keysym().find('\0') == std::string::npos,
         "Invalid physical keysym");
    if (event.repeat()) return;
    if (event.pressed()) {
        need(source.physical_keys.contains(event.keysym()) || source.physical_keys.size() < maximum_held_keys,
             "Keyboard held-key limit reached");
        if (!source.physical_keys.insert(event.keysym()).second) return;
        if (modifier_key(event.keysym())) source.fresh_modifiers.insert(event.keysym());
    } else {
        if (!source.physical_keys.erase(event.keysym())) return;
        source.fresh_modifiers.erase(event.keysym());
    }
    const auto action = binding(event.keysym());
    const auto style = active_profile(profiles_).interaction();
    if (action == v1::CANCEL || action == v1::OPEN_CONTROLS || action == v1::SWITCH_MODE) {
        if (action == v1::CANCEL && event.pressed()) cancel();
        else if (action == v1::OPEN_CONTROLS && event.pressed()) set_menu(!menu_);
        else if (action == v1::SWITCH_MODE && !menu_) {
            if (style == v1::HOLD_TO_NAVIGATE) switch_mode(event.pressed());
            else if (event.pressed()) switch_mode(!navigating_);
        }
        return;
    }
    if (menu_) return;
    const bool globally_down = key_held(event.keysym());
    if (event.pressed()) {
        if (!source.keys.insert(event.keysym()).second) return;
    } else {
        if (!source.keys.erase(event.keysym())) return;
    }
    if (navigating_) return;
    if ((event.pressed() && globally_down) || (!event.pressed() && key_held(event.keysym()))) return;
    core::v1::ActorActionRequest request;
    if (active_profile(profiles_).key_delivery() == v1::KEY_TAP) {
        // XKB repeat is time based. Send a complete tap in one native request,
        // so a slow world or renderer reply cannot leave a text key held.
        if (!event.pressed() || modifier_key(event.keysym())) return;
        std::vector<std::string> chord;
        for (const auto& [id, candidate] : sources_) {
            (void)id;
            for (const auto& held : candidate.fresh_modifiers)
                if (std::find(chord.begin(), chord.end(), held) == chord.end()) chord.push_back(held);
        }
        need(chord.size() < 8, "Too many simultaneous keyboard modifiers");
        chord.push_back(event.keysym());
        auto* batch = request.mutable_key_tap_batch();
        auto* tap = batch->add_chords();
        for (const auto& keysym : chord) tap->add_keysyms(keysym);
        send(std::move(request));
        return;
    }
    request.mutable_key()->set_keysym(event.keysym()); request.mutable_key()->set_pressed(event.pressed());
    send(std::move(request));
}
core::v1::AimReply DeviceAdapters::aim() {
    core::v1::ActorActionRequest request;
    auto* direction = request.mutable_aim()->mutable_direction();
    const double scale = std::tan(fov_ / 2);
    direction->set_x(navigating_ ? 0 : (2 * cursor_x_ - width_) * scale / height_);
    direction->set_y(navigating_ ? 0 : (height_ - 2 * cursor_y_) * scale / height_);
    direction->set_z(-1);
    const auto reply = send(std::move(request));
    need(reply.has_aim(), "Actor aim reply is missing");
    return reply.aim();
}
bool DeviceAdapters::application_target(const core::v1::AimReply& target) {
    if (!target.has_hit()) return false;
    core::v1::ActorActionRequest request;
    request.mutable_observe()->set_width(width_); request.mutable_observe()->set_height(height_);
    const auto reply = send(std::move(request));
    need(reply.has_observation() && reply.observation().actor() == actor_.context().actor &&
         reply.observation().viewport().width() == width_ && reply.observation().viewport().height() == height_,
         "Observation changed the bound actor or viewport");
    return std::any_of(reply.observation().visible().begin(), reply.observation().visible().end(), [&](const auto& item) {
        return item.id() == target.hit().id() && item.generation() == target.hit().generation() && !item.has_kind();
    });
}
void DeviceAdapters::pointer(Source& source, const v1::DeviceButton& event) {
    need(event.button() >= 1 && event.button() <= 3, "Unsupported pointer button");
    if (event.pressed()) {
        if (!source.physical_buttons.insert(event.button()).second) return;
    } else if (!source.physical_buttons.erase(event.button())) return;
    if (menu_) return;
    const bool globally_down = button_held(event.button());
    if (!event.pressed() && !source.buttons.contains(event.button())) return;
    if (event.pressed() && source.buttons.contains(event.button())) return;
    // Cancel old navigation before acquiring new application focus.
    if (event.pressed() && navigating_ && active_profile(profiles_).interaction() == v1::CLICK_TO_CONTROL)
        cancel();
    // Aim and acquire through worldd. A rejection never enters application mode.
    if (event.pressed()) {
        const auto target = aim();
        const bool captured = std::any_of(sources_.begin(), sources_.end(), [](const auto& item) { return !item.second.buttons.empty(); });
        if (!captured && !application_target(target)) return;
    }
    core::v1::ActorActionRequest request;
    request.mutable_pointer()->set_button(event.button()); request.mutable_pointer()->set_pressed(event.pressed());
    if ((event.pressed() && !globally_down) || (!event.pressed() &&
         std::count_if(sources_.begin(), sources_.end(), [&](const auto& item) { return item.second.buttons.contains(event.button()); }) == 1))
        send(std::move(request));
    if (event.pressed()) {
        source.buttons.insert(event.button());
        if (navigating_ && active_profile(profiles_).interaction() == v1::CLICK_TO_CONTROL) click_enters_ = true;
    } else {
        source.buttons.erase(event.button());
        if (click_enters_ && !button_held(1) && !button_held(2) && !button_held(3)) {
            // Old input was cancelled before the fresh click. Keep its new focus.
            click_enters_ = false; navigating_ = false;
        }
    }
}
void DeviceAdapters::motion(const v1::DeviceMotion& event) {
    need(std::isfinite(event.dx()) && std::isfinite(event.dy()) &&
         std::abs(event.dx()) <= 100000 && std::abs(event.dy()) <= 100000, "Invalid pointer motion");
    if (menu_ || (event.dx() == 0 && event.dy() == 0)) return;
    if (!navigating_) {
        cursor_x_ = std::clamp(cursor_x_ + event.dx(), 0., std::nextafter(double(width_), 0.));
        cursor_y_ = std::clamp(cursor_y_ + event.dy(), 0., std::nextafter(double(height_), 0.));
        aim(); return;
    }
    const auto& profile = active_profile(profiles_);
    double pitch = -event.dy() * profile.mouse_radians_per_count() * (profile.invert_mouse_y() ? -1 : 1);
    double yaw = -event.dx() * profile.mouse_radians_per_count();
    need(std::hypot(pitch, yaw) <= 16, "Pointer sample exceeds the bounded look batch");
    // Split large physical samples into bounded semantic deltas without losing counts.
    const auto count = static_cast<unsigned>(std::max(1., std::ceil(std::hypot(pitch, yaw) / 1.0)));
    for (unsigned i = 0; i < count; ++i) {
        core::v1::ActorActionRequest request;
        auto* delta = request.mutable_relative_look()->mutable_angular_delta();
        delta->set_x(pitch / count); delta->set_y(yaw / count);
        send(std::move(request));
    }
}
bool DeviceAdapters::event(const v1::DeviceEvent& event, Clock::time_point now) {
    return this->event(event, now, now);
}
bool DeviceAdapters::event(const v1::DeviceEvent& event, Clock::time_point captured, Clock::time_point now) {
    validate_message(event);
    need(event.protocol_version() == 1 && !event.source_id().empty() && event.source_id().size() <= 80 &&
         event.source_id().find('\0') == std::string::npos && event.source_generation() > 0 &&
         event.event_sequence() > 0 && event.event_case() != v1::DeviceEvent::EVENT_NOT_SET, "Invalid device event envelope");
    need(captured >= Clock::time_point{} && now >= captured, "Invalid or future capture timestamp");
    const bool fresh = captured >= fresh_after_ && now - captured < event_max_age;
    auto found = sources_.find(event.source_id());
    if (event.has_connected()) {
        need(known_kind(event.connected().kind()) && found == sources_.end() && sources_.size() < 64 &&
             (retired_generations_.contains(event.source_id()) || sources_.size() + retired_generations_.size() < 64) &&
             (!retired_generations_.contains(event.source_id()) || event.source_generation() > retired_generations_.at(event.source_id())),
             "Invalid, duplicate, or retired device connection");
        Source source{}; source.kind = event.connected().kind(); source.generation = event.source_generation();
        source.sequence = event.event_sequence(); source.heartbeat = captured; source.axes_time = captured;
        sources_.emplace(event.source_id(), std::move(source));
        return fresh;
    }
    need(found != sources_.end() && found->second.generation == event.source_generation() &&
         event.event_sequence() > found->second.sequence && captured >= found->second.heartbeat, "Stale or unknown device event");
    auto& source = found->second;
    source.sequence = event.event_sequence();
    if (event.has_disconnected()) {
        need(retired_generations_.contains(event.source_id()) || retired_generations_.size() < 64,
             "Device retirement history limit reached");
        retired_generations_[event.source_id()] = source.generation;
        sources_.erase(found); cancel(); return fresh;
    }
    source.heartbeat = captured;
    if (event.has_heartbeat()) return fresh;
    if (!fresh) {
        // Old gestures cannot resume input after a checked mode/context barrier.
        // Still remember physical edges so a later release permits a fresh press.
        const bool active = axes_sent_ || !navigating_ || std::any_of(sources_.begin(), sources_.end(), [](const auto& item) {
            return !item.second.keys.empty() || !item.second.buttons.empty();
        });
        if (active) cancel();
        if (event.has_key()) {
            need(source.kind == v1::KEYBOARD && !event.key().keysym().empty() && event.key().keysym().size() <= 64 &&
                 event.key().keysym().find('\0') == std::string::npos, "Invalid stale key event");
            if (!event.key().repeat()) {
                if (event.key().pressed()) {
                    need(source.physical_keys.contains(event.key().keysym()) || source.physical_keys.size() < maximum_held_keys,
                         "Stale keyboard held-key limit reached");
                    source.physical_keys.insert(event.key().keysym());
                }
                else {
                    source.physical_keys.erase(event.key().keysym());
                    source.fresh_modifiers.erase(event.key().keysym());
                }
            }
        } else if (event.has_button()) {
            need((source.kind == v1::MOUSE || source.kind == v1::SPACEMOUSE) &&
                 event.button().button() >= 1 && event.button().button() <= 3, "Invalid stale button event");
            if (event.button().pressed()) source.physical_buttons.insert(event.button().button());
            else source.physical_buttons.erase(event.button().button());
        }
        return false;
    }
    event_capture_ = captured;
    struct ResetCapture {
        std::optional<Clock::time_point>& capture;
        ~ResetCapture() { capture.reset(); }
    } reset{event_capture_};
    try {
    if (event.has_key()) { need(source.kind == v1::KEYBOARD, "Key from wrong device kind"); key(source, event.key()); }
    else if (event.has_motion()) { need(source.kind == v1::MOUSE, "Motion from wrong device kind"); motion(event.motion()); }
    else if (event.has_button()) {
        need(source.kind == v1::MOUSE || source.kind == v1::SPACEMOUSE, "Button from wrong device kind");
        pointer(source, event.button());
    } else if (event.has_scroll()) {
        need(source.kind == v1::MOUSE && event.scroll().steps_y() >= -64 && event.scroll().steps_y() <= 64,
             "Invalid scroll device or magnitude");
        if (!menu_) {
            if (!application_target(aim())) return true;
            core::v1::ActorActionRequest request;
            request.mutable_scroll()->set_steps_y(event.scroll().steps_y()); send(std::move(request));
        }
    } else if (event.has_axes()) {
        need(source.kind == v1::SPACEMOUSE && event.axes().raw_size() == 6, "Invalid SpaceMouse sample");
        std::array<double, 6> raw{};
        for (unsigned i = 0; i < 6; ++i) {
            raw[i] = event.axes().raw(static_cast<int>(i));
            need(std::isfinite(raw[i]) && std::abs(raw[i]) <= 1e9, "Invalid raw SpaceMouse axis");
        }
        last_raw_ = raw;
        const auto& profile = active_profile(profiles_);
        std::array<double, 6> normalized{};
        if (profile.spacemouse_calibrated()) for (unsigned i = 0; i < 6; ++i) {
            const auto& calibration = profile.spacemouse_axes(static_cast<int>(i));
            const auto index = static_cast<unsigned>(std::abs(calibration.raw_axis()) - 1);
            normalized[i] = std::clamp((raw[index] - calibration.center()) / calibration.full_scale() *
                                     (calibration.raw_axis() < 0 ? -1 : 1), -1., 1.);
        }
        source.axes = normalized; source.axes_time = captured; source.has_axes = profile.spacemouse_calibrated();
    }
    return true;
    } catch (const ExpiredGesture&) { return false; }
}
void DeviceAdapters::tick(Clock::time_point now) {
    if (std::any_of(sources_.begin(), sources_.end(), [&](const auto& item) {
        return now < item.second.heartbeat || now - item.second.heartbeat >= source_timeout;
    })) {
        const auto newly_retired = std::count_if(sources_.begin(), sources_.end(), [&](const auto& item) {
            return !retired_generations_.contains(item.first);
        });
        need(retired_generations_.size() + newly_retired <= 64, "Device retirement history limit reached");
        for (const auto& [id, source] : sources_) retired_generations_[id] = source.generation;
        cancel(); sources_.clear(); return;
    }
    if (menu_ || !navigating_) return;
    std::array<double, 6> axes{};
    for (const auto& [id, source] : sources_) {
        (void)id;
        for (const auto& key : source.keys) switch (binding(key)) {
        case v1::FORWARD: axes[2] -= 1; break; case v1::BACKWARD: axes[2] += 1; break;
        case v1::LEFT: axes[0] -= 1; break; case v1::RIGHT: axes[0] += 1; break;
        case v1::UP: axes[1] += 1; break; case v1::DOWN: axes[1] -= 1; break;
        case v1::ROLL_LEFT: if (active_profile(profiles_).movement() == v1::FREE_FLIGHT) axes[5] += 1; break;
        case v1::ROLL_RIGHT: if (active_profile(profiles_).movement() == v1::FREE_FLIGHT) axes[5] -= 1; break;
        default: break;
        }
    }
    std::array<double, 6> spacemouse{};
    for (const auto& [id, source] : sources_) {
        (void)id;
        if (source.has_axes && now >= source.axes_time && now - source.axes_time < axis_timeout)
            for (unsigned i = 0; i < 6; ++i) spacemouse[i] += source.axes[i];
    }
    const bool level_keyboard = active_profile(profiles_).movement() == v1::LEVEL_MOVEMENT &&
        spacemouse[0] == 0 && spacemouse[1] == 0 && spacemouse[2] == 0;
    if (active_profile(profiles_).movement() == v1::LEVEL_MOVEMENT && !level_keyboard)
        for (unsigned i = 0; i < 3; ++i) axes[i] = 0;
    for (unsigned i = 0; i < 6; ++i) axes[i] += spacemouse[i];
    bool active = false;
    for (auto& value : axes) { value = std::clamp(value, -1., 1.); active = active || value != 0; }
    if (active || axes_sent_) {
        core::v1::ActorActionRequest request;
        for (const auto value : axes) request.mutable_input()->add_axes(value);
        request.mutable_input()->set_translation_frame(level_keyboard ? core::v1::LEVEL_TRANSLATION : core::v1::BODY_TRANSLATION);
        send(std::move(request));
    }
    axes_sent_ = active;
}
v1::ControlStatus DeviceAdapters::status() const {
    v1::ControlStatus result; result.set_protocol_version(1); result.set_active_profile(profiles_.active_profile());
    result.set_navigating(navigating_); result.set_menu_open(menu_); result.set_available(actor_.available());
    result.set_cursor_x(cursor_x_); result.set_cursor_y(cursor_y_); result.set_viewport_width(width_); result.set_viewport_height(height_);
    return result;
}
}
