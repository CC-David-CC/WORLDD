#include "workspace/storage_channel.hpp"
#include "core/protocol.hpp"
#include "identity/tls_support.hpp"
#include "platform/process_handle.hpp"
#include <algorithm>
#include <array>
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <poll.h>
#include <stdexcept>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <unistd.h>

namespace spatial {
namespace {
using Fd = identity::tls_detail::Fd;
void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
}
struct StorageChannel::Impl {
    Fd socket, peer;
    const uid_t uid;
    std::optional<pid_t> pid;
    Impl(int descriptor, uid_t peer_uid, std::optional<pid_t> peer_pid, int borrowed_peer_pidfd)
        : socket(descriptor), uid(peer_uid), pid(peer_pid) {
        require(descriptor >= 3, "Storage channel requires a private inherited descriptor");
        int type = 0, domain = 0; socklen_t size = sizeof(int);
        require(getsockopt(descriptor, SOL_SOCKET, SO_TYPE, &type, &size) == 0 && type == SOCK_STREAM,
                "Storage channel requires a stream socket");
        size = sizeof(int);
        require(getsockopt(descriptor, SOL_SOCKET, SO_DOMAIN, &domain, &size) == 0 && domain == AF_UNIX,
                "Storage channel requires a Unix socket");
        const int one = 1;
        require(setsockopt(descriptor, SOL_SOCKET, SO_PASSCRED, &one, sizeof(one)) == 0,
                "Cannot enable storage sender credentials");
        const int flags = fcntl(descriptor, F_GETFL), inherited = fcntl(descriptor, F_GETFD);
        require(flags >= 0 && inherited >= 0 && fcntl(descriptor, F_SETFL, flags | O_NONBLOCK) == 0 &&
                fcntl(descriptor, F_SETFD, inherited | FD_CLOEXEC) == 0,
                "Cannot protect private storage descriptor");
        if (borrowed_peer_pidfd >= 0) {
            require(pid.has_value(), "Retained storage peer requires its process identity");
            auto handle = ProcessHandle::duplicate(borrowed_peer_pidfd, *pid);
            peer.reset(handle.release()); alive();
        } else if (pid) pin(*pid);
    }
    void pin(pid_t value) {
        require(value > 1, "Invalid storage peer identity");
        peer.reset(static_cast<int>(syscall(SYS_pidfd_open, value, 0)));
        require(peer.value >= 0, "Cannot pin storage peer process");
        pid = value; alive();
    }
    void alive() const {
        if (peer.value < 0) return;
        pollfd target{peer.value, POLLIN, 0};
        int result;
        do { result = poll(&target, 1, 0); } while (result < 0 && errno == EINTR);
        require(result == 0, "Storage peer process is unavailable");
    }
    void wait(short events, Clock::time_point deadline, const std::function<void()>& tick) {
        alive();
        if (tick) tick();
        require(Clock::now() < deadline, "Storage channel deadline expired; outcome may be uncertain");
        pollfd targets[2]{{socket.value, events, 0}, {peer.value, POLLIN, 0}};
        const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(deadline-Clock::now()).count();
        const int timeout = deadline == Clock::time_point::max() ? 20 : static_cast<int>(std::clamp<std::int64_t>(remaining, 1, 20));
        const int result = poll(targets, peer.value >= 0 ? 2 : 1, timeout);
        require(result >= 0 || errno == EINTR, "Cannot poll storage channel");
        require(!(targets[0].revents & POLLNVAL) && !targets[1].revents, "Storage channel peer is unavailable");
    }
    ssize_t read(char* bytes, std::size_t size) {
        // Close unwanted rights before rejecting them, including truncated ancillary data.
        alignas(cmsghdr) std::array<char, CMSG_SPACE(sizeof(ucred))+CMSG_SPACE(16*sizeof(int))> control{};
        iovec vector{bytes, size}; msghdr message{};
        message.msg_iov = &vector; message.msg_iovlen = 1;
        message.msg_control = control.data(); message.msg_controllen = control.size();
        const auto count = recvmsg(socket.value, &message, MSG_CMSG_CLOEXEC);
        if (count <= 0) return count;
        std::optional<ucred> sender; bool rejected = (message.msg_flags & (MSG_CTRUNC | MSG_TRUNC)) != 0;
        for (auto* header = CMSG_FIRSTHDR(&message); header; header = CMSG_NXTHDR(&message, header)) {
            if (header->cmsg_level == SOL_SOCKET && header->cmsg_type == SCM_RIGHTS) {
                const auto length = header->cmsg_len >= CMSG_LEN(0) ? header->cmsg_len-CMSG_LEN(0) : 0;
                for (std::size_t at = 0; at+sizeof(int) <= length; at += sizeof(int)) {
                    int received = -1; std::memcpy(&received, CMSG_DATA(header)+at, sizeof(received));
                    if (received >= 0) close(received);
                }
                rejected = true;
            } else if (header->cmsg_level == SOL_SOCKET && header->cmsg_type == SCM_CREDENTIALS &&
                       header->cmsg_len == CMSG_LEN(sizeof(ucred)) && !sender) {
                ucred value{}; std::memcpy(&value, CMSG_DATA(header), sizeof(value)); sender = value;
            } else rejected = true;
        }
        require(!rejected && sender && sender->uid == uid && sender->pid > 1 &&
                (!pid || sender->pid == *pid), "Storage sender identity rejected");
        if (!pid) pin(sender->pid);
        alive(); return count;
    }
};
StorageChannel::StorageChannel(int descriptor, uid_t uid, std::optional<pid_t> pid, int borrowed_peer_pidfd)
    : impl_(std::make_unique<Impl>(descriptor, uid, pid, borrowed_peer_pidfd)) {}
StorageChannel::~StorageChannel() = default;
pid_t StorageChannel::peer_pid() const { return impl_->pid.value_or(-1); }
void StorageChannel::check_alive() const { impl_->alive(); }
void StorageChannel::send(const std::string& payload, Clock::time_point deadline, const std::function<void()>& tick) {
    require(!payload.empty() && payload.size() <= max_frame_bytes, "Storage frame exceeds bounds");
    const auto size = static_cast<std::uint32_t>(payload.size());
    std::string bytes;
    for (int shift : {24, 16, 8, 0}) bytes += static_cast<char>(size >> shift);
    bytes += payload; std::size_t used = 0;
    while (used < bytes.size()) {
        impl_->alive();
        if (tick) tick();
        require(Clock::now() < deadline, "Storage channel deadline expired; outcome may be uncertain");
        const auto count = ::send(impl_->socket.value, bytes.data()+used, bytes.size()-used, MSG_NOSIGNAL);
        if (count > 0) { used += static_cast<std::size_t>(count); continue; }
        require(count < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK), "Storage channel write failed");
        impl_->wait(POLLOUT, deadline, tick);
    }
}
std::optional<std::string> StorageChannel::receive(Clock::time_point deadline, bool idle, const std::function<void()>& tick) {
    std::array<char, 4> prefix{}; std::size_t used = 0;
    auto limit = idle ? Clock::time_point::max() : deadline;
    while (used < prefix.size()) {
        impl_->alive();
        if (tick) tick();
        require(Clock::now() < limit, "Storage prefix deadline expired");
        const auto count = impl_->read(prefix.data()+used, prefix.size()-used);
        if (count > 0) {
            if (!used && idle) limit = Clock::now()+std::chrono::seconds(5);
            used += static_cast<std::size_t>(count); continue;
        }
        if (!count) { require(!used, "Truncated storage prefix"); return std::nullopt; }
        require(errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK, "Storage prefix read failed");
        impl_->wait(POLLIN, limit, tick);
    }
    std::uint32_t size = 0;
    for (const auto byte : prefix) size = (size << 8) | static_cast<unsigned char>(byte);
    require(size > 0 && size <= max_frame_bytes, "Storage frame exceeds bounds");
    std::string bytes(size, '\0'); used = 0;
    while (used < bytes.size()) {
        impl_->alive();
        if (tick) tick();
        require(Clock::now() < limit, "Storage payload deadline expired");
        const auto count = impl_->read(bytes.data()+used, bytes.size()-used);
        if (count > 0) { used += static_cast<std::size_t>(count); continue; }
        require(count < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK), "Truncated storage payload");
        impl_->wait(POLLIN, limit, tick);
    }
    return bytes;
}
}
