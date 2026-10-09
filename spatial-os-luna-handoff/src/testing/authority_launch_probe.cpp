#include "supervisor/launch.hpp"
#include "core/protocol.hpp"
#include <csignal>
#include <fcntl.h>
#include <iostream>
#include <unistd.h>

int main(int argc, char** argv) {
    namespace pb = spatial::supervisor::v1;
    std::signal(SIGPIPE, SIG_IGN);
    try {
        if (argc != 4 && argc != 6) throw std::runtime_error("Expected journal directory, delegated root, sealed native config, and optional child streams");
        spatial::supervisor::AuthorityLaunch launch(argv[1]);
        const int config = std::stoi(argv[3]);
        const int empty = open("/dev/null", O_RDWR | O_CLOEXEC);
        if (empty < 0) throw std::runtime_error("Cannot open child streams");
        const int child_input = argc == 6 ? std::stoi(argv[4]) : empty;
        const int child_output = argc == 6 ? std::stoi(argv[5]) : empty;
        std::string bytes;
        while (spatial::read_frame(std::cin, bytes)) {
            pb::AuthorityLaunchReply reply; reply.set_protocol_version(1);
            try {
                pb::AuthorityLaunchRequest request;
                if (!request.ParseFromString(bytes)) throw std::runtime_error("Malformed launch request");
                reply.set_request_id(request.request_id()); spatial::validate_message(request);
                if (request.protocol_version() != 1) throw std::runtime_error("Unsupported launch version");
                if (request.has_prepare()) *reply.mutable_view() = launch.prepare(argv[2], request.prepare(), config);
                else if (request.has_start()) *reply.mutable_view()->mutable_record() = launch.start(request.start(), {child_input,child_output,STDERR_FILENO});
                else if (request.has_attach()) { launch.attach(request.attach()); *reply.mutable_view()->mutable_record() = launch.inspect(); }
                else if (request.has_participant_poll()) {
                    if (auto update = launch.poll_participant()) *reply.mutable_participant_update() = *update;
                    else *reply.mutable_view()->mutable_record() = launch.inspect();
                }
                else if (request.has_authorize()) *reply.mutable_view()->mutable_record() = launch.authorize(request.authorize());
                else if (request.has_participant_command()) {
                    launch.submit_participant(request.participant_command()); *reply.mutable_view()->mutable_record() = launch.inspect();
                }
                else if (request.has_prepare_channel()) *reply.mutable_participant_config() = launch.prepare_channel(request.prepare_channel().target_fd());
                else if (request.has_pump_channel()) { launch.pump_channel(); *reply.mutable_view()->mutable_record() = launch.inspect(); }
                else if (request.has_inspect()) *reply.mutable_view()->mutable_record() = launch.inspect();
                else if (request.has_stop()) *reply.mutable_view()->mutable_record() = launch.stop();
                else throw std::runtime_error("Unknown launch operation");
            } catch (const std::exception& error) {
                reply.mutable_error()->set_message(error.what());
            }
            spatial::write_frame(std::cout, reply);
        }
        close(empty);
        return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 2; }
}
