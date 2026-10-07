#include "platform/render_worker.hpp"
#include "core/scene.hpp"
#include <array>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <fcntl.h>
#include <poll.h>
#include <stdexcept>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>
namespace spatial {
using Clock = std::chrono::steady_clock;
RenderWorker::RenderWorker(const std::filesystem::path& executable, const std::filesystem::path& state, std::string display)
    : display_(std::move(display)) {
    int pair[2];
    if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, pair) != 0) throw std::runtime_error("Cannot create renderer channel");
    const auto parent = getpid();
    pid_ = fork();
    if (pid_ == 0) {
        if (prctl(PR_SET_PDEATHSIG, SIGKILL) != 0 || getppid() != parent) _exit(126);
        if (dup2(pair[1], STDIN_FILENO) < 0 || dup2(pair[1], STDOUT_FILENO) < 0) _exit(126);
        close(pair[0]); close(pair[1]);
        execl(executable.c_str(), executable.c_str(), "--state-dir", state.c_str(), "--display", display_.c_str(),
              static_cast<char*>(nullptr));
        _exit(127);
    }
    close(pair[1]);
    if (pid_ < 0) { close(pair[0]); throw std::runtime_error("Cannot fork compositor"); }
    socket_ = pair[0];
    try {
        pidfd_ = static_cast<int>(syscall(SYS_pidfd_open, pid_, 0));
        if (pidfd_ < 0) throw std::runtime_error("Cannot monitor compositor");
        const auto flags = fcntl(socket_, F_GETFL);
        if (flags < 0 || fcntl(socket_, F_SETFL, flags | O_NONBLOCK) < 0) throw std::runtime_error("Cannot configure renderer channel");
        deadline_ = Clock::now() + std::chrono::seconds(10);
        const auto ready = receive();
        if (ready.request_id() != 0 || !ready.has_ready()) throw std::runtime_error("Missing compositor readiness");
    } catch (...) {
        close(socket_); kill(pid_, SIGKILL);
        while (waitpid(pid_, nullptr, 0) < 0 && errno == EINTR) {}
        if (pidfd_ >= 0) close(pidfd_);
        throw;
    }
}
RenderWorker::~RenderWorker() {
    close(socket_);
    pollfd fd{pidfd_, POLLIN, 0};
    int ready;
    do { ready = poll(&fd, 1, 2000); } while (ready < 0 && errno == EINTR);
    if (ready <= 0) kill(pid_, SIGKILL);
    while (waitpid(pid_, nullptr, 0) < 0 && errno == EINTR) {}
    close(pidfd_);
}
void RenderWorker::check_alive() const {
    pollfd fd{pidfd_, POLLIN, 0};
    const int result = poll(&fd, 1, 0);
    if (result < 0 && errno == EINTR) return;
    if (result < 0 || fd.revents) throw std::runtime_error("Compositor exited");
}
void RenderWorker::wait(short events) {
    while (true) {
        tick_();
        if (Clock::now() >= deadline_) {
            kill(pid_, SIGKILL); // A stalled compositor cannot resume an uncertain frame.
            throw std::runtime_error("Compositor I/O deadline exceeded");
        }
        pollfd fds[2]{{socket_, events, 0}, {pidfd_, POLLIN, 0}};
        const int ready = poll(fds, 2, 10);
        if (ready < 0 && errno == EINTR) continue;
        if (ready < 0 || fds[1].revents) throw std::runtime_error("Compositor exited during request");
        if (fds[0].revents & (POLLERR | POLLHUP | POLLNVAL)) throw std::runtime_error("Compositor channel closed");
        if (fds[0].revents & events) return;
    }
}
void RenderWorker::read_all(char* out, std::size_t size) {
    std::size_t used = 0;
    while (used < size) {
        wait(POLLIN);
        const auto count = read(socket_, out + used, size - used);
        if (count < 0 && (errno == EAGAIN || errno == EINTR)) continue;
        if (count <= 0) throw std::runtime_error("Truncated compositor frame");
        used += static_cast<std::size_t>(count);
    }
}
wire::RenderResult RenderWorker::receive() {
    std::array<unsigned char, 4> header{};
    read_all(reinterpret_cast<char*>(header.data()), header.size());
    const std::uint32_t size = (std::uint32_t(header[0]) << 24) | (std::uint32_t(header[1]) << 16) |
                               (std::uint32_t(header[2]) << 8) | header[3];
    if (size > max_frame_bytes) throw std::runtime_error("Excessive compositor frame");
    std::string bytes(size, '\0'); read_all(bytes.data(), size);
    wire::RenderResult result;
    if (!result.ParseFromString(bytes) || result.protocol_version() != 1) throw std::runtime_error("Invalid compositor reply");
    validate_message(result); return result;
}
wire::FrameReply RenderWorker::render(const wire::RenderScene& scene, std::uint32_t width, std::uint32_t height, bool capture) {
    validate_render_extent(width, height);
    wire::RenderCommand command;
    command.set_protocol_version(1); command.set_request_id(++sequence_);
    *command.mutable_scene() = scene;
    command.set_width(width); command.set_height(height); command.set_capture(capture);
    const auto payload = command.SerializeAsString();
    if (payload.size() > max_frame_bytes) throw std::invalid_argument("Scene exceeds renderer channel limit");
    const auto size = static_cast<std::uint32_t>(payload.size());
    const std::array<char, 4> header{static_cast<char>(size >> 24), static_cast<char>(size >> 16),
                                    static_cast<char>(size >> 8), static_cast<char>(size)};
    std::string bytes(header.data(), header.size()); bytes += payload;
    deadline_ = Clock::now() + std::chrono::seconds(10);
    std::size_t sent = 0;
    while (sent < bytes.size()) {
        wait(POLLOUT);
        const auto count = send(socket_, bytes.data() + sent, bytes.size() - sent, MSG_NOSIGNAL);
        if (count < 0 && (errno == EAGAIN || errno == EINTR)) continue;
        if (count <= 0) throw std::runtime_error("Cannot submit compositor scene");
        sent += static_cast<std::size_t>(count);
    }
    const auto reply = receive();
    if (reply.request_id() != sequence_ || !reply.has_frame()) throw std::runtime_error("Compositor render failed");
    if (reply.frame().world_revision() != scene.world_revision() || reply.frame().frame_id() != sequence_)
        throw std::runtime_error("Compositor frame identity mismatch");
    return reply.frame();
}
}
