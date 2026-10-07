#include "platform/broker.hpp"
#include "world/store.hpp"
#include <array>
#include <cerrno>
#include <csignal>
#include <fcntl.h>
#include <poll.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>

namespace spatial {
BrokerProcess::BrokerProcess(const std::filesystem::path& executable, const std::filesystem::path& state) {
    int pair[2];
    if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, pair) != 0) throw std::runtime_error("Cannot create broker channel");
    const auto parent = getpid(); pid_ = fork();
    if (pid_ == 0) {
        if (prctl(PR_SET_PDEATHSIG, SIGKILL) != 0 || getppid() != parent) _exit(126);
        if (dup2(pair[1], STDIN_FILENO) < 0 || dup2(pair[1], STDOUT_FILENO) < 0) _exit(126);
        close(pair[0]); close(pair[1]);
        execl(executable.c_str(), executable.c_str(), "--state-dir", state.c_str(), static_cast<char*>(nullptr)); _exit(127);
    }
    close(pair[1]); channel_ = pair[0];
    if (pid_ < 0) { close(channel_); throw std::runtime_error("Cannot fork workspace authority"); }
    pidfd_ = static_cast<int>(syscall(SYS_pidfd_open, pid_, 0));
    if (pidfd_ < 0) {
        close(channel_); kill(pid_, SIGKILL); while (waitpid(pid_, nullptr, 0) < 0 && errno == EINTR) {}
        throw std::runtime_error("Cannot monitor workspace authority");
    }
}
void BrokerProcess::release_channel() { if (channel_ >= 0) { close(channel_); channel_ = -1; } }
BrokerProcess::~BrokerProcess() {
    release_channel();
    pollfd fd{pidfd_, POLLIN, 0}; int ready;
    do { ready = poll(&fd, 1, 1000); } while (ready < 0 && errno == EINTR);
    if (ready <= 0) kill(pid_, SIGKILL);
    while (waitpid(pid_, nullptr, 0) < 0 && errno == EINTR) {}
    close(pidfd_);
}
BrokerClient::BrokerClient(int inherited_channel, pid_t pid) : channel_(inherited_channel), pid_(pid) {
    if (channel_ <= STDERR_FILENO || pid_ <= 1) throw std::invalid_argument("Invalid inherited broker identity");
    try {
        pidfd_ = static_cast<int>(syscall(SYS_pidfd_open, pid_, 0));
        const auto flags = fcntl(channel_, F_GETFL);
        if (pidfd_ < 0 || flags < 0 || fcntl(channel_, F_SETFL, flags | O_NONBLOCK) < 0 ||
            fcntl(channel_, F_SETFD, FD_CLOEXEC) < 0) throw std::runtime_error("Cannot configure broker channel");
        wire::Request request; request.mutable_runtime_identities();
        const auto ready = call(request);
        if (!ready.has_identities() || ready.identities().workspaced_pid() != static_cast<std::uint64_t>(pid_))
            throw std::runtime_error("Workspace authority identity mismatch");
    } catch (...) { if (channel_ >= 0) close(channel_); if (pidfd_ >= 0) close(pidfd_); throw; }
}
BrokerClient::~BrokerClient() { if (channel_ >= 0) close(channel_); close(pidfd_); }
void BrokerClient::check_alive() const {
    if (channel_ < 0) throw std::runtime_error("Workspace channel failed");
    pollfd fd{pidfd_, POLLIN, 0};
    const auto result = poll(&fd, 1, 0);
    if (result < 0 && errno == EINTR) return;
    if (result < 0 || fd.revents) throw std::runtime_error("Workspace authority exited");
}
void BrokerClient::wait(short events) {
    while (true) {
        tick_();
        if (std::chrono::steady_clock::now() >= deadline_) throw std::runtime_error("Workspace authority deadline exceeded");
        pollfd fds[2]{{channel_, events, 0}, {pidfd_, POLLIN, 0}};
        const auto ready = poll(fds, 2, 10);
        if (ready < 0 && errno == EINTR) continue;
        if (ready < 0 || fds[1].revents || (fds[0].revents & (POLLHUP | POLLERR | POLLNVAL)))
            throw std::runtime_error("Workspace authority channel closed");
        if (fds[0].revents & events) return;
    }
}
void BrokerClient::read_all(char* data, std::size_t size) {
    std::size_t used = 0;
    while (used < size) {
        wait(POLLIN);
        const auto n = read(channel_, data + used, size - used);
        if (n < 0 && (errno == EAGAIN || errno == EINTR)) continue;
        if (n <= 0) throw std::runtime_error("Truncated broker frame");
        used += static_cast<std::size_t>(n);
    }
}
wire::Reply BrokerClient::call(wire::Request request) {
    request.set_protocol_version(1); request.set_request_id(++sequence_);
    const auto payload = request.SerializeAsString();
    if (payload.size() > max_frame_bytes) throw std::invalid_argument("Broker frame exceeds limit");
    wire::Reply reply;
    try {
        const auto size = static_cast<std::uint32_t>(payload.size());
        const std::array<char, 4> header{static_cast<char>(size >> 24), static_cast<char>(size >> 16), static_cast<char>(size >> 8), static_cast<char>(size)};
        std::string bytes(header.data(), header.size()); bytes += payload;
        deadline_ = std::chrono::steady_clock::now() + std::chrono::seconds(10);
        std::size_t sent = 0;
        while (sent < bytes.size()) {
            wait(POLLOUT);
            const auto n = send(channel_, bytes.data() + sent, bytes.size() - sent, MSG_NOSIGNAL);
            if (n < 0 && (errno == EAGAIN || errno == EINTR)) continue;
            if (n <= 0) throw std::runtime_error("Cannot send broker request");
            sent += static_cast<std::size_t>(n);
        }
        std::array<unsigned char, 4> incoming{}; read_all(reinterpret_cast<char*>(incoming.data()), incoming.size());
        const std::uint32_t n = (std::uint32_t(incoming[0]) << 24) | (std::uint32_t(incoming[1]) << 16) | (std::uint32_t(incoming[2]) << 8) | incoming[3];
        if (n > max_frame_bytes) throw std::runtime_error("Broker reply exceeds limit");
        bytes.resize(n); read_all(bytes.data(), n);
        if (!reply.ParseFromString(bytes) || reply.protocol_version() != 1 || reply.request_id() != sequence_)
            throw std::runtime_error("Invalid broker reply identity");
        validate_message(reply);
        if (!reply.has_error()) {
            const bool valid = (request.has_runtime_identities() && reply.has_identities()) ||
                (request.has_git_repo_import() && reply.has_repository()) || (request.has_git_worktree_create() && reply.has_worktree()) ||
                (request.has_git_operation() && reply.has_git_operation()) || (request.has_git_status() && reply.has_git_status());
            if (!valid) throw std::runtime_error("Broker reply has the wrong outcome");
        }
    } catch (...) { close(channel_); channel_ = -1; throw; }
    if (reply.has_error()) throw WorldError(reply.error().code(), reply.error().message().c_str());
    return reply;
}
}
