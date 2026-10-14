#include "world/actor_transition_coordinator.hpp"
#include "actor_transition_crosshost.pb.h"
#include "core/protocol.hpp"
#include <google/protobuf/io/coded_stream.h>
#include <google/protobuf/io/zero_copy_stream_impl_lite.h>
#include <csignal>
#include <iostream>
#include <memory>
#include <stdexcept>

namespace {
template<class Message> Message read_message() {
    std::string data;
    if (!spatial::read_frame(std::cin, data)) throw std::runtime_error("missing protobuf frame");
    google::protobuf::io::ArrayInputStream array(data.data(), static_cast<int>(data.size()));
    google::protobuf::io::CodedInputStream input(&array);
    input.SetTotalBytesLimit(static_cast<int>(spatial::max_frame_bytes));
    input.SetRecursionLimit(64);
    Message message;
    if (!message.ParseFromCodedStream(&input) || !input.ConsumedEntireMessage())
        throw std::runtime_error("malformed protobuf frame");
    spatial::validate_message(message);
    if (message.protocol_version() != 1) throw std::runtime_error("unsupported protocol version");
    return message;
}
void inspect(spatial::ActorSessionClient& client, const std::string& world,
             spatial::testing::v1::ActorTransitionCrosshostReply& reply) {
    auto* item = reply.add_observations();
    item->set_expected_world_id(world);
    item->set_client_failed(client.failed());
    if (client.failed()) { item->set_inspection_error("client failed; no replay attempted"); return; }
    try { *item->mutable_state() = client.inspect().state(); }
    catch (const std::exception& error) { item->set_inspection_error(error.what()); item->set_client_failed(client.failed()); }
}
void actions(spatial::ActorSessionClient& client,
             const google::protobuf::RepeatedPtrField<spatial::core::v1::ActorActionRequest>& list,
             spatial::testing::v1::ActorTransitionCrosshostReply& reply) {
    for (const auto& action : list) {
        auto response = client.action(action);
        if (!response.has_action() || !response.action().accepted())
            throw std::runtime_error("planned actor action was not accepted");
        *reply.add_action_replies() = response.action();
    }
}
void boundary(spatial::testing::v1::ActorTransitionCrosshostReply& reply, unsigned index) {
    reply.set_boundary_index(index);
    spatial::write_frame(std::cout, reply);
    std::cout.flush();
    const auto ack = read_message<spatial::testing::v1::ActorTransitionCrosshostAck>();
    if (ack.boundary_index() != index) throw std::runtime_error("durable witness boundary mismatch");
}
}

int main() {
    std::signal(SIGPIPE, SIG_IGN);
    spatial::testing::v1::ActorTransitionCrosshostReply reply;
    reply.set_protocol_version(1);
    try {
        const auto request = read_message<spatial::testing::v1::ActorTransitionCrosshostRequest>();
        if (request.source_tls_config_path().empty() || request.source_world_id().empty() ||
            (!request.has_seed_context() && request.steps_size() < 1) || request.steps_size() > 8)
            throw std::runtime_error("invalid cross-host transition request");
        auto active = std::make_unique<spatial::ActorSessionClient>(request.source_tls_config_path());
        if (active->expected_world() != request.source_world_id())
            throw std::runtime_error("source TLS configuration world mismatch");
        if (request.has_seed_context() && (request.steps_size() != 0 ||
                request.initial_actions_size() != 0 || request.seed_context().room().empty() ||
                !request.seed_context().has_pose() || request.seed_context().departure_id().size() != 32))
            throw std::runtime_error("invalid setup seed context");
        const auto opened = request.has_seed_context() ?
            active->authenticate(request.seed_context().room(), {}, &request.seed_context().pose()) :
            active->authenticate();
        if (!opened.has_opened() || opened.opened().world_id() != request.source_world_id() ||
            opened.opened().actor_id() != active->expected_actor_id() ||
            opened.opened().identity().actor_id().size() != 32)
            throw std::runtime_error("source authentication identity mismatch");
        *reply.mutable_initial_opened() = opened.opened();
        if (request.has_seed_context()) {
            inspect(*active, request.source_world_id(), reply);
            if (reply.observations_size() != 1 || !reply.observations(0).has_state() ||
                    reply.observations(0).state().room() != request.seed_context().room() ||
                    reply.observations(0).state().pose().SerializeAsString() !=
                        request.seed_context().pose().SerializeAsString())
                throw std::runtime_error("seed context inspection mismatch");
            const auto departed = active->depart(1, request.seed_context().departure_id());
            if (!departed.has_departed() || departed.departed().world_revision() == 0)
                throw std::runtime_error("seed durable departure was not confirmed");
            *reply.mutable_seeded_departure() = departed.departed();
            reply.set_done(true);
            spatial::write_frame(std::cout, reply);
            return 0;
        }
        actions(*active, request.initial_actions(), reply);
        std::string active_world = request.source_world_id();
        inspect(*active, active_world, reply);
        boundary(reply, 0);
        unsigned boundary_index = 0;
        for (const auto& step : request.steps()) {
            if (step.destination_tls_config_path().empty() || step.destination_world_id().empty() ||
                step.destination_world_id() == active_world || step.transition_id().size() != 32 ||
                step.departure_sequence() == 0)
                throw std::runtime_error("invalid cross-host transition step");
            const auto* pose = step.has_destination_pose() ? &step.destination_pose() : nullptr;
            auto outcome = spatial::ActorTransitionCoordinator::transition(*active, active_world,
                step.destination_world_id(), step.transition_id(), step.departure_sequence(), [&]() {
                    return std::make_unique<spatial::ActorSessionClient>(step.destination_tls_config_path());
                }, step.has_destination_room() ? step.destination_room() : std::string{}, pose);
            *reply.add_results() = outcome.result;
            if (outcome.destination) {
                active = std::move(outcome.destination);
                active_world = step.destination_world_id();
                actions(*active, step.after_actions(), reply);
            } else actions(*active, step.source_after_failure_actions(), reply);
            reply.set_active_world_id(active_world);
            inspect(*active, active_world, reply);
            boundary(reply, ++boundary_index);
        }
        reply.set_done(true);
    } catch (const std::exception& error) {
        reply.set_driver_error(error.what());
        reply.set_done(true);
    }
    try { spatial::write_frame(std::cout, reply); }
    catch (...) { return 2; }
    return reply.driver_error().empty() ? 0 : 2;
}
