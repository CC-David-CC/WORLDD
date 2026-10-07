#include "world/actors.hpp"
#include "world/surfaces.hpp"
#include "platform/render_worker.hpp"
#include "core/geometry.hpp"
#include "core/scene.hpp"
#include <google/protobuf/text_format.h>
#include <sys/random.h>
#include <algorithm>
#include <cerrno>
#include <cmath>
#include <fstream>
#include <iterator>
#include <iostream>

namespace spatial {
namespace {
void require(bool condition, const char* message) {
    if (!condition) throw std::invalid_argument(message);
}
std::string random_token() {
    std::array<unsigned char, 32> bytes{};
    std::size_t offset = 0;
    while (offset < bytes.size()) {
        const auto count = getrandom(bytes.data() + offset, bytes.size() - offset, 0);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) throw std::runtime_error("Cannot obtain session entropy");
        offset += static_cast<std::size_t>(count);
    }
    constexpr char hex[] = "0123456789abcdef";
    std::string result;
    for (const auto byte : bytes) { result += hex[byte >> 4]; result += hex[byte & 15]; }
    return result;
}
Pose as_pose(const wire::Pose& value) {
    const auto& p = value.position(); const auto& q = value.orientation();
    return {{p.x(), p.y(), p.z()}, {q.x(), q.y(), q.z(), q.w()}};
}
void set_pose(wire::Pose* out, const Pose& value) {
    auto* p = out->mutable_position(); p->set_x(value.p[0]); p->set_y(value.p[1]); p->set_z(value.p[2]);
    auto* q = out->mutable_orientation();
    q->set_x(value.q[0]); q->set_y(value.q[1]); q->set_z(value.q[2]); q->set_w(value.q[3]);
}
}
wire::RuntimeConfig load_runtime_config(const std::filesystem::path& path) {
    require(std::filesystem::file_size(path) <= 16384, "Runtime configuration exceeds size limit");
    std::ifstream input(path);
    if (!input) throw std::runtime_error("Cannot read runtime configuration");
    const std::string text((std::istreambuf_iterator<char>(input)), {});
    wire::RuntimeConfig result;
    require(google::protobuf::TextFormat::ParseFromString(text, &result), "Invalid runtime configuration protobuf");
    validate_message(result);
    require(result.has_linear_gain() && result.linear_gain() > 0 && result.linear_gain() <= 1000, "Invalid linear gain");
    require(result.has_angular_gain() && result.angular_gain() > 0 && result.angular_gain() <= 1000, "Invalid angular gain");
    require(result.has_deadzone() && result.deadzone() >= 0 && result.deadzone() < 1, "Invalid deadzone");
    require(result.has_input_timeout() && result.input_timeout() > 0 && result.input_timeout() <= 60, "Invalid input timeout");
    if (result.has_render()) validate_render_config(result.render());
    return result;
}
Actors::Actors(WorldStore& store, const wire::RuntimeConfig& config, bool manual_clock, Surfaces* surfaces, RenderWorker* renderer)
    : store_(store), surfaces_(surfaces), renderer_(renderer), config_(config), manual_clock_(manual_clock) {
    for (const auto& entity : store_.actors()) {
        Actor actor; actor.entity = entity; actors_.emplace(entity.id(), std::move(actor));
    }
}
Actors::Actor& Actors::authenticated(const std::string& token) {
    const auto found = sessions_.find(token);
    if (token.empty() || found == sessions_.end()) throw WorldError(wire::UNAUTHORIZED, "Invalid actor session");
    return actors_.at(found->second);
}
wire::ActorOpenReply Actors::open(const wire::ActorOpenRequest& request) {
    require(request.grants_size() <= 64, "Too many grants");
    const std::set<std::string> allowed{"navigate", "interact", "world.write", "git.write", "process.launch"};
    std::set<std::string> grants;
    for (const auto& grant : request.grants()) {
        require(allowed.contains(grant), "Unknown actor grant");
        require(grants.insert(grant).second, "Duplicate actor grant");
    }
    auto found = actors_.find(request.id());
    if (found != actors_.end() && !found->second.token.empty())
        throw WorldError(wire::IN_USE, "Actor already has a controller");
    const auto token = random_token();
    if (sessions_.contains(token)) throw std::runtime_error("Session entropy collision");
    if (found == actors_.end()) {
        wire::Entity entity; entity.set_id(request.id()); entity.set_kind(wire::ACTOR);
        entity.set_room(request.has_room() ? request.room() : store_.info().entry_room());
        entity.set_label(request.id());
        if (request.has_pose()) *entity.mutable_pose() = request.pose();
        else { entity.mutable_pose()->mutable_position(); entity.mutable_pose()->mutable_orientation()->set_w(1); }
        Actor actor; actor.entity = store_.create_actor(entity);
        found = actors_.emplace(entity.id(), std::move(actor)).first;
    }
    auto& actor = found->second;
    // An existing actor keeps its last valid pose, including a durable restore.
    actor.token = token; actor.grants = std::move(grants); actor.sequence = 0;
    actor.axes.fill(0); actor.expires = now_;
    actor.aim.Clear();
    sessions_.emplace(token, actor.entity.id());
    wire::ActorOpenReply result; result.set_id(actor.entity.id()); result.set_token(token); return result;
}
wire::ActorStateReply Actors::inspect(const std::string& token) {
    const auto& actor = authenticated(token);
    refresh_native();
    wire::ActorStateReply result; result.set_id(actor.entity.id()); result.set_room(actor.entity.room());
    *result.mutable_pose() = actor.entity.pose();
    for (const auto axis : actor.axes) result.add_axes(axis);
    *result.mutable_held_keys() = actor.native.held_keys();
    *result.mutable_held_buttons() = actor.native.held_buttons();
    if (actor.native.has_focus()) {
        result.set_focus(actor.native.focus().id()); result.set_focus_generation(actor.native.focus().generation());
    }
    return result;
}
wire::ActorCloseReply Actors::close(const std::string& token) {
    auto& actor = authenticated(token);
    release_input(actor, true);
    actor.axes.fill(0); actor.expires = now_; actor.grants.clear(); actor.token.clear();
    actor.aim.Clear();
    sessions_.erase(token);
    wire::ActorCloseReply result; result.set_closed(true); return result;
}
wire::ActorActionReply Actors::act(const wire::ActorActionRequest& request) {
    auto& actor = authenticated(request.token());
    require(request.has_seq() && request.seq() > 0, "Missing positive actor sequence");
    if (request.seq() <= actor.sequence) throw WorldError(wire::STALE_SEQUENCE, "Actor sequence already consumed");
    actor.sequence = request.seq();
    switch (request.action_case()) {
    case wire::ActorActionRequest::kInput: {
        if (!actor.grants.contains("navigate")) throw WorldError(wire::FORBIDDEN, "Navigation grant required");
        require(request.input().axes_size() == 6, "Expected six motion axes");
        std::array<double, 6> axes{};
        for (std::size_t i = 0; i < axes.size(); ++i) {
            const double value = request.input().axes(static_cast<int>(i));
            require(std::isfinite(value) && std::abs(value) <= 1, "Axis must be finite and within [-1,1]");
            axes[i] = std::abs(value) <= config_.deadzone() ? 0 :
                std::copysign((std::abs(value) - config_.deadzone()) / (1 - config_.deadzone()), value);
        }
        actor.axes = axes; actor.expires = now_ + config_.input_timeout();
        break;
    }
    case wire::ActorActionRequest::kStop:
        actor.axes.fill(0); actor.expires = now_; actor.aim.Clear(); release_input(actor); break;
    case wire::ActorActionRequest::kUnfocus:
        release_input(actor); break;
    case wire::ActorActionRequest::kPointer:
    case wire::ActorActionRequest::kKey:
    case wire::ActorActionRequest::kScroll:
        if (!actor.grants.contains("interact")) throw WorldError(wire::FORBIDDEN, "Interaction grant required");
        route_input(actor, request); break;
    case wire::ActorActionRequest::kAim: {
        if (!actor.grants.contains("navigate") && !actor.grants.contains("interact"))
            throw WorldError(wire::FORBIDDEN, "Navigation or interaction grant required");
        require(request.aim().has_direction(), "Missing aim direction");
        refresh_native();
        const auto aim = pick_scene(scene(request.token()), request.aim().direction());
        move_capture(actor, request.aim().direction());
        actor.aim = aim; actor.direction = request.aim().direction();
        wire::ActorActionReply result; result.set_accepted(true); *result.mutable_aim() = actor.aim;
        return result;
    }
    case wire::ActorActionRequest::ACTION_NOT_SET:
        throw WorldError(wire::UNKNOWN_METHOD, "No recognized actor action");
    }
    wire::ActorActionReply result; result.set_accepted(true); return result;
}
void Actors::release_input(Actor& actor, bool close) {
    if (renderer_ && !actor.native.actor_id().empty()) {
        wire::RendererInputCommand command; command.set_actor_id(actor.entity.id());
        if (close) command.mutable_close(); else command.mutable_cancel();
        actor.native = renderer_->input(command);
        if (close) actor.native.Clear();
    }
    if (!actor.lease.empty()) leases_.erase(actor.lease);
    actor.lease.clear(); actor.capture.Clear(); actor.capture_motion_pending = false;
}
std::string Actors::control_group(const wire::SurfaceRecord& surface) const {
    // Temporary native surface grouping. Broker application identities supersede
    // WM_TRANSIENT_FOR in the mandatory application-authority milestone.
    auto current = surface;
    std::set<std::string> visited;
    while (current.has_parent_id()) {
        if (!visited.insert(current.id()).second || visited.size() > 32)
            throw WorldError(wire::STALE_TARGET, "Invalid native surface group");
        current = surfaces_->get(current.parent_id());
    }
    return current.id();
}
void Actors::refresh_native() {
    if (!renderer_ || !surfaces_) return;
    const auto snapshot = renderer_->synchronize();
    surfaces_->refresh(snapshot);
    for (const auto& state : snapshot.actors()) {
        const auto found = actors_.find(state.actor_id());
        if (found == actors_.end()) throw std::runtime_error("Compositor returned an unknown actor");
        auto& actor = found->second; actor.native = state;
        bool valid = state.has_focus();
        if (valid) {
            try {
                const auto current = surfaces_->get(state.focus().id());
                valid = current.generation() == state.focus().generation() && current.mapped() &&
                    current.room() == actor.entity.room() && control_group(current) == actor.lease;
            } catch (const WorldError& error) {
                if (error.code != wire::NOT_FOUND && error.code != wire::STALE_TARGET) throw;
                valid = false;
            }
        }
        if (!valid && (!actor.lease.empty() || state.has_focus() || state.held_keys_size() || state.held_buttons_size()))
            release_input(actor);
        if (!actor.native.held_buttons_size()) actor.capture.Clear();
    }
}
wire::AimHit Actors::input_target(Actor& actor, bool captured) {
    if (captured && actor.native.held_buttons_size()) return actor.capture;
    if (!actor.aim.has_hit() || !actor.aim.hit().has_pixel()) throw WorldError(wire::NO_TARGET, "No aimed application surface");
    const auto& hit = actor.aim.hit();
    try {
        const auto surface = surfaces_->get(hit.id());
        if (surface.generation() != hit.generation() || !surface.mapped() || surface.room() != actor.entity.room())
            throw WorldError(wire::STALE_TARGET, "Aimed surface changed");
    } catch (const WorldError& error) {
        if (error.code == wire::NOT_FOUND) throw WorldError(wire::STALE_TARGET, "Aimed surface was removed");
        throw;
    }
    const auto current = pick_scene(scene(actor.token), actor.direction);
    if (!current.has_hit() || current.hit().id() != hit.id() || !current.hit().has_pixel() ||
        current.hit().generation() != hit.generation() || current.hit().pixel().x() != hit.pixel().x() ||
        current.hit().pixel().y() != hit.pixel().y()) throw WorldError(wire::STALE_TARGET, "Aimed geometry or visibility changed");
    return hit;
}
void Actors::move_capture(Actor& actor, const wire::Vec3& input) {
    actor.capture_motion_pending = false;
    if (!actor.native.held_buttons_size()) return;
    if (!actor.grants.contains("interact")) throw WorldError(wire::FORBIDDEN, "Interaction grant required");
    const auto surface = surfaces_->get(actor.capture.id());
    const auto camera = scene_pose(actor.entity.pose());
    const auto plane = scene_pose(surface.pose());
    Vec3 local{input.x(), input.y(), input.z()};
    const double scale = std::max({std::abs(local[0]), std::abs(local[1]), std::abs(local[2])});
    require(std::isfinite(scale) && scale > 0, "Invalid captured aim direction");
    for (auto& value : local) value /= scale;
    const auto ray = inverse_point(Pose{{0,0,0}, plane.q}, transform_point(Pose{{0,0,0}, camera.q}, local));
    const auto origin = inverse_point(plane, camera.p);
    // An aim parallel to, or pointing away from, the capture plane has no new pixel.
    // Keep the last native position and capture until release or cancellation.
    if (ray[2] == 0) return;
    const double distance = -origin[2] / ray[2];
    if (distance < 0) return;
    const double x = std::floor(((origin[0] + ray[0] * distance) / surface.size().x() + .5) * surface.pixels().width());
    const double y = std::floor((.5 - (origin[1] + ray[1] * distance) / surface.size().y()) * surface.pixels().height());
    require(std::isfinite(x) && std::isfinite(y) && x >= -32768 && x <= 32767 && y >= -32768 && y <= 32767,
            "Captured pixel exceeds native coordinate range");
    wire::RendererInputCommand command; command.set_actor_id(actor.entity.id());
    command.mutable_target()->set_id(actor.capture.id());
    command.mutable_target()->set_generation(actor.capture.generation());
    command.mutable_motion()->set_x(static_cast<std::int32_t>(x));
    command.mutable_motion()->set_y(static_cast<std::int32_t>(y));
    actor.native = renderer_->input(command);
}
void Actors::route_input(Actor& actor, const wire::ActorActionRequest& request) {
    if (!renderer_ || !surfaces_) throw WorldError(wire::UNIMPLEMENTED, "Native actor input is unavailable");
    if (request.has_pointer()) require(request.pointer().has_button() && request.pointer().has_pressed() &&
        request.pointer().button() >= 1 && request.pointer().button() <= 3, "Expected button 1, 2, or 3 and pressed state");
    if (request.has_key()) require(request.key().has_keysym() && request.key().has_pressed() &&
        !request.key().keysym().empty() && request.key().keysym().size() <= 128 &&
        request.key().keysym().find('\0') == std::string::npos, "Expected keysym and pressed state");
    if (request.has_scroll()) require(request.scroll().has_steps_y() && request.scroll().steps_y() >= -64 &&
        request.scroll().steps_y() <= 64, "Expected scroll steps within [-64,64]");
    refresh_native();
    wire::RendererInputCommand command; command.set_actor_id(actor.entity.id());
    if (request.has_key()) {
        if (!actor.native.has_focus()) throw WorldError(wire::NO_FOCUS, "Actor has no focus");
        *command.mutable_key() = request.key(); actor.native = renderer_->input(command); return;
    }
    const auto hit = input_target(actor, !request.has_scroll());
    if (request.has_scroll() && actor.native.held_buttons_size() && hit.id() != actor.capture.id())
        throw WorldError(wire::BUSY, "Scroll cannot change the captured pointer target");
    const auto surface = surfaces_->get(hit.id());
    const auto group = control_group(surface);
    const auto holder = leases_.find(group);
    if (holder != leases_.end() && holder->second != actor.entity.id())
        throw WorldError(wire::BUSY, "Application surface group has another controller");
    if (request.has_pointer()) {
        const bool held = std::find(actor.native.held_buttons().begin(), actor.native.held_buttons().end(),
            request.pointer().button()) != actor.native.held_buttons().end();
        require(held != request.pointer().pressed(), "Pointer transition does not match held state");
        *command.mutable_pointer() = request.pointer();
    } else *command.mutable_scroll() = request.scroll();
    if (request.has_scroll() && request.scroll().steps_y() == 0) return;
    if (actor.native.has_focus() && actor.native.focus().id() != hit.id()) release_input(actor);
    command.mutable_target()->set_id(hit.id()); command.mutable_target()->set_generation(hit.generation());
    *command.mutable_pixel() = hit.pixel();
    command.set_keep_capture_position(request.has_pointer() && actor.native.held_buttons_size() > 0);
    actor.native = renderer_->input(command);
    if (actor.native.has_focus()) { actor.lease = group; leases_[group] = actor.entity.id(); }
    if (actor.native.held_buttons_size()) actor.capture = hit; else actor.capture.Clear();
}
wire::RenderScene Actors::scene(const std::string& token) {
    const auto& actor = authenticated(token);
    require(config_.has_render(), "Render configuration is missing");
    auto result = panel_scene(store_.snapshot(), actor.entity, config_.render());
    if (surfaces_) surfaces_->append(result, actor.entity.room());
    return result;
}
void Actors::step(double target) {
    require(std::isfinite(target) && target >= now_, "Invalid monotonic time");
    for (auto& [id, actor] : actors_) {
        (void)id;
        const double end = std::min(target, actor.expires);
        if (end > now_) {
            const Vec3 linear{actor.axes[0] * config_.linear_gain(), actor.axes[1] * config_.linear_gain(), actor.axes[2] * config_.linear_gain()};
            const Vec3 angular{actor.axes[3] * config_.angular_gain(), actor.axes[4] * config_.angular_gain(), actor.axes[5] * config_.angular_gain()};
            set_pose(actor.entity.mutable_pose(), integrate(as_pose(actor.entity.pose()), linear, angular, end - now_));
            if (actor.native.held_buttons_size() && std::any_of(actor.axes.begin(), actor.axes.end(), [](double v) { return v != 0; }))
                actor.capture_motion_pending = true;
        }
        if (target >= actor.expires) actor.axes.fill(0);
    }
    now_ = target;
}
void Actors::tick(double monotonic_seconds) { if (!manual_clock_) step(monotonic_seconds); }
void Actors::flush_input() {
    // Tick also runs while waiting on compositor I/O. Submit deferred motion only
    // from the outer scheduler, never recursively inside a renderer exchange.
    std::vector<std::string> pending;
    for (const auto& [id, actor] : actors_) if (actor.capture_motion_pending) pending.push_back(id);
    if (pending.empty()) return;
    refresh_native();
    for (const auto& id : pending) {
        auto& actor = actors_.at(id);
        try {
            move_capture(actor, actor.direction);
        } catch (const std::invalid_argument& error) {
            release_input(actor); actor.axes.fill(0); actor.expires = now_;
            std::cerr << "worldd: cancelled unrepresentable capture: " << error.what() << '\n';
        } catch (const WorldError& error) {
            if (error.code != wire::STALE_TARGET && error.code != wire::NO_FOCUS && error.code != wire::FORBIDDEN) throw;
            release_input(actor); actor.axes.fill(0); actor.expires = now_;
            std::cerr << "worldd: cancelled invalid capture: " << error.what() << '\n';
        }
    }
}
wire::ClockReply Actors::advance(const wire::ClockAdvanceRequest& request) {
    if (!manual_clock_) throw WorldError(wire::FORBIDDEN, "Manual clock is disabled");
    require(request.has_seconds() && std::isfinite(request.seconds()) && request.seconds() >= 0 &&
            request.seconds() <= 3600, "Invalid clock interval");
    step(now_ + request.seconds());
    flush_input();
    wire::ClockReply result; result.set_seconds(now_); return result;
}
wire::WorldCheckpointReply Actors::checkpoint() {
    std::vector<wire::Entity> entities;
    for (const auto& [id, actor] : actors_) { (void)id; entities.push_back(actor.entity); }
    for (const auto& entity : store_.checkpoint_actors(entities)) actors_.at(entity.id()).entity = entity;
    wire::WorldCheckpointReply result; result.set_world_revision(store_.info().world_revision()); return result;
}
const wire::Entity* Actors::find(const std::string& id) const {
    const auto found = actors_.find(id);
    return found == actors_.end() ? nullptr : &found->second.entity;
}
} // namespace spatial
