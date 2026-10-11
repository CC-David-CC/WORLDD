#include "platform/worker.hpp"
#include "platform/broker.hpp"
#include "core/protocol.hpp"
#include <array>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <fcntl.h>
#include <poll.h>
#include <stdexcept>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>

namespace spatial {
namespace {
using Clock = std::chrono::steady_clock;
std::chrono::seconds request_timeout(const std::string& frame) {
    if (frame.size() < 4) throw std::runtime_error("Truncated protobuf request");
    wire::Request request;
    if (!request.ParseFromArray(frame.data() + 4, static_cast<int>(frame.size() - 4)))
        throw std::runtime_error("Invalid protobuf request");
    if (request.has_git_repo_import()) return std::chrono::minutes(5);
    if (request.has_git_worktree_create() || request.has_git_status() ||
        request.has_git_worktree_remove() || request.has_git_operation())
        return std::chrono::seconds(60);
    return std::chrono::seconds(10);
}
void wait_ready(int fd, short events, int worker, Clock::time_point deadline) {
    while (true) {
        pollfd fds[2]{{fd, events, 0}, {worker, POLLIN, 0}};
        int timeout = -1;
        if (deadline != Clock::time_point::max()) {
            const auto left = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - Clock::now()).count();
            if (left <= 0) throw std::runtime_error("IPC deadline exceeded");
            timeout = static_cast<int>(left);
        }
        const int rc = poll(fds, 2, timeout);
        if (rc < 0 && errno == EINTR) continue;
        if (rc < 0) throw std::runtime_error("IPC poll failed");
        if (rc == 0) throw std::runtime_error("IPC deadline exceeded");
        if (fds[1].revents) throw std::runtime_error("World authority exited");
        if (fds[0].revents & POLLNVAL) throw std::runtime_error("Invalid IPC descriptor");
        if (fds[0].revents & (events | POLLHUP | POLLERR)) return;
    }
}
bool read_all(int fd, char* data, std::size_t size, int worker, Clock::time_point deadline, bool clean_eof = false) {
    std::size_t used = 0;
    while (used < size) {
        wait_ready(fd, POLLIN, worker, deadline);
        const auto count = read(fd, data + used, size - used);
        if (count < 0 && (errno == EINTR || errno == EAGAIN)) continue;
        if (count == 0 && used == 0 && clean_eof) return false;
        if (count <= 0) throw std::runtime_error("Truncated protobuf IPC frame");
        used += static_cast<std::size_t>(count);
    }
    return true;
}
std::string frame_from(int fd, int worker, bool idle, bool& ended,
                       std::chrono::seconds timeout = std::chrono::seconds(10)) {
    if (idle) wait_ready(fd, POLLIN, worker, Clock::time_point::max());
    const auto deadline = Clock::now() + timeout;
    std::string bytes(4, '\0');
    ended = !read_all(fd, bytes.data(), 4, worker, deadline, idle);
    if (ended) return {};
    const auto* h = reinterpret_cast<const unsigned char*>(bytes.data());
    const std::uint32_t length = (std::uint32_t(h[0]) << 24) | (std::uint32_t(h[1]) << 16) |
                                 (std::uint32_t(h[2]) << 8) | h[3];
    if (length > max_frame_bytes) throw std::runtime_error("IPC frame exceeds size limit");
    bytes.resize(4 + length); read_all(fd, bytes.data() + 4, length, worker, deadline); return bytes;
}
void send_all(int fd, const std::string& frame, int worker,
              std::chrono::seconds timeout = std::chrono::seconds(10)) {
    const auto deadline = Clock::now() + timeout;
    std::size_t sent = 0;
    while (sent < frame.size()) {
        wait_ready(fd, POLLOUT, worker, deadline);
        const auto count = write(fd, frame.data() + sent, frame.size() - sent);
        if (count < 0 && (errno == EINTR || errno == EAGAIN)) continue;
        if (count <= 0) throw std::runtime_error("IPC write failed");
        sent += static_cast<std::size_t>(count);
    }
}
void nonblocking(int fd) {
    const int flags = fcntl(fd, F_GETFL);
    if (flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0) throw std::runtime_error("Cannot configure IPC descriptor");
}
}
WorldWorker::WorldWorker(const std::filesystem::path& executable, const std::filesystem::path& state,
                         const std::filesystem::path& broker_resources) {
    broker_ = std::make_unique<BrokerProcess>(executable.parent_path() / "workspaced", state / "execution", true, broker_resources);
    const auto broker_fd = std::to_string(broker_->channel());
    const auto broker_pid = std::to_string(broker_->pid());
    int input[2], output[2];
    if (pipe2(input, O_CLOEXEC) != 0) throw std::runtime_error("Cannot create world input pipe");
    if (pipe2(output, O_CLOEXEC) != 0) { close(input[0]); close(input[1]); throw std::runtime_error("Cannot create world output pipe"); }
    const auto parent = getpid();
    pid_ = fork();
    if (pid_ == 0) {
        if (prctl(PR_SET_PDEATHSIG, SIGKILL) != 0 || getppid() != parent) _exit(126);
        if (dup2(input[0], STDIN_FILENO) < 0 || dup2(output[1], STDOUT_FILENO) < 0) _exit(126);
        close(input[0]); close(input[1]); close(output[0]); close(output[1]);
        if (fcntl(broker_->channel(), F_SETFD, 0) < 0) _exit(126);
        const char* display = std::getenv("SPATIAL_TEST_DISPLAY");
        if (display && *display) execl(executable.c_str(), executable.c_str(), "--state-dir", state.c_str(),
            "--workspace-fd", broker_fd.c_str(), "--workspace-pid", broker_pid.c_str(),
            "--fixture-clock", "--fixture-faults", "--display", display, static_cast<char*>(nullptr));
        else execl(executable.c_str(), executable.c_str(), "--state-dir", state.c_str(), "--fixture-clock", "--fixture-faults",
            "--workspace-fd", broker_fd.c_str(), "--workspace-pid", broker_pid.c_str(), static_cast<char*>(nullptr));
        _exit(127);
    }
    close(input[0]); close(output[1]);
    broker_->release_channel();
    if (pid_ < 0) { close(input[1]); close(output[0]); throw std::runtime_error("Cannot fork world authority"); }
    input_ = input[1]; output_ = output[0];
    try {
        pidfd_ = static_cast<int>(syscall(SYS_pidfd_open, pid_, 0));
        if (pidfd_ < 0) throw std::runtime_error("Cannot monitor world authority");
        nonblocking(input_); nonblocking(output_);
    } catch (...) {
        kill(pid_, SIGKILL); while (waitpid(pid_, nullptr, 0) < 0 && errno == EINTR) {}
        close(input_); close(output_); if (pidfd_ >= 0) close(pidfd_); throw;
    }
}
WorldWorker::~WorldWorker() {
    if (input_ >= 0) close(input_);
    pollfd child{pidfd_, POLLIN, 0};
    int ready;
    do { ready = poll(&child, 1, 2000); } while (ready < 0 && errno == EINTR);
    if (ready <= 0) kill(pid_, SIGKILL);
    while (waitpid(pid_, nullptr, 0) < 0 && errno == EINTR) {}
    close(output_); close(pidfd_);
}
void WorldWorker::proxy() {
    // The worker inherited blocking stdio before these parent-side flags change.
    nonblocking(STDIN_FILENO); nonblocking(STDOUT_FILENO);
    signal(SIGPIPE, SIG_IGN);
    while (true) {
        bool ended = false;
        const auto request = frame_from(STDIN_FILENO, pidfd_, true, ended);
        if (ended) return;
        const auto timeout = request_timeout(request);
        send_all(input_, request, pidfd_);
        const auto reply = frame_from(output_, pidfd_, false, ended, timeout);
        send_all(STDOUT_FILENO, reply, pidfd_, timeout);
    }
}
} // namespace spatial
