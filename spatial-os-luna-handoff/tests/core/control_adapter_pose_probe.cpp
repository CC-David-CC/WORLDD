// This fixture translates actual adapter output. It does not simulate a world.
#include "control/adapters.hpp"
#include "control_adapter_pose.pb.h"
#include "core/protocol.hpp"
#include <iostream>
#include <stdexcept>

int main() {
    try {
        std::string bytes;
        if (!spatial::read_frame(std::cin, bytes)) throw std::runtime_error("Missing adapter fixture request");
        spatial::testing::control::AdapterPoseRequest request;
        if (!request.ParseFromString(bytes)) throw std::runtime_error("Malformed adapter fixture request");
        spatial::validate_message(request);
        if (request.protocol_version() != 1 || !request.has_state() || request.events_size() > 128)
            throw std::runtime_error("Invalid adapter fixture envelope");
        spatial::testing::control::AdapterPoseReply reply;
        reply.set_protocol_version(1); reply.set_request_id(request.request_id());
        spatial::control::ActorControl actor({"fixture-world", request.state().id(), 0},
            [&](const auto& action) {
                *reply.add_actions() = action;
                spatial::identity::v1::WorldSessionReply result; result.set_protocol_version(1);
                result.mutable_action()->set_accepted(true); return result;
            }, [&] {
                spatial::identity::v1::WorldSessionReply result; result.set_protocol_version(1);
                *result.mutable_state() = request.state(); return result;
            }, [] {});
        spatial::control::DeviceAdapters adapters(actor, request.has_profiles() ? request.profiles() : spatial::control::default_profiles(),
            [] { return spatial::control::DeviceAdapters::Clock::time_point{}; });
        for (const auto& event : request.events()) adapters.event(event, spatial::control::DeviceAdapters::Clock::time_point{});
        adapters.tick(spatial::control::DeviceAdapters::Clock::time_point{});
        *reply.mutable_profiles() = adapters.profiles();
        spatial::write_frame(std::cout, reply);
        return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
