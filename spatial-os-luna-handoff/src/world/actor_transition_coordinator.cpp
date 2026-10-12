#include "world/actor_transition_coordinator.hpp"
#include <exception>
#include <stdexcept>

namespace spatial {
namespace pb = identity::v1;
namespace {
std::string exception_text() {
    try { throw; } catch (const std::exception& e) { return e.what(); }
    catch (...) { return "unknown actor transition failure"; }
}
}

ActorTransitionCoordinator::Outcome ActorTransitionCoordinator::transition(
    ActorSessionClient& source, std::string source_world, std::string destination_world, std::string transition_id,
    std::uint64_t departure_sequence, DestinationFactory make_destination,
    std::string destination_room, const core::v1::Pose* destination_pose) {
    if (source_world.empty() || destination_world.empty() || source_world == destination_world ||
        transition_id.size() != 32 || !departure_sequence || !make_destination ||
        source_world != source.expected_world())
        throw std::invalid_argument("invalid actor transition request");
    Outcome out;
    auto& r = out.result;
    r.set_protocol_version(1);
    r.set_transition_id(transition_id);
    r.set_destination_world_id(destination_world);
    r.set_source_world_id(source_world);
    r.set_host_presentation_selection_pending(true);
    try {
        const auto state = source.inspect().state();
        if (state.id() != source.expected_actor_id())
            throw std::runtime_error("source inspection actor identity mismatch");
        r.set_actor_id(state.id());
        r.set_source_inspection_confirmed(true);
        r.set_phase(pb::SOURCE_INSPECTED);
    } catch (const ActorSessionRejected& e) {
        r.set_phase(pb::SOURCE_INSPECTION_REJECTED);
        *r.mutable_server_error() = e.error();
        r.set_detail(e.what());
        return out;
    } catch (...) {
        r.set_phase(pb::SOURCE_INSPECTION_UNCERTAIN);
        r.set_effect_uncertain(true);
        r.set_detail(exception_text());
        return out;
    }
    std::unique_ptr<ActorSessionClient> destination;
    try {
        destination = make_destination();
        if (!destination) throw std::runtime_error("destination factory returned no client");
        if (destination->expected_world() != destination_world ||
            destination->expected_actor_id() != r.actor_id())
            throw std::invalid_argument("destination client identity mismatch");
        const auto prepared = destination->authenticate(destination_room, transition_id, destination_pose);
        const auto& actor = prepared.prepared().actor();
        if (actor.actor_id() != r.actor_id())
            throw std::runtime_error("destination authenticated a different actor");
        if (actor.world_id() != destination_world)
            throw std::runtime_error("destination authenticated an unexpected world");
        r.set_destination_preparation_confirmed(true);
        r.set_phase(pb::DESTINATION_PREPARED);
    } catch (const ActorSessionRejected& e) {
        r.set_phase(pb::PREPARATION_REJECTED);
        r.set_effect_uncertain(true);
        *r.mutable_server_error() = e.error();
        r.set_detail(e.what());
        return out;
    } catch (...) {
        r.set_phase(destination && destination->failed() ? pb::PREPARATION_UNCERTAIN : pb::PREPARATION_REJECTED);
        r.set_effect_uncertain(destination && destination->failed());
        r.set_detail(exception_text());
        return out; // Destruction abandons any prepared session; source is untouched.
    }
    try {
        const auto departed = source.depart(departure_sequence, transition_id).departed();
        if (departed.actor_id() != r.actor_id() || departed.world_id() != r.source_world_id() ||
            departed.transition_id() != transition_id)
            throw std::runtime_error("source departure receipt did not match transition");
        r.set_source_departure_confirmed(true);
        r.set_departure_world_revision(departed.world_revision());
        r.set_phase(pb::SOURCE_DEPARTURE_CONFIRMED);
    } catch (const ActorSessionRejected& e) {
        r.set_phase(pb::DEPARTURE_UNCERTAIN);
        r.set_effect_uncertain(true);
        *r.mutable_server_error() = e.error();
        r.set_detail(e.what());
        return out;
    } catch (...) {
        r.set_phase(pb::DEPARTURE_UNCERTAIN);
        r.set_effect_uncertain(true);
        r.set_detail(exception_text());
        return out;
    }
    try {
        const auto opened = destination->activate(transition_id).opened();
        if (opened.actor_id() != r.actor_id() || opened.world_id() != destination_world)
            throw std::runtime_error("destination activation identity mismatch");
        r.set_destination_activation_confirmed(true);
        r.set_phase(pb::DESTINATION_ACTIVE);
        out.destination = std::move(destination);
    } catch (const ActorSessionRejected& e) {
        r.set_phase(pb::ACTIVATION_UNCERTAIN);
        r.set_effect_uncertain(true);
        *r.mutable_server_error() = e.error();
        r.set_detail(e.what());
    } catch (...) {
        r.set_phase(pb::ACTIVATION_UNCERTAIN);
        r.set_effect_uncertain(true);
        r.set_detail(exception_text());
    }
    return out;
}
}
