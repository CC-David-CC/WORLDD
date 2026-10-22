#include "render/renderer.hpp"
#include "render/presentation_server.hpp"
#include <fcntl.h>
#include "platform/compositor_config.hpp"
#include <charconv>
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
        int native_fd = -1;
        pid_t presentation_owner = -1;
        for (int i = 1; i < argc; ++i) {
            const std::string_view argument(argv[i]);
            if (argument == "--state-dir" && state.empty() && i + 1 < argc) state = argv[++i];
            else if (argument == "--display" && display.empty() && i + 1 < argc) display = argv[++i];
            else if (argument == "--native-security-fd" && native_fd == -1 && i + 1 < argc) {
                const std::string_view number(argv[++i]);
                const auto parsed = std::from_chars(number.data(), number.data()+number.size(), native_fd);
                if (parsed.ec != std::errc{} || parsed.ptr != number.data()+number.size() || native_fd <= STDERR_FILENO)
                    throw std::invalid_argument("Native compositor configuration: invalid inherited descriptor");
            }
            else if (argument == "--presentation-owner-pid" && presentation_owner == -1 && i + 1 < argc) {
                const std::string_view number(argv[++i]);
                const auto parsed = std::from_chars(number.data(), number.data()+number.size(), presentation_owner);
                if (parsed.ec != std::errc{} || parsed.ptr != number.data()+number.size() || presentation_owner <= 1)
                    throw std::invalid_argument("Invalid presentation owner identity");
            }
            else throw std::invalid_argument("Unknown or incomplete compositor argument");
        }
        std::unique_ptr<spatial::CompositorConfig> native;
        if (native_fd >= 0) {
            native = std::make_unique<spatial::CompositorConfig>(native_fd);
            if (!display.empty() && display != native->display())
                throw std::invalid_argument("Native compositor configuration: conflicting display");
            display = native->display();
        }
        if (!state.is_absolute() || display.empty()) throw std::invalid_argument("Explicit state and display are required");
        if (presentation_owner > 1 && !native) throw std::invalid_argument("Presentation service requires authenticated graphics");
        umask(0077);
        spatial::Renderer renderer(display, state, native ? &native->value() : nullptr);
        if (native) native->consume();
        if (presentation_owner > 1) {
            const int control = fcntl(STDIN_FILENO, F_DUPFD_CLOEXEC, 3);
            if (control < 3) throw std::runtime_error("Cannot retain presentation control channel");
            spatial::serve_presentations(renderer, control, presentation_owner);
            return 0;
        }
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
                if (!command.ParseFromString(bytes)) throw std::invalid_argument("Invalid render command");
                if (command.protocol_version() == 2) {
                    spatial::render::v1::NativeActorSeatQuery query;
                    if (bytes.size() > 1024 || !query.ParseFromString(bytes)) throw std::invalid_argument("Invalid private seat query");
                    spatial::validate_message(query);
                    if (query.SerializeAsString() != bytes || query.protocol_version() != 2 ||
                        query.request_id() <= sequence || query.actor_entity_id().empty() ||
                        query.actor_entity_id().size() > 256 || query.actor_entity_id().find('\0') != std::string::npos)
                        throw std::invalid_argument("Invalid private seat query identity");
                    sequence=query.request_id();
                    spatial::render::v1::NativeActorSeatReply reply;
                    reply.set_protocol_version(2); reply.set_request_id(sequence);
                    try { *reply.mutable_identity()=renderer.native_actor_seat(query.actor_entity_id()); }
                    catch (const spatial::WorldError& error) { reply.set_error_code(error.code); }
                    catch (const std::exception&) { reply.set_error_code(spatial::wire::UNAVAILABLE); }
                    spatial::write_frame(std::cout,reply);
                    continue;
                }
                if (command.protocol_version() != 1) throw std::invalid_argument("Invalid render command");
                spatial::validate_message(command);
                if (command.request_id() <= sequence || (int(command.has_scene()) + int(command.has_sync()) + int(command.has_input())) != 1)
                    throw std::invalid_argument("Invalid compositor command sequence or operation");
                if (command.observe() && (!command.has_scene() || command.capture()))
                    throw std::invalid_argument("Invalid compositor observation command");
                if (command.include_pixels() && !command.observe())
                    throw std::invalid_argument("Pixel image requires compositor observation");
                sequence=command.request_id();
                spatial::wire::RenderResult reply;
                reply.set_protocol_version(1); reply.set_request_id(command.request_id());
                try {
                    if (command.has_sync()) *reply.mutable_snapshot() = renderer.synchronize();
                    else if (command.has_input()) *reply.mutable_input() = renderer.input(command.input());
                    else if (command.observe()) *reply.mutable_visibility() = renderer.observe(command);
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
