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
BrokerProcess::BrokerProcess(const std::filesystem::path& executable, const std::filesystem::path& state, bool fixture_faults) {
    int pair[2];
    if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, pair) != 0) throw std::runtime_error("Cannot create broker channel");
    const auto parent = getpid(); pid_ = fork();
    if (pid_ == 0) {
        if (prctl(PR_SET_PDEATHSIG, SIGKILL) != 0 || getppid() != parent) _exit(126);
        if (dup2(pair[1], STDIN_FILENO) < 0 || dup2(pair[1], STDOUT_FILENO) < 0) _exit(126);
        close(pair[0]); close(pair[1]);
        if (fixture_faults) execl(executable.c_str(), executable.c_str(), "--state-dir", state.c_str(),
                                "--fixture-faults", static_cast<char*>(nullptr));
        if (fixture_faults) _exit(127);
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
broker_wire::Reply BrokerClient::exchange(broker_wire::Request request) {
    request.set_protocol_version(1); request.set_request_id(++sequence_);
    if (request.has_management()) {
        request.mutable_management()->set_protocol_version(1);
        request.mutable_management()->set_request_id(sequence_);
    }
    const auto payload = request.SerializeAsString();
    if (payload.size() > max_frame_bytes) throw std::invalid_argument("Broker frame exceeds limit");
    broker_wire::Reply reply;
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
            bool valid = (request.has_access() && reply.has_access()) || (request.has_launch() && reply.has_launched());
            if (request.has_application_access() && reply.has_application_access()) {
                const auto& expected = request.application_access().applications();
                const auto& received = reply.application_access().applications();
                valid = expected.size() == received.size();
                for (int i = 0; valid && i < expected.size(); ++i) {
                    const auto& item = received.Get(i);
                    valid = item.has_application() && item.application().SerializeAsString() == expected.Get(i).SerializeAsString() &&
                        item.has_observe() && item.has_control() && (!item.control() || item.observe());
                }
            }
            if (request.has_grants() && reply.has_grants()) {
                valid = reply.grants().grants_size() <= 16 && reply.grants().revision() <= INT64_MAX &&
                    (reply.grants().next_after().empty() || reply.grants().next_after().size() == 32);
                for (const auto& record : reply.grants().grants())
                    valid = valid && record.protocol_version() == 1 && record.id().size() == 32 &&
                        record.terms().subject().SerializeAsString() == request.grants().principal().SerializeAsString() &&
                        record.terms().scope().domain().kind() == authority::v1::WORKSPACE &&
                        record.terms().scope().domain().id() == request.grants().workspace();
            }
            if (request.has_delegate() && reply.has_grant())
                valid = reply.grant().protocol_version() == 1 && reply.grant().id().size() == 32 &&
                    reply.grant().parent_id() == request.delegate().parent_id() &&
                    reply.grant().issuer().SerializeAsString() == request.delegate().principal().SerializeAsString() &&
                    reply.grant().terms().SerializeAsString() == request.delegate().terms().SerializeAsString();
            if (request.has_revoke() && reply.has_grant())
                valid = reply.grant().protocol_version() == 1 && reply.grant().id() == request.revoke().id() &&
                    reply.grant().revoked_revision() > 0 && reply.grant().terms().scope().domain().kind() == authority::v1::WORKSPACE &&
                    reply.grant().terms().scope().domain().id() == request.revoke().workspace();
            if (request.has_management() && reply.has_management()) {
                const auto& command = request.management(); const auto& result = reply.management();
                valid = result.protocol_version() == 1 && result.request_id() == sequence_ &&
                    (result.has_error() || (command.has_runtime_identities() && result.has_identities()) ||
                     (command.has_fault_arm() && result.has_fault_armed() && result.fault_armed().has_armed() && result.fault_armed().armed()) ||
                     (command.has_git_repo_import() && result.has_repository()) || (command.has_git_worktree_create() && result.has_worktree()) ||
                     (command.has_git_operation() && result.has_git_operation()) || (command.has_git_status() && result.has_git_status()) ||
                     (command.has_git_worktree_remove() && result.has_worktree_removed()) ||
                     ((command.has_workspace_create() || command.has_workspace_get()) && result.has_workspace()) ||
                     (command.has_workspace_member_set() && result.has_workspace_ack()) ||
                     (command.has_process_wait() && result.has_process_wait()));
            }
            if (!valid) throw std::runtime_error("Broker reply has the wrong outcome");
        }
    } catch (...) { close(channel_); channel_ = -1; throw; }
    if (reply.has_error()) throw WorldError(reply.error().code(), reply.error().message().c_str());
    return reply;
}
wire::Reply BrokerClient::call(wire::Request request) {
    broker_wire::Request envelope; *envelope.mutable_management() = std::move(request);
    const auto reply = exchange(envelope).management();
    if (reply.has_error()) throw WorldError(reply.error().code(), reply.error().message().c_str());
    return reply;
}
broker_wire::AccessReply BrokerClient::access(const std::string& workspace, const std::string& actor, const authority::v1::ActorRef* principal) {
    broker_wire::Request request; request.mutable_access()->set_workspace(workspace); request.mutable_access()->set_actor(actor);
    if (principal) *request.mutable_access()->mutable_principal() = *principal;
    return exchange(request).access();
}
broker_wire::ApplicationAccessReply BrokerClient::application_access(const broker_wire::ApplicationAccessRequest& command) {
    broker_wire::Request request; *request.mutable_application_access() = command;
    return exchange(request).application_access();
}
wire::ProcessLaunchReply BrokerClient::launch(const std::string& workspace, const std::string& actor, const wire::ProcessLaunchRequest& command,
                                            const authority::v1::ActorRef* principal) {
    broker_wire::Request request; auto* launch = request.mutable_launch();
    launch->set_workspace(workspace); launch->set_actor(actor); *launch->mutable_command() = command;
    if (principal) *launch->mutable_principal() = *principal;
    return exchange(request).launched();
}
authority::v1::GrantPage BrokerClient::grants(const broker_wire::GrantListRequest& command) {
    broker_wire::Request request; *request.mutable_grants() = command; return exchange(request).grants();
}
authority::v1::GrantRecord BrokerClient::delegate(const broker_wire::GrantDelegateRequest& command) {
    broker_wire::Request request; *request.mutable_delegate() = command; return exchange(request).grant();
}
authority::v1::GrantRecord BrokerClient::revoke(const broker_wire::GrantRevokeRequest& command) {
    broker_wire::Request request; *request.mutable_revoke() = command; return exchange(request).grant();
}
}
