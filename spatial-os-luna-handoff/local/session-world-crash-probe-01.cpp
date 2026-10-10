#include "supervisor/session_coordinator.hpp"
#include "platform/broker.hpp"
#include "identity/tls_support.hpp"
#include <google/protobuf/text_format.h>
#include <iostream>

// Transport-only fixture for the production coordinator and real broker/world children.
int main(int argc, char** argv) {
    try {
        if (argc != 6) throw std::runtime_error("Expected broker and world configs with sealed native descriptors");
        const auto binary = std::filesystem::path(argv[5]);
        const auto load = [](const char* path) {
            spatial::supervisor::v1::AuthoritySupervisorConfig config;
            if (!google::protobuf::TextFormat::ParseFromString(spatial::identity::tls_detail::load_config(path), &config))
                throw std::runtime_error("Malformed session fixture configuration");
            return config;
        };
        spatial::supervisor::SessionCoordinator session(binary / "spatial-authority-supervisor",
            std::string(argv[1]) + ".owned", load(argv[1]), std::stoi(argv[2]));
        const auto report = [](const spatial::supervisor::v1::AuthorityLaunchRecord& record) {
            spatial::supervisor::v1::AuthorityLaunchReply reply; reply.set_protocol_version(1);
            *reply.mutable_view()->mutable_record() = record; spatial::write_frame(std::cout, reply);
        };
        report(session.broker_launch());
        spatial::core::v1::Request hello; hello.set_protocol_version(1); hello.set_request_id(44); hello.mutable_hello();
        spatial::workspace::v1::ControlOwner owner;
        try {
            owner = session.start_world(std::string(argv[3]) + ".owned", load(argv[3]), std::stoi(argv[4]));
        } catch (const std::exception& error) {
            std::cerr << "world start rejected: " << error.what() << '\n';
            spatial::write_frame(std::cout, session.root().call(hello)); report(session.stop()); return 2;
        }
        report(session.world_launch(owner));
        spatial::supervisor::v1::AuthorityLaunchReply binding; binding.set_protocol_version(1);
        *binding.mutable_control_owner() = owner; spatial::write_frame(std::cout, binding);
        spatial::core::v1::Request identities; identities.set_protocol_version(1); identities.set_request_id(45); identities.mutable_runtime_identities();
        spatial::write_frame(std::cout, session.world_call(owner, identities));
        std::string bytes; spatial::workspace::v1::ControlSupervisorRequest request;
        if (!spatial::read_frame(std::cin, bytes) || !request.ParseFromString(bytes) || !request.has_fence())
            throw std::runtime_error("Expected fixture owner inspection");
        spatial::workspace::v1::ControlSupervisorReply response;
        response.set_protocol_version(1); response.set_request_id(request.request_id());
        *response.mutable_state() = session.inspect_world(request.fence());
        spatial::write_frame(std::cout, response);
        spatial::supervisor::v1::AuthorityLaunchReply cleanup; cleanup.set_protocol_version(1);
        try { *cleanup.mutable_view()->mutable_record() = session.stop_world(owner); }
        catch (const std::exception& error) { cleanup.mutable_error()->set_message(error.what()); }
        spatial::write_frame(std::cout, cleanup);
        spatial::write_frame(std::cout, session.root().call(hello));
        return 0; // Disposable fixture teardown follows the independent observations.

    } catch (const std::exception& error) { std::cerr << "session world rejected: " << error.what() << '\n'; return 2; }
}
