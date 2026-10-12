#include "supervisor/session_broker.hpp"
#include "supervisor/control_admin.hpp"
#include "platform/broker.hpp"
#include "identity/participant_stream.hpp"
#include "identity/tls_support.hpp"
#include <google/protobuf/text_format.h>
#include <iostream>
#include <poll.h>

// Thin test boundary. SessionBroker owns and drives the production supervisor and broker.
int main(int argc, char** argv) {
    try {
        if (argc != 4) throw std::runtime_error("Expected broker config, native descriptor, and fixture reply channel");
        const auto binary = std::filesystem::canonical("/proc/self/exe").parent_path();
        const auto path = std::filesystem::path(argv[1]);
        spatial::supervisor::v1::AuthoritySupervisorConfig config;
        if (!google::protobuf::TextFormat::ParseFromString(spatial::identity::tls_detail::load_config(path), &config))
            throw std::runtime_error("Malformed broker fixture config");
        spatial::identity::ParticipantStream output(std::stoi(argv[3]), getppid());
        spatial::supervisor::SessionBroker session(binary / "spatial-authority-supervisor",
            path.string() + ".session", config, std::stoi(argv[2]));
        const auto report = [&](const spatial::supervisor::v1::AuthorityLaunchRecord& record) {
            spatial::supervisor::v1::AuthorityLaunchReply reply; reply.set_protocol_version(1);
            *reply.mutable_view()->mutable_record() = record; output.send(reply.SerializeAsString());
            while (output.pending_output()) { output.flush(); if (output.pending_output()) poll(nullptr, 0, 2); }
        };
        report(session.launch());
        spatial::core::v1::Request hello; hello.set_protocol_version(1); hello.mutable_hello();
        spatial::write_frame(std::cout, session.root().call(hello));
        std::string bytes; spatial::workspace::v1::ControlSupervisorRequest request;
        if (!spatial::read_frame(std::cin, bytes) || !request.ParseFromString(bytes))
            throw std::runtime_error("Expected fixture administrative query");
        spatial::write_frame(std::cout, session.administrative().exchange(request));
        spatial::write_frame(std::cout, session.root().call(hello));
        report(session.stop());
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "session broker rejected: " << error.what() << '\n';
        return 2;
    }
}
