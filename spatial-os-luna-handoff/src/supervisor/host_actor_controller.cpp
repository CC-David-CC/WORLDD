#include "supervisor/host_actor_controller.hpp"
#include <stdexcept>
#include "core/protocol.hpp"
#include "core/geometry.hpp"
#include "world/control_action.hpp"
#include <limits>

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
        std::move(world), std::move(client)) { session_ = &session; }

HostActorController::HostActorController(PresentationInspect inspect, PresentationSelect select,
    std::string world, std::unique_ptr<ActorSessionClient> client)
    : inspect_(std::move(inspect)), select_(std::move(select)),
      current_world_(std::move(world)), current_client_(std::move(client)) {
    require(bool(inspect_) && bool(select_) && !current_world_.empty() && current_client_ &&
            current_client_->expected_world() == current_world_,
            "Initial actor client does not match current world");
}
HostActorController::~HostActorController() {
    controls_menu_.reset(); adapters_.reset(); controls_.reset();
    if(session_ && geometry_target_) { try { session_->retire_geometry(*geometry_target_);geometry_target_.reset(); } catch (...) { control_fenced_=true; } }
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
    const identity::v1::HostActorTransitionRequest& request) {
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

    if(geometry_target_) {
        try { session_->retire_geometry(*geometry_target_); geometry_target_.reset(); }
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
            try { refresh_geometry_locked(); }
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
        retire_geometry_locked();
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
bool HostActorController::retire_geometry_locked() noexcept {
    geometry_status_.Clear();
    if (!geometry_target_) return true;
    try {
        session_->retire_geometry(*geometry_target_);
        geometry_target_.reset();
        return true;
    } catch (...) {
        // Retain ownership until the coordinator obtains a checked retirement.
        control_fenced_ = true;
        return false;
    }
}
void HostActorController::clear_foreground_locked() {
    if(!retire_geometry_locked())throw std::runtime_error("Foreground geometry retirement unconfirmed");
    try { session_->clear_presentation(); }
    catch (...) { control_fenced_=true;throw; }
}
void HostActorController::refresh_geometry_locked() {
    const auto view = current_client_->geometry_view();
    if (!geometry_target_) {
        // Record ownership before any selection or publication can fail.
        geometry_target_ = session_->attach_geometry(view);
        session_->select_geometry(*geometry_target_);
    }
    session_->publish_geometry(*geometry_target_, view);
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
    if(control_fenced_ || !current_client_ || current_client_->failed() || !geometry_target_)
        return unavailable;
    return geometry_status_;
}
void HostActorController::start_geometry_presentation() {
    std::lock_guard lock(mutex_);
    require(session_ && current_client_ && !control_fenced_ && !geometry_target_,
            "Geometry presentation requires an unpresented live production actor client");
    try { refresh_geometry_locked(); }
    catch (const ActorSessionRejected&) {
        clear_foreground_locked();
        if (current_client_->failed()) throw;
    } catch (...) {
        retire_geometry_locked();
        throw;
    }
}
void HostActorController::pump_geometry_presentation() {
    std::unique_lock lock(mutex_, std::try_to_lock);
    if (!lock.owns_lock() || transitioning_ || control_fenced_ || !session_ || !current_client_) return;
    try { refresh_geometry_locked(); }
    catch (...) {
        try { clear_foreground_locked(); } catch (...) { control_fenced_=true; }
        if (current_client_->failed()) control_fenced_ = true;
    }
}
std::string HostActorController::current_world() const {
    std::lock_guard lock(mutex_);
    return current_world_;
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
                if (generation == generation_) { control_fenced_ = true; current_client_.reset(); }
            }, initial_sequence);
        adapters_ = std::make_unique<control::DeviceAdapters>(*controls_, profiles);
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
    return controls_menu_->view();
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
    const identity::v1::HostActorTransitionRequest& proposal) {
    std::lock_guard lock(mutex_); require_controls_locked(generation);
    validate_message(proposal);
    require(proposal.departure_sequence() == 0, "Shared transition proposals cannot choose an actor sequence");
    const auto before_generation = generation_;
    const auto source_world = current_world_;
    const auto profiles = adapters_->profiles();
    adapters_->cancel();
    auto request = proposal; request.set_departure_sequence(controls_->reserve_sequence(generation));
    actor_sequence_ = request.departure_sequence();
    auto result = transition_locked(request);
    if (generation_ != before_generation) {
        // Existing transition code advanced the host generation. Old Close
        // callbacks are now stale and cannot close the adopted destination.
        controls_menu_.reset(); adapters_.reset(); controls_.reset();
        if (result.actor_control_available() && !control_fenced_ && current_client_ && !current_client_->failed()) {
            try { create_controls_locked(profiles, current_world_ == source_world ? actor_sequence_ : 0); }
            catch (...) {
                result.set_actor_control_available(false);
                result.set_detail("Actor transition completed with shared control fenced during context handoff");
            }
        }
    }
    return result;
}
}
