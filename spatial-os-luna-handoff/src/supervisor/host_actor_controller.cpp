#include "supervisor/host_actor_controller.hpp"
#include <stdexcept>
#include "core/protocol.hpp"
#include "core/geometry.hpp"

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
        std::move(world), std::move(client)) {}

HostActorController::HostActorController(PresentationInspect inspect, PresentationSelect select,
    std::string world, std::unique_ptr<ActorSessionClient> client)
    : inspect_(std::move(inspect)), select_(std::move(select)),
      current_world_(std::move(world)), current_client_(std::move(client)) {
    require(bool(inspect_) && bool(select_) && !current_world_.empty() && current_client_ &&
            current_client_->expected_world() == current_world_,
            "Initial actor client does not match current world");
}
HostActorController::~HostActorController() = default;

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

    quarantined_client_ = std::move(outcome.destination);
    try {
        const auto selected = select_(destination.owner);
        if (!selected.selected() || selected.stage() != render::v1::READY ||
            selected.binding().world_id() != request.destination_world_id())
            throw std::runtime_error("Foreground selection returned a mismatched world state");
        current_world_ = request.destination_world_id();
        current_client_ = std::move(quarantined_client_);
        result.mutable_actor_transition()->set_host_presentation_selection_pending(false);
        result.set_host_selection(HOST_SELECTION_CONFIRMED);
        result.set_actor_control_available(true);
        control_fenced_ = false;
        return result;
    } catch (...) {
        current_client_.reset(); // Source departure is confirmed; its channel cannot control this actor.
        control_fenced_ = true;
        result.set_host_selection(HOST_SELECTION_UNCERTAIN);
        result.set_actor_control_available(false);
        result.set_detail(exception_detail());
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
    validate_message(request);
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
std::string HostActorController::current_world() const {
    std::lock_guard lock(mutex_);
    return current_world_;
}
}
