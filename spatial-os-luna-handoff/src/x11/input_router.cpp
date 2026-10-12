#include "x11/input_router.hpp"
#include "x11/surfaces.hpp"
#include "world/store.hpp"
#include <algorithm>
#include <cstdlib>
namespace spatial {
XInputRouter::XInputRouter(Display* display, NativeInput& input, XSurfaces& surfaces)
    : display_(display), input_(input), surfaces_(surfaces) {}
native_input::Reply XInputRouter::checked(native_input::Request request) {
    const auto reply = input_.call(std::move(request));
    switch (reply.code()) {
    case native_input::OK: return reply;
    case native_input::INVALID_ARGUMENT: throw WorldError(wire::INVALID_ARGUMENT, "Invalid native input transition");
    case native_input::FORBIDDEN: throw WorldError(wire::FORBIDDEN, "Native input controller denied");
    case native_input::STALE_TARGET: throw WorldError(wire::STALE_TARGET, "Native surface generation changed");
    case native_input::NO_FOCUS: throw WorldError(wire::NO_FOCUS, "Native focus is unavailable");
    case native_input::BUSY: throw WorldError(wire::BUSY, "Native pointer is captured");
    case native_input::RESOURCE_LIMIT: throw WorldError(wire::IN_USE, "Native input device limit reached");
    default: throw std::runtime_error("Native input seat or reply is invalid");
    }
}
wire::NativeActorInput XInputRouter::state(const std::string& actor, Seat& seat, const native_input::Reply& reply) {
    wire::NativeActorInput result; result.set_actor_id(actor);
    for (unsigned button = 1; button < 32; ++button)
        if (reply.held_buttons() & (1u << button)) result.add_held_buttons(button);
    if (reply.held_keys().size() != 32) throw std::runtime_error("Invalid native key bitmap");
    std::erase_if(seat.keys, [&](const auto& key) {
        return !(static_cast<unsigned char>(reply.held_keys()[key.first / 8]) & (1u << (key.first % 8)));
    });
    for (const auto& [code, name] : seat.keys) { (void)code; result.add_held_keys(name); }
    const auto surface = surfaces_.native(seat.focus);
    if (reply.has_focus() && surface && surface->xid() == reply.focus().xid() &&
        surface->server_generation() == reply.focus().generation()) *result.mutable_focus() = seat.focus;
    else seat.focus.Clear();
    return result;
}
void XInputRouter::close_all() {
    while (!seats_.empty()) {
        auto found = seats_.begin();
        native_input::Request request; request.mutable_close()->set_id(found->second.id);
        checked(request);
        seats_.erase(found);
    }
}
void XInputRouter::snapshot(wire::NativeSnapshot& snapshot) {
    for (auto& [actor, seat] : seats_) {
        native_input::Request request; request.mutable_status()->set_id(seat.id);
        *snapshot.add_actors() = state(actor, seat, checked(request));
    }
}
wire::NativeActorInput XInputRouter::execute(const wire::RendererInputCommand& command) {
    if (command.actor_id().empty() || command.actor_id().size() > 256)
        throw std::invalid_argument("Invalid actor input identity");
    const bool cancel = command.has_cancel() || command.has_close();
    auto found = seats_.find(command.actor_id());
    if (cancel && found == seats_.end()) {
        wire::NativeActorInput result; result.set_actor_id(command.actor_id()); return result;
    }
    if (!cancel && !command.has_pointer() && !command.has_key() && !command.has_scroll() && !command.has_motion())
        throw std::invalid_argument("Missing renderer input operation");
    std::optional<wire::NativeSurface> target;
    if (command.has_pointer() || command.has_scroll() || command.has_motion()) {
        target = surfaces_.native(command.target());
        if (!target || !target->server_generation()) throw WorldError(wire::STALE_TARGET, "Surface handle is stale");
        if (!command.has_motion() && (!command.has_pixel() || command.pixel().x() >= target->pixels().width() ||
            command.pixel().y() >= target->pixels().height())) throw std::invalid_argument("Input pixel is outside surface");
    }
    if (command.has_pointer() && (!command.pointer().has_button() || !command.pointer().has_pressed() ||
        command.pointer().button() < 1 || command.pointer().button() > 3))
        throw std::invalid_argument("Invalid actor pointer action");
    if (command.has_scroll() && (!command.scroll().has_steps_y() ||
        command.scroll().steps_y() < -64 || command.scroll().steps_y() > 64))
        throw std::invalid_argument("Scroll exceeds action limit");
    unsigned keycode = 0;
    if (command.has_key()) {
        const auto& key = command.key();
        if (!key.has_pressed() || !key.has_keysym() || key.keysym().empty() || key.keysym().size() > 128 ||
            key.keysym().find('\0') != std::string::npos) throw std::invalid_argument("Invalid keysym");
        const auto symbol = XStringToKeysym(key.keysym().c_str());
        keycode = symbol == NoSymbol ? 0 : XKeysymToKeycode(display_, symbol);
        if (!keycode) throw std::invalid_argument("Keysym is absent from the server keymap");
        if (found == seats_.end() || !surfaces_.native(found->second.focus))
            throw WorldError(wire::NO_FOCUS, "Actor has no current native focus");
        if (!key.pressed() && (!found->second.keys.contains(keycode) || found->second.keys.at(keycode) != key.keysym()))
            throw std::invalid_argument("Key release does not match held keysym");
    }
    if (command.has_motion() && found == seats_.end())
        throw WorldError(wire::NO_FOCUS, "Actor has no native capture");
    const bool created = found == seats_.end();
    if (created) {
        native_input::Request request; request.mutable_open();
        const auto reply = checked(request);
        if (!reply.seat()) throw std::runtime_error("Missing native seat identity");
        Seat seat; seat.id = reply.seat(); found = seats_.emplace(command.actor_id(), std::move(seat)).first;
    }
    auto& seat = found->second;
    try {
        native_input::Request request;
        if (cancel) {
            if (command.has_close()) request.mutable_close()->set_id(seat.id);
            else request.mutable_cancel()->set_id(seat.id);
            checked(request);
            seat.focus.Clear(); seat.keys.clear();
            if (command.has_close()) seats_.erase(found);
            wire::NativeActorInput result; result.set_actor_id(command.actor_id()); return result;
        }
        if (command.has_key()) {
            auto* key = request.mutable_key(); key->set_seat(seat.id); key->set_keycode(keycode);
            key->set_pressed(command.key().pressed());
            const auto reply = checked(request);
            if (key->pressed()) seat.keys[keycode] = command.key().keysym();
            return state(command.actor_id(), seat, reply);
        }
        if (command.has_motion()) {
            auto* motion = request.mutable_motion(); motion->set_seat(seat.id);
            motion->mutable_target()->set_xid(target->xid());
            motion->mutable_target()->set_generation(target->server_generation());
            motion->set_x(command.motion().x()); motion->set_y(command.motion().y());
            return state(command.actor_id(), seat, checked(request));
        }
        auto button = [&](unsigned number, bool pressed) {
            native_input::Request action;
            auto* value = action.mutable_button(); value->set_seat(seat.id);
            value->mutable_target()->set_xid(target->xid());
            value->mutable_target()->set_generation(target->server_generation());
            value->set_x(command.pixel().x()); value->set_y(command.pixel().y());
            value->set_button(number); value->set_pressed(pressed); value->set_focus(true);
            value->set_keep_capture_position(command.keep_capture_position());
            const auto reply = checked(action);
            if (pressed) seat.focus = command.target();
            return state(command.actor_id(), seat, reply);
        };
        if (command.has_pointer()) return button(command.pointer().button(), command.pointer().pressed());
        wire::NativeActorInput result;
        for (int count = 0; count < std::abs(command.scroll().steps_y()); ++count) {
            const unsigned number = command.scroll().steps_y() > 0 ? 4 : 5;
            button(number, true); result = button(number, false);
        }
        if (!command.scroll().steps_y()) {
            request.mutable_status()->set_id(seat.id); result = state(command.actor_id(), seat, checked(request));
        }
        return result;
    } catch (...) {
        if (created) {
            // A failed first action must not orphan the newly allocated devices.
            native_input::Request close; close.mutable_close()->set_id(seat.id);
            checked(close); seats_.erase(found);
        }
        throw;
    }
}
}
