#include "identity/issuer_watch.hpp"
#include "identity/tls_support.hpp"
#include "core/protocol.hpp"
#include <cerrno>
#include <charconv>
#include <chrono>
#include <csignal>
#include <iostream>
#include <poll.h>
#include <sstream>
#include <sys/stat.h>
#include <unistd.h>

int main(int argc, char** argv) {
    using spatial::identity::tls_detail::require;
    using Clock = std::chrono::steady_clock;
    umask(0077); signal(SIGPIPE, SIG_IGN);
    try {
        require((argc == 3 || argc == 5) && std::string_view(argv[1]) == "--config",
                "Usage: spatial-issuer-watch --config ABSOLUTE_FILE [--after-revision N]");
        std::optional<std::uint64_t> after;
        if (argc == 5) {
            require(std::string_view(argv[3]) == "--after-revision", "Unknown issuer watcher option");
            std::uint64_t value = 0; const std::string_view text(argv[4]);
            const auto result = std::from_chars(text.data(), text.data()+text.size(), value);
            require(result.ec == std::errc() && result.ptr == text.data()+text.size() && value <= INT64_MAX,
                    "Invalid revocation cursor"); after = value;
        }
        spatial::identity::tls_detail::nonblocking(STDOUT_FILENO);
        spatial::identity::IssuerWatcher watcher(argv[2], after);
        std::string pending; std::size_t offset = 0; bool terminal = false;
        Clock::time_point deadline;
        for (;;) {
            pollfd fds[]{{STDIN_FILENO, POLLIN, 0}, {STDOUT_FILENO, static_cast<short>(pending.empty() ? 0 : POLLOUT), 0}};
            const auto result = ::poll(fds, 2, 10);
            if (result < 0 && errno == EINTR) continue;
            require(result >= 0, "Cannot poll issuer watcher output");
            // Supervisor shutdown remains possible even with a stalled output reader.
            if (fds[0].revents) {
                char byte; const auto count = read(STDIN_FILENO, &byte, 1);
                if (!count) return 0;
                if (count < 0 && errno == EINTR) continue;
                throw std::runtime_error("Issuer watcher supervisor accepts EOF only");
            }
            require(!(fds[1].revents & (POLLERR | POLLHUP | POLLNVAL)), "Issuer watcher output closed");
            require(!watcher.failed() || pending.empty() || terminal, "Issuer revocation feed failed during output");
            if (pending.empty()) {
                if (auto update = watcher.poll()) {
                    terminal = update->has_error();
                    std::ostringstream stream; spatial::write_frame(stream, *update); pending = stream.str(); offset = 0;
                    deadline = Clock::now()+std::chrono::seconds(5);
                }
            }
            if (pending.empty()) continue;
            require(Clock::now() < deadline, "Issuer watcher output deadline exceeded");
            if (!(fds[1].revents & POLLOUT)) continue;
            const auto count = write(STDOUT_FILENO, pending.data()+offset, pending.size()-offset);
            if (count < 0 && (errno == EINTR || errno == EAGAIN)) continue;
            require(count > 0, "Cannot write issuer revocation update"); offset += static_cast<std::size_t>(count);
            if (offset == pending.size()) {
                if (terminal) return 2;
                pending.clear(); offset = 0;
            }
        }
    } catch (const std::exception& error) {
        std::cerr << "spatial-issuer-watch: " << error.what() << '\n'; return 2;
    }
}
