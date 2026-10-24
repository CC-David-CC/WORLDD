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
bool known_kind(v1::DeviceKind kind) { return kind == v1::KEYBOARD || kind == v1::MOUSE || kind == v1::SPACEMOUSE; }
}
DeviceAdapters::DeviceAdapters(ActorControl& actor, v1::ControlProfiles profiles)
    : actor_(actor), profiles_(std::move(profiles)) {
    validate_profiles(profiles_);
    navigating_ = active_profile(profiles_).interaction() != v1::HOLD_TO_NAVIGATE;
}
core::v1::ActorActionReply DeviceAdapters::send(core::v1::ActorActionRequest request) {
    try { return actor_.propose(actor_.context().generation, request); }
    catch (const ActorInputCancelled&) { clear_state(); navigating_ = true; throw; }
}
void DeviceAdapters::clear_state() {
    for (auto& [id, source] : sources_) {
        (void)id; source.keys.clear(); source.buttons.clear(); source.axes.fill(0); source.has_axes = false;
    }
    axes_sent_ = false; click_enters_ = false;
}
void DeviceAdapters::cancel() {
    clear_state(); navigating_ = true;
    actor_.cancel(actor_.context().generation);
}
void DeviceAdapters::switch_mode(bool navigating) {
    if (navigating_ == navigating) return;
    clear_state();
    actor_.cancel(actor_.context().generation);
    navigating_ = navigating;
}
void DeviceAdapters::set_menu(bool open) {
    if (menu_ == open) return;
    cancel(); menu_ = open;
}
void DeviceAdapters::set_profiles(const v1::ControlProfiles& profiles) {
    validate_profiles(profiles); // Invalid edits leave both configuration and input untouched.
    auto replacement = profiles;
    cancel();
    profiles_.Swap(&replacement);
    navigating_ = active_profile(profiles_).interaction() != v1::HOLD_TO_NAVIGATE;
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
        if (!source.physical_keys.insert(event.keysym()).second) return;
    } else if (!source.physical_keys.erase(event.keysym())) return;
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
void DeviceAdapters::event(const v1::DeviceEvent& event, Clock::time_point now) {
    validate_message(event);
    need(event.protocol_version() == 1 && !event.source_id().empty() && event.source_id().size() <= 80 &&
         event.source_id().find('\0') == std::string::npos && event.source_generation() > 0 &&
         event.event_sequence() > 0 && event.event_case() != v1::DeviceEvent::EVENT_NOT_SET, "Invalid device event envelope");
    auto found = sources_.find(event.source_id());
    if (event.has_connected()) {
        need(known_kind(event.connected().kind()) && found == sources_.end() && sources_.size() < 64 &&
             (!retired_generations_.contains(event.source_id()) || event.source_generation() > retired_generations_.at(event.source_id())),
             "Invalid, duplicate, or retired device connection");
        Source source{}; source.kind = event.connected().kind(); source.generation = event.source_generation();
        source.sequence = event.event_sequence(); source.heartbeat = now; source.axes_time = now;
        sources_.emplace(event.source_id(), std::move(source));
        return;
    }
    need(found != sources_.end() && found->second.generation == event.source_generation() &&
         event.event_sequence() > found->second.sequence && now >= found->second.heartbeat, "Stale or unknown device event");
    auto& source = found->second;
    source.sequence = event.event_sequence();
    if (event.has_disconnected()) {
        retired_generations_[event.source_id()] = source.generation;
        sources_.erase(found); cancel(); return;
    }
    if (event.has_heartbeat()) { source.heartbeat = now; return; }
    if (event.has_key()) { need(source.kind == v1::KEYBOARD, "Key from wrong device kind"); key(source, event.key()); }
    else if (event.has_motion()) { need(source.kind == v1::MOUSE, "Motion from wrong device kind"); motion(event.motion()); }
    else if (event.has_button()) {
        need(source.kind == v1::MOUSE || source.kind == v1::SPACEMOUSE, "Button from wrong device kind");
        pointer(source, event.button());
    } else if (event.has_scroll()) {
        need(source.kind == v1::MOUSE && event.scroll().steps_y() >= -64 && event.scroll().steps_y() <= 64,
             "Invalid scroll device or magnitude");
        if (!menu_) {
            if (!application_target(aim())) return;
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
        source.axes = normalized; source.axes_time = now; source.has_axes = profile.spacemouse_calibrated();
    }
    source.heartbeat = now;
}
void DeviceAdapters::tick(Clock::time_point now) {
    if (std::any_of(sources_.begin(), sources_.end(), [&](const auto& item) {
        return now < item.second.heartbeat || now - item.second.heartbeat >= source_timeout;
    })) {
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
