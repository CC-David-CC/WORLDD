#include "supervisor/host_actor_controller.hpp"
#include <stdexcept>
#include "core/protocol.hpp"
#include "core/geometry.hpp"
#include "world/control_action.hpp"
#include <limits>
#include <algorithm>
#include <iostream>

namespace spatial::supervisor {
namespace {
void require(bool value, const char* detail) { if (!value) throw std::invalid_argument(detail); }
std::string exception_detail() {
    try { throw; } catch (const std::exception& error) { return error.what(); }
    catch (...) { return "Unknown host controller exception"; }
}
}

HostActorController::HostActorController(SessionCoordinator& session, std::string world,
    std::unique_ptr<ActorSessionClient> client)
    : HostActorController(
        [&session](const auto& owner) { return session.inspect_presentation(owner); },
        [&session](const auto& owner) { return session.select_world(owner); },
        std::move(world), std::move(client)) {
    session_ = &session;
    present_controls_ = [&session](auto generation,const auto& view) {session.present_controls(generation,view);};
}

HostActorController::HostActorController(PresentationInspect inspect, PresentationSelect select,
    std::string world, std::unique_ptr<ActorSessionClient> client, ControlsPresentation controls)
    : inspect_(std::move(inspect)), select_(std::move(select)), present_controls_(std::move(controls)),
      current_world_(std::move(world)), current_client_(std::move(client)) {
    require(bool(inspect_) && bool(select_) && !current_world_.empty() && current_client_ &&
            current_client_->expected_world() == current_world_,
            "Initial actor client does not match current world");
}
HostActorController::~HostActorController() {
    controls_menu_.reset(); adapters_.reset(); controls_.reset();
    if(session_) (void)retire_foreground_locked();
}

void HostActorController::register_world(std::string world, workspace::v1::ControlOwner owner,
                                         ChannelFactory channel) {
    std::lock_guard lock(mutex_);
    require(!transitioning_ && !control_fenced_, "Cannot change host world registrations now");
    require(!world.empty() && owner.IsInitialized() && bool(channel),
            "Invalid retained actor destination registration");
    validate_message(owner);
    const auto retained = inspect_(owner);
    require(retained.binding().world_id() == world && retained.stage() != render::v1::RETIRED,
            "Retained presentation owner does not map to the registered world");
    require(destinations_.emplace(std::move(world), Destination{std::move(owner), std::move(channel)}).second,
            "Destination world already registered");
}

void HostActorController::register_remote_world(std::string world, ChannelFactory channel) {
    std::lock_guard lock(mutex_);require(!transitioning_ && !control_fenced_,"Cannot change host world registrations now");
    require(!world.empty() && bool(channel),"Invalid remote actor destination registration");
    require(destinations_.emplace(std::move(world),Destination{std::nullopt,std::move(channel)}).second,"Destination world already registered");
}

identity::v1::HostActorTransitionResult HostActorController::transition(
    const identity::v1::HostActorTransitionRequest& request) {
    using namespace identity::v1;
    std::unique_lock lock(mutex_, std::try_to_lock);
    HostActorTransitionResult result;
    result.set_protocol_version(1);
    if (!lock.owns_lock() || transitioning_) {
        result.set_detail("Another host actor transition is in progress");
        return result;
    }
    require(!shared_enabled_, "Use the shared transition proposal API while controls own sequencing");
    legacy_used_ = true;
    return transition_locked(request);
}
identity::v1::HostActorTransitionResult HostActorController::transition_locked(
    const identity::v1::HostActorTransitionRequest& request, const TransitionProgress& progress) {
    using namespace identity::v1;
    HostActorTransitionResult result; result.set_protocol_version(1);
    result.set_host_selection(HOST_SELECTION_NOT_ATTEMPTED);
    result.set_actor_control_available(!control_fenced_ && current_client_ && !current_client_->failed());
    try {
        validate_message(request);
        if (request.protocol_version() != 1) {
            result.mutable_request_error()->set_code(core::v1::UNSUPPORTED_VERSION);
            throw std::invalid_argument("Unsupported host actor transition version");
        }
        require(request.transition_id().size() == 32 && request.departure_sequence() != 0 &&
                !request.destination_world_id().empty() && request.destination_world_id() != current_world_,
                "Invalid host actor transition");
        if (request.has_destination_pose()) {
            const auto& pose = request.destination_pose();
            require(pose.has_position() && pose.has_orientation(), "Incomplete destination pose");
            validate_pose({{pose.position().x(), pose.position().y(), pose.position().z()},
                {pose.orientation().x(), pose.orientation().y(), pose.orientation().z(), pose.orientation().w()}});
        }
    } catch (const std::exception& error) {
        if (!result.has_request_error()) result.mutable_request_error()->set_code(core::v1::INVALID_ARGUMENT);
        result.mutable_request_error()->set_message(error.what());
        result.set_detail(error.what());
        return result;
    }
    if (control_fenced_ || !current_client_ || current_client_->failed()) {
        control_fenced_ = true;
        result.set_detail("Host actor control is fenced");
        return result;
    }
    const auto found = destinations_.find(request.destination_world_id());
    if (found == destinations_.end()) {
        result.set_detail("Destination world is not registered by this host");
        result.mutable_actor_transition()->set_protocol_version(1);
        result.mutable_actor_transition()->set_source_world_id(current_world_);
        result.mutable_actor_transition()->set_destination_world_id(request.destination_world_id());
        result.mutable_actor_transition()->set_transition_id(request.transition_id());
        result.mutable_actor_transition()->set_phase(SOURCE_INSPECTION_REJECTED);
        result.set_host_selection(HOST_SELECTION_NOT_ATTEMPTED);
        result.set_actor_control_available(true);
        return result;
    }
    // Finish all potentially throwing setup before setting the in-flight flag.
    const auto source_world = current_world_;
    auto* source = current_client_.get();
    const auto destination = found->second;
    if (generation_ == UINT64_MAX) {
        result.set_detail("Host transition generation exhausted");
        return result;
    }
    result.set_operation_generation(++generation_);
    transitioning_ = true;
    struct Reset { bool& flag; ~Reset() { flag = false; } } reset{transitioning_};
    // Hold the mutex through all bounded exchanges. No client escapes this lock.
    // Exceptions after effects begin leave control fenced without replay.
    control_fenced_ = true;

    ActorTransitionCoordinator::Outcome outcome;
    try {
        if (progress) progress(TransitionPhase::CaptureRetired);
        outcome = ActorTransitionCoordinator::transition(*source, source_world,
            request.destination_world_id(), request.transition_id(), request.departure_sequence(),
            destination.channel, request.destination_room(), request.has_destination_pose() ? &request.destination_pose() : nullptr);
    } catch (...) {
        result.set_detail(exception_detail());
        result.set_host_selection(HOST_SELECTION_NOT_ATTEMPTED);
        result.set_actor_control_available(false);
        control_fenced_ = true;
        return result;
    }
    *result.mutable_actor_transition() = outcome.result;
    result.set_host_selection(HOST_SELECTION_NOT_ATTEMPTED);

    if (!outcome.result.destination_preparation_confirmed()) {
        result.set_actor_control_available(!source->failed());
        control_fenced_ = source->failed();
        return result;
    }
    if (!outcome.result.source_departure_confirmed() || !outcome.result.destination_activation_confirmed() || !outcome.destination) {
        result.set_actor_control_available(false);
        if (outcome.result.source_departure_confirmed()) current_client_.reset();
        control_fenced_ = true;
        return result;
    }

    if(geometry_target_ || local_target_) {
        try { if (!retire_foreground_locked()) throw std::runtime_error("Foreground retirement remains unconfirmed"); }
        catch (...) { current_client_.reset(); control_fenced_=true; result.set_actor_control_available(false); result.set_host_selection(HOST_SELECTION_UNCERTAIN); result.set_detail(exception_detail()); return result; }
    }
    quarantined_client_ = std::move(outcome.destination);
    try {
        if(destination.owner) {
            // Production retains local input ownership but selects only the
            // authenticated geometry target after its observation check.
            const auto selected = session_ ? inspect_(*destination.owner) : select_(*destination.owner);
            if ((!session_ && !selected.selected()) || selected.stage() != render::v1::READY ||
                selected.binding().world_id() != request.destination_world_id())
                throw std::runtime_error("Foreground selection returned a mismatched world state");
        }
        current_world_ = request.destination_world_id();
        current_client_ = std::move(quarantined_client_);
        if (session_) {
            try { refresh_foreground_locked(); }
            catch (const ActorSessionRejected&) {
                // Observation is separate from navigation and other actor authority.
                clear_foreground_locked();
                if (current_client_->failed()) throw;
                result.mutable_actor_transition()->set_host_presentation_selection_pending(true);
                result.set_host_selection(HOST_SELECTION_NOT_ATTEMPTED);
                result.set_actor_control_available(true);
                control_fenced_ = false;
                return result;
            }
        }
        result.mutable_actor_transition()->set_host_presentation_selection_pending(false);
        result.set_host_selection(HOST_SELECTION_CONFIRMED);
        result.set_actor_control_available(true);
        control_fenced_ = false;
        return result;
    } catch (...) {
        const auto detail = exception_detail();
        retire_foreground_locked();
        current_client_.reset(); // Source departure is confirmed; its channel cannot control this actor.
        control_fenced_ = true;
        result.set_host_selection(HOST_SELECTION_UNCERTAIN);
        result.set_actor_control_available(false);
        result.set_detail(detail);
        return result;
    }
}

identity::v1::WorldSessionReply HostActorController::inspect(std::uint64_t generation) {
    std::unique_lock lock(mutex_, std::try_to_lock);
    require(lock.owns_lock(), "Host actor operation already in progress");
    require(!control_fenced_ && !transitioning_ && current_client_ && generation == generation_,
            "Unavailable or stale host actor context");
    return current_client_->inspect();
}
identity::v1::WorldSessionReply HostActorController::action(std::uint64_t generation,
    const core::v1::ActorActionRequest& request) {
    std::unique_lock lock(mutex_, std::try_to_lock);
    require(lock.owns_lock(), "Host actor operation already in progress");
    require(!control_fenced_ && !transitioning_ && current_client_ && generation == generation_,
            "Unavailable or stale host actor context");
    require(!shared_enabled_, "Use shared actor proposals while controls own sequencing");
    validate_message(request);
    legacy_used_ = true;
    return current_client_->action(request);
}
std::uint64_t HostActorController::generation() const {
    std::lock_guard lock(mutex_);
    return generation_;
}
bool HostActorController::control_available() const {
    std::lock_guard lock(mutex_);
    return !control_fenced_ && !transitioning_ && current_client_ && !current_client_->failed();
}
bool HostActorController::retire_foreground_locked() noexcept {
    geometry_status_.Clear();
    if (!geometry_target_ && !local_target_) return true;
    try {
        if(local_frame_) {
            session_->cancel_present_local_actor(local_frame_->ticket);
            local_frame_.reset();
        }
        if (local_target_) {
            session_->retire_local_actor(local_target_->owner,local_target_->identity);
            local_target_.reset();
        }
        if (geometry_target_) {
            session_->retire_geometry(*geometry_target_);
            geometry_target_.reset();
        }
        return true;
    } catch (...) {
        // Retain ownership until the coordinator obtains a checked retirement.
        control_fenced_ = true;
        return false;
    }
}
void HostActorController::defer_foreground_locked(bool release_binding) {
    geometry_status_.Clear();
    if(presentation_continuations_ && local_target_) {
        // The coordinator retains the frame, exchange, target, and cleanup proof.
        // Cancel the exact view before returning to the host reply publisher.
        session_->defer_retire_local_actor(local_target_->owner,local_target_->identity,
            release_binding?SessionCoordinator::LocalRetirementKind::context:SessionCoordinator::LocalRetirementKind::presentation);
        if(std::none_of(retiring_targets_.begin(),retiring_targets_.end(),[&](const auto& old){
                return old.owner.SerializeAsString()==local_target_->owner.SerializeAsString() &&
                    old.identity.SerializeAsString()==local_target_->identity.SerializeAsString();}))
            retiring_targets_.push_back(*local_target_);
        local_frame_.reset();
        if(release_binding)local_target_.reset();
        return;
    }
    if(!retiring_targets_.empty()) {
        if(release_binding)for(const auto& target:retiring_targets_)
            session_->defer_retire_local_actor(target.owner,target.identity);
        return;
    }
    clear_foreground_locked();
}
bool HostActorController::pump_deferred_cleanup() {
    std::unique_lock lock(mutex_,std::try_to_lock);
    if(!lock.owns_lock() || !session_)return false;
    const bool pending=session_->pump_deferred_cleanup();
    std::erase_if(retiring_targets_,[&](const auto& target){
        return !session_->local_retirement_pending(target.owner,target.identity);
    });
    return pending;
}
void HostActorController::clear_foreground_locked() {
    if(!retire_foreground_locked())throw std::runtime_error("Foreground geometry retirement unconfirmed");
    try { session_->clear_presentation(); }
    catch (...) { control_fenced_=true;throw; }
}
void HostActorController::publish_local_frame_locked(const core::v1::ActorLocalViewIdentity& identity,
    const core::v1::ActorLocalViewState& view) {
    presentation_failure_.clear();
    if(adapters_ && controls_ && controls_->context().generation == generation_)
        adapters_->set_viewport(view.viewport().width(),view.viewport().height(),view.vertical_fov());
    geometry_status_.set_protocol_version(1);geometry_status_.set_available(true);
    geometry_status_.set_world_id(identity.world_id());geometry_status_.set_actor_id(identity.actor_id());
    geometry_status_.set_room(view.room());*geometry_status_.mutable_camera()=view.camera();
    geometry_status_.set_view_sequence(view.frame_id());geometry_status_.set_world_revision(view.world_revision());
    geometry_status_.set_presentation_generation(view.presentation_generation());
    geometry_status_.set_authority_generation(identity.authority_generation());
    geometry_status_.set_authority_service_incarnation(identity.authority_service_incarnation());
}
void HostActorController::refresh_foreground_locked() {
    const auto destination=destinations_.find(current_world_);
    if (shared_enabled_ && destination!=destinations_.end() && destination->second.owner) {
        const auto identity=current_client_->local_view_identity();
        if (!local_target_ || local_target_->identity.SerializeAsString()!=identity.SerializeAsString()) {
            if (!retire_foreground_locked()) throw std::runtime_error("Previous foreground retirement is unconfirmed");
            local_target_=LocalTarget{*destination->second.owner,identity};
        }
        const auto view=session_->present_local_actor(local_target_->owner,identity,0,0);
        publish_local_frame_locked(identity,view);
        return;
    }
    const auto view = current_client_->geometry_view();
    if (!geometry_target_) {
        // Record ownership before any selection or publication can fail.
        geometry_target_ = session_->attach_geometry(view);
        session_->select_geometry(*geometry_target_);
    }
    session_->publish_geometry(*geometry_target_, view);
    if(adapters_ && controls_ && controls_->context().generation == generation_){
        const auto viewport=session_->native_viewport();
        adapters_->set_viewport(viewport.width(),viewport.height(),view.config().vertical_fov());
    }
    // Store only a checked drawing receipt after the compositor acknowledges it.
    geometry_status_.set_protocol_version(1);geometry_status_.set_available(true);
    geometry_status_.set_world_id(view.world_id());geometry_status_.set_actor_id(view.actor_id());
    geometry_status_.set_room(view.room());*geometry_status_.mutable_camera()=view.camera();
    geometry_status_.set_view_sequence(view.sequence());geometry_status_.set_world_revision(view.world_revision());
    geometry_status_.set_presentation_generation(geometry_target_->presentation_generation());
    geometry_status_.set_authority_generation(view.authority_generation());
    geometry_status_.set_authority_service_incarnation(view.authority_service_incarnation());
}
identity::v1::ActorGeometryView HostActorController::geometry_view(std::uint64_t generation) {
    std::unique_lock lock(mutex_, std::try_to_lock);
    require(lock.owns_lock(), "Host actor operation already in progress");
    require(!control_fenced_ && !transitioning_ && current_client_ && generation == generation_,
            "Unavailable or stale host actor context");
    return current_client_->geometry_view();
}
identity::v1::HostGeometryStatus HostActorController::geometry_status(std::uint64_t generation) const {
    std::unique_lock lock(mutex_,std::try_to_lock);
    require(lock.owns_lock(),"Host actor operation already in progress");
    require(!transitioning_ && generation==generation_,"Unavailable or stale host actor context");
    identity::v1::HostGeometryStatus unavailable;unavailable.set_protocol_version(1);
    if(control_fenced_ || !current_client_ || current_client_->failed() || (!geometry_target_ && !local_target_))
        return unavailable;
    return geometry_status_;
}
void HostActorController::start_geometry_presentation() {
    std::lock_guard lock(mutex_);
    require(session_ && current_client_ && !control_fenced_ && !geometry_target_ && !local_target_,
            "Geometry presentation requires an unpresented live production actor client");
    try { refresh_foreground_locked(); }
    catch (const ActorSessionRejected&) {
        clear_foreground_locked();
        if (current_client_->failed()) throw;
    } catch (...) {
        retire_foreground_locked();
        throw;
    }
}
void HostActorController::enable_presentation_continuations() {
    std::lock_guard lock(mutex_);
    require(session_ && shared_enabled_ && !local_frame_ && !local_target_ && !geometry_target_,
        "Presentation continuations require an unpresented shared actor");
    presentation_continuations_=true;
}
bool HostActorController::pump_geometry_presentation(const std::function<void(const char*)>& trace) {
    if (trace) trace("geometry_deferred_cleanup");
    (void)pump_deferred_cleanup();
    if (trace) trace("geometry_lock");
    std::unique_lock lock(mutex_, std::try_to_lock);
    if (!lock.owns_lock() || transitioning_ || control_fenced_ || !session_ || !current_client_) return false;
    try {
        const auto destination=destinations_.find(current_world_);
        if(presentation_continuations_ && shared_enabled_ && destination!=destinations_.end() && destination->second.owner) {
            if (trace) trace("geometry_view_identity");
            const auto identity=current_client_->local_view_identity();
            if(std::any_of(retiring_targets_.begin(),retiring_targets_.end(),[&](const auto& target){
                    return target.identity.SerializeAsString()==identity.SerializeAsString();}))return true;
            if(!local_target_ || local_target_->identity.SerializeAsString()!=identity.SerializeAsString()) {
                if (trace) trace("geometry_retire_foreground");
                if(!retire_foreground_locked())throw std::runtime_error("Previous foreground retirement is unconfirmed");
                local_target_=LocalTarget{*destination->second.owner,identity};
            }
            if(local_frame_ && (local_frame_->actor_generation!=generation_ ||
                    local_frame_->capture_generation!=capture_generation_ ||
                    local_frame_->ticket.identity.SerializeAsString()!=identity.SerializeAsString() ||
                    local_frame_->ticket.owner.SerializeAsString()!=local_target_->owner.SerializeAsString())) {
                if (trace) trace("geometry_cancel_frame");
                session_->cancel_present_local_actor(local_frame_->ticket);
                local_frame_.reset();
            }
            if (trace) trace("geometry_begin_frame");
            if(!local_frame_)local_frame_=LocalFrame{
                session_->begin_present_local_actor(local_target_->owner,identity,0,0),generation_,capture_generation_};
            const auto pending=*local_frame_;
            if (trace) trace("geometry_poll_frame");
            auto result=session_->poll_present_local_actor(pending.ticket);
            if(!result)return true;
            local_frame_.reset();
            // No completion may update projection or status after its exact
            // actor, capture, view, owner, or producer has changed.
            require(!transitioning_ && !control_fenced_ && current_client_ && !current_client_->failed() &&
                pending.actor_generation==generation_ && pending.capture_generation==capture_generation_ && local_target_ &&
                pending.ticket.identity.SerializeAsString()==current_client_->local_view_identity().SerializeAsString() &&
                pending.ticket.owner.SerializeAsString()==local_target_->owner.SerializeAsString() &&
                pending.ticket.presentation_generation==result->presentation_generation(),
                "Local presentation completion lost its actor context");
            if (trace) trace("geometry_publish_frame");
            publish_local_frame_locked(identity,*result);
        }else {
            if (trace) trace("geometry_refresh_foreground");
            refresh_foreground_locked();
        }
    }
    catch (...) {
        if (trace) trace("geometry_failure_cleanup");
        const auto detail=exception_detail();
        if(detail!=presentation_failure_)std::cerr<<"Local presentation failed: "<<detail<<'\n';
        presentation_failure_=detail;
        const bool context_lost=!current_client_ || current_client_->failed();
        try { defer_foreground_locked(context_lost); } catch (...) { control_fenced_=true; }
        if(context_lost)control_fenced_=true;
    }
    return false;
}
std::string HostActorController::current_world() const {
    std::lock_guard lock(mutex_);
    return current_world_;
}
void HostActorController::synchronize_controls_presentation_locked() {
    if(!present_controls_ || !shared_enabled_ || !capture_generation_)return;
    control::v1::ControlMenuView view;view.set_protocol_version(1);
    if(!control_fenced_ && current_client_ && !current_client_->failed() && controls_menu_)view=controls_menu_->view();
    if(session_ && (presentation_continuations_ || control_fenced_ || !current_client_ || current_client_->failed())) {
        // Menu output is a presentation continuation. It cannot hold the actor worker.
        session_->defer_present_controls(capture_generation_,view);return;
    }
    const auto bytes=view.SerializeAsString();
    if(presented_menu_generation_==capture_generation_ && presented_menu_==bytes)return;
    try { present_controls_(capture_generation_,view); }
    catch (...) {
        control_fenced_=true;
        current_client_.reset(); // Do not retain held input after unconfirmed host output.
        throw;
    }
    presented_menu_=bytes;presented_menu_generation_=capture_generation_;
}
void HostActorController::synchronize_controls_presentation() {
    std::lock_guard lock(mutex_);synchronize_controls_presentation_locked();
}
void HostActorController::require_controls_locked(std::uint64_t generation) const {
    require(shared_enabled_ && controls_ && adapters_ && controls_menu_ && generation == generation_ &&
            !transitioning_ && !control_fenced_ && current_client_ && !current_client_->failed(),
            "Unavailable or stale shared actor context");
}
void HostActorController::create_controls_locked(const control::v1::ControlProfiles& profiles,
                                                 std::uint64_t initial_sequence) {
    require(current_client_ && !current_client_->failed() && !controls_ && !adapters_ && !controls_menu_ &&
            capture_generation_ < std::numeric_limits<std::uint64_t>::max(), "Cannot create shared actor context");
    const auto generation = generation_;
    try {
        // Revocation is independent of the host/actor operation locks. An old
        // watchdog cannot interrupt a retained source after this handoff.
        (void)current_client_->rotate_interrupt_handle();
        controls_ = std::make_unique<control::ActorControl>(
            control::ActorContext{current_world_, current_client_->expected_actor_id(), generation},
            [this, generation](const auto& request) {
                require(generation == generation_ && current_client_ && !control_fenced_, "Retired actor proposal callback");
                actor_sequence_ = request.seq();
                if (!actor_input::reversible(request)) return current_client_->action(request);
                identity::v1::WorldSessionReply reply; reply.set_protocol_version(1);
                *reply.mutable_action() = current_client_->controlled_action(request);
                return reply;
            }, [this, generation] {
                require(generation == generation_ && current_client_ && !control_fenced_, "Retired actor inspection callback");
                return current_client_->inspect();
            }, [this, generation] {
                if (generation == generation_) {
                    control_fenced_ = true; current_client_.reset();
                    if (session_) defer_foreground_locked();
                }
            }, initial_sequence);
        adapters_ = std::make_unique<control::DeviceAdapters>(*controls_, profiles);
        std::weak_ptr<control::MotionIntent> motion = adapters_->motion_intent();
        current_client_->set_motion_refresh([motion] {
            const auto intent = motion.lock();
            return intent ? intent->next(control::DeviceAdapters::Clock::now()) :
                std::optional<core::v1::ActorInputRequest>{};
        });
        controls_menu_ = std::make_unique<control::ControlsMenu>(*adapters_, persist_controls_);
        actor_sequence_ = initial_sequence; ++capture_generation_; capture_sequence_ = 0;
    } catch (...) {
        controls_menu_.reset(); adapters_.reset(); controls_.reset();
        control_fenced_ = true; current_client_.reset();
        throw;
    }
}
void HostActorController::start_controls(const control::v1::ControlProfiles& profiles,
                                         control::ControlsMenu::Persist persist) {
    std::lock_guard lock(mutex_);
    require(!shared_enabled_ && !legacy_used_ && !transitioning_ && !control_fenced_ && current_client_ &&
            !current_client_->failed() && bool(persist), "Shared control requires a fresh authenticated host context");
    control::validate_profiles(profiles);
    persist_controls_ = std::move(persist);
    create_controls_locked(profiles, 0);
    shared_enabled_ = true;
}
core::v1::ActorActionReply HostActorController::propose(std::uint64_t generation,
    const core::v1::ActorActionRequest& request) {
    std::lock_guard lock(mutex_); require_controls_locked(generation);
    validate_message(request);
    require(request.token().empty() && !request.has_seq(), "Shared proposals cannot choose actor tokens or sequences");
    if (request.has_stop()) {
        adapters_->cancel();
        core::v1::ActorActionReply reply; reply.set_accepted(true); return reply;
    }
    try { return controls_->propose(generation, request); }
    catch (const ActorInputCancelled&) { adapters_->acknowledged_cancellation(); throw; }
}
control::v1::ControlStatus HostActorController::control_status_locked() const {
    require(shared_enabled_ && adapters_, "Shared actor controls are not configured");
    auto result = adapters_->status(); result.set_capture_generation(capture_generation_);
    result.set_available(!control_fenced_ && !transitioning_ && current_client_ &&
                         !current_client_->failed() && result.available());
    return result;
}
control::v1::ControlStatus HostActorController::control_status(std::uint64_t generation) const {
    std::lock_guard lock(mutex_);
    require(generation == generation_, "Stale shared actor status context");
    return control_status_locked();
}
control::v1::ControlMenuView HostActorController::controls_menu(std::uint64_t generation) const {
    std::lock_guard lock(mutex_);
    require(shared_enabled_ && controls_menu_ && generation == generation_, "Stale or missing controls menu");
    if (control_fenced_ || !current_client_ || current_client_->failed()) {
        control::v1::ControlMenuView hidden; hidden.set_protocol_version(1); return hidden;
    }
    return controls_menu_->view();
}
std::shared_ptr<control::MotionIntent> HostActorController::motion_intent(std::uint64_t generation) const {
    std::lock_guard lock(mutex_);
    require_controls_locked(generation);
    return adapters_->motion_intent();
}
control::v1::ControlStatus HostActorController::device_event(std::uint64_t generation,
    const control::v1::CapturedDeviceEvent& packet) {
    std::lock_guard lock(mutex_); require_controls_locked(generation);
    validate_message(packet);
    require(packet.protocol_version() == 1 && packet.capture_generation() == capture_generation_ &&
            packet.packet_sequence() > capture_sequence_ && packet.has_event() && packet.monotonic_nanoseconds() > 0 &&
            packet.monotonic_nanoseconds() <= static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max()),
            "Stale or mismatched host capture envelope");
    capture_sequence_ = packet.packet_sequence();
    const auto captured = control::DeviceAdapters::Clock::time_point(std::chrono::duration_cast<control::DeviceAdapters::Clock::duration>(
        std::chrono::nanoseconds(static_cast<std::int64_t>(packet.monotonic_nanoseconds()))));
    const auto now = control::DeviceAdapters::Clock::now();
    const bool open = adapters_->status().menu_open();
    try {
        const bool fresh = adapters_->event(packet.event(), captured, now);
        controls_menu_->event(packet.event(), captured, control::DeviceAdapters::Clock::now(), open, fresh);
    } catch (const ActorInputCancelled&) {
        // The world confirmed cancellation. The adapter already cleared its held
        // input and established a fresh-gesture barrier. Never replay the packet.
        controls_menu_->event(packet.event(), captured, control::DeviceAdapters::Clock::now(), open, false);
    }
    return control_status_locked();
}
control::v1::ControlStatus HostActorController::tick_controls(std::uint64_t generation) {
    std::lock_guard lock(mutex_); require_controls_locked(generation);
    try { adapters_->tick(control::DeviceAdapters::Clock::now()); }
    catch (const ActorInputCancelled&) {}
    return control_status_locked();
}
std::weak_ptr<const ActorSessionInterrupt> HostActorController::interrupt_handle(std::uint64_t generation) {
    std::lock_guard lock(mutex_); require_controls_locked(generation);
    return current_client_->interrupt_handle();
}
identity::v1::HostActorTransitionResult HostActorController::transition_proposed(std::uint64_t generation,
    const identity::v1::HostActorTransitionRequest& proposal, TransitionProgress progress) {
    std::lock_guard lock(mutex_); require_controls_locked(generation);
    validate_message(proposal);
    require(proposal.departure_sequence() == 0, "Shared transition proposals cannot choose an actor sequence");
    const auto before_generation = generation_;
    const auto source_world = current_world_;
    const auto profiles = adapters_->profiles();
    adapters_->cancel();
    if (progress) progress(TransitionPhase::InputReleased);
    // A denied transition can retain this actor and view. Retire its frame
    // operation before changing host generations or restoring synchronous output.
    if(local_frame_) {
        session_->cancel_present_local_actor(local_frame_->ticket);
        local_frame_.reset();
    }
    adapters_->set_menu(false);
    synchronize_controls_presentation_locked();
    auto request = proposal; request.set_departure_sequence(controls_->reserve_sequence(generation));
    actor_sequence_ = request.departure_sequence();
    auto result = transition_locked(request, progress);
    if (generation_ != before_generation) {
        // Existing transition code advanced the host generation. Old Close
        // callbacks are now stale and cannot close the adopted destination.
        controls_menu_.reset(); adapters_.reset(); controls_.reset();
        if (result.actor_control_available() && !control_fenced_ && current_client_ && !current_client_->failed()) {
            try {
                create_controls_locked(profiles, current_world_ == source_world ? actor_sequence_ : 0);
                // The adopted adapter owns sequencing now. Bind its projection
                // before the worker can publish this context's capture binding.
                if (session_) {
                    try { refresh_foreground_locked(); }
                    catch (const ActorSessionRejected&) {
                        clear_foreground_locked();
                        if (!current_client_ || current_client_->failed()) throw;
                        result.mutable_actor_transition()->set_host_presentation_selection_pending(true);
                        result.set_host_selection(identity::v1::HOST_SELECTION_NOT_ATTEMPTED);
                    }
                }
            }
            catch (...) {
                if (session_) (void)retire_foreground_locked();
                control_fenced_ = true;
                current_client_.reset();
                result.set_actor_control_available(false);
                result.set_host_selection(identity::v1::HOST_SELECTION_UNCERTAIN);
                result.set_detail("Actor transition completed with shared control fenced during context handoff");
            }
        }
    }
    return result;
}
}
