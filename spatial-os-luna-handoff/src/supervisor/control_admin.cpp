#include "supervisor/control_admin.hpp"
#include "supervisor/control_reply.hpp"
#include "workspace/control_owner.hpp"
#include <array>
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <poll.h>
#include <stdexcept>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/un.h>
#include <unistd.h>

namespace spatial::supervisor {
namespace {
void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
}
ControlAdmin::ControlAdmin(int socket, pid_t child, int retained_pidfd)
    : socket_(socket), creator_(getpid()), broker_(child) {
    try {
        require(socket_ >= 3 && child > 0 && child != creator_, "Invalid administrative endpoint or broker child");
        sockaddr_un address{}; socklen_t length = sizeof(address);
        int type = 0; socklen_t type_length = sizeof(type);
        ucred peer{}; socklen_t peer_length = sizeof(peer);
        require(getpeername(socket_, reinterpret_cast<sockaddr*>(&address), &length) == 0 &&
            address.sun_family == AF_UNIX &&
            getsockopt(socket_, SOL_SOCKET, SO_TYPE, &type, &type_length) == 0 && type == SOCK_SEQPACKET &&
            getsockopt(socket_, SOL_SOCKET, SO_PEERCRED, &peer, &peer_length) == 0 &&
            peer_length == sizeof(peer) && peer.pid == creator_ && peer.uid == geteuid(),
            "Administrative socket was not created by this coordinator");
        require(retained_pidfd >= 3, "Missing retained broker process handle");
        pidfd_ = fcntl(retained_pidfd, F_DUPFD_CLOEXEC, 3);
        require(pidfd_ >= 3, "Cannot duplicate retained broker process handle");
        std::ifstream info("/proc/self/fdinfo/" + std::to_string(pidfd_));
        std::string line; bool matched = false;
        while (std::getline(info, line)) {
            if (line.rfind("Pid:\t", 0) == 0) matched = line == "Pid:\t" + std::to_string(child);
        }
        require(matched && syscall(SYS_pidfd_send_signal, pidfd_, 0, nullptr, 0) == 0,
            "Retained broker process handle does not match live child");
        const int enabled = 1, flags = fcntl(socket_, F_GETFL);
        require(pidfd_ >= 0 && flags >= 0 && fcntl(socket_, F_SETFL, flags | O_NONBLOCK) == 0 &&
            fcntl(socket_, F_SETFD, FD_CLOEXEC) == 0 &&
            setsockopt(socket_, SOL_SOCKET, SO_PASSCRED, &enabled, sizeof(enabled)) == 0,
            "Cannot configure administrative transport");
    } catch (...) { close_all(); throw; }
}
ControlAdmin::~ControlAdmin() { close_all(); }
void ControlAdmin::close_all() {
    if (socket_ >= 3) close(socket_);
    if (pidfd_ >= 0) close(pidfd_);
    socket_ = pidfd_ = -1;
}
void ControlAdmin::wait(short events, Clock::time_point deadline) {
    for (;;) {
        require(getpid() == creator_ && socket_ >= 3 && pidfd_ >= 0, "Administrative transport ownership lost");
        const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - Clock::now()).count();
        require(remaining > 0, "Administrative operation timed out; effects are uncertain");
        pollfd descriptors[]{{socket_, events, 0}, {pidfd_, POLLIN, 0}};
        const int result = poll(descriptors, 2, static_cast<int>(remaining));
        if (result < 0 && errno == EINTR) continue;
        require(result >= 0 && descriptors[1].revents == 0,
            "Broker process exited; administrative effects are uncertain");
        require(!(descriptors[0].revents & (POLLERR | POLLNVAL | POLLHUP)),
            "Administrative channel failed; effects are uncertain");
        if (descriptors[0].revents & events) return;
    }
}
workspace::v1::ControlSupervisorReply ControlAdmin::exchange(
    const workspace::v1::ControlSupervisorRequest& request, int endpoint) {
    // Reject caller errors before sending anything.
    validate_message(request);
    require(request.protocol_version() == 1 && request.ByteSizeLong() <= 65536,
        "Invalid administrative request version or size");
    const workspace::v1::ControlOwner* owner = request.has_register_() ? &request.register_() :
        request.has_inspect() ? &request.inspect() : request.has_fence() ? &request.fence() :
        request.has_recover() ? &request.recover().owner() : nullptr;
    require(owner != nullptr && (request.has_register_() ? endpoint >= 3 : endpoint == -1),
        "Administrative operation has incorrect descriptor count");
    validate_control_owner(*owner);
    if (request.has_recover()) validate_control_recovery(request.recover());
    const auto bytes = request.SerializeAsString();
    const auto deadline = Clock::now() + std::chrono::seconds(5);
    try {
        for (;;) {
            wait(POLLOUT, deadline);
            iovec data{const_cast<char*>(bytes.data()), bytes.size()};
            alignas(cmsghdr) std::array<char, CMSG_SPACE(sizeof(int))> ancillary{};
            msghdr message{}; message.msg_iov = &data; message.msg_iovlen = 1;
            if (endpoint >= 3) {
                message.msg_control = ancillary.data(); message.msg_controllen = ancillary.size();
                auto* header = CMSG_FIRSTHDR(&message);
                header->cmsg_level = SOL_SOCKET; header->cmsg_type = SCM_RIGHTS;
                header->cmsg_len = CMSG_LEN(sizeof(int));
                std::memcpy(CMSG_DATA(header), &endpoint, sizeof(endpoint));
            }
            const auto count = sendmsg(socket_, &message, MSG_NOSIGNAL | MSG_DONTWAIT);
            if (count < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) continue;
            require(count == static_cast<ssize_t>(bytes.size()), "Administrative send failed; effects are uncertain");
            break;
        }
        for (;;) {
            wait(POLLIN, deadline);
            std::array<char, 65536> bytes_in{};
            alignas(cmsghdr) std::array<char, CMSG_SPACE(sizeof(int) * 16) + CMSG_SPACE(sizeof(ucred))> ancillary{};
            iovec data{bytes_in.data(), bytes_in.size()}; msghdr message{};
            message.msg_iov = &data; message.msg_iovlen = 1;
            message.msg_control = ancillary.data(); message.msg_controllen = ancillary.size();
            const auto count = recvmsg(socket_, &message, MSG_DONTWAIT | MSG_CMSG_CLOEXEC);
            if (count < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) continue;
            unsigned credentials = 0; ucred sender{}; bool unexpected = false;
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
                credentials == 1 && sender.pid == broker_ && sender.uid == geteuid(),
                "Administrative reply authentication failed; effects are uncertain");
            workspace::v1::ControlSupervisorReply reply;
            require(reply.ParseFromArray(bytes_in.data(), static_cast<int>(count)), "Malformed administrative reply");
            validate_control_reply(request, reply);
            return reply; // Typed remote errors remain explicit errors.
        }
    } catch (...) { close_all(); throw; }
}
}
