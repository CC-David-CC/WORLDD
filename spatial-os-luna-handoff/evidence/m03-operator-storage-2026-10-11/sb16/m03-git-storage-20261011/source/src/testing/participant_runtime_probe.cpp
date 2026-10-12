#include "identity/participant_runtime.hpp"
#include "identity/tls_support.hpp"
#include "core/protocol.hpp"
#include <cerrno>
#include <chrono>
#include <csignal>
#include <iostream>
#include <poll.h>
#include <sstream>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

// Thin transport probe: all service state and transitions belong to ParticipantRuntime.
int main(int argc, char** argv) {
    using spatial::identity::tls_detail::require;
    namespace id = spatial::identity::v1;
    namespace authority = spatial::authority::v1;
    using Clock = std::chrono::steady_clock;
    umask(0077); signal(SIGPIPE, SIG_IGN);
    try {
        const bool overflow = argc > 1 && std::string_view(argv[1]) == "--queue-overflow";
        const bool check_exec = argc > 1 && std::string_view(argv[1]) == "--check-channel-exec";
        const int config_index = check_exec ? 3 : overflow ? 2 : 1;
        require(argc >= config_index+3 && (argc-config_index-1) % 2 == 0,
                "Expected CONFIG and one or more world|workspace ID pairs");
        std::vector<authority::Domain> domains;
        for (int i = config_index+1; i < argc; i += 2) {
            authority::Domain domain; const std::string_view kind(argv[i]);
            require(kind == "world" || kind == "workspace", "Invalid authority domain kind");
            domain.set_kind(kind == "world" ? authority::WORLD : authority::WORKSPACE); domain.set_id(argv[i+1]);
            domains.push_back(std::move(domain));
        }
        spatial::identity::tls_detail::nonblocking(STDIN_FILENO);
        spatial::identity::tls_detail::nonblocking(STDOUT_FILENO);
        spatial::identity::ParticipantRuntime runtime(argv[config_index], domains);
        if (check_exec) {
            char executable[] = "/usr/bin/python3";
            char option[] = "-c";
            char code[] = "import os,sys\ntry: os.fstat(int(sys.argv[1]))\nexcept OSError: sys.exit(0)\nelse: sys.exit(44)";
            char* command[]{executable, option, code, argv[2], nullptr};
            char* environment[]{nullptr};
            const pid_t child = fork(); require(child >= 0, "Cannot fork descriptor witness");
            if (!child) { execve(executable, command, environment); _exit(127); }
            int status = 0; pid_t waited;
            do { waited = waitpid(child, &status, 0); } while (waited < 0 && errno == EINTR);
            require(waited == child && WIFEXITED(status) && WEXITSTATUS(status) == 0,
                "Participant channel survived a later exec");
        }
        if (overflow) {
            // The independent TLS witness stalls the handshake. Exercise the real bounded queue.
            for (std::uint64_t i = 0; i < 129; ++i) {
                id::ParticipantCommand command; command.set_request_id(i);
                command.mutable_activate()->set_synchronized_revision(0); runtime.submit(std::move(command));
            }
            const auto update = runtime.poll();
            require(runtime.failed() && update && update->has_error() &&
                    update->error().code() == spatial::wire::RESOURCE_EXHAUSTED && !runtime.poll(),
                    "Command overflow did not discard stale work and permanently fail");
            spatial::write_frame(std::cout,*update); return 2;
        }
        std::string input, output; std::size_t sent = 0; bool terminal = false;
        auto output_deadline = Clock::now(), input_deadline = Clock::now();
        for (;;) {
            pollfd fds[]{{STDIN_FILENO, POLLIN, 0}, {STDOUT_FILENO, static_cast<short>(output.empty() ? 0 : POLLOUT), 0}};
            const int result = ::poll(fds, 2, 5);
            if (result < 0 && errno == EINTR) continue;
            require(result >= 0, "Probe poll failed");
            if (fds[0].revents & (POLLIN | POLLHUP)) {
                char bytes[4096]; const auto count = read(STDIN_FILENO, bytes, sizeof(bytes));
                if (!count) { require(input.empty(), "Truncated command frame"); return 0; }
                if (count > 0) {
                    if (input.empty()) input_deadline = Clock::now()+std::chrono::seconds(5);
                    input.append(bytes, static_cast<std::size_t>(count));
                    while (input.size() >= 4) {
                        std::uint32_t size = 0;
                        for (int i = 0; i < 4; ++i) size = (size << 8) | static_cast<unsigned char>(input[i]);
                        require(size <= spatial::max_frame_bytes, "Excessive command frame");
                        if (input.size() < 4+size) break;
                        id::ParticipantCommand command;
                        require(command.ParseFromArray(input.data()+4, static_cast<int>(size)), "Malformed participant command");
                        input.erase(0,4+size); runtime.submit(std::move(command));
                        input_deadline = Clock::now()+std::chrono::seconds(5);
                    }
                } else require(errno == EAGAIN || errno == EINTR, "Probe command read failed");
            }
            require(input.empty() || Clock::now() < input_deadline, "Command frame deadline exceeded");
            require(!(fds[0].revents & (POLLERR | POLLNVAL)), "Probe input failed");
            require(!(fds[1].revents & (POLLERR | POLLHUP | POLLNVAL)), "Probe output closed");
            if (output.empty()) {
                if (auto update = runtime.poll()) {
                    terminal = runtime.failed() || update->has_released();
                    std::ostringstream frame; spatial::write_frame(frame,*update); output = frame.str(); sent = 0;
                    output_deadline = Clock::now()+std::chrono::seconds(5);
                }
            }
            if (output.empty()) continue;
            require(Clock::now() < output_deadline, "Probe output deadline exceeded");
            if (!(fds[1].revents & POLLOUT)) continue;
            const auto count = write(STDOUT_FILENO,output.data()+sent,output.size()-sent);
            if (count < 0 && (errno == EAGAIN || errno == EINTR)) continue;
            require(count > 0, "Probe output write failed"); sent += static_cast<std::size_t>(count);
            if (sent == output.size()) {
                if (terminal) return runtime.failed() ? 2 : 0;
                output.clear();
            }
        }
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 2; }
}
