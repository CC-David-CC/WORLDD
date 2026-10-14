#include "world/actor_transition_coordinator.hpp"
#include "actor_transition_integration.pb.h"
#include "core/protocol.hpp"
#include <google/protobuf/io/coded_stream.h>
#include <google/protobuf/io/zero_copy_stream_impl_lite.h>
#include <csignal>
#include <iostream>
#include <memory>
#include <set>
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
    if (message.protocol_version() != 1) throw std::runtime_error("invalid protocol version");
    return message;
}
void barrier(const spatial::testing::v1::ActorTransitionIntegrationReply& reply) {
    spatial::write_frame(std::cout, reply);
    read_message<spatial::testing::v1::ActorTransitionIntegrationContinue>();
}
void observe(spatial::ActorSessionClient& client, const std::string& world,
             spatial::testing::v1::ActorTransitionIntegrationReply& reply) {
    auto* observation = reply.add_observations();
    observation->set_world_id(world);
    observation->set_client_failed(client.failed());
    if (client.failed()) {
        observation->set_inspection_error("client failed; no request replayed");
        return;
    }
    try {
        const auto state = client.inspect();
        *observation->mutable_state() = state.state();
        *reply.mutable_active_state() = state.state();
    } catch (const std::exception& e) {
        observation->set_inspection_error(e.what());
        observation->set_client_failed(client.failed());
    }
}
void actions(spatial::ActorSessionClient& client,
             const google::protobuf::RepeatedPtrField<spatial::core::v1::ActorActionRequest>& requests) {
    for (const auto& action : requests) client.action(action);
}
}
int main() {
    std::signal(SIGPIPE, SIG_IGN);
    spatial::testing::v1::ActorTransitionIntegrationReply reply;
    reply.set_protocol_version(1);
    try {
        const auto request = read_message<spatial::testing::v1::ActorTransitionIntegrationRequest>();
        if (request.steps().empty() || request.steps_size() > 16 || request.actor_identity().size() != 32)
            throw std::runtime_error("invalid integration request");
        std::set<int> descriptors{request.source_fd()};
        if (request.source_fd() < 3) throw std::runtime_error("invalid actor descriptor");
        for (const auto& step : request.steps()) {
            if (step.destination_fd() < 3 || !descriptors.insert(step.destination_fd()).second ||
                step.transition_id().size() != 32 || !step.departure_sequence() || step.destination_world_id().empty())
                throw std::runtime_error("invalid or reused destination channel");
        }
        auto active = std::make_unique<spatial::ActorSessionClient>(request.source_fd(), request.credential(),
            request.signing_key_path(), request.source_world_id());
        const auto opened = active->authenticate(request.has_source_room() ? request.source_room() : std::string{}, {},
            request.has_source_pose() ? &request.source_pose() : nullptr);
        if (!opened.has_opened() || opened.opened().actor_id() != active->expected_actor_id() ||
            opened.opened().identity().actor_id() != request.actor_identity() ||
            opened.opened().world_id() != request.source_world_id())
            throw std::runtime_error("source authentication identity mismatch");
        *reply.mutable_source_opened() = opened.opened();
        std::string source_world = request.source_world_id();
        actions(*active, request.initial_actions());
        reply.set_active_world_id(source_world);
        observe(*active, source_world, reply);
        barrier(reply);
        for (const auto& step : request.steps()) {
            const auto* pose = step.has_destination_pose() ? &step.destination_pose() : nullptr;
            auto outcome = spatial::ActorTransitionCoordinator::transition(*active, source_world,
                step.destination_world_id(), step.transition_id(), step.departure_sequence(), [&]() {
                    return std::make_unique<spatial::ActorSessionClient>(step.destination_fd(), request.credential(),
                        request.signing_key_path(), step.destination_world_id());
                }, step.has_destination_room() ? step.destination_room() : std::string{}, pose);
            *reply.add_results() = outcome.result;
            if (outcome.destination) {
                active = std::move(outcome.destination);
                source_world = step.destination_world_id();
                actions(*active, step.after_actions());
            }
            reply.set_active_world_id(source_world);
            reply.clear_active_state();
            observe(*active, source_world, reply);
            if (&step != &request.steps(request.steps_size() - 1)) barrier(reply);
        }
        reply.set_done(true);
    } catch (const std::exception& error) {
        reply.set_driver_error(error.what());
        reply.set_done(true);
    }
    try {
        spatial::write_frame(std::cout, reply);
        // The harness closes input after the terminal reply. Extra frames fail closed.
        if (std::cin.peek() != std::char_traits<char>::eof()) return 2;
    } catch (...) { return 2; }
    return reply.driver_error().empty() ? 0 : 2;
}
