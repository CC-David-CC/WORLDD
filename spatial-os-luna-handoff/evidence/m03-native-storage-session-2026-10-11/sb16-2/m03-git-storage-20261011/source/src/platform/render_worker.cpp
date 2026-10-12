#include "platform/render_worker.hpp"
#include "core/scene.hpp"
#include "world/store.hpp"
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
RenderWorker::RenderWorker(const std::filesystem::path& executable, const std::filesystem::path& state, std::string display, int native_fd)
    : display_(std::move(display)) {
    int pair[2];
    if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, pair) != 0) throw std::runtime_error("Cannot create renderer channel");
    const auto parent = getpid();
    const auto native_argument = std::to_string(native_fd);
    pid_ = fork();
    if (pid_ == 0) {
        if (prctl(PR_SET_PDEATHSIG, SIGKILL) != 0 || getppid() != parent) _exit(126);
        if (dup2(pair[1], STDIN_FILENO) < 0 || dup2(pair[1], STDOUT_FILENO) < 0) _exit(126);
        close(pair[0]); close(pair[1]);
        if (native_fd >= 0) {
            if (native_fd <= STDERR_FILENO) _exit(126);
            if (native_fd > 3 && syscall(SYS_close_range, 3u, static_cast<unsigned>(native_fd - 1), 0u) != 0) _exit(126);
            if (syscall(SYS_close_range, static_cast<unsigned>(native_fd) + 1u, ~0u, 0u) != 0) _exit(126);
            const int flags = fcntl(native_fd, F_GETFD);
            if (flags < 0 || fcntl(native_fd, F_SETFD, flags & ~FD_CLOEXEC) < 0) _exit(126);
            execl(executable.c_str(), executable.c_str(), "--state-dir", state.c_str(), "--display", display_.c_str(),
                  "--native-security-fd", native_argument.c_str(), static_cast<char*>(nullptr));
            _exit(127);
        }
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
    *command.mutable_scene() = scene;
    command.set_width(width); command.set_height(height); command.set_capture(capture);
    const auto reply = exchange(command);
    if (!reply.has_frame() || reply.frame().world_revision() != scene.world_revision() || reply.frame().frame_id() <= frame_id_)
        throw std::runtime_error("Compositor frame identity mismatch");
    frame_id_ = reply.frame().frame_id();
    return reply.frame();
}
wire::NativeSnapshot RenderWorker::synchronize() {
    wire::RenderCommand command; command.mutable_sync();
    const auto reply = exchange(command);
    if (!reply.has_snapshot()) throw std::runtime_error("Missing native surface snapshot");
    return reply.snapshot();
}
wire::RenderVisibility RenderWorker::observe(const wire::RenderScene& scene, std::uint32_t width, std::uint32_t height, bool include_pixels) {
    validate_observation_extent(width, height, include_pixels);
    wire::RenderCommand command;
    *command.mutable_scene() = scene;
    command.set_width(width); command.set_height(height); command.set_observe(true);
    command.set_include_pixels(include_pixels);
    const auto reply = exchange(command);
    if (!reply.has_visibility() || reply.visibility().world_revision() != scene.world_revision() ||
        reply.visibility().frame_id() <= frame_id_) throw std::runtime_error("Compositor observation identity mismatch");
    if (reply.visibility().has_image() != include_pixels)
        throw std::runtime_error("Compositor observation image presence mismatch");
    if (include_pixels) {
        const auto& image = reply.visibility().image();
        if (image.width() != width || image.height() != height || image.rgb8().size() != std::uint64_t(width) * height * 3)
            throw std::runtime_error("Invalid compositor observation image");
    }
    const auto count = scene_panels(scene).size();
    unsigned previous = 0;
    std::uint64_t total = 0;
    for (const auto& sample : reply.visibility().visible()) {
        if (sample.index() == 0 || sample.index() > count || sample.index() <= previous || !sample.has_pixel() ||
            sample.pixel().x() >= width || sample.pixel().y() >= height || sample.pixels() == 0 ||
            sample.pixels() > std::uint64_t(width) * height - total)
            throw std::runtime_error("Invalid compositor visibility sample");
        total += sample.pixels();
        previous = sample.index();
    }
    frame_id_ = reply.visibility().frame_id();
    return reply.visibility();
}
wire::NativeActorInput RenderWorker::input(const wire::RendererInputCommand& input, const std::function<void()>& before_send) {
    wire::RenderCommand command; *command.mutable_input() = input;
    const auto reply = exchange(command, before_send);
    if (!reply.has_input() || reply.input().actor_id() != input.actor_id())
        throw std::runtime_error("Invalid compositor actor input reply");
    return reply.input();
}
wire::RenderResult RenderWorker::exchange(wire::RenderCommand command, const std::function<void()>& before_send) {
    if (busy_) throw std::runtime_error("Recursive compositor exchange");
    busy_ = true;
    struct Reset { bool& value; ~Reset() { value = false; } } reset{busy_};
    command.set_protocol_version(1); command.set_request_id(sequence_ + 1);
    const auto payload = command.SerializeAsString();
    if (payload.size() > max_frame_bytes) throw std::invalid_argument("Scene exceeds renderer channel limit");
    ++sequence_;
    const auto size = static_cast<std::uint32_t>(payload.size());
    const std::array<char, 4> header{static_cast<char>(size >> 24), static_cast<char>(size >> 16),
                                    static_cast<char>(size >> 8), static_cast<char>(size)};
    std::string bytes(header.data(), header.size()); bytes += payload;
    deadline_ = Clock::now() + std::chrono::seconds(10);
    std::size_t sent = 0;
    while (sent < bytes.size()) {
        wait(POLLOUT);
        // A scheduler callback may revoke input while the socket is blocked.
        // Once bytes have been sent, finish the frame and cancel after its reply.
        if (!sent && before_send) before_send();
        const auto count = send(socket_, bytes.data() + sent, bytes.size() - sent, MSG_NOSIGNAL);
        if (count < 0 && (errno == EAGAIN || errno == EINTR)) continue;
        if (count <= 0) throw std::runtime_error("Cannot submit compositor scene");
        sent += static_cast<std::size_t>(count);
    }
    const auto reply = receive();
    if (reply.request_id() != sequence_) throw std::runtime_error("Compositor response identity mismatch");
    if (reply.has_error()) throw WorldError(reply.error().code(), reply.error().message().c_str());
    return reply;
}
}
