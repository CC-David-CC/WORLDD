#include "world/actors.hpp"
#include "world/geometry_view_validation.hpp"
#include "platform/broker.hpp"
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
    if (!result.has_actor_radius()) result.set_actor_radius(.2);
    require(result.actor_radius() > 0 && result.actor_radius() <= 1000, "Invalid actor collision radius");
    if (!result.has_interaction_reach()) result.set_interaction_reach(3);
    require(result.interaction_reach() > 0 && result.interaction_reach() <= 1e6, "Invalid interaction reach");
    if (result.has_render()) validate_render_config(result.render());
    return result;
}
Actors::Actors(WorldStore& store, const wire::RuntimeConfig& config, bool manual_clock, Surfaces* surfaces, RenderWorker* renderer, BrokerClient* broker)
    : store_(store), surfaces_(surfaces), renderer_(renderer), broker_(broker), config_(config), manual_clock_(manual_clock) {
    for (const auto& entity : store_.actors()) {
        Actor actor; actor.entity = entity; actors_.emplace(entity.id(), std::move(actor));
    }
}
Actors::Actor& Actors::authenticated(const std::string& token) {
    const auto found = sessions_.find(token);
    if (token.empty() || found == sessions_.end()) throw WorldError(wire::UNAUTHORIZED, "Invalid actor session");
    return actors_.at(found->second);
}
bool Actors::granted(const Actor& actor, const std::string& right, const std::vector<std::string>& components) const {
    return actor.authority ? actor.authority(right, components) : actor.grants.contains(right);
}
void Actors::consume_sequence(const std::string& token, std::uint64_t sequence) {
    auto& actor = authenticated(token);
    require(sequence > 0, "Missing positive actor sequence");
    if (sequence <= actor.sequence) throw WorldError(wire::STALE_SEQUENCE, "Actor sequence already consumed");
    actor.sequence = sequence;
}
void Actors::refresh_authority() {
    refresh_objects();
    for (auto& [id, actor] : actors_) {
        (void)id;
        if (!actor.authority) continue;
        if (!granted(actor, "navigate")) { actor.axes.fill(0); actor.expires = now_; }
        if (!granted(actor, "observe")) actor.aim.Clear();
        const bool control_lost = actor.application_control ?
            (!granted(actor, "observe") || !broker_ || !broker_->control_active(*actor.application_control)) :
            !granted(actor, "interact");
        if (control_lost && (!actor.native.actor_id().empty() || actor.application_control)) {
            actor.interaction_reset_pending = true; actor.capture_motion_pending = false;
        }
    }
}
std::string Actors::selected_workspace(const Actor& actor) {
    const auto selected = store_.selected_workspace(actor.entity.id());
    if (selected.empty()) return {};
    bool allowed = false;
    if ((!actor.authority || actor.principal || granted(actor, "workspace.select", {selected})) && broker_ && store_.workspace_available(selected)) {
        try {
            const auto access = broker_->access(selected, actor.entity.id(), actor.principal ? &*actor.principal : nullptr);
            allowed = std::find(access.rights().begin(), access.rights().end(), wire::WORKSPACE_SELECT) != access.rights().end();
        } catch (const WorldError& error) {
            if (error.code != wire::NOT_FOUND && error.code != wire::FORBIDDEN && error.code != wire::UNAVAILABLE) throw;
        }
    }
    if (!allowed) { store_.clear_workspace(actor.entity.id()); return {}; }
    return selected;
}
wire::ActorOpenReply Actors::open(const wire::ActorOpenRequest& request, bool require_observation_grant, Authority authority,
                                  std::optional<spatial::authority::v1::ActorRef> principal, bool local_application_output) {
    require(request.grants_size() <= 64, "Too many grants");
    const std::set<std::string> allowed{"navigate", "interact", "observe", "world.write", "git.write", "process.launch"};
    std::set<std::string> grants;
    for (const auto& grant : request.grants()) {
        require(allowed.contains(grant), "Unknown actor grant");
        require(grants.insert(grant).second, "Duplicate actor grant");
    }
    auto found = actors_.find(request.id());
    if (found != actors_.end() && (!found->second.token.empty() || found->second.close_pending || found->second.departure_pending))
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
    actor.authority = std::move(authority); actor.principal = std::move(principal);
    selected_workspace(actor);
    // An existing actor keeps its last valid pose, including a durable restore.
    actor.token = token; actor.grants = std::move(grants); actor.sequence = 0;
    actor.application_input = false;
    actor.require_observation_grant = require_observation_grant;
    actor.local_application_output = local_application_output;
    actor.axes.fill(0); actor.expires = now_;
    actor.aim.Clear();
    sessions_.emplace(token, actor.entity.id());
    wire::ActorOpenReply result; result.set_id(actor.entity.id()); result.set_token(token); return result;
}
wire::ActorStateReply Actors::inspect(const std::string& token) {
    const auto& actor = authenticated(token);
    refresh_native();
    wire::ActorStateReply result; result.set_id(actor.entity.id()); result.set_room(actor.entity.room());
    const auto workspace = selected_workspace(actor);
    if (!workspace.empty()) result.set_workspace(workspace);
    *result.mutable_pose() = actor.entity.pose();
    for (const auto axis : actor.axes) result.add_axes(axis);
    *result.mutable_held_keys() = actor.native.held_keys();
    *result.mutable_held_buttons() = actor.native.held_buttons();
    if (actor.native.has_focus()) {
        result.set_focus(actor.native.focus().id()); result.set_focus_generation(actor.native.focus().generation());
    }
    return result;
}
std::string Actors::depart(const std::string& token) {
    // Native cleanup can run revocation callbacks that clear the channel's token.
    const std::string retained=token;
    auto& actor=authenticated(retained);const auto id=actor.entity.id();
    actor.departure_pending=true;
    try{close(retained);}catch(...){actor.departure_pending=false;throw;}
    return id;
}
wire::ActorCloseReply Actors::close(const std::string& token) {
    auto& actor = authenticated(token);
    cancel_object(actor);
    if (renderer_ && renderer_->busy()) {
        actor.close_pending = true; actor.interaction_reset_pending = true; actor.capture_motion_pending = false;
    } else release_input(actor, true);
    actor.axes.fill(0); actor.expires = now_; actor.grants.clear(); actor.authority = {}; actor.principal.reset(); actor.token.clear();
    actor.aim.Clear();
    sessions_.erase(token);
    wire::ActorCloseReply result; result.set_closed(true); return result;
}
wire::ActorActionReply Actors::act(const wire::ActorActionRequest& request) {
    auto& actor = authenticated(request.token());
    require(request.has_seq(), "Missing actor sequence");
    consume_sequence(request.token(), request.seq());
    switch (request.action_case()) {
    case wire::ActorActionRequest::kGitWorktreeCreate: {
        if (!granted(actor, "git.write")) throw WorldError(wire::FORBIDDEN, "Git administration grant required");
        if (!broker_) throw WorldError(wire::UNIMPLEMENTED, "No workspace broker configured");
        wire::Request delegated; *delegated.mutable_git_worktree_create() = request.git_worktree_create();
        const auto result = broker_->call(delegated);
        wire::ActorActionReply reply; reply.set_accepted(true); *reply.mutable_worktree() = result.worktree(); return reply;
    }
    case wire::ActorActionRequest::kProcessLaunch: {
        if (!actor.principal && !granted(actor, "process.launch")) throw WorldError(wire::FORBIDDEN, "Process launch grant required");
        if (actor.principal && !broker_) throw WorldError(wire::FORBIDDEN, "Workspace execution authority is unavailable");
        const auto workspace = selected_workspace(actor);
        if (workspace.empty()) throw WorldError(wire::NO_WORKSPACE, "Actor has no selected workspace");
        const auto access = broker_->access(workspace, actor.entity.id(), actor.principal ? &*actor.principal : nullptr);
        if (std::find(access.rights().begin(), access.rights().end(), wire::PROCESS_LAUNCH) == access.rights().end())
            throw WorldError(wire::FORBIDDEN, "Workspace execution authority required");
        const auto process = broker_->launch(workspace, actor.entity.id(), request.process_launch(), actor.principal ? &*actor.principal : nullptr);
        wire::ActorActionReply reply; reply.set_accepted(true); *reply.mutable_process() = process; return reply;
    }
    case wire::ActorActionRequest::kWorkspaceSelect: {
        if (actor.authority && !actor.principal && !granted(actor, "workspace.select", {request.workspace_select().id()}))
            throw WorldError(wire::FORBIDDEN, "Workspace selection authority required");
        if (!broker_) throw WorldError(actor.principal ? wire::FORBIDDEN : wire::UNIMPLEMENTED, "No workspace authority configured");
        const auto& id = request.workspace_select().id();
        const auto access = broker_->access(id, actor.entity.id(), actor.principal ? &*actor.principal : nullptr);
        if (std::find(access.rights().begin(), access.rights().end(), wire::WORKSPACE_SELECT) == access.rights().end())
            throw WorldError(wire::FORBIDDEN, "Workspace selection authority required");
        store_.select_workspace(actor.entity.id(), id); break;
    }
    case wire::ActorActionRequest::kWorkspaceClear:
        store_.clear_workspace(actor.entity.id()); break;
    case wire::ActorActionRequest::kObjectGrab:
    case wire::ActorActionRequest::kObjectMove:
    case wire::ActorActionRequest::kObjectRelease:
        if (!actor.authority && !granted(actor, "world.write")) throw WorldError(wire::FORBIDDEN, "World editing grant required");
        object_action(actor, request); break;
    case wire::ActorActionRequest::kObserve: {
        if (actor.require_observation_grant && !granted(actor, "observe"))
            throw WorldError(wire::FORBIDDEN, "World observation grant required");
        wire::ActorActionReply result; result.set_accepted(true);
        *result.mutable_observation() = observe(actor, request.observe());
        return result;
    }
    case wire::ActorActionRequest::kInput: {
        if (!granted(actor, "navigate")) throw WorldError(wire::FORBIDDEN, "Navigation grant required");
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
        actor.axes.fill(0); actor.expires = now_; actor.aim.Clear(); cancel_object(actor); release_input(actor); break;
    case wire::ActorActionRequest::kUnfocus:
        release_input(actor); break;
    case wire::ActorActionRequest::kPointer:
    case wire::ActorActionRequest::kKey:
    case wire::ActorActionRequest::kScroll:
        if (!actor.local_application_output) throw WorldError(wire::UNIMPLEMENTED, "Remote application input is not supported");
        if (!actor.principal && !granted(actor, "interact")) throw WorldError(wire::FORBIDDEN, "Interaction grant required");
        try { route_input(actor, request); }
        catch (...) {
            if (actor.application_control && !actor.native.has_focus()) { release_input(actor); drain_control_releases(); }
            throw;
        }
        break;
    case wire::ActorActionRequest::kAim: {
        if (actor.authority && !granted(actor, "observe")) throw WorldError(wire::FORBIDDEN, "Observation authority required");
        if (!granted(actor, "navigate") && !granted(actor, "interact"))
            throw WorldError(wire::FORBIDDEN, "Navigation or interaction grant required");
        require(request.aim().has_direction(), "Missing aim direction");
        refresh_native();
        const auto aim = pick_scene(scene(request.token()), request.aim().direction());
        move_capture(actor, request.aim().direction());
        actor.aim = aim; actor.direction = request.aim().direction();
        wire::ActorActionReply result; result.set_accepted(true); *result.mutable_aim() = actor.aim;
        return result;
    }
    case wire::ActorActionRequest::kPresentationCreate:
    case wire::ActorActionRequest::kPresentationMove:
    case wire::ActorActionRequest::kPresentationRemove:
    case wire::ActorActionRequest::kWorkspaceCopy:
    case wire::ActorActionRequest::kWorkspaceCopyStatus:
        throw WorldError(wire::UNIMPLEMENTED, "M07 workspace actions are not implemented");
    case wire::ActorActionRequest::ACTION_NOT_SET:
        throw WorldError(wire::UNKNOWN_METHOD, "No recognized actor action");
    }
    wire::ActorActionReply result; result.set_accepted(true); return result;
}
void Actors::release_input(Actor& actor, bool close, bool keep_reservation) {
    close = close || actor.close_pending;
    if (renderer_ && !actor.native.actor_id().empty()) {
        wire::RendererInputCommand command; command.set_actor_id(actor.entity.id());
        if (close) command.mutable_close(); else command.mutable_cancel();
        actor.native = renderer_->input(command);
        if (actor.native.has_focus() || actor.native.held_keys_size() || actor.native.held_buttons_size())
            throw std::runtime_error("Native input cancellation was not confirmed");
        if (close) actor.native.Clear();
    }
    // Revocation can request closure while the cancellation exchange is waiting.
    // Finish that closure before discarding the pending flag or broker reservation.
    if (!close && actor.close_pending) { release_input(actor, true); return; }
    actor.interaction_reset_pending = false;
    actor.close_pending = false;
    actor.capture.Clear(); actor.capture_motion_pending = false;
    if (keep_reservation && !close) return;
    if (actor.application_control) {
        control_releases_.push_back(*actor.application_control);
        actor.application_control.reset();
    }
    if (!actor.lease.empty()) leases_.erase(actor.lease);
    actor.lease.clear(); actor.capture.Clear(); actor.capture_motion_pending = false;
}
void Actors::poll_control() {
    if (!broker_) return;
    broker_->poll_control();
    for (auto& [id, actor] : actors_) {
        (void)id;
        if (actor.application_control && !broker_->control_active(*actor.application_control)) {
            actor.interaction_reset_pending = true; actor.capture_motion_pending = false;
        }
    }
}
void Actors::drain_control_releases() {
    if (!broker_ || broker_->busy()) return;
    while (!control_releases_.empty()) {
        const auto lease = control_releases_.front();
        workspace::v1::ApplicationControlRelease request;
        request.set_id(lease.id()); *request.mutable_principal() = lease.principal();
        broker_->control_release(request);
        control_releases_.erase(control_releases_.begin());
    }
}
void Actors::authorize_input(Actor& actor, const wire::SurfaceHandle& target) {
    const auto app = surfaces_->application(target);
    if (!actor.principal || !app) {
        if (!granted(actor, "interact")) throw WorldError(wire::FORBIDDEN, "Interaction grant required");
        if (actor.application_control) { release_input(actor); drain_control_releases(); }
        actor.application_input = false;
        return;
    }
    actor.application_input = true;
    if (!broker_ || !granted(actor, "observe"))
        throw WorldError(wire::FORBIDDEN, "Application control authority required");
    if (actor.application_control && actor.application_control->application().SerializeAsString() != app->SerializeAsString())
        release_input(actor);
    drain_control_releases();
    workspace::v1::ApplicationControlRequest request;
    *request.mutable_principal() = *actor.principal; *request.mutable_application() = *app;
    const auto token = actor.token;
    const auto lease = broker_->control_acquire(request);
    // A wait callback may already have cancelled and queued this same reservation.
    if (std::any_of(control_releases_.begin(), control_releases_.end(), [&](const auto& item) { return item.id() == lease.id(); }))
        throw WorldError(wire::FORBIDDEN, "Application control was cancelled");
    actor.application_control = lease;
    if (actor.token != token || actor.token.empty() || !granted(actor, "observe") || !broker_->control_active(lease)) {
        release_input(actor); drain_control_releases();
        throw WorldError(wire::FORBIDDEN, "Application control authority changed");
    }
    // Refresh after the broker wait; never route to a replaced surface generation.
    refresh_native();
    const auto current = surfaces_->application(target);
    const auto placement = surfaces_->get(target.id());
    if (!current || current->SerializeAsString() != app->SerializeAsString() || !placement.mapped() ||
        placement.room() != actor.entity.room() || placement.generation() != target.generation()) {
        release_input(actor); drain_control_releases();
        throw WorldError(wire::STALE_TARGET, "Application target changed during authorization");
    }
    finish_input(actor);
}
void Actors::finish_input(Actor& actor) {
    poll_control();
    if (actor.token.empty() || actor.interaction_reset_pending || (actor.application_input &&
        (!actor.application_control || !broker_->control_active(*actor.application_control) || !granted(actor, "observe")))) {
        release_input(actor); drain_control_releases();
        throw WorldError(wire::FORBIDDEN, "Application control authority changed");
    }
}
void Actors::send_input(Actor& actor, const wire::RendererInputCommand& command) {
    try {
        actor.native = renderer_->input(command, [&] {
            poll_control();
            if (actor.token.empty() || actor.interaction_reset_pending || (actor.application_input &&
                (!actor.application_control || !broker_->control_active(*actor.application_control) || !granted(actor, "observe"))))
                throw WorldError(wire::FORBIDDEN, "Input authority changed before native submission");
        });
    } catch (const WorldError& error) {
        if (error.code == wire::FORBIDDEN) { release_input(actor); drain_control_releases(); }
        throw;
    }
    finish_input(actor);
}
std::string Actors::control_group(const wire::SurfaceRecord& surface) const {
    return surfaces_->control_group(surface);
}
void Actors::refresh_native() {
    if (!renderer_ || !surfaces_) return;
    const auto snapshot = renderer_->synchronize();
    surfaces_->refresh(snapshot);
    for (const auto& state : snapshot.actors()) {
        const auto found = actors_.find(state.actor_id());
        if (found == actors_.end()) throw std::runtime_error("Compositor returned an unknown actor");
        auto& actor = found->second; actor.native = state;
        bool valid = state.has_focus() && !actor.interaction_reset_pending;
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
    if (!actor.aim.has_hit() || !actor.aim.hit().has_pixel())
        throw WorldError(actor.principal ? wire::FORBIDDEN : wire::NO_TARGET, "No aimed application surface");
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
    wire::SurfaceHandle captured; captured.set_id(actor.capture.id()); captured.set_generation(actor.capture.generation());
    authorize_input(actor, captured);
    const auto surface = surfaces_->get(captured.id());
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
    send_input(actor, command);
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
        const auto target = actor.native.focus(); authorize_input(actor, target);
        *command.mutable_key() = request.key(); send_input(actor, command); return;
    }
    const auto hit = input_target(actor, !request.has_scroll());
    if (request.has_scroll() && actor.native.held_buttons_size() && hit.id() != actor.capture.id())
        throw WorldError(wire::BUSY, "Scroll cannot change the captured pointer target");
    const auto surface = surfaces_->get(hit.id());
    wire::SurfaceHandle target; target.set_id(hit.id()); target.set_generation(hit.generation());
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
    authorize_input(actor, target);
    const auto checked = input_target(actor, !request.has_scroll());
    if (checked.SerializeAsString() != hit.SerializeAsString()) throw WorldError(wire::STALE_TARGET, "Input target changed");
    if (actor.native.has_focus() && actor.native.focus().id() != hit.id())
        release_input(actor, false, actor.application_control.has_value() && actor.lease == group);
    finish_input(actor);
    command.mutable_target()->set_id(hit.id()); command.mutable_target()->set_generation(hit.generation());
    *command.mutable_pixel() = hit.pixel();
    command.set_keep_capture_position(request.has_pointer() && actor.native.held_buttons_size() > 0);
    send_input(actor, command);
    if (actor.native.has_focus()) { actor.lease = group; leases_[group] = actor.entity.id(); }
    if (actor.native.held_buttons_size()) actor.capture = hit; else actor.capture.Clear();
}
std::set<std::string> Actors::observable_applications(const Actor& actor, const std::vector<ObservedApplication>& applications) {
    std::set<std::string> allowed;
    if (applications.empty() || !actor.principal || !broker_ || !surfaces_) return allowed;
    workspace::v1::ApplicationAccessRequest request; *request.mutable_principal() = *actor.principal;
    std::map<std::string, int> groups;
    for (const auto& item : applications) {
        const auto key = item.application.SerializeAsString();
        if (groups.contains(key)) continue;
        if (request.applications_size() == 64)
            throw WorldError(wire::RESOURCE_EXHAUSTED, "Observation exceeds 64 application groups");
        groups.emplace(key, request.applications_size()); *request.add_applications() = item.application;
    }
    const auto token = actor.token;
    const auto result = broker_->application_access(request);
    if (actor.token.empty() || actor.token != token || !granted(actor, "observe")) return allowed;
    for (const auto& item : applications) {
        if (!result.applications(groups.at(item.application.SerializeAsString())).observe()) continue;
        try {
            const auto current = surfaces_->application(item.handle);
            const auto placement = surfaces_->get(item.handle.id());
            if (current && current->SerializeAsString() == item.application.SerializeAsString() && placement.room() == actor.entity.room())
                allowed.insert(item.handle.id());
        } catch (const WorldError& error) {
            if (error.code != wire::NOT_FOUND && error.code != wire::STALE_TARGET) throw;
        }
    }
    return allowed;
}
bool Actors::application_observation_valid(const std::string& token) {
    const auto& actor = authenticated(token);
    const auto applications = actor.observed_applications;
    return observable_applications(actor, applications).size() == applications.size();
}
wire::RenderScene Actors::scene(const std::string& token, bool geometry_only) {
    auto& actor = authenticated(token);
    if (!geometry_only) actor.observed_applications.clear();
    require(config_.has_render(), "Render configuration is missing");
    refresh_objects();
    auto snapshot = store_.snapshot();
    for (auto& entity : *snapshot.mutable_entities()) {
        if (const auto live = actors_.find(entity.id()); live != actors_.end())
            entity = live->second.entity;
        const auto holder = object_leases_.find(entity.id());
        if (holder != object_leases_.end()) *entity.mutable_pose() = actors_.at(holder->second).object->preview;
    }
    auto result = panel_scene(snapshot, actor.entity, config_.render());
    if (!geometry_only && surfaces_ && actor.local_application_output) {
        surfaces_->append(result, actor.entity.room());
        std::vector<ObservedApplication> applications;
        std::set<std::string> diagnostic;
        for (const auto& surface : result.surfaces()) {
            const auto app = surfaces_->application(surface.handle());
            if (app && actor.principal) applications.push_back({surface.handle(), *app});
            else if (!actor.authority && (!app || !actor.principal)) diagnostic.insert(surface.handle().id());
        }
        const auto allowed = observable_applications(actor, applications);
        auto* surfaces = result.mutable_surfaces();
        for (int i = surfaces->size() - 1; i >= 0; --i)
            if (!allowed.contains(surfaces->Get(i).handle().id()) && !diagnostic.contains(surfaces->Get(i).handle().id()))
                surfaces->DeleteSubrange(i, 1);
        for (const auto& app : applications) if (allowed.contains(app.handle.id())) actor.observed_applications.push_back(app);
    }
    return result;
}
identity::v1::ActorGeometryView Actors::geometry_view(const std::string& token, const std::string& world_id,
        const std::string& actor_id, const std::string& view_id, std::uint64_t authority_generation,
        std::uint64_t sequence) {
    auto scene_data = scene(token, true);
    require(!world_id.empty() && !actor_id.empty() && view_id.size() == 32 && authority_generation > 0 && sequence > 0,
            "Invalid geometry view context");
    identity::v1::ActorGeometryView result;
    result.set_world_id(world_id); result.set_actor_id(actor_id); result.set_view_id(view_id);
    result.set_authority_generation(authority_generation); result.set_sequence(sequence);
    result.set_world_revision(scene_data.world_revision());
    const auto state = inspect(token);
    result.set_room(state.room()); *result.mutable_camera() = state.pose(); *result.mutable_config() = scene_data.config();
    const auto copy = [](const auto& input, auto* output) {
        for (const auto& entity : input) {
            auto* item = output->Add(); item->set_id(entity.id());
            if (entity.has_kind()) item->set_kind(entity.kind());
            if (entity.has_pose()) *item->mutable_pose() = entity.pose();
            if (entity.has_size()) *item->mutable_size() = entity.size();
            if (entity.has_color()) *item->mutable_color() = entity.color();
            if (entity.has_bounds()) *item->mutable_bounds() = entity.bounds();
        }
    };
    copy(scene_data.panels(), result.mutable_panels()); copy(scene_data.barriers(), result.mutable_barriers());
    copy(scene_data.portals(), result.mutable_portals()); copy(scene_data.objects(), result.mutable_objects());
    copy(scene_data.actors(), result.mutable_actors());
    return result;
}
wire::ActorObservation Actors::observe(Actor& actor, const wire::ActorObserveRequest& request) {
    require(request.has_width() == request.has_height(), "Observation requires both viewport dimensions");
    const unsigned width = request.has_width() ? request.width() : 512;
    const unsigned height = request.has_height() ? request.height() : 512;
    validate_observation_extent(width, height, request.include_pixels());
    if (!renderer_) throw WorldError(wire::UNIMPLEMENTED, "Observation requires the compositor");
    refresh_native();
    const auto view = scene(actor.token);
    wire::ActorObservation result;
    result.set_actor(actor.entity.id()); result.set_room(actor.entity.room());
    *result.mutable_pose() = view.camera(); result.set_world_revision(view.world_revision());
    result.mutable_viewport()->set_width(width); result.mutable_viewport()->set_height(height);
    // Copy camera context before compositor I/O. The scheduler may move actors while waiting.
    const auto visibility = renderer_->observe(view, width, height, request.include_pixels());
    if (!application_observation_valid(actor.token))
        throw WorldError(wire::FORBIDDEN, "Application observation authority changed");
    result.set_frame_id(visibility.frame_id());
    if (visibility.has_image()) *result.mutable_image() = visibility.image();
    const double focal = static_cast<float>(1 / std::tan(view.config().vertical_fov() / 2));
    const auto geometry = scene_panels(view);
    std::map<std::string, wire::ObservedRepresentation> visible;
    for (const auto& sample : visibility.visible()) {
        const auto& description = geometry.at(sample.index() - 1).representation;
        auto& item = visible[description.id()];
        if (item.visible_pixels()) { item.set_visible_pixels(item.visible_pixels() + sample.pixels()); continue; }
        item = description;
        *item.mutable_sample() = sample.pixel(); item.set_visible_pixels(sample.pixels());
        auto* direction = item.mutable_direction();
        direction->set_x((2.0 * (sample.pixel().x() + .5) - width) / (focal * height));
        direction->set_y((height - 2.0 * (sample.pixel().y() + .5)) / (focal * height));
        direction->set_z(-1);
    }
    for (const auto& [id, item] : visible) { (void)id; *result.add_visible() = item; }
    if (result.ByteSizeLong() > max_frame_bytes - 128)
        throw WorldError(wire::RESOURCE_EXHAUSTED, "Observation exceeds message limit");
    return result;
}
void Actors::refresh_collisions() {
    const auto revision = store_.info().world_revision();
    if (revision == collision_revision_) return;
    std::map<std::string, RoomGeometry> rooms;
    const auto snapshot = store_.snapshot();
    for (const auto& entity : snapshot.entities()) {
        if (entity.kind() == wire::ROOM) rooms.try_emplace(entity.id());
        else if (entity.kind() == wire::BARRIER) {
            const auto& low = entity.bounds().min(); const auto& high = entity.bounds().max();
            rooms[entity.room()].colliders.push_back({scene_pose(entity.pose()), {{low.x(), low.y(), low.z()}, {high.x(), high.y(), high.z()}}});
        } else if (entity.kind() == wire::PORTAL) {
            const auto entry = scene_pose(entity.pose());
            const auto mapping = compose_pose(scene_pose(entity.destination().pose()), inverse_pose(entry));
            rooms[entity.room()].portals.push_back({entity.id(), entity.destination().room(), entry, mapping,
                                                   {entity.size().x(), entity.size().y()}});
        }
    }
    collision_rooms_ = std::move(rooms); collision_revision_ = revision;
}
void Actors::step(double target) {
    require(std::isfinite(target) && target >= now_, "Invalid monotonic time");
    bool collision_ready = false;
    for (auto& [id, actor] : actors_) {
        (void)id;
        if (actor.authority && !granted(actor, "navigate")) { actor.axes.fill(0); actor.expires = now_; }
        const double end = std::min(target, actor.expires);
        if (end > now_) {
            const Vec3 linear{actor.axes[0] * config_.linear_gain(), actor.axes[1] * config_.linear_gain(), actor.axes[2] * config_.linear_gain()};
            const Vec3 angular{actor.axes[3] * config_.angular_gain(), actor.axes[4] * config_.angular_gain(), actor.axes[5] * config_.angular_gain()};
            const double speed = std::hypot(linear[0], linear[1], linear[2]);
            auto current = as_pose(actor.entity.pose());
            if (speed > 0 && !collision_ready) { refresh_collisions(); collision_ready = true; }
            const auto found = collision_rooms_.find(actor.entity.room());
            if (speed == 0 || found == collision_rooms_.end() ||
                (found->second.colliders.empty() && found->second.portals.empty()))
                current = integrate(current, linear, angular, end - now_);
            else {
                const double radius = config_.actor_radius();
                const double rate = std::hypot(angular[0], angular[1], angular[2]);
                const double duration = end - now_;
                // A body-twist arc deviates from its chord by at most |v||w|dt^2/8.
                // Inflate each swept sphere by that bound so curved motion cannot skip a thin wall.
                const double count = std::max(1.0, std::ceil(std::sqrt(speed * rate * duration * duration / (8 * radius * 1e-4))));
                if (count > 4096) {
                    actor.axes.fill(0); actor.expires = now_;
                    std::cerr << "worldd: cancelled excessive collision subdivision\n";
                } else {
                    const auto steps = static_cast<unsigned>(count);
                    const double interval = duration / steps;
                    const double margin = speed * rate * interval * interval / 8;
                    unsigned crossings_left = 32;
                    for (unsigned i = 0; i < steps; ++i) {
                        const auto next = integrate(current, linear, angular, interval);
                        const auto motion = navigate_rooms(current.p, next, actor.entity.room(), radius + margin,
                                                           collision_rooms_, crossings_left);
                        current = motion.pose; actor.entity.set_room(motion.room);
                        if (motion.crossed) {
                            cancel_object(actor);
                            actor.aim.Clear(); actor.interaction_reset_pending = true;
                            actor.capture_motion_pending = false;
                        }
                        if (motion.stopped) {
                            actor.axes.fill(0); actor.expires = now_;
                            std::cerr << "worldd: stopped obstructed or excessive portal navigation\n";
                            break;
                        }
                    }
                }
            }
            set_pose(actor.entity.mutable_pose(), current);
            if (!actor.interaction_reset_pending && actor.native.held_buttons_size() &&
                std::any_of(actor.axes.begin(), actor.axes.end(), [](double v) { return v != 0; }))
                actor.capture_motion_pending = true;
        }
        if (target >= actor.expires) actor.axes.fill(0);
    }
    now_ = target;
}
void Actors::tick(double monotonic_seconds) { if (!manual_clock_) step(monotonic_seconds); }
bool Actors::cancellation_settled() const {
    if (flushing_input_ || !control_releases_.empty() || (renderer_ && renderer_->busy()) || (broker_ && broker_->busy())) return false;
    return std::none_of(actors_.begin(), actors_.end(), [](const auto& item) {
        return item.second.close_pending || item.second.interaction_reset_pending;
    });
}
void Actors::flush_input() {
    poll_control();
    refresh_authority();
    if (flushing_input_ || (renderer_ && renderer_->busy())) return;
    flushing_input_ = true;
    struct Reset { bool& value; ~Reset() { value = false; } } reset{flushing_input_};
    // Tick also runs while waiting on compositor I/O. Submit deferred motion only
    // from the outer scheduler, never recursively inside a renderer exchange.
    for (auto& [id, actor] : actors_) {
        (void)id;
        if (actor.interaction_reset_pending) release_input(actor);
    }
    drain_control_releases();
    if (broker_ && broker_->busy()) return;
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
wire::WorldCheckpointReply Actors::checkpoint_closed(const std::string& id) {
    auto found=actors_.find(id);
    if(found==actors_.end())throw WorldError(wire::NOT_FOUND,"Departing actor is unavailable");
    if(!found->second.token.empty() || !cancellation_settled())
        throw WorldError(wire::IN_USE,"Actor departure cleanup is not settled");
    if(!found->second.departure_pending)throw WorldError(wire::IN_USE,"No retained actor departure");
    struct Clear {bool& pending;~Clear(){pending=false;}} clear{found->second.departure_pending};
    const auto saved=store_.checkpoint_actors({found->second.entity});
    if(!saved.empty())found->second.entity=saved.front();
    wire::WorldCheckpointReply reply;reply.set_world_revision(store_.info().world_revision());return reply;
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
bool Actors::room_in_use(const std::string& id) const {
    return std::any_of(actors_.begin(), actors_.end(), [&](const auto& item) { return item.second.entity.room() == id; });
}
} // namespace spatial
