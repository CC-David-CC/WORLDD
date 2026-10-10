#include "supervisor/session_launch.hpp"
#include "identity/participant_stream.hpp"
#include "identity/tls_support.hpp"
#include <google/protobuf/text_format.h>
#include <iostream>
#include <poll.h>
#include <fcntl.h>

// Independent lifecycle fault peer. This tests reply and exit validation, not native cleanup.
int main(int argc, char** argv) {
    namespace launch = spatial::supervisor::v1;
    try {
        if (argc == 4) {
            launch::AuthoritySupervisorConfig config;
            if (!google::protobuf::TextFormat::ParseFromString(spatial::identity::tls_detail::load_config(argv[1]), &config))
                throw std::runtime_error("Malformed exit fixture configuration");
            spatial::identity::ParticipantStream channel(std::stoi(argv[3]), getppid());
            const auto send = [&](const launch::AuthorityLaunchRecord& record) {
                launch::AuthorityLaunchReply reply; reply.set_protocol_version(1);
                *reply.mutable_view()->mutable_record() = record;
                channel.send(reply.SerializeAsString());
                while (channel.pending_output()) { channel.flush(); poll(nullptr, 0, 1); }
            };
            launch::AuthorityLaunchRecord record;
            record.set_schema_version(2); record.set_revision(1); record.set_stage(launch::START_ATTEMPTED);
            *record.mutable_binding() = config.binding(); record.mutable_authorization();
            record.mutable_process()->set_launch_id(std::string(32, 'l'));
            record.mutable_native()->set_launch_id(record.process().launch_id());
            record.mutable_native()->set_role(spatial::security::v1::COMPOSITOR);
            send(record);
            const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
            for (;;) {
                if (std::chrono::steady_clock::now() >= deadline) throw std::runtime_error("Exit fixture stop deadline");
                if (auto bytes = channel.receive()) {
                    launch::AuthorityLaunchRequest request;
                    if (!request.ParseFromString(*bytes) || !request.has_stop()) throw std::runtime_error("Expected stop request");
                    break;
                }
                poll(nullptr, 0, 1);
            }
            record.set_revision(2); record.set_stage(launch::OWNED_SCOPES_STOPPED);
            *record.mutable_process_stopped()->mutable_identity() = record.process();
            *record.mutable_native_stopped()->mutable_identity() = record.native();
            send(record); // Unsolicited final receipt, as produced on autonomous completion.
            _exit(std::stoi(config.launch().arguments(0)));
        }
        if (argc != 3) throw std::runtime_error("Expected private directory and fixture exit status");
        launch::AuthoritySupervisorConfig config;
        config.set_protocol_version(1); config.set_compositor_native_target_fd(38);
        config.mutable_binding()->set_supervised(true);
        config.mutable_binding()->set_issuer_id(std::string(32, 'i'));
        config.mutable_binding()->set_participant_id(std::string(32, 'p'));
        config.mutable_binding()->set_registered_revision(1);
        config.mutable_binding()->set_service_incarnation(std::string(32, 's'));
        config.mutable_launch()->add_arguments(argv[2]);
        spatial::identity::tls_detail::Fd native(open("/dev/null", O_RDONLY | O_CLOEXEC));
        spatial::supervisor::SessionLaunch session(std::filesystem::canonical("/proc/self/exe"),
            std::filesystem::path(argv[1]) / "config.textproto", config, native.value,
            std::array<int, 3>{STDIN_FILENO, STDERR_FILENO, STDERR_FILENO}, false);
        for (unsigned attempt = 1; attempt <= 2; ++attempt) {
            launch::AuthorityLaunchReply reply; reply.set_protocol_version(1); reply.set_request_id(attempt);
            try { *reply.mutable_view()->mutable_record() = session.stop(); }
            catch (const std::exception& error) { reply.mutable_error()->set_message(error.what()); }
            spatial::write_frame(std::cout, reply);
        }
        return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 2; }
}
