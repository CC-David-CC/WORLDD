#include "workspace/control_channels.hpp"
#include "workspace/control_owner.hpp"
#include "identity/participant_stream.hpp"
#include "world/store.hpp"
#include <cerrno>
#include <cstring>
#include <iostream>
#include <poll.h>
#include <vector>
#include <sys/syscall.h>

namespace spatial {
namespace {
namespace pb = workspace::v1;
using Stream = identity::ParticipantStream;
using Clock = std::chrono::steady_clock;
constexpr std::size_t packet_limit = 65536;
void require(bool okay, const char* message) {
    if (!okay) throw std::invalid_argument(message);
}
struct Received {
    std::vector<int> fds;
    ~Received() { for (int fd : fds) close(fd); }
    int take() { int fd = fds.front(); fds.clear(); return fd; }
};
}
struct ControlChannels::Impl {
    struct Owner {
        pb::ControlOwner binding;
        std::shared_ptr<Stream> channel;
        dev_t device;
        ino_t inode;
        std::unique_ptr<pb::ControlRecoveryReceipt> recovery;
    };
    int fd = -1, peer = 0, pidfd = -1;
    ControlLeases& controls;
    std::map<std::string, Owner> owners;
    std::string output;
    Clock::time_point deadline;
    Impl(int descriptor, int expected, ControlLeases& leases) : fd(descriptor), peer(expected), controls(leases) {
        try {
            require(fd >= 3 && peer > 0, "Invalid control supervisor descriptor or peer");
            sockaddr_un address{}; socklen_t size = sizeof(address);
            int type = 0; socklen_t type_size = sizeof(type);
            ucred cred{}; socklen_t cred_size = sizeof(cred);
            require(getpeername(fd, reinterpret_cast<sockaddr*>(&address), &size) == 0 && address.sun_family == AF_UNIX &&
                getsockopt(fd, SOL_SOCKET, SO_TYPE, &type, &type_size) == 0 && type == SOCK_SEQPACKET &&
                getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &cred, &cred_size) == 0 && cred_size == sizeof(cred) &&
                cred.pid == peer && cred.uid == geteuid(), "Unexpected control supervisor kernel peer");
            pidfd = static_cast<int>(syscall(SYS_pidfd_open, peer, 0));
            const int flags = fcntl(fd, F_GETFL), enabled = 1;
            require(pidfd >= 0 && flags >= 0 && fcntl(fd, F_SETFL, flags | O_NONBLOCK) == 0 &&
                fcntl(fd, F_SETFD, FD_CLOEXEC) == 0 &&
                setsockopt(fd, SOL_SOCKET, SO_PASSCRED, &enabled, sizeof(enabled)) == 0,
                "Cannot configure control supervisor channel");
        } catch (...) { close_all(); throw; }
    }
    ~Impl() { close_all(); }
    void close_all() {
        if (fd >= 3) close(fd);
        if (pidfd >= 0) close(pidfd);
        fd = pidfd = -1;
    }
    void fence(const std::string& key, Owner& owner) {
        controls.fence(key);
        // shutdown also disables descriptors retained by an in-flight dispatch.
        if (owner.channel) shutdown(owner.channel->descriptor(), SHUT_RDWR);
        owner.channel.reset();
    }
    void fail() {
        for (auto& [key, owner] : owners) fence(key, owner);
        output.clear(); close_all();
    }
    pb::ControlOwnerState state(const std::string& key, const Owner& owner) {
        pb::ControlOwnerState result;
        *result.mutable_owner() = owner.binding;
        controls.inspect(key, result);
        if (owner.recovery) *result.mutable_recovery() = *owner.recovery;
        return result;
    }
    pb::ControlOwnerState dispatch(const pb::ControlSupervisorRequest& request, Received& received) {
        validate_message(request);
        if (request.protocol_version() != 1) throw WorldError(wire::UNSUPPORTED_VERSION, "Unsupported control supervisor protocol");
        const pb::ControlOwner* binding = nullptr;
        if (request.has_register_()) binding = &request.register_();
        else if (request.has_inspect()) binding = &request.inspect();
        else if (request.has_fence()) binding = &request.fence();
        else if (request.has_recover()) binding = &request.recover().owner();
        else throw WorldError(wire::UNKNOWN_METHOD, "No control supervisor operation");
        validate_control_owner(*binding);
        const auto key = binding->SerializeAsString();
        if (request.has_register_()) {
            require(received.fds.size() == 1, "Register requires exactly one descriptor");
            if (owners.size() >= 256) throw WorldError(wire::RESOURCE_EXHAUSTED, "Control owner limit reached");
            const auto& launch = binding->launch();
            struct stat info{};
            require(fstat(received.fds.front(), &info) == 0, "Cannot inspect admitted channel");
            for (const auto& [existing_key, owner] : owners) {
                (void)existing_key;
                const auto& previous = owner.binding.launch();
                if (previous.launch_id() == launch.launch_id() || previous.authorization_id() == launch.authorization_id() ||
                    (owner.binding.issuer_id() == binding->issuer_id() && previous.participant_id() == launch.participant_id() &&
                        previous.generation() == launch.generation()) || (owner.device == info.st_dev && owner.inode == info.st_ino))
                    throw WorldError(wire::ALREADY_EXISTS, "Control launch or channel already registered");
            }
            // The new-expression allocates before take(). Stream owns the fd if construction starts.
            // shared_ptr deletes the constructed stream if its control-block allocation fails.
            auto channel = std::shared_ptr<Stream>(new Stream(received.take(), peer));
            auto [position, inserted] = owners.emplace(key, Owner{*binding, std::move(channel), info.st_dev, info.st_ino, {}});
            require(inserted, "Control owner already registered");
            return state(key, position->second);
        }
        require(received.fds.empty(), "Control query cannot transfer descriptors");
        const auto found = owners.find(key);
        if (found == owners.end()) throw WorldError(wire::NOT_FOUND, "Exact control owner does not exist");
        if (request.has_fence()) fence(key, found->second);
        if (request.has_recover()) {
            validate_control_recovery(request.recover());
            auto& owner = found->second;
            require(!owner.channel, "Control recovery requires a fenced channel");
            if (owner.recovery) {
                require(owner.recovery->cleanup().SerializeAsString() == request.recover().SerializeAsString(),
                    "Control recovery retry changed its cleanup receipt");
            } else {
                auto receipt = std::make_unique<pb::ControlRecoveryReceipt>();
                *receipt->mutable_cleanup() = request.recover();
                controls.recover(key, *receipt);
                owner.recovery = std::move(receipt); // Cannot allocate after reservation erasure.
            }
        }
        return state(key, found->second);
    }
    void poll_owner_loss() {
        // Bound work by the 256 admitted owners. Never consume or dispatch their
        // ordinary requests from an issuer progress callback.
        for (auto& [key, owner] : owners) {
            auto channel = owner.channel;
            if (!channel) continue;
            pollfd socket{channel->descriptor(), POLLIN | POLLRDHUP, 0};
            const auto status = poll(&socket, 1, 0);
            if (status < 0 && errno == EINTR) continue;
            bool lost = status < 0 || (socket.revents & (POLLERR | POLLHUP | POLLRDHUP | POLLNVAL));
            if (!lost && (socket.revents & POLLIN)) {
                char byte;
                const auto count = recv(socket.fd, &byte, 1, MSG_PEEK | MSG_DONTWAIT);
                lost = count == 0 || (count < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR);
            }
            // The local shared_ptr keeps a borrowed stream alive. Existing fence
            // semantics shut it down and retain every unresolved reservation.
            if (lost) fence(key, owner);
        }
    }
    void administrative() {
        poll_owner_loss();
        if (fd < 0) return;
        pollfd live{pidfd, POLLIN, 0};
        const auto status = poll(&live, 1, 0);
        if (status < 0 && errno == EINTR) return;
        require(status >= 0 && !live.revents, "Control supervisor exited");
        if (!output.empty()) {
            require(Clock::now() < deadline, "Control supervisor response expired");
            const auto count = send(fd, output.data(), output.size(), MSG_NOSIGNAL | MSG_DONTWAIT);
            if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) return;
            require(count == static_cast<ssize_t>(output.size()), "Control supervisor response failed");
            output.clear();
        }
        std::array<char,packet_limit> bytes{};
        alignas(cmsghdr) std::array<char,CMSG_SPACE(sizeof(int)*16)+CMSG_SPACE(sizeof(ucred))> ancillary{};
        iovec vector{bytes.data(), bytes.size()}; msghdr message{};
        message.msg_iov = &vector; message.msg_iovlen = 1;
        message.msg_control = ancillary.data(); message.msg_controllen = ancillary.size();
        const auto count = recvmsg(fd, &message, MSG_DONTWAIT | MSG_CMSG_CLOEXEC);
        if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) return;
        require(count >= 0, "Control supervisor request failed");
        Received received; bool unexpected = false; unsigned credentials = 0;
        ucred sender{};
        for (auto* header = CMSG_FIRSTHDR(&message); header; header = CMSG_NXTHDR(&message, header)) {
            require(header->cmsg_len >= CMSG_LEN(0), "Invalid ancillary header");
            if (header->cmsg_level == SOL_SOCKET && header->cmsg_type == SCM_RIGHTS) {
                const auto size = header->cmsg_len-CMSG_LEN(0);
                unexpected |= size % sizeof(int) != 0;
                for (std::size_t offset = 0; offset+sizeof(int) <= size; offset += sizeof(int)) {
                    int descriptor; std::memcpy(&descriptor, CMSG_DATA(header)+offset, sizeof(descriptor));
                    received.fds.push_back(descriptor);
                }
            } else if (header->cmsg_level == SOL_SOCKET && header->cmsg_type == SCM_CREDENTIALS && header->cmsg_len == CMSG_LEN(sizeof(ucred))) {
                ++credentials; std::memcpy(&sender, CMSG_DATA(header), sizeof(sender));
            } else unexpected = true;
        }
        require(count > 0 && credentials == 1 && sender.pid == peer && sender.uid == geteuid(),
            "Control supervisor sender disconnected or changed");
        pb::ControlSupervisorRequest request; pb::ControlSupervisorReply reply; reply.set_protocol_version(1);
        try {
            require(!(message.msg_flags & MSG_TRUNC), "Truncated administrative payload");
            require(request.ParseFromArray(bytes.data(), static_cast<int>(count)), "Malformed control supervisor protobuf");
            reply.set_request_id(request.request_id());
            require(!(message.msg_flags & MSG_CTRUNC) && !unexpected, "Truncated or unexpected administrative packet");
            *reply.mutable_state() = dispatch(request, received);
        } catch (const WorldError& error) {
            reply.mutable_error()->set_code(error.code); reply.mutable_error()->set_message(error.what());
        } catch (const std::invalid_argument& error) {
            reply.mutable_error()->set_code(wire::INVALID_ARGUMENT); reply.mutable_error()->set_message(error.what());
        } catch (const std::exception&) {
            reply.mutable_error()->set_code(wire::INTERNAL_ERROR); reply.mutable_error()->set_message("Control registration failed");
        }
        output = reply.SerializeAsString();
        require(output.size() <= packet_limit, "Control owner response exceeds packet limit");
        deadline = Clock::now()+std::chrono::seconds(5);
    }
};
ControlChannels::ControlChannels(int fd, int peer, ControlLeases& controls) : impl_(std::make_unique<Impl>(fd, peer, controls)) {}
ControlChannels::~ControlChannels() = default;
void ControlChannels::administrative() {
    try { impl_->administrative(); }
    catch (const std::exception& error) {
        std::cerr << "control supervisor channel fenced: " << error.what() << '\n';
        impl_->fail();
    }
}
void ControlChannels::serve(const Dispatch& dispatch) {
    administrative();
    for (auto& [key, owner] : impl_->owners) {
        auto channel = owner.channel;
        if (!channel) continue;
        try {
            channel->flush();
            if (channel->pending_output()) continue;
            const auto bytes = channel->receive();
            if (!bytes) continue;
            pb::Request request; pb::Reply reply;
            if (!request.ParseFromString(*bytes)) {
                reply.set_protocol_version(1); reply.mutable_error()->set_code(wire::INVALID_ARGUMENT);
                reply.mutable_error()->set_message("Malformed broker protobuf payload");
            } else reply = dispatch(request, key, owner.binding.native().server_id());
            if (owner.channel) { channel->send(reply.SerializeAsString()); channel->flush(); }
        } catch (const std::exception& error) {
            std::cerr << "control owner channel fenced: " << error.what() << '\n';
            impl_->fence(key, owner);
        }
    }
}
void ControlChannels::revoked(const std::string& key, const ControlLeases::Lease& lease) {
    auto& owner = impl_->owners.at(key);
    if (!owner.channel) return;
    try {
        pb::Reply event; event.set_protocol_version(1); *event.mutable_control_revoked() = lease;
        owner.channel->send(event.SerializeAsString()); owner.channel->flush();
    } catch (const std::exception&) { impl_->fence(key, owner); }
}
bool ControlChannels::pending_output() const {
    for (const auto& [key, owner] : impl_->owners) {
        (void)key;
        if (owner.channel && owner.channel->pending_output()) return true;
    }
    return false;
}
}
