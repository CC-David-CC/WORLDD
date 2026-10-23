#include "world/actors.hpp"
#include "world/geometry_view_validation.hpp"
#include "world/workspace_presentations.hpp"
#include "world/presentation_geometry.hpp"
#include "platform/broker.hpp"
#include "world/surfaces.hpp"
#include "platform/render_worker.hpp"
#include "core/geometry.hpp"
#include "core/scene.hpp"
#include <google/protobuf/text_format.h>
#include <sys/random.h>
#include <limits>
#include <algorithm>
#include <cerrno>
#include <cmath>
#include <fstream>
#include <iterator>
#include <iostream>
#include <set>
#include <tuple>

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
std::optional<broker_wire::ScopedAuthorityCheckReply> Actors::workspace_check(
    const Actor& actor, const std::string& workspace, authority::v1::Right right,
    const std::vector<std::string>& components, std::vector<broker_wire::AuthorizationFence>* fences) {
    if (!broker_ || !actor.principal || workspace.empty()) return std::nullopt;
    broker_wire::ScopedAuthorityCheckRequest check;
    check.set_workspace(workspace); *check.mutable_principal() = *actor.principal; check.set_right(right);
    for (const auto& component : components) check.add_components(component);
    try {
        auto reply=broker_->scoped_authority_check(check);
        if (fences) {
            broker_wire::AuthorizationFence stamp;
            stamp.set_incarnation(reply.incarnation()); stamp.set_epoch(reply.epoch());
            fences->push_back(std::move(stamp));
        }
        return reply;
    }
    catch (const WorldError& error) {
        if (error.code == wire::NOT_FOUND || error.code == wire::FORBIDDEN || error.code == wire::UNAVAILABLE) return std::nullopt;
        throw;
    }
}
bool Actors::workspace_right(const Actor& actor, const std::string& workspace, authority::v1::Right right,
                             const std::vector<std::string>& components) {
    const auto decision=workspace_check(actor,workspace,right,components);
    return decision && decision->allowed();
}
bool Actors::workspace_file_access(const Actor& actor, const std::string& workspace,
                                   std::vector<broker_wire::AuthorizationFence>* fences) {
    if (!broker_ || workspace.empty()) return false;
    const auto resolve_binding = [&]() -> std::optional<wire::WorkspaceReply> {
        try {
            wire::Request query;
            query.mutable_workspace_get()->set_id(workspace);
            const auto reply = broker_->call(std::move(query));
            if (!reply.has_workspace()) return std::nullopt;
            auto binding = reply.workspace();
            if (binding.id() != workspace || binding.worktree().empty()) return std::nullopt;
            return binding;
        } catch (...) {
            return std::nullopt;
        }
    };
    const auto binding = resolve_binding();
    if (!binding) return false;
    const std::vector<std::string> components{binding->worktree()};
    using Right = authority::v1::Right;
    static constexpr Right file_rights[] = {Right::FILE_BROWSE, Right::FILE_READ, Right::FILE_CREATE,
        Right::FILE_EDIT, Right::FILE_RENAME, Right::FILE_MOVE, Right::FILE_COPY, Right::FILE_TRASH,
        Right::FILE_RESTORE, Right::FILE_PERMANENT_DELETE};
    for (const auto right : file_rights) {
        const auto decision = workspace_check(actor, workspace, right, components, fences);
        if (!decision || !decision->allowed()) continue;
        const auto confirmed = resolve_binding();
        if (!confirmed || confirmed->id() != binding->id() ||
            confirmed->worktree() != binding->worktree() || confirmed->revision() != binding->revision())
            return false;
        return true;
    }
    return false;
}
void Actors::validate_presentation_placement(const std::string& room, const wire::Pose& pose,
                                               const std::string& exclude_id) {
    if (store_.get(room).kind() != wire::ROOM) throw WorldError(wire::INVALID_ARGUMENT, "Presentation room is invalid");
    const auto center=scene_pose(pose).p;
    const double half=(config_.render().has_object_size()?config_.render().object_size():.2)/2;
    const double radius=std::sqrt(3.0)*half;
    const double diameter=2*radius;
    const auto snapshot=store_.snapshot();
    for (const auto& entity : snapshot.entities()) {
        if (entity.room()!=room) continue;
        if (entity.kind()==wire::OBJECT) {
            const auto p=scene_pose(entity.pose()).p;
            if (std::hypot(center[0]-p[0],center[1]-p[1],center[2]-p[2]) < diameter)
                throw WorldError(wire::INVALID_ARGUMENT,"Presentation placement overlaps an object");
        } else if (entity.kind()==wire::BARRIER) {
            const auto local=inverse_point(scene_pose(entity.pose()),center);
            const auto& low=entity.bounds().min(); const auto& high=entity.bounds().max();
            if (local[0]>=low.x()-radius && local[0]<=high.x()+radius &&
                local[1]>=low.y()-radius && local[1]<=high.y()+radius &&
                local[2]>=low.z()-radius && local[2]<=high.z()+radius)
                throw WorldError(wire::INVALID_ARGUMENT,"Presentation placement overlaps a room collider");
        }
    }
    for (const auto& other : store_.presentations()) {
        if (other.room_id!=room || other.id==exclude_id) continue;
        const auto p=scene_pose(other.pose).p;
        if (std::hypot(center[0]-p[0],center[1]-p[1],center[2]-p[2]) < diameter)
            throw WorldError(wire::INVALID_ARGUMENT,"Presentation placement overlaps another presentation");
    }
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
        if (!granted(actor, "observe")) { actor.aim.Clear(); actor.aim_application.reset(); }
        // A retained native seat is identity, not active input authority.
        // Reentrant wait callbacks must not rearm cancellation for idle seats.
        const bool owns_input = actor.application_control || !actor.lease.empty() ||
            actor.native.has_focus() || actor.native.held_keys_size() || actor.native.held_buttons_size();
        if (!owns_input) continue;
        const bool application_input = actor.application_input || actor.application_control.has_value();
        const bool control_lost = application_input ?
            (!granted(actor, "observe") || !broker_ || !actor.application_control ||
             !broker_->control_active(*actor.application_control)) : !granted(actor, "interact");
        if (control_lost) {
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
    if (next_witness_instance_id_ == UINT64_MAX) throw std::runtime_error("Actor instance identity exhausted");
    actor.witness_instance_id = ++next_witness_instance_id_;
    actor.token = token; actor.grants = std::move(grants); actor.sequence = 0;
    actor.application_input = false; actor.keyboard_application.reset();
    actor.require_observation_grant = require_observation_grant;
    actor.local_application_output = local_application_output;
    actor.axes.fill(0); actor.expires = now_;
    actor.aim.Clear(); actor.aim_application.reset();
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
    actor.aim.Clear(); actor.aim_application.reset(); actor.keyboard_application.reset();
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
        actor.axes.fill(0); actor.expires = now_; actor.aim.Clear(); actor.aim_application.reset(); cancel_object(actor); release_input(actor); actor.keyboard_application.reset(); break;
    case wire::ActorActionRequest::kUnfocus:
        release_input(actor); actor.keyboard_application.reset(); break;
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
        std::optional<workspace::v1::ApplicationRef> application;
        if (surfaces_ && aim.has_hit() && aim.hit().has_pixel()) {
            wire::SurfaceHandle target;
            target.set_id(aim.hit().id()); target.set_generation(aim.hit().generation());
            application = surfaces_->application(target);
        }
        move_capture(actor, request.aim().direction());
        actor.aim = aim; actor.aim_application = std::move(application);
        actor.direction = request.aim().direction();
        wire::ActorActionReply result; result.set_accepted(true); *result.mutable_aim() = actor.aim;
        return result;
    }
    case wire::ActorActionRequest::kPresentationCreate: {
        const auto& input = request.presentation_create();
        if (input.protocol_version() != 1 || input.operation_id().size() != 16 || input.workspace_id().empty() ||
            !input.has_transform() || !actor.principal ||
            (input.kind() != wire::PRESENTATION_KIND_GENERIC && input.kind() != wire::PRESENTATION_KIND_CABINET))
            throw WorldError(wire::INVALID_ARGUMENT, "Invalid presentation create request");
        (void)scene_pose(input.transform());
        if (!broker_)
            throw WorldError(wire::UNAVAILABLE, "Workspace authorization broker is unavailable");
        const auto room = input.has_room_id() ? input.room_id() : store_.info().entry_room();
        const std::vector<std::string> scope{room};
        if (!granted(actor, "presentation.create", scope)) throw WorldError(wire::FORBIDDEN, "Presentation placement authority required");
        if (!workspace_right(actor, input.workspace_id(), authority::v1::WORKSPACE_PRESENTATION_ATTACH))
            throw WorldError(wire::FORBIDDEN, "Workspace presentation attachment authority required");
        if (input.kind() == wire::PRESENTATION_KIND_CABINET && !workspace_file_access(actor, input.workspace_id()))
            throw WorldError(wire::FORBIDDEN, "Cabinet placement requires project-file authority");
        if (!workspace_right(actor, input.workspace_id(), authority::v1::WORKSPACE_PRESENTATION_ATTACH) ||
            !granted(actor, "presentation.create", scope))
            throw WorldError(wire::FORBIDDEN, "Presentation creation authority changed");
        const auto principal = actor.principal->issuer_id() + actor.principal->actor_id();
        const auto replay = store_.presentation_operation(input.operation_id(), request_digest(input), principal,
                                                           WorkspacePresentationOperation::create);
        if (replay) {
            if (replay->removed) throw WorldError(wire::STORAGE_ERROR, "Invalid create operation receipt");
            const auto& committed=replay->presentation;
            if (!workspace_right(actor, input.workspace_id(), authority::v1::WORKSPACE_PRESENTATION_ATTACH) ||
                !granted(actor,"presentation.create",scope))
                throw WorldError(wire::FORBIDDEN,"Presentation creation authority changed");
            const bool locked=!workspace_right(actor,committed.workspace_id,authority::v1::WORKSPACE_ACCESS);
            const bool cabinet_locked = committed.kind == PresentationKind::cabinet && !workspace_file_access(actor,committed.workspace_id);
            if (committed.kind == PresentationKind::cabinet && cabinet_locked)
                throw WorldError(wire::FORBIDDEN,"Cabinet file authority changed");
            if (!granted(actor,"presentation.create",scope))
                throw WorldError(wire::FORBIDDEN,"Presentation creation authority changed");
            const std::vector<std::string> view_scope{committed.room_id,committed.id};
            if (!granted(actor,"observe",view_scope) || !granted(actor,"representation.view",view_scope))
                throw WorldError(wire::FORBIDDEN,"Presentation view authority changed");
            wire::ActorActionReply reply; reply.set_accepted(true);
            auto* view=reply.mutable_presentation(); view->set_representation_id(committed.id); view->set_room_id(committed.room_id);
            *view->mutable_transform()=committed.pose; view->set_generation(committed.generation); view->set_locked(locked);
            view->set_kind(committed.kind == PresentationKind::cabinet ? wire::PRESENTATION_KIND_CABINET : wire::PRESENTATION_KIND_GENERIC);
            view->set_cabinet_locked(cabinet_locked);
            return reply;
        }
        validate_presentation_placement(room, input.transform());
        std::string id(16, '\0');
        std::size_t offset = 0;
        while (offset < id.size()) {
            const auto count = getrandom(id.data() + offset, id.size() - offset, 0);
            if (count < 0 && errno == EINTR) continue;
            if (count <= 0) throw std::runtime_error("Cannot obtain presentation identity entropy");
            offset += static_cast<std::size_t>(count);
        }
        if (!workspace_right(actor, input.workspace_id(), authority::v1::WORKSPACE_PRESENTATION_ATTACH) ||
            !granted(actor, "presentation.create", scope) ||
            (input.kind() == wire::PRESENTATION_KIND_CABINET && !workspace_file_access(actor, input.workspace_id())))
            throw WorldError(wire::FORBIDDEN, "Presentation creation authority changed");
        WorkspacePresentation desired{id, input.workspace_id(), room, input.transform(), 0,
            input.kind() == wire::PRESENTATION_KIND_CABINET ? PresentationKind::cabinet : PresentationKind::generic};
        const auto created = store_.create_presentation(desired, input.expected_world_revision(), input.operation_id(),
                                                        request_digest(input), principal);
        wire::ActorActionReply reply; reply.set_accepted(true);
        auto* view = reply.mutable_presentation(); view->set_representation_id(created.id); view->set_room_id(created.room_id);
        *view->mutable_transform() = created.pose; view->set_generation(created.generation);
        view->set_locked(!workspace_right(actor, created.workspace_id, authority::v1::WORKSPACE_ACCESS));
        view->set_kind(created.kind == PresentationKind::cabinet ? wire::PRESENTATION_KIND_CABINET : wire::PRESENTATION_KIND_GENERIC);
        view->set_cabinet_locked(created.kind == PresentationKind::cabinet && !workspace_file_access(actor, created.workspace_id));
        if (created.kind == PresentationKind::cabinet && view->cabinet_locked())
            throw WorldError(wire::FORBIDDEN,"Cabinet file authority changed");
        const std::vector<std::string> view_scope{created.room_id,created.id};
        if (!granted(actor,"observe",view_scope) || !granted(actor,"representation.view",view_scope))
            throw WorldError(wire::FORBIDDEN,"Presentation view authority changed");
        return reply;
    }
    case wire::ActorActionRequest::kPresentationMove:
    case wire::ActorActionRequest::kPresentationRemove: {
        const bool moving = request.action_case() == wire::ActorActionRequest::kPresentationMove;
        const auto& id = moving ? request.presentation_move().representation_id() : request.presentation_remove().representation_id();
        const auto& operation = moving ? request.presentation_move().operation_id() : request.presentation_remove().operation_id();
        const auto expected_generation = moving ? request.presentation_move().expected_representation_generation() :
                                                   request.presentation_remove().expected_representation_generation();
        if (operation.size() != 16 || id.size() != 16 || expected_generation == 0 || !actor.principal ||
            (moving && (request.presentation_move().protocol_version()!=1 || request.presentation_move().room_id().empty() ||
                        !request.presentation_move().has_transform())) ||
            (!moving && request.presentation_remove().protocol_version()!=1))
            throw WorldError(wire::INVALID_ARGUMENT, "Invalid presentation action request");
        if (moving) (void)scene_pose(request.presentation_move().transform());
        const auto source_room = actor.entity.room();
        const auto principal = actor.principal->issuer_id() + actor.principal->actor_id();
        const auto digest = moving ? request_digest(request.presentation_move()) : request_digest(request.presentation_remove());
        const auto operation_kind = moving ? WorkspacePresentationOperation::move : WorkspacePresentationOperation::remove;
        const auto receipt = store_.presentation_operation(operation, digest, principal, operation_kind);
        if (receipt) {
            if (receipt->source_room_id.empty())
                throw WorldError(wire::STALE_TARGET, "Presentation replay scope is unavailable");
            const auto recorded_source = receipt->source_room_id;
            const auto destination = moving ? request.presentation_move().room_id() : recorded_source;
            const std::vector<std::string> source_scope{recorded_source, id};
            const std::vector<std::string> destination_scope{destination, id};
            const auto source_allowed = granted(actor, "observe", source_scope) && granted(actor, "representation.view", source_scope);
            const auto destination_allowed = granted(actor, "observe", destination_scope) && granted(actor, "representation.view", destination_scope);
            if (!source_allowed || !destination_allowed ||
                (moving ? (!granted(actor, "representation.move", source_scope) || !granted(actor, "representation.move", destination_scope)) :
                          !granted(actor, "presentation.remove", source_scope)))
                throw WorldError(wire::FORBIDDEN, "Presentation action authority required");
            bool locked=true;
            if (moving && !receipt->removed) {
                const auto& committed = receipt->presentation;
                locked=!workspace_right(actor, committed.workspace_id, authority::v1::WORKSPACE_ACCESS);
            }
            if (!granted(actor,"observe",source_scope) || !granted(actor,"representation.view",source_scope) ||
                !granted(actor,"observe",destination_scope) || !granted(actor,"representation.view",destination_scope) ||
                (moving ? (!granted(actor, "representation.move", source_scope) || !granted(actor, "representation.move", destination_scope)) :
                          !granted(actor, "presentation.remove", source_scope)))
                throw WorldError(wire::FORBIDDEN,"Presentation action authority changed");
            wire::ActorActionReply reply; reply.set_accepted(true);
            if (moving && !receipt->removed) {
                const auto& committed=receipt->presentation;
                auto* view=reply.mutable_presentation(); view->set_representation_id(committed.id); view->set_room_id(committed.room_id);
                *view->mutable_transform()=committed.pose; view->set_generation(committed.generation); view->set_locked(locked);
                view->set_kind(committed.kind == PresentationKind::cabinet ? wire::PRESENTATION_KIND_CABINET : wire::PRESENTATION_KIND_GENERIC);
                view->set_cabinet_locked(committed.kind == PresentationKind::cabinet && !workspace_file_access(actor, committed.workspace_id));
            }
            return reply;
        }
        const std::vector<std::string> source_scope{source_room, id};
        if (!granted(actor, "observe", source_scope) || !granted(actor, "representation.view", source_scope) ||
            !granted(actor, moving ? "representation.move" : "presentation.remove", source_scope))
            throw WorldError(wire::FORBIDDEN, "Presentation action authority required");
        WorkspacePresentation current;
        try { current = store_.presentation(id); }
        catch (const WorldError& error) {
            if (error.code == wire::NOT_FOUND) throw WorldError(wire::NOT_VISIBLE, "Presentation target is unavailable");
            throw;
        }
        if (current.room_id != source_room) throw WorldError(wire::NOT_VISIBLE, "Presentation target is unavailable");
        const auto camera = scene_pose(actor.entity.pose());
        const auto target = scene_pose(current.pose);
        if (std::hypot(target.p[0]-camera.p[0], target.p[1]-camera.p[1], target.p[2]-camera.p[2]) > config_.interaction_reach())
            throw WorldError(wire::OUT_OF_REACH, "Presentation target exceeds interaction reach");
        const auto observation = observe(actor, wire::ActorObserveRequest{});
        if (actor.entity.room() != observation.room() || actor.entity.pose().SerializeAsString() != observation.pose().SerializeAsString())
            throw WorldError(wire::STALE_TARGET, "Actor moved during presentation visibility check");
        const auto geometry_id = presentation_geometry_id(id);
        const auto visible = std::find_if(observation.visible().begin(), observation.visible().end(),
            [&](const auto& item) { return item.id() == geometry_id && item.kind() == wire::OBJECT; });
        if (visible == observation.visible().end() || visible->generation() != current.generation)
            throw WorldError(wire::NOT_VISIBLE, "Presentation target is unavailable");
        WorkspacePresentation after_observe;
        try { after_observe = store_.presentation(id); }
        catch (const WorldError& error) {
            if (error.code == wire::NOT_FOUND) throw WorldError(wire::STALE_TARGET, "Presentation changed during visibility check");
            throw;
        }
        if (after_observe.generation != current.generation || after_observe.room_id != current.room_id ||
            after_observe.pose.SerializeAsString() != current.pose.SerializeAsString())
            throw WorldError(wire::STALE_TARGET, "Presentation changed during visibility check");
        if (!granted(actor, "observe", source_scope) || !granted(actor, "representation.view", source_scope) ||
            !granted(actor, moving ? "representation.move" : "presentation.remove", source_scope))
            throw WorldError(wire::FORBIDDEN, "Presentation action authority changed");
        if (moving) {
            const auto& input = request.presentation_move();
            if (input.protocol_version() != 1 || !input.has_transform() || input.room_id().empty())
                throw WorldError(wire::INVALID_ARGUMENT, "Invalid presentation move request");
            const std::vector<std::string> destination_scope{input.room_id(), id};
            if (!granted(actor, "representation.move", destination_scope))
                throw WorldError(wire::FORBIDDEN, "Destination presentation movement authority required");
            const auto placement_revision=store_.info().world_revision();
            validate_presentation_placement(input.room_id(), input.transform(), id);
            if (!granted(actor, "observe", source_scope) || !granted(actor, "representation.view", source_scope) ||
                !granted(actor, "representation.move", source_scope) || !granted(actor, "representation.move", destination_scope))
                throw WorldError(wire::FORBIDDEN, "Presentation movement authority changed");
            const auto moved = store_.move_presentation(id, input.room_id(), input.transform(), expected_generation,
                operation, digest, principal, placement_revision);
            wire::ActorActionReply reply; reply.set_accepted(true);
            auto* view = reply.mutable_presentation(); view->set_representation_id(moved.id); view->set_room_id(moved.room_id);
            *view->mutable_transform() = moved.pose; view->set_generation(moved.generation);
            view->set_locked(!workspace_right(actor, moved.workspace_id, authority::v1::WORKSPACE_ACCESS));
            view->set_kind(moved.kind == PresentationKind::cabinet ? wire::PRESENTATION_KIND_CABINET : wire::PRESENTATION_KIND_GENERIC);
            view->set_cabinet_locked(moved.kind == PresentationKind::cabinet && !workspace_file_access(actor, moved.workspace_id));
            const std::vector<std::string> view_scope{moved.room_id,moved.id};
            if (!granted(actor,"observe",view_scope) || !granted(actor,"representation.view",view_scope))
                throw WorldError(wire::FORBIDDEN,"Presentation view authority changed");
            actor.aim.Clear(); actor.aim_application.reset(); return reply;
        }
        const auto& input = request.presentation_remove();
        if (input.protocol_version() != 1) throw WorldError(wire::INVALID_ARGUMENT, "Invalid presentation remove request");
        store_.remove_presentation(id, expected_generation, operation, digest, principal);
        actor.aim.Clear(); actor.aim_application.reset(); break;
    }
    case wire::ActorActionRequest::kWorkspaceCopy: {
        if (!actor.principal) throw WorldError(wire::FORBIDDEN, "Workspace copy requires an authenticated principal");
        if (!broker_) throw WorldError(wire::UNAVAILABLE, "Workspace copy authority is unavailable");
        const auto status = broker_->workspace_copy_begin(request.workspace_copy(), *actor.principal);
        wire::ActorActionReply reply; reply.set_accepted(true); *reply.mutable_copy_status() = status; return reply;
    }
    case wire::ActorActionRequest::kWorkspaceCopyStatus: {
        if (!actor.principal) throw WorldError(wire::FORBIDDEN, "Workspace copy status requires an authenticated principal");
        if (!broker_) throw WorldError(wire::UNAVAILABLE, "Workspace copy authority is unavailable");
        const auto status = broker_->workspace_copy_status(request.workspace_copy_status(), *actor.principal);
        wire::ActorActionReply reply; reply.set_accepted(true); *reply.mutable_copy_status() = status; return reply;
    }
    case wire::ActorActionRequest::kCabinet: {
        wire::ActorActionReply result;
        result.set_accepted(true);
        auto* cabinet = result.mutable_cabinet();
        cabinet_action(actor, request.cabinet(), cabinet);
        return result;
    }
    case wire::ActorActionRequest::ACTION_NOT_SET:
        throw WorldError(wire::UNKNOWN_METHOD, "No recognized actor action");
    }
    wire::ActorActionReply result; result.set_accepted(true); return result;
}
void Actors::cabinet_action(Actor& actor, const workspace::v1::ActorCabinetRequest& input,
                           workspace::v1::ActorCabinetReply* reply) {
    using namespace workspace::v1;
    using Right = authority::v1::Right;
    if (!reply) throw std::invalid_argument("Missing cabinet reply storage");

    // Prepare the no-metadata recovery envelope before any broker call can take effect.
    const std::string operation_id = input.operation_id();
    const std::string representation_id = input.representation_id();
    const auto expected_generation = input.expected_generation();
    reply->set_operation_id(operation_id);
    reply->set_representation_id(representation_id);
    reply->set_expected_generation(expected_generation);
    reply->mutable_error()->set_code(FileActionError::UNCERTAIN);

    if (operation_id.size() != 16 || representation_id.size() != 16 || expected_generation == 0 ||
        input.ByteSizeLong() > 1100000 || input.action_case() == ActorCabinetRequest::ACTION_NOT_SET)
        throw WorldError(wire::INVALID_ARGUMENT, "Invalid actor cabinet request");
    if (!actor.principal) throw WorldError(wire::FORBIDDEN, "Authenticated workspace identity required");
    if (!broker_) throw WorldError(wire::UNAVAILABLE, "Workspace file broker is unavailable");

    // Snapshot identity before BrokerClient waits and pumps issuer/control traffic.
    const std::string actor_token = actor.token;
    const std::string actor_id = actor.entity.id();
    const std::string source_room = actor.entity.room();
    const std::string source_pose = actor.entity.pose().SerializeAsString();
    const authority::v1::ActorRef principal = *actor.principal;
    const std::vector<std::string> target_scope{source_room, representation_id};
    const auto saved_context_current = [&]() {
        const auto session = sessions_.find(actor_token);
        return session != sessions_.end() && session->second == actor_id && actor.token == actor_token &&
            actor.entity.id() == actor_id && actor.entity.room() == source_room &&
            actor.entity.pose().SerializeAsString() == source_pose && actor.principal &&
            actor.principal->SerializeAsString() == principal.SerializeAsString();
    };
    const auto same_binding = [](const wire::WorkspaceReply& a, const wire::WorkspaceReply& b) {
        return a.id() == b.id() && a.worktree() == b.worktree() && a.revision() == b.revision();
    };
    if (!granted(actor, "observe", target_scope) || !granted(actor, "representation.view", target_scope))
        throw WorldError(wire::FORBIDDEN, "Presentation visibility authority required");

    WorkspacePresentation current;
    try {
        current = store_.presentation(representation_id);
    } catch (const WorldError& error) {
        if (error.code == wire::NOT_FOUND)
            throw WorldError(wire::NOT_VISIBLE, "Presentation target is unavailable");
        throw;
    }
    if (current.room_id != source_room)
        throw WorldError(wire::NOT_VISIBLE, "Presentation target is unavailable");
    if (current.generation != expected_generation)
        throw WorldError(wire::STALE_TARGET, "Presentation generation changed");
    if (current.kind != PresentationKind::cabinet)
        throw WorldError(wire::NOT_VISIBLE, "Cabinet target is unavailable");
    if (current.workspace_id.empty())
        throw WorldError(wire::UNAVAILABLE, "Presentation workspace binding is unavailable");
    const auto camera = scene_pose(actor.entity.pose());
    const auto target = scene_pose(current.pose);
    if (std::hypot(target.p[0]-camera.p[0], target.p[1]-camera.p[1], target.p[2]-camera.p[2]) > config_.interaction_reach())
        throw WorldError(wire::OUT_OF_REACH, "Presentation target exceeds interaction reach");

    const auto observation = observe(actor, wire::ActorObserveRequest{});
    if (!saved_context_current() || actor.entity.room() != observation.room() ||
        actor.entity.pose().SerializeAsString() != observation.pose().SerializeAsString())
        throw WorldError(wire::STALE_TARGET, "Actor session or context changed during cabinet visibility check");
    const auto geometry_id = presentation_geometry_id(representation_id);
    const auto visible = std::find_if(observation.visible().begin(), observation.visible().end(),
        [&](const auto& item) { return item.id() == geometry_id && item.kind() == wire::OBJECT; });
    if (visible == observation.visible().end() || visible->generation() != current.generation)
        throw WorldError(wire::NOT_VISIBLE, "Presentation target is unavailable");
    WorkspacePresentation after_observe;
    try {
        after_observe = store_.presentation(representation_id);
    } catch (const WorldError& error) {
        if (error.code == wire::NOT_FOUND)
            throw WorldError(wire::STALE_TARGET, "Presentation changed during visibility check");
        throw;
    }
    if (after_observe.generation != current.generation || after_observe.room_id != current.room_id ||
        after_observe.pose.SerializeAsString() != current.pose.SerializeAsString())
        throw WorldError(wire::STALE_TARGET, "Presentation changed during visibility check");
    if (!granted(actor, "observe", target_scope) || !granted(actor, "representation.view", target_scope))
        throw WorldError(wire::FORBIDDEN, "Presentation visibility authority changed");
    if (!granted(actor, "representation.use", target_scope))
        throw WorldError(wire::FORBIDDEN, "Cabinet use authority required");
    if (!saved_context_current())
        throw WorldError(wire::UNAUTHORIZED, "Actor session or context changed before cabinet action");

    const auto local_target_still_current = [&]() {
        if (!saved_context_current() || !granted(actor, "observe", target_scope) ||
            !granted(actor, "representation.view", target_scope) ||
            !granted(actor, "representation.use", target_scope)) return false;
        try {
            const auto live = store_.presentation(representation_id);
            const auto found = std::find_if(observation.visible().begin(), observation.visible().end(),
                [&](const auto& item) { return item.id() == geometry_id && item.kind() == wire::OBJECT; });
            return live.workspace_id == current.workspace_id && live.room_id == current.room_id &&
                live.generation == current.generation && live.pose.SerializeAsString() == current.pose.SerializeAsString() &&
                found != observation.visible().end() && found->generation() == current.generation;
        } catch (...) { return false; }
    };

    wire::WorkspaceReply dispatch_binding;
    try {
        wire::Request query;
        query.mutable_workspace_get()->set_id(current.workspace_id);
        dispatch_binding = broker_->call(std::move(query)).workspace();
    } catch (...) {
        throw WorldError(wire::UNAVAILABLE, "Registered workspace binding is unavailable");
    }
    if (dispatch_binding.id() != current.workspace_id || dispatch_binding.worktree().empty() ||
        !saved_context_current())
        throw WorldError(wire::STALE_TARGET, "Workspace binding changed before cabinet action");

    FileActionRequest private_request;
    private_request.set_protocol_version(1);
    private_request.set_operation_id(operation_id);
    private_request.set_workspace_id(current.workspace_id);
    *private_request.mutable_principal() = principal;
    Right required = authority::v1::RIGHT_UNSPECIFIED;
    bool effectful = true;
    switch (input.action_case()) {
    case ActorCabinetRequest::kBrowse:
        *private_request.mutable_browse() = input.browse(); required = Right::FILE_BROWSE; effectful = false; break;
    case ActorCabinetRequest::kRead:
        *private_request.mutable_read() = input.read(); required = Right::FILE_READ; effectful = false; break;
    case ActorCabinetRequest::kCreate:
        *private_request.mutable_create() = input.create(); required = Right::FILE_CREATE; break;
    case ActorCabinetRequest::kEdit:
        *private_request.mutable_edit() = input.edit(); required = Right::FILE_EDIT; break;
    case ActorCabinetRequest::kRename:
        *private_request.mutable_rename() = input.rename(); required = Right::FILE_RENAME; break;
    case ActorCabinetRequest::kMove:
        *private_request.mutable_move() = input.move(); required = Right::FILE_MOVE; break;
    case ActorCabinetRequest::kTrash:
        *private_request.mutable_trash() = input.trash(); required = Right::FILE_TRASH; break;
    case ActorCabinetRequest::kRestore:
        *private_request.mutable_restore() = input.restore(); required = Right::FILE_RESTORE; break;
    case ActorCabinetRequest::kPermanentDelete:
        *private_request.mutable_permanent_delete() = input.permanent_delete(); required = Right::FILE_PERMANENT_DELETE; break;
    case ActorCabinetRequest::kCopyJob:
        *private_request.mutable_copy_job() = input.copy_job(); required = Right::FILE_COPY; break;
    case ActorCabinetRequest::kTrashInspect:
        *private_request.mutable_trash_inspect() = input.trash_inspect(); required = Right::FILE_BROWSE; effectful = false; break;
    case ActorCabinetRequest::ACTION_NOT_SET:
        throw WorldError(wire::INVALID_ARGUMENT, "No cabinet action selected");
    }
    if (required == authority::v1::RIGHT_UNSPECIFIED || private_request.ByteSizeLong() > 1100000)
        throw WorldError(wire::INVALID_ARGUMENT, "Invalid private cabinet action");
    if (!saved_context_current())
        throw WorldError(wire::UNAUTHORIZED, "Actor session or context changed before cabinet delivery");
    try {
        wire::Request verify;
        verify.mutable_workspace_get()->set_id(current.workspace_id);
        const auto now_binding = broker_->call(std::move(verify)).workspace();
        if (!same_binding(dispatch_binding, now_binding))
            throw WorldError(wire::STALE_TARGET, "Workspace binding changed before cabinet delivery");
        if (!local_target_still_current())
            throw WorldError(wire::UNAUTHORIZED, "Actor authority or presentation changed before cabinet delivery");
    } catch (const WorldError&) { throw; }
      catch (...) { throw WorldError(wire::UNAVAILABLE, "Registered workspace binding is unavailable"); }

    std::optional<FileActionReply> file_result;
    try {
        file_result.emplace(broker_->file_action(private_request));
    } catch (const WorldError&) {
        // BrokerClient throws a controlled rejection only before possible delivery.
        reply->mutable_error()->set_code(FileActionError::UNAVAILABLE);
        return;
    } catch (...) {
        // A failure moving or retaining a possibly delivered reply keeps the prepared UNCERTAIN outcome.
        return;
    }
    const auto& received = *file_result;

    const bool broker_error = received.has_error();
    const auto broker_error_code = broker_error &&
        FileActionError::Code_IsValid(received.error().code()) &&
        received.error().code() != FileActionError::CODE_UNSPECIFIED
        ? received.error().code() : FileActionError::UNAVAILABLE;

    // Revalidate authority and all public bindings after the broker may have waited.
    // Any failure here follows possible delivery, so effects retain the prepared UNCERTAIN envelope.
    bool current_authority = false;
    try {
        const auto post_observation = observe(actor, wire::ActorObserveRequest{});
        wire::Request query;
        query.mutable_workspace_get()->set_id(current.workspace_id);
        const auto after_effect_binding = broker_->call(std::move(query)).workspace();
        const bool binding_matches = same_binding(dispatch_binding, after_effect_binding) &&
            after_effect_binding.id() == current.workspace_id;

        broker_wire::ScopedAuthorityCheckRequest check;
        check.set_workspace(current.workspace_id);
        *check.mutable_principal() = principal;
        check.set_right(required);
        check.add_components(dispatch_binding.worktree());
        const auto decision = broker_->scoped_authority_check(check);

        wire::Request confirm;
        confirm.mutable_workspace_get()->set_id(current.workspace_id);
        const auto confirmed_binding = broker_->call(std::move(confirm)).workspace();
        broker_wire::AuthorizationFenceRequest fence_request;
        *fence_request.mutable_principal() = principal;
        const auto fence = broker_->authorization_fence(fence_request);
        const bool authority_stable = decision.allowed() &&
            decision.incarnation() == fence.incarnation() && decision.epoch() == fence.epoch();

        // granted() consults only the local world authority store. Do it before
        // the final identity/context/target comparison, which is the last operation.
        const bool local_rights = granted(actor, "observe", target_scope) &&
            granted(actor, "representation.view", target_scope) &&
            granted(actor, "representation.use", target_scope);
        const auto session = sessions_.find(actor_token);
        const bool session_matches = session != sessions_.end() && session->second == actor_id &&
            actor.token == actor_token && actor.entity.id() == actor_id && actor.principal &&
            actor.principal->SerializeAsString() == principal.SerializeAsString() &&
            actor.entity.room() == source_room && actor.entity.pose().SerializeAsString() == source_pose;
        const auto live = store_.presentation(representation_id);
        const auto post_visible = std::find_if(post_observation.visible().begin(), post_observation.visible().end(),
            [&](const auto& item) { return item.id() == geometry_id && item.kind() == wire::OBJECT; });
        const bool target_matches = live.workspace_id == current.workspace_id && live.room_id == current.room_id &&
            live.generation == current.generation && live.pose.SerializeAsString() == current.pose.SerializeAsString() &&
            post_observation.room() == source_room && post_observation.pose().SerializeAsString() == source_pose &&
            post_visible != post_observation.visible().end() && post_visible->generation() == current.generation;
        current_authority = binding_matches && same_binding(after_effect_binding, confirmed_binding) &&
            authority_stable && local_rights && session_matches && target_matches;
    } catch (...) {
        current_authority = false;
    }
    if (!current_authority) {
        const bool broker_confirmed_no_effect = broker_error &&
            broker_error_code == FileActionError::UNAUTHORIZED;
        reply->mutable_error()->set_code(broker_confirmed_no_effect || !effectful
            ? FileActionError::UNAUTHORIZED : FileActionError::UNCERTAIN);
        return;
    }
    if (broker_error) {
        // Project a file-state code only while the actor still has cabinet authority.
        // The actor-facing envelope intentionally omits FileActionError.detail.
        reply->mutable_error()->set_code(broker_error_code);
        return;
    }

    try {
        ActorCabinetReply projected;
        projected.set_operation_id(operation_id);
        projected.set_representation_id(representation_id);
        projected.set_expected_generation(expected_generation);
        if (received.has_directory()) *projected.mutable_directory() = received.directory();
        else if (received.has_content()) *projected.mutable_content() = received.content();
        else if (received.has_object()) *projected.mutable_object() = received.object();
        else if (received.has_trash()) *projected.mutable_trash() = received.trash();
        else if (received.has_trash_entry()) *projected.mutable_trash_entry() = received.trash_entry();
        else if (received.has_copy_job()) {
            *projected.mutable_copy_job() = received.copy_job();
            projected.mutable_copy_job()->clear_detail();
        } else {
            reply->mutable_error()->set_code(effectful ? FileActionError::UNCERTAIN : FileActionError::UNAVAILABLE);
            return;
        }
        reply->Swap(&projected);
    } catch (...) {
        // Keep the preallocated UNCERTAIN envelope if result projection allocates or fails.
    }
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
void Actors::preflight_input_authority(Actor& actor, const workspace::v1::ApplicationRef& application) {
    if (!actor.principal) return;
    if (!broker_ || actor.close_pending || actor.departure_pending || !granted(actor, "observe"))
        throw WorldError(wire::FORBIDDEN, "Application control authority required");
    const auto principal = *actor.principal;
    const auto token = actor.token;
    workspace::v1::ApplicationAccessRequest request;
    *request.mutable_principal() = principal; *request.add_applications() = application;
    const auto access = broker_->application_access(request);
    if (actor.token.empty() || actor.token != token || actor.close_pending || actor.departure_pending || !actor.principal ||
        actor.principal->SerializeAsString() != principal.SerializeAsString() ||
        !granted(actor, "observe") || access.applications_size() != 1 ||
        !access.applications(0).observe() || !access.applications(0).control())
        throw WorldError(wire::FORBIDDEN, "Application control authority required");
    workspace::v1::AuthorizationFence stamp;
    stamp.set_incarnation(access.incarnation()); stamp.set_epoch(access.epoch());
    validate_authority_fence(actor, {stamp});
    if (actor.token.empty() || actor.token != token || actor.close_pending || actor.departure_pending || !actor.principal ||
        actor.principal->SerializeAsString() != principal.SerializeAsString() || !granted(actor, "observe"))
        throw WorldError(wire::FORBIDDEN, "Application control authority changed");
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
Actors::InputWitnessBinding Actors::input_witness_binding(const std::string& id,
        const workspace::v1::ApplicationRef& application) {
    validate_message(application);
    if(application.workspace().empty() || application.workspace().size()>256 || application.workspace().find('\0')!=std::string::npos ||
       application.application().empty() || application.application().size()>256 || application.application().find('\0')!=std::string::npos ||
       application.server_id().size()!=32)throw std::invalid_argument("Invalid input witness application");
    const auto found=actors_.find(id);
    if(found==actors_.end())throw WorldError(wire::NOT_FOUND,"Input witness actor is unavailable");
    auto& actor=found->second;
    auto active=[&]{return actor.principal && actor.principal->issuer_id().size()==32 && actor.principal->actor_id().size()==32 &&
        !actor.token.empty() && !actor.close_pending && !actor.departure_pending && !actor.interaction_reset_pending &&
        actor.local_application_output && granted(actor,"observe") && actor.witness_instance_id>0;};
    if(!renderer_ || !broker_ || !active())throw WorldError(wire::FORBIDDEN,"Authenticated active actor required for input witness");
    const auto token=actor.token;const auto principal=*actor.principal;const auto instance=actor.witness_instance_id;
    workspace::v1::ApplicationAccessRequest request;*request.mutable_principal()=principal;
    *request.add_applications()=application;
    const auto access=broker_->application_access(request);
    if(access.applications_size()!=1 || !access.applications(0).observe() || !access.applications(0).control() ||
       access.applications(0).application().SerializeAsString()!=application.SerializeAsString())
        throw WorldError(wire::FORBIDDEN,"Input witness application authority denied");
    auto native=renderer_->native_actor_seat(id);
    workspace::v1::AuthorizationFenceRequest fence_request;*fence_request.mutable_principal()=principal;
    const auto fence=broker_->authorization_fence(fence_request);
    if(!active() || actor.token!=token || actor.witness_instance_id!=instance ||
       actor.principal->SerializeAsString()!=principal.SerializeAsString() ||
       access.incarnation()!=fence.incarnation() || access.epoch()!=fence.epoch())
        throw WorldError(wire::FORBIDDEN,"Input witness actor authority changed");
    if(native.server_id()!=application.server_id())throw WorldError(wire::STALE_TARGET,"Input witness native server changed");
    InputWitnessBinding result;result.principal=principal;result.actor_entity_id=id;
    result.actor_instance_id=instance;result.native=std::move(native);result.fence=fence;result.private_session_token=token;return result;
}
bool Actors::input_witness_current(const InputWitnessBinding& binding) const {
    const auto found=actors_.find(binding.actor_entity_id);
    if(found==actors_.end())return false;
    const auto& actor=found->second;
    return actor.principal && actor.principal->SerializeAsString()==binding.principal.SerializeAsString() &&
        !actor.token.empty() && actor.token==binding.private_session_token && actor.witness_instance_id==binding.actor_instance_id &&
        !actor.close_pending && !actor.departure_pending && !actor.interaction_reset_pending &&
        actor.local_application_output && granted(actor,"observe");
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
    if (actor.native.has_focus()) {
        if (actor.application_control) actor.keyboard_application = actor.application_control->application();
        else actor.keyboard_application.reset();
    }
}
std::string Actors::control_group(const wire::SurfaceRecord& surface) const {
    return surfaces_->control_group(surface);
}
void Actors::refresh_native() {
    if (!renderer_ || !surfaces_) return;
    const auto snapshot = renderer_->synchronize();
    surfaces_->refresh(snapshot);
    // Visibility filtering cannot distinguish revocation from application exit.
    // Index complete native groups once for all remembered keyboard contexts.
    std::set<std::tuple<std::string, std::string, std::string>> input_applications;
    for (const auto& surface : snapshot.surfaces()) {
        if (!surface.mapped() || !surface.has_application_identity()) continue;
        const auto& identity = surface.application_identity();
        input_applications.emplace(identity.server_id(), identity.workspace(), identity.application());
    }
    for (auto& [id, actor] : actors_) {
        (void)id;
        if (!actor.keyboard_application) continue;
        const auto& application = *actor.keyboard_application;
        if (!input_applications.contains({application.server_id(), application.workspace(), application.application()}))
            actor.keyboard_application.reset();
    }
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
    const auto hit = actor.aim.hit();
    const auto application = actor.aim_application;
    auto stale = [&](const char* message) -> wire::AimHit {
        // Scene filtering can wait for broker IPC after the initial preflight.
        if (application) preflight_input_authority(actor, *application);
        throw WorldError(wire::STALE_TARGET, message);
    };
    try {
        const auto surface = surfaces_->get(hit.id());
        if (surface.generation() != hit.generation() || !surface.mapped() || surface.room() != actor.entity.room())
            return stale("Aimed surface changed");
    } catch (const WorldError& error) {
        if (error.code == wire::NOT_FOUND) return stale("Aimed surface was removed");
        throw;
    }
    const auto current = pick_scene(scene(actor.token), actor.direction);
    if (!current.has_hit() || current.hit().id() != hit.id() || !current.hit().has_pixel() ||
        current.hit().generation() != hit.generation() || current.hit().pixel().x() != hit.pixel().x() ||
        current.hit().pixel().y() != hit.pixel().y()) return stale("Aimed geometry or visibility changed");
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
        if (!actor.native.has_focus()) {
            // Cancellation removes focus, but cannot hide a missing application grant.
            // Copy the identity before broker waits can reenter session cleanup.
            const auto application = actor.keyboard_application;
            if (application) preflight_input_authority(actor, *application);
            throw WorldError(wire::NO_FOCUS, "Actor has no focus");
        }
        const auto target = actor.native.focus(); authorize_input(actor, target);
        *command.mutable_key() = request.key(); send_input(actor, command); return;
    }
    // Check the accepted application's authority before exposing saved-target state.
    // This read-only check cannot reserve or transfer application control.
    const auto application = !request.has_scroll() && actor.native.held_buttons_size() && actor.application_control ?
        std::optional<workspace::v1::ApplicationRef>(actor.application_control->application()) : actor.aim_application;
    if (application) preflight_input_authority(actor, *application);
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
void Actors::validate_authority_fence(const Actor& actor,
        const std::vector<broker_wire::AuthorizationFence>& stamps) {
    if (stamps.empty()) return;
    if (!broker_ || !actor.principal) throw WorldError(wire::UNAVAILABLE,"Workspace authority fence unavailable");
    broker_wire::AuthorizationFenceRequest request; *request.mutable_principal()=*actor.principal;
    broker_wire::AuthorizationFence current;
    try { current=broker_->authorization_fence(request); }
    catch (const WorldError&) { throw WorldError(wire::UNAVAILABLE,"Workspace authority fence unavailable"); }
    if (current.incarnation().size()!=16 || current.epoch()==0)
        throw WorldError(wire::UNAVAILABLE,"Workspace authority fence is malformed");
    for (const auto& stamp : stamps)
        if (stamp.incarnation()!=current.incarnation() || stamp.epoch()!=current.epoch())
            throw WorldError(wire::FORBIDDEN,"Workspace authority changed during operation");
}
bool Actors::observed_application_current(const Actor& actor, const ObservedApplication& item) const {
    if (!surfaces_) return false;
    try {
        const auto current=surfaces_->application(item.handle);
        const auto placement=surfaces_->get(item.handle.id());
        return current && current->SerializeAsString()==item.application.SerializeAsString() &&
            placement.room()==actor.entity.room();
    } catch (const WorldError& error) {
        if (error.code==wire::NOT_FOUND || error.code==wire::STALE_TARGET) return false;
        throw;
    }
}
bool Actors::observed_applications_current(const Actor& actor) const {
    return std::all_of(actor.observed_applications.begin(),actor.observed_applications.end(),
        [&](const auto& item) { return observed_application_current(actor,item); });
}
std::set<std::string> Actors::observable_applications(const Actor& actor,
        const std::vector<ObservedApplication>& applications,
        std::vector<broker_wire::AuthorizationFence>* fences) {
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
    if (fences) {
        broker_wire::AuthorizationFence stamp;
        stamp.set_incarnation(result.incarnation()); stamp.set_epoch(result.epoch());
        fences->push_back(std::move(stamp));
    }
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
bool Actors::application_observation_valid(const std::string& token, std::vector<broker_wire::AuthorizationFence>* fences) {
    const auto& actor = authenticated(token);
    const auto applications = actor.observed_applications;
    std::vector<broker_wire::AuthorizationFence> local;
    auto* collected=fences ? fences : &local;
    const bool valid=observable_applications(actor, applications, collected).size() == applications.size();
    if (!fences) {
        validate_authority_fence(actor,local);
        if (valid && !observed_applications_current(actor)) return false;
    }
    return valid;
}
wire::RenderScene Actors::scene(const std::string& token, bool geometry_only,
        std::vector<broker_wire::AuthorizationFence>* fences) {
    auto& actor = authenticated(token);
    std::vector<broker_wire::AuthorizationFence> local_fences;
    auto* collected=fences ? fences : &local_fences;
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
        const auto allowed = observable_applications(actor, applications, collected);
        auto* surfaces = result.mutable_surfaces();
        for (int i = surfaces->size() - 1; i >= 0; --i)
            if (!allowed.contains(surfaces->Get(i).handle().id()) && !diagnostic.contains(surfaces->Get(i).handle().id()))
                surfaces->DeleteSubrange(i, 1);
        for (const auto& app : applications) if (allowed.contains(app.handle.id())) actor.observed_applications.push_back(app);
    }
    struct ProjectionCandidate { PresentationGeometry geometry; std::string workspace_id; };
    std::vector<ProjectionCandidate> candidates;
    const auto projection_room=actor.entity.room();
    const auto projection_pose=actor.entity.pose().SerializeAsString();
    for (const auto& record : store_.presentations()) {
        if (record.room_id!=projection_room) continue;
        const std::vector<std::string> scope{record.room_id,record.id};
        if (!granted(actor,"observe",scope) || !granted(actor,"representation.view",scope)) continue;
        const auto decision=workspace_check(actor,record.workspace_id,authority::v1::WORKSPACE_ACCESS,{},collected);
        ProjectionCandidate candidate;
        candidate.geometry={record.id,record.room_id,record.pose,record.generation,
                            !decision || !decision->allowed(),false,record.kind,
                            record.kind == PresentationKind::cabinet && !workspace_file_access(actor,record.workspace_id,collected)};
        candidate.workspace_id=record.workspace_id;
        candidates.push_back(std::move(candidate));
    }
    if (!fences) validate_authority_fence(actor,local_fences);
    if (actor.entity.room()!=projection_room || actor.entity.pose().SerializeAsString()!=projection_pose)
        throw WorldError(wire::STALE_TARGET,"Actor moved during scene projection");
    if (actor.require_observation_grant && !granted(actor,"observe"))
        throw WorldError(wire::FORBIDDEN,"World observation authority changed during scene projection");
    if (!geometry_only && !actor.observed_applications.empty()) {
        auto* panels=result.mutable_surfaces();
        for (int i=panels->size()-1;i>=0;--i) {
            const auto handle=panels->Get(i).handle();
            const auto found=std::find_if(actor.observed_applications.begin(),actor.observed_applications.end(),
                [&](const auto& item) { return item.handle.SerializeAsString()==handle.SerializeAsString(); });
            if (found!=actor.observed_applications.end() && !observed_application_current(actor,*found))
                panels->DeleteSubrange(i,1);
        }
        actor.observed_applications.erase(std::remove_if(actor.observed_applications.begin(),actor.observed_applications.end(),
            [&](const auto& item) { return !observed_application_current(actor,item); }),actor.observed_applications.end());
    }
    std::vector<PresentationGeometry> visible;
    for (auto& candidate : candidates) {
        const auto& old=candidate.geometry;
        WorkspacePresentation current;
        try { current=store_.presentation(old.representation_id); }
        catch (const WorldError& error) {
            if (error.code==wire::NOT_FOUND) throw WorldError(wire::STALE_TARGET,"Presentation changed during scene projection");
            throw;
        }
        if (current.room_id!=old.room_id || current.generation!=old.generation ||
            current.pose.SerializeAsString()!=old.pose.SerializeAsString())
            throw WorldError(wire::STALE_TARGET,"Presentation changed during scene projection");
        const std::vector<std::string> scope{old.room_id,old.representation_id};
        if (!granted(actor,"observe",scope) || !granted(actor,"representation.view",scope)) continue;
        visible.push_back(std::move(candidate.geometry));
    }
    append_presentation_geometry(result,projection_room,visible);
    validate_presentation_views(result,projection_room);
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
    for (const auto& metadata : scene_data.presentation_views())
        *result.add_presentation_views()=metadata;
    return result;
}
wire::ActorObservation Actors::observe(Actor& actor, const wire::ActorObserveRequest& request) {
    require(request.has_width() == request.has_height(), "Observation requires both viewport dimensions");
    const unsigned width = request.has_width() ? request.width() : 512;
    const unsigned height = request.has_height() ? request.height() : 512;
    validate_observation_extent(width, height, request.include_pixels());
    if (!renderer_) throw WorldError(wire::UNIMPLEMENTED, "Observation requires the compositor");
    refresh_native();
    const auto rendered_room=actor.entity.room();
    const auto rendered_pose=actor.entity.pose().SerializeAsString();
    std::vector<broker_wire::AuthorizationFence> fences;
    const auto view = scene(actor.token,false,&fences);
    struct RenderedPresentation { std::string id, room, workspace; wire::Pose pose; std::uint64_t generation; bool allowed; };
    std::vector<RenderedPresentation> rendered_presentations;
    for (const auto& metadata : view.presentation_views()) {
        const auto& representation=metadata.representation_id();
        const auto scope=std::vector<std::string>{metadata.room_id(),representation};
        if (!granted(actor,"observe",scope) || !granted(actor,"representation.view",scope))
            throw WorldError(wire::FORBIDDEN,"Presentation observation authority changed");
        const auto record=store_.presentation(representation);
        if (record.room_id!=metadata.room_id() || record.generation!=metadata.generation() ||
            record.pose.SerializeAsString()!=metadata.transform().SerializeAsString())
            throw WorldError(wire::STALE_TARGET,"Presentation changed before observation");
        const auto decision=workspace_check(actor,record.workspace_id,authority::v1::WORKSPACE_ACCESS,{},&fences);
        const bool allowed=decision&&decision->allowed();
        if (metadata.locked()==allowed)
            throw WorldError(wire::UNAVAILABLE,"Workspace authority changed before rendering observation");
        rendered_presentations.push_back({representation,record.room_id,record.workspace_id,record.pose,record.generation,allowed});
    }
    wire::ActorObservation result;
    result.set_actor(actor.entity.id()); result.set_room(actor.entity.room());
    *result.mutable_pose() = view.camera(); result.set_world_revision(view.world_revision());
    result.mutable_viewport()->set_width(width); result.mutable_viewport()->set_height(height);
    const auto visibility = renderer_->observe(view, width, height, request.include_pixels());
    if (!application_observation_valid(actor.token,&fences))
        throw WorldError(wire::FORBIDDEN, "Application observation authority changed");
    for (const auto& before : rendered_presentations) {
        const auto decision=workspace_check(actor,before.workspace,authority::v1::WORKSPACE_ACCESS,{},&fences);
        const bool allowed=decision&&decision->allowed();
        const auto current=store_.presentation(before.id);
        if (current.room_id!=before.room || current.workspace_id!=before.workspace ||
            current.generation!=before.generation || current.pose.SerializeAsString()!=before.pose.SerializeAsString())
            throw WorldError(wire::STALE_TARGET,"Presentation changed during observation");
        if (allowed != before.allowed)
            throw WorldError(wire::UNAVAILABLE,"Workspace authority changed during observation");
    }
    validate_authority_fence(actor,fences);
    if (!observed_applications_current(actor))
        throw WorldError(wire::FORBIDDEN,"Application surface changed during observation");
    if (actor.require_observation_grant && !granted(actor,"observe"))
        throw WorldError(wire::FORBIDDEN,"Application observation authority changed");
    for (const auto& before : rendered_presentations) {
        const std::vector<std::string> scope{before.room,before.id};
        if (!granted(actor,"observe",scope) || !granted(actor,"representation.view",scope))
            throw WorldError(wire::FORBIDDEN,"Presentation observation authority changed");
    }
    if (actor.entity.room()!=rendered_room || actor.entity.pose().SerializeAsString()!=rendered_pose)
        throw WorldError(wire::STALE_TARGET,"Actor moved during observation");
    if (actor.require_observation_grant && !granted(actor,"observe"))
        throw WorldError(wire::FORBIDDEN,"Application observation authority changed");
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
    std::set<std::string> visible_presentation_ids;
    for (const auto& [id, item] : visible) {
        *result.add_visible() = item;
        if (item.kind()==wire::OBJECT && is_reserved_presentation_geometry_id(id))
            visible_presentation_ids.insert(id);
    }
    for (const auto& metadata : view.presentation_views())
        if (visible_presentation_ids.contains(presentation_geometry_id(metadata.representation_id())))
            *result.add_presentation_views()=metadata;
    validate_presentation_views(result);
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
                            actor.aim.Clear(); actor.aim_application.reset(); actor.interaction_reset_pending = true;
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
