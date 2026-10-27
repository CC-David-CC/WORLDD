#include "render/renderer.hpp"
#include "render/presentation_server.hpp"
#include <fcntl.h>
#include "platform/compositor_config.hpp"
#include <charconv>
#include <chrono>
#include "world/store.hpp"
#include <cerrno>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <poll.h>
#include <stdexcept>
#include <string_view>
#include <thread>
#include <sys/stat.h>
#include <unistd.h>

namespace {
unsigned fixture_unsigned(const char* name, unsigned maximum) {
    const char* raw = std::getenv(name);
    if (!raw || !*raw) return 0;
    unsigned value = 0;
    const std::string_view text(raw);
    const auto parsed = std::from_chars(text.data(), text.data()+text.size(), value);
    if (parsed.ec != std::errc{} || parsed.ptr != text.data()+text.size() || value > maximum)
        throw std::invalid_argument(std::string("Invalid renderer fixture value: ")+name);
    return value;
}
void write_fixture_marker(const char* path) {
    if (!path || !*path || !std::filesystem::path(path).is_absolute())
        throw std::invalid_argument("Renderer fixture marker must use an absolute path");
    const int fd = open(path, O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC, 0600);
    if (fd < 0) throw std::runtime_error("Cannot create renderer fixture delay marker");
    constexpr char marker[] = "renderer input delay entered\n";
    std::size_t offset = 0;
    while (offset < sizeof(marker)-1) {
        const auto count = write(fd, marker+offset, sizeof(marker)-1-offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) { close(fd); throw std::runtime_error("Cannot write renderer fixture delay marker"); }
        offset += static_cast<std::size_t>(count);
    }
    if (close(fd) != 0) throw std::runtime_error("Cannot close renderer fixture delay marker");
}
}
int main(int argc, char** argv) {
    try {
        std::filesystem::path state;
        std::string display;
        int native_fd = -1;
        pid_t presentation_owner = -1;
        bool fixture_faults = false;
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
            else if (argument == "--fixture-faults" && !fixture_faults) fixture_faults = true;
            else throw std::invalid_argument("Unknown or incomplete compositor argument");
        }
        const char* delay_setting = std::getenv("SPATIAL_TEST_RENDER_INPUT_DELAY_MS");
        const char* command_setting = std::getenv("SPATIAL_TEST_RENDER_INPUT_DELAY_COMMAND");
        const char* marker_setting = std::getenv("SPATIAL_TEST_RENDER_INPUT_DELAY_MARKER");
        const char* bad_prepare_setting = std::getenv("SPATIAL_TEST_RENDER_INVALID_PREPARED_SEAT");
        if (!fixture_faults && ((delay_setting && *delay_setting) || (command_setting && *command_setting) ||
                                (marker_setting && *marker_setting) || (bad_prepare_setting && *bad_prepare_setting)))
            throw std::invalid_argument("Renderer fixture delay requires --fixture-faults");
        const unsigned input_delay_ms = fixture_faults ? fixture_unsigned("SPATIAL_TEST_RENDER_INPUT_DELAY_MS", 5000) : 0;
        const unsigned delay_command = fixture_faults ? fixture_unsigned("SPATIAL_TEST_RENDER_INPUT_DELAY_COMMAND", 1024) : 0;
        const bool invalid_prepared_seat = fixture_faults && fixture_unsigned("SPATIAL_TEST_RENDER_INVALID_PREPARED_SEAT", 1);
        if ((input_delay_ms == 0) != (delay_command == 0))
            throw std::invalid_argument("Renderer fixture delay requires both duration and command index");
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
        unsigned input_commands=0;
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
                if (command.has_local_actor_view() || command.prepare_local_frame() || command.has_commit_local_frame() || command.has_clear_local_view())
                    throw std::invalid_argument("Local actor frame operations require the retained presentation service");
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
                    else if (command.has_input()) {
                        ++input_commands;
                        if (input_delay_ms && input_commands == delay_command) {
                            if (marker_setting && *marker_setting) write_fixture_marker(marker_setting);
                            std::this_thread::sleep_for(std::chrono::milliseconds(input_delay_ms));
                        }
                        *reply.mutable_input() = renderer.input(command.input());
                        if (invalid_prepared_seat && command.input().has_prepare())
                            reply.mutable_input()->set_native_seat_id(0);
                    }
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
