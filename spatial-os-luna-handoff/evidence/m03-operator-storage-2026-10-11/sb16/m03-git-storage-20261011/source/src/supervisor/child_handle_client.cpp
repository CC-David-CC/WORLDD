#include "supervisor/child_handle_client.hpp"
#include "identity/tls_support.hpp"
#include "core/protocol.hpp"
#include <array>
#include <chrono>
#include <cstring>
#include <sys/socket.h>
#include <sys/un.h>

namespace spatial::supervisor {
namespace {
using File = identity::tls_detail::Fd;
using Clock = std::chrono::steady_clock;
void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
}
struct ChildHandleClient::Impl {
    File socket;
    ProcessHandle supervisor;
    pid_t owner = getpid();
    std::uint64_t sequence = 0;
    Impl(int fd, pid_t pid, int pin) : socket(fd), supervisor(ProcessHandle::duplicate(pin, pid)) {
        require(fd >= 3 && pid != owner, "Invalid supervisor handle endpoint");
        sockaddr_un address{}; socklen_t size = sizeof(address);
        int type = 0; socklen_t type_size = sizeof(type);
        ucred peer{}; socklen_t peer_size = sizeof(peer);
        require(getpeername(fd, reinterpret_cast<sockaddr*>(&address), &size) == 0 && address.sun_family == AF_UNIX &&
            getsockopt(fd, SOL_SOCKET, SO_TYPE, &type, &type_size) == 0 && type == SOCK_SEQPACKET &&
            getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &peer, &peer_size) == 0 && peer_size == sizeof(peer) &&
            peer.pid == owner && peer.uid == geteuid(), "Supervisor handle socket was not created by this coordinator");
        const int enabled = 1, flags = fcntl(fd, F_GETFL);
        require(flags >= 0 && fcntl(fd, F_SETFL, flags | O_NONBLOCK) == 0 &&
            fcntl(fd, F_SETFD, FD_CLOEXEC) == 0 &&
            setsockopt(fd, SOL_SOCKET, SO_PASSCRED, &enabled, sizeof(enabled)) == 0,
            "Cannot configure supervisor handle receiver");
    }
    void wait(short events, Clock::time_point deadline) const {
        for (;;) {
            require(getpid() == owner && socket.value >= 3, "Supervisor handle receiver ownership lost");
            const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - Clock::now()).count();
            require(remaining > 0, "Supervisor handle reply timed out");
            pollfd fds[]{{socket.value, events, 0}, {supervisor.descriptor(), POLLIN, 0}};
            const int result = poll(fds, 2, static_cast<int>(remaining));
            if (result < 0 && errno == EINTR) continue;
            require(result >= 0 && fds[1].revents == 0 && !(fds[0].revents & (POLLERR | POLLHUP | POLLNVAL)),
                "Supervisor exited or handle channel failed");
            if (fds[0].revents & events) return;
        }
    }
};
ChildHandleClient::ChildHandleClient(int fd, pid_t pid, int pin) : impl_(std::make_unique<Impl>(fd, pid, pin)) {}
ChildHandleClient::~ChildHandleClient() = default;
ProcessHandle ChildHandleClient::broker(const v1::AuthorityLaunchRecord& expected) {
    validate_message(expected);
    require(expected.binding().supervised() && expected.has_authorization() &&
        expected.stage() == v1::START_ATTEMPTED && expected.native().role() == security::v1::BROKER &&
        expected.process().launch_id().size() == 32 && expected.native().launch_id() == expected.process().launch_id(),
        "Expected an authorized running broker launch snapshot");
    auto& state = *impl_;
    require(state.sequence != UINT64_MAX, "Supervisor handle request sequence exhausted");
    v1::AuthorityChildHandleRequest request;
    request.set_protocol_version(1); request.set_request_id(++state.sequence); *request.mutable_expected() = expected;
    const auto bytes = request.SerializeAsString();
    require(bytes.size() <= 65536, "Excessive supervisor handle request");
    const auto deadline = Clock::now() + std::chrono::seconds(5);
    try {
        for (;;) {
            state.wait(POLLOUT, deadline);
            const auto sent = send(state.socket.value, bytes.data(), bytes.size(), MSG_DONTWAIT | MSG_NOSIGNAL);
            if (sent < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) continue;
            require(sent == static_cast<ssize_t>(bytes.size()), "Supervisor handle request failed");
            break;
        }
        for (;;) {
            state.wait(POLLIN, deadline);
            std::array<char, 65536> bytes_in{};
            alignas(cmsghdr) std::array<char, CMSG_SPACE(16 * sizeof(int)) + CMSG_SPACE(sizeof(ucred))> ancillary{};
            iovec data{bytes_in.data(), bytes_in.size()}; msghdr message{};
            message.msg_iov = &data; message.msg_iovlen = 1;
            message.msg_control = ancillary.data(); message.msg_controllen = ancillary.size();
            const auto count = recvmsg(state.socket.value, &message, MSG_CMSG_CLOEXEC | MSG_DONTWAIT);
            if (count < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) continue;
            File received; unsigned descriptors = 0, credentials = 0; ucred sender{}; bool unexpected = false;
            for (auto* h = CMSG_FIRSTHDR(&message); h; h = CMSG_NXTHDR(&message, h)) {
                if (h->cmsg_len < CMSG_LEN(0)) { unexpected = true; continue; }
                if (h->cmsg_level == SOL_SOCKET && h->cmsg_type == SCM_RIGHTS) {
                    const auto size = h->cmsg_len - CMSG_LEN(0);
                    if (size % sizeof(int)) unexpected = true;
                    for (std::size_t offset = 0; offset + sizeof(int) <= size; offset += sizeof(int)) {
                        int fd; std::memcpy(&fd, CMSG_DATA(h) + offset, sizeof(fd));
                        if (++descriptors == 1) received.reset(fd); else close(fd);
                    }
                } else if (h->cmsg_level == SOL_SOCKET && h->cmsg_type == SCM_CREDENTIALS &&
                    h->cmsg_len == CMSG_LEN(sizeof(ucred))) {
                    ++credentials; std::memcpy(&sender, CMSG_DATA(h), sizeof(sender));
                } else unexpected = true;
            }
            require(count > 0 && !(message.msg_flags & (MSG_TRUNC | MSG_CTRUNC)) && !unexpected &&
                credentials == 1 && sender.pid == state.supervisor.pid() && sender.uid == geteuid() && state.supervisor.live(),
                "Supervisor handle reply authentication failed");
            v1::AuthorityChildHandleReply reply;
            require(reply.ParseFromArray(bytes_in.data(), static_cast<int>(count)), "Malformed supervisor handle reply");
            validate_message(reply);
            require(reply.protocol_version() == 1 && reply.request_id() == request.request_id(),
                "Supervisor handle reply correlation failed");
            if (reply.has_error()) {
                require(descriptors == 0, "Supervisor error carried a descriptor");
                throw std::runtime_error("Supervisor denied broker handle: " + reply.error().message());
            }
            require(reply.has_launch() && reply.launch().SerializeAsString() == expected.SerializeAsString() && descriptors == 1,
                "Supervisor handle reply has another launch or incorrect descriptor count");
            auto result = ProcessHandle::take(std::exchange(received.value, -1));
            std::ifstream status("/proc/" + std::to_string(result.pid()) + "/status");
            std::string line; bool direct_child = false;
            while (std::getline(status, line))
                if (line.rfind("PPid:\t", 0) == 0) direct_child = line == "PPid:\t" + std::to_string(state.supervisor.pid());
            require(direct_child && result.live() && state.supervisor.live(), "Transferred process is not the retained supervisor's live child");
            return result;
        }
    } catch (...) { state.socket.reset(); throw; }
}
}
