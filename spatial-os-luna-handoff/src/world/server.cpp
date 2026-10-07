#include "world/server.hpp"
#include "core/protocol.hpp"
#include "platform/render_worker.hpp"
#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <fcntl.h>
#include <poll.h>
#include <stdexcept>
#include <unistd.h>

namespace spatial {
namespace {
using Clock = std::chrono::steady_clock;
void nonblocking(int fd) {
    const auto flags = fcntl(fd, F_GETFL);
    if (flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0)
        throw std::runtime_error("Cannot configure management pipe");
}
std::uint32_t length(const std::string& buffer) {
    const auto* h = reinterpret_cast<const unsigned char*>(buffer.data());
    return (std::uint32_t(h[0]) << 24) | (std::uint32_t(h[1]) << 16) | (std::uint32_t(h[2]) << 8) | h[3];
}
}
void serve(WorldStore& world, Actors& actors, RenderWorker* renderer) {
    nonblocking(STDIN_FILENO); nonblocking(STDOUT_FILENO);
    signal(SIGPIPE, SIG_IGN);
    const auto origin = Clock::now();
    if (renderer) renderer->set_tick([&] { actors.tick(std::chrono::duration<double>(Clock::now() - origin).count()); });
    auto input_started = origin, output_started = origin;
    std::string input, output;
    std::size_t sent = 0;
    bool eof = false;
    while (true) {
        const auto now = Clock::now();
        actors.tick(std::chrono::duration<double>(now - origin).count());
        if (renderer) renderer->check_alive();
        if (output.empty() && input.size() >= 4) {
            const auto size = length(input);
            if (size > max_frame_bytes) throw std::runtime_error("Frame exceeds size limit");
            if (input.size() >= size + 4) {
                const auto reply = handle_frame(std::string_view(input).substr(4, size), &world, &actors, renderer);
                input.erase(0, size + 4); input_started = now;
                const auto payload = reply.SerializeAsString();
                if (payload.size() > max_frame_bytes) throw std::runtime_error("Reply exceeds size limit");
                const auto n = static_cast<std::uint32_t>(payload.size());
                const std::array<char, 4> header{static_cast<char>(n >> 24), static_cast<char>(n >> 16),
                                               static_cast<char>(n >> 8), static_cast<char>(n)};
                output.assign(header.data(), header.size()); output += payload; sent = 0; output_started = Clock::now();
            }
        }
        if (eof && output.empty()) {
            if (!input.empty()) throw std::runtime_error("Truncated protobuf frame");
            return;
        }
        if ((!input.empty() && now - input_started > std::chrono::seconds(10)) ||
            (!output.empty() && now - output_started > std::chrono::seconds(10)))
            throw std::runtime_error("Management I/O deadline exceeded");
        pollfd fds[2]{{!eof && output.empty() ? STDIN_FILENO : -1, POLLIN, 0},
                      {STDOUT_FILENO, static_cast<short>(output.empty() ? 0 : POLLOUT), 0}};
        const int ready = poll(fds, 2, 10);
        if (ready < 0 && errno == EINTR) continue;
        if (ready < 0) throw std::runtime_error("Management poll failed");
        if ((fds[0].revents | fds[1].revents) & POLLNVAL) throw std::runtime_error("Invalid management pipe");
        if (fds[1].revents & (POLLHUP | POLLERR)) throw std::runtime_error("Management reader disconnected");
        if (!output.empty() && (fds[1].revents & (POLLOUT | POLLHUP | POLLERR))) {
            const auto count = write(STDOUT_FILENO, output.data() + sent, output.size() - sent);
            if (count < 0 && errno != EAGAIN && errno != EINTR) throw std::runtime_error("Cannot write management reply");
            if (count > 0) {
                sent += static_cast<std::size_t>(count);
                if (sent == output.size()) output.clear();
            }
        }
        if (!eof && output.empty() && (fds[0].revents & (POLLIN | POLLHUP | POLLERR))) {
            std::array<char, 65536> bytes{};
            const auto count = read(STDIN_FILENO, bytes.data(), std::min(bytes.size(), max_frame_bytes + 4 - input.size()));
            if (count < 0 && errno != EAGAIN && errno != EINTR) throw std::runtime_error("Cannot read management request");
            if (count == 0) eof = true;
            if (count > 0) {
                if (input.empty()) input_started = Clock::now();
                input.append(bytes.data(), static_cast<std::size_t>(count));
            }
        }
        // Buffered complete frames must not wait for more client bytes.
    }
}
} // namespace spatial
