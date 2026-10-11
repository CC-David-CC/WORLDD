#include "supervisor/child_handle.hpp"
#include "supervisor/launch.hpp"
#include "core/protocol.hpp"
#include "identity/tls_support.hpp"
#include <array>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <fcntl.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/un.h>
#include <unistd.h>

namespace spatial::supervisor {
namespace {
using File = identity::tls_detail::Fd;
using Clock = std::chrono::steady_clock;
void require(bool okay, const char* message) {
    if (!okay) throw std::runtime_error(message);
}
}
struct ChildHandleServer::Impl {
    File socket, parent_handle, pending_handle;
    pid_t parent, owner = getpid();
    std::string output;
    Clock::time_point deadline;
    Impl(int fd, pid_t peer) : socket(fd), parent(peer) {
        require(fd >= 3 && parent > 1 && parent == getppid(), "Invalid handle channel parent");
        sockaddr_un address{}; socklen_t length = sizeof(address);
        int type = 0; socklen_t type_length = sizeof(type);
        ucred credentials{}; socklen_t credential_length = sizeof(credentials);
        require(getpeername(fd, reinterpret_cast<sockaddr*>(&address), &length) == 0 &&
            address.sun_family == AF_UNIX &&
            getsockopt(fd, SOL_SOCKET, SO_TYPE, &type, &type_length) == 0 && type == SOCK_SEQPACKET &&
            getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &credentials, &credential_length) == 0 &&
            credential_length == sizeof(credentials) && credentials.pid == parent && credentials.uid == geteuid(),
            "Handle channel was not created by the supervisor parent");
        parent_handle.reset(static_cast<int>(syscall(SYS_pidfd_open, parent, 0)));
        require(parent_handle.value >= 3 && getppid() == parent, "Cannot retain handle channel parent");
        const int enabled = 1, flags = fcntl(fd, F_GETFL);
        require(flags >= 0 && fcntl(fd, F_SETFL, flags | O_NONBLOCK) == 0 &&
            fcntl(fd, F_SETFD, FD_CLOEXEC) == 0 &&
            setsockopt(fd, SOL_SOCKET, SO_PASSCRED, &enabled, sizeof(enabled)) == 0,
            "Cannot configure private handle channel");
    }
    void live() const {
        require(getpid() == owner && getppid() == parent, "Handle channel ownership lost");
        pollfd event{parent_handle.value, POLLIN, 0};
        int result;
        do { result = poll(&event, 1, 0); } while (result < 0 && errno == EINTR);
        require(result == 0, "Handle channel parent exited");
    }
    bool flush() {
        if (output.empty()) return true;
        require(Clock::now() < deadline, "Private handle reply deadline exceeded");
        // Do not send a handle which became dead while its reply was waiting.
        if (pending_handle.value >= 0) {
            pollfd event{pending_handle.value, POLLIN, 0};
            int result;
            do { result = poll(&event, 1, 0); } while (result < 0 && errno == EINTR);
            require(result == 0, "Broker exited before handle transfer");
        }
        iovec data{output.data(), output.size()}; msghdr message{};
        message.msg_iov = &data; message.msg_iovlen = 1;
        alignas(cmsghdr) std::array<char, CMSG_SPACE(sizeof(int))> ancillary{};
        if (pending_handle.value >= 0) {
            message.msg_control = ancillary.data(); message.msg_controllen = ancillary.size();
            auto* header = CMSG_FIRSTHDR(&message);
            header->cmsg_level = SOL_SOCKET; header->cmsg_type = SCM_RIGHTS;
            header->cmsg_len = CMSG_LEN(sizeof(int));
            std::memcpy(CMSG_DATA(header), &pending_handle.value, sizeof(int));
        }
        const auto count = sendmsg(socket.value, &message, MSG_DONTWAIT | MSG_NOSIGNAL);
        if (count < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) return false;
        require(count == static_cast<ssize_t>(output.size()), "Private handle reply failed");
        pending_handle.reset(); output.clear();
        return true;
    }
};
ChildHandleServer::ChildHandleServer(int fd, pid_t parent) : impl_(std::make_unique<Impl>(fd, parent)) {}
ChildHandleServer::~ChildHandleServer() = default;
void ChildHandleServer::pump(const AuthorityLaunch& launch) {
    auto& state = *impl_;
    state.live();
    if (!state.flush()) return;
    std::array<char, 65536> bytes{};
    alignas(cmsghdr) std::array<char, CMSG_SPACE(sizeof(int) * 16) + CMSG_SPACE(sizeof(ucred))> ancillary{};
    iovec data{bytes.data(), bytes.size()}; msghdr message{};
    message.msg_iov = &data; message.msg_iovlen = 1;
    message.msg_control = ancillary.data(); message.msg_controllen = ancillary.size();
    const auto count = recvmsg(state.socket.value, &message, MSG_DONTWAIT | MSG_CMSG_CLOEXEC);
    if (count < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) return;
    bool unexpected = false; unsigned credentials = 0; ucred sender{};
    for (auto* header = CMSG_FIRSTHDR(&message); header; header = CMSG_NXTHDR(&message, header)) {
        if (header->cmsg_len < CMSG_LEN(0)) { unexpected = true; continue; }
        if (header->cmsg_level == SOL_SOCKET && header->cmsg_type == SCM_RIGHTS) {
            const auto size = header->cmsg_len - CMSG_LEN(0);
            for (std::size_t offset = 0; offset + sizeof(int) <= size; offset += sizeof(int)) {
                int fd; std::memcpy(&fd, CMSG_DATA(header) + offset, sizeof(fd)); close(fd);
            }
            unexpected = true;
        } else if (header->cmsg_level == SOL_SOCKET && header->cmsg_type == SCM_CREDENTIALS &&
            header->cmsg_len == CMSG_LEN(sizeof(ucred))) {
            ++credentials; std::memcpy(&sender, CMSG_DATA(header), sizeof(sender));
        } else unexpected = true;
    }
    require(count > 0 && !(message.msg_flags & (MSG_TRUNC | MSG_CTRUNC)) && !unexpected &&
        credentials == 1 && sender.pid == state.parent && sender.uid == geteuid(),
        "Private handle request authentication failed");
    v1::AuthorityChildHandleRequest request;
    v1::AuthorityChildHandleReply reply; reply.set_protocol_version(1);
    try {
        require(request.ParseFromArray(bytes.data(), static_cast<int>(count)), "Malformed private handle request");
        reply.set_request_id(request.request_id()); validate_message(request);
        require(request.protocol_version() == 1 && request.has_expected(), "Invalid private handle request");
        state.pending_handle.reset(launch.duplicate_broker_pidfd(request.expected()));
        *reply.mutable_launch() = launch.inspect();
    } catch (const std::exception& error) {
        state.pending_handle.reset(); reply.clear_outcome();
        reply.mutable_error()->set_message(error.what());
    }
    state.output = reply.SerializeAsString();
    require(state.output.size() <= bytes.size(), "Excessive private handle reply");
    state.deadline = Clock::now() + std::chrono::seconds(5);
    state.flush();
}
}
