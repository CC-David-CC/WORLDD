#include "render/renderer.hpp"
#include "world/store.hpp"
#include <cerrno>
#include <iostream>
#include <poll.h>
#include <stdexcept>
#include <string_view>
#include <sys/stat.h>
#include <unistd.h>
int main(int argc, char** argv) {
    try {
        std::filesystem::path state;
        std::string display;
        for (int i = 1; i < argc; ++i) {
            const std::string_view argument(argv[i]);
            if (argument == "--state-dir" && state.empty() && i + 1 < argc) state = argv[++i];
            else if (argument == "--display" && display.empty() && i + 1 < argc) display = argv[++i];
            else throw std::invalid_argument("Unknown or incomplete compositor argument");
        }
        if (!state.is_absolute() || display.empty()) throw std::invalid_argument("Explicit state and display are required");
        umask(0077);
        spatial::Renderer renderer(display, state);
        spatial::wire::RenderResult ready; ready.set_protocol_version(1); *ready.mutable_ready() = renderer.ready();
        spatial::write_frame(std::cout, ready);
        std::uint64_t sequence=0;
        while (true) {
            renderer.events();
            pollfd fds[2]{{STDIN_FILENO, POLLIN, 0}, {renderer.connection(), POLLIN, 0}};
            const int count = poll(fds, 2, -1);
            if (count < 0 && errno == EINTR) continue;
            if (count < 0) throw std::runtime_error("Compositor poll failed");
            if (fds[0].revents & (POLLIN | POLLHUP | POLLERR)) {
                std::string bytes;
                if (!spatial::read_frame(std::cin, bytes)) return 0;
                spatial::wire::RenderCommand command;
                if (!command.ParseFromString(bytes) || command.protocol_version() != 1) throw std::invalid_argument("Invalid render command");
                spatial::validate_message(command);
                if (command.request_id() <= sequence || (int(command.has_scene()) + int(command.has_sync()) + int(command.has_input())) != 1)
                    throw std::invalid_argument("Invalid compositor command sequence or operation");
                sequence=command.request_id();
                spatial::wire::RenderResult reply;
                reply.set_protocol_version(1); reply.set_request_id(command.request_id());
                try {
                    if (command.has_sync()) *reply.mutable_snapshot() = renderer.synchronize();
                    else if (command.has_input()) *reply.mutable_input() = renderer.input(command.input());
                    else *reply.mutable_frame() = renderer.draw(command);
                } catch (const spatial::WorldError& error) {
                    reply.mutable_error()->set_code(error.code); reply.mutable_error()->set_message(error.what());
                } catch (const std::invalid_argument& error) {
                    reply.mutable_error()->set_code(spatial::wire::INVALID_ARGUMENT);
                    reply.mutable_error()->set_message(error.what());
                }
                spatial::write_frame(std::cout, reply);
            }
        }
    } catch (const std::exception& error) {
        std::cerr << "spatialwm: " << error.what() << '\n'; return 2;
    }
}
