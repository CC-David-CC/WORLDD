#include "world/actor_session_client.hpp"
#include "core/protocol.hpp"
#include "identity/keys.hpp"

#include <array>
#include <cerrno>
#include <fcntl.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <stdexcept>
#include <utility>

namespace spatial {
namespace {
using Clock = std::chrono::steady_clock;
namespace pb = identity::v1;

void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

std::string hex(const std::string& value) {
    constexpr char digits[] = "0123456789abcdef";
    std::string result;
    result.reserve(value.size() * 2);
    for (const unsigned char byte : value) {
        result.push_back(digits[byte >> 4]);
        result.push_back(digits[byte & 15]);
    }
    return result;
}

class OwnedDescriptor {
public:
    explicit OwnedDescriptor(int fd) noexcept : fd_(fd) {}
    ~OwnedDescriptor() { reset(); }
    OwnedDescriptor(const OwnedDescriptor&) = delete;
    OwnedDescriptor& operator=(const OwnedDescriptor&) = delete;
    int get() const noexcept { return fd_; }
    void reset(int fd = -1) noexcept {
        if (fd_ >= 0) close(fd_);
        fd_ = fd;
    }
private:
    int fd_;
};

enum class Lifecycle { New, Prepared, Active, Departed, Failed };
} // namespace

struct ActorSessionClient::Impl {
    // Declare ownership before key loading so every constructor exception closes the fd.
    OwnedDescriptor channel;
    pb::SignedCredential credential;
    identity::KeyPair key;
    pb::ActorCredential claims;
    std::string world;
    std::string audience;
    std::chrono::milliseconds timeout;
    std::uint64_t next_id = 1;
    Lifecycle state = Lifecycle::New;
    std::string prepared_transition;

    Impl(int descriptor, pb::SignedCredential signed_credential,
         std::filesystem::path key_path, std::string expected_world,
         std::chrono::milliseconds io_timeout)
        : channel(descriptor), credential(std::move(signed_credential)),
          key(identity::KeyPair::load(key_path)), world(std::move(expected_world)),
          timeout(io_timeout) {
        validate_configuration();
    }

    void validate_configuration() {
        const int fd = channel.get();
        require(fd > 2 && timeout >= std::chrono::milliseconds(50) &&
                    timeout <= std::chrono::seconds(60),
                "Invalid actor client descriptor or timeout");
        sockaddr_un local{}, peer{};
        socklen_t local_size = sizeof(local), peer_size = sizeof(peer);
        int type = 0;
        socklen_t type_size = sizeof(type);
        ucred peer_credentials{};
        socklen_t credentials_size = sizeof(peer_credentials);
        require(getsockname(fd, reinterpret_cast<sockaddr*>(&local), &local_size) == 0 &&
                    local.sun_family == AF_UNIX && local_size == sizeof(sa_family_t) &&
                    getpeername(fd, reinterpret_cast<sockaddr*>(&peer), &peer_size) == 0 &&
                    peer.sun_family == AF_UNIX && peer_size == sizeof(sa_family_t) &&
                    getsockopt(fd, SOL_SOCKET, SO_TYPE, &type, &type_size) == 0 &&
                    type == SOCK_STREAM &&
                    getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &peer_credentials,
                               &credentials_size) == 0 &&
                    credentials_size == sizeof(peer_credentials) &&
                    peer_credentials.uid == geteuid(),
                "Actor channel is not a private local stream");
        const int flags = fcntl(fd, F_GETFD);
        require(flags >= 0 && fcntl(fd, F_SETFD, flags | FD_CLOEXEC) == 0,
                "Cannot protect actor channel descriptor");
        const int status = fcntl(fd, F_GETFL);
        require(status >= 0 && fcntl(fd, F_SETFL, status | O_NONBLOCK) == 0,
                "Cannot make actor channel nonblocking");

        require(!credential.content().payload().empty() &&
                    credential.proof().purpose() == pb::ISSUER_CREDENTIAL &&
                    credential.content().purpose() == pb::ISSUER_CREDENTIAL &&
                    credential.content().payload().size() <= identity::max_signed_payload &&
                    claims.ParseFromString(credential.content().payload()),
                "Invalid signed actor credential");
        validate_message(claims);
        require(claims.protocol_version() == 1 && claims.public_key() == key.public_key() &&
                    claims.actor_id().size() == 32 && claims.issuer_id().size() == 32 &&
                    credential.proof().public_key().size() == 32 &&
                    credential.proof().signature().size() == 64 && !world.empty(),
                "Actor credential does not match local key or expected identity");
        audience = "spatial.world-session/v1:" + world;
    }

    void fail() noexcept {
        state = Lifecycle::Failed;
        prepared_transition.clear();
        channel.reset();
    }

    void require_state(Lifecycle expected, const char* message) const {
        require(state == expected && channel.get() >= 0, message);
    }

    void transfer(void* buffer, std::size_t size, bool writing, Clock::time_point deadline) {
        auto* bytes = static_cast<char*>(buffer);
        std::size_t offset = 0;
        while (offset < size) {
            const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(
                deadline - Clock::now()).count();
            require(remaining > 0, "Actor channel deadline expired");
            pollfd descriptor{channel.get(), static_cast<short>(writing ? POLLOUT : POLLIN), 0};
            const int ready = poll(&descriptor, 1, static_cast<int>(remaining));
            if (ready < 0 && errno == EINTR) continue;
            // A peer may close after queuing a complete reply. Drain readable bytes.
            require(ready > 0 && !(descriptor.revents & (POLLERR | POLLNVAL)) &&
                        (!(descriptor.revents & POLLHUP) ||
                         (!writing && (descriptor.revents & POLLIN))),
                    "Actor channel transport lost or timed out");
            const auto count = writing
                ? send(channel.get(), bytes + offset, size - offset, MSG_NOSIGNAL | MSG_DONTWAIT)
                : recv(channel.get(), bytes + offset, size - offset, MSG_DONTWAIT);
            if (count < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) continue;
            require(count > 0, "Actor channel closed during exchange");
            offset += static_cast<std::size_t>(count);
        }
    }

    pb::WorldSessionReply exchange(pb::WorldSessionRequest request,
                                   pb::WorldSessionReply::OutcomeCase expected) {
        require(state != Lifecycle::Failed && channel.get() >= 0,
                "Actor client is failed");
        const std::uint64_t request_id = next_id++;
        request.set_protocol_version(1);
        request.set_request_id(request_id);
        std::string body;
        require(request.SerializeToString(&body) && body.size() <= max_frame_bytes,
                "Invalid or excessive actor request");
        std::uint32_t frame_size = static_cast<std::uint32_t>(body.size());
        std::array<char, 4> header{
            static_cast<char>(frame_size >> 24), static_cast<char>(frame_size >> 16),
            static_cast<char>(frame_size >> 8), static_cast<char>(frame_size)};
        const auto deadline = Clock::now() + timeout;
        try {
            transfer(header.data(), header.size(), true, deadline);
            transfer(body.data(), body.size(), true, deadline);
            transfer(header.data(), header.size(), false, deadline);
            frame_size = (std::uint32_t(static_cast<unsigned char>(header[0])) << 24) |
                         (std::uint32_t(static_cast<unsigned char>(header[1])) << 16) |
                         (std::uint32_t(static_cast<unsigned char>(header[2])) << 8) |
                         std::uint32_t(static_cast<unsigned char>(header[3]));
            require(frame_size > 0 && frame_size <= max_frame_bytes,
                    "Invalid actor reply frame length");
            body.resize(frame_size);
            transfer(body.data(), body.size(), false, deadline);
            pb::WorldSessionReply reply;
            require(reply.ParseFromString(body) && reply.protocol_version() == 1 &&
                        reply.request_id() == request_id,
                    "Invalid actor reply envelope");
            validate_message(reply);
            require(!reply.has_error(), "World rejected actor session request");
            require(reply.outcome_case() == expected, "Unexpected actor session reply outcome");
            return reply;
        } catch (...) {
            fail();
            throw;
        }
    }

    std::string expected_actor_id() const {
        return "identity:" + hex(claims.issuer_id()) + ":" + hex(claims.actor_id());
    }

    void validate_opened(const pb::WorldSessionOpened& opened) {
        require(opened.world_id() == world && opened.actor_id() == expected_actor_id(),
                "Opened actor identity mismatch");
    }

    void validate_challenge(const pb::ChallengeReply& challenge) {
        pb::AuthenticationChallenge context;
        require(challenge.nonce().size() == 32 &&
                    challenge.signing_request().purpose() == pb::ACTOR_AUTHENTICATION &&
                    context.ParseFromString(challenge.signing_request().payload()),
                "Invalid actor challenge payload");
        validate_message(context);
        const auto& request = context.request();
        require(context.protocol_version() == 1 && context.nonce() == challenge.nonce() &&
                    context.issuer_id() == claims.issuer_id() && context.incarnation().size() == 32 &&
                    context.lifetime_ms() >= 50 && context.lifetime_ms() <= 60000 &&
                    request.credential().SerializeAsString() == credential.SerializeAsString() &&
                    request.audience() == audience && request.channel_binding().size() == 32,
                "Actor challenge context mismatch");
    }

    pb::WorldSessionReply authenticate(std::string room, std::string transition,
                                       const core::v1::Pose* pose) {
        require_state(Lifecycle::New, "Actor session is not new");
        if (!transition.empty()) require(transition.size() == 32,
                                         "Transition identity must be 32 bytes");
        pb::WorldSessionRequest begin;
        auto* operation = begin.mutable_begin();
        *operation->mutable_credential() = credential;
        if (!room.empty()) operation->set_room(room);
        if (pose != nullptr) *operation->mutable_pose() = *pose;
        if (!transition.empty()) operation->set_transition_id(transition);
        try {
            const auto challenge = exchange(std::move(begin), pb::WorldSessionReply::kChallenge).challenge();
            validate_challenge(challenge);
            pb::WorldSessionRequest complete;
            complete.mutable_complete()->set_nonce(challenge.nonce());
            *complete.mutable_complete()->mutable_proof() = key.sign(challenge.signing_request());
            auto reply = exchange(std::move(complete), transition.empty()
                ? pb::WorldSessionReply::kOpened : pb::WorldSessionReply::kPrepared);
            if (transition.empty()) {
                validate_opened(reply.opened());
                state = Lifecycle::Active;
            } else {
                const auto& prepared = reply.prepared();
                validate_opened(prepared.actor());
                require(prepared.transition_id() == transition,
                        "Prepared transition identity mismatch");
                prepared_transition = transition;
                state = Lifecycle::Prepared;
            }
            return reply;
        } catch (...) {
            fail();
            throw;
        }
    }
};

ActorSessionClient::ActorSessionClient(int descriptor, pb::SignedCredential credential,
                                       std::filesystem::path key_path, std::string world,
                                       std::chrono::milliseconds timeout)
    : impl_(std::make_unique<Impl>(descriptor, std::move(credential),
                                  std::move(key_path), std::move(world), timeout)) {}
ActorSessionClient::~ActorSessionClient() = default;

pb::WorldSessionReply ActorSessionClient::authenticate(
    std::string room, std::string transition, const core::v1::Pose* pose) {
    return impl_->authenticate(std::move(room), std::move(transition), pose);
}

pb::WorldSessionReply ActorSessionClient::activate(std::string transition) {
    impl_->require_state(Lifecycle::Prepared, "Actor session is not prepared");
    require(transition.size() == 32 && transition == impl_->prepared_transition,
            "No matching local preparation");
    pb::WorldSessionRequest request;
    request.mutable_activate()->set_transition_id(transition);
    try {
        auto reply = impl_->exchange(std::move(request), pb::WorldSessionReply::kOpened);
        impl_->validate_opened(reply.opened());
        impl_->prepared_transition.clear();
        impl_->state = Lifecycle::Active;
        return reply;
    } catch (...) {
        impl_->fail();
        throw;
    }
}

pb::WorldSessionReply ActorSessionClient::depart(std::uint64_t sequence, std::string transition) {
    impl_->require_state(Lifecycle::Active, "Actor session is inactive");
    require(transition.size() == 32, "Invalid departure context");
    pb::WorldSessionRequest request;
    request.mutable_depart()->set_seq(sequence);
    request.mutable_depart()->set_transition_id(transition);
    try {
        auto reply = impl_->exchange(std::move(request), pb::WorldSessionReply::kDeparted);
        const auto& receipt = reply.departed();
        require(receipt.transition_id() == transition && receipt.world_id() == impl_->world &&
                    receipt.actor_id() == impl_->expected_actor_id(),
                "Departure receipt identity mismatch");
        impl_->state = Lifecycle::Departed;
        return reply;
    } catch (...) {
        impl_->fail();
        throw;
    }
}

pb::WorldSessionReply ActorSessionClient::inspect() {
    impl_->require_state(Lifecycle::Active, "Actor session is inactive");
    pb::WorldSessionRequest request;
    request.mutable_inspect();
    return impl_->exchange(std::move(request), pb::WorldSessionReply::kState);
}

pb::WorldSessionReply ActorSessionClient::action(const core::v1::ActorActionRequest& action) {
    impl_->require_state(Lifecycle::Active, "Actor session is inactive");
    pb::WorldSessionRequest request;
    *request.mutable_action() = action;
    return impl_->exchange(std::move(request), pb::WorldSessionReply::kAction);
}

bool ActorSessionClient::failed() const noexcept {
    return impl_->state == Lifecycle::Failed;
}
} // namespace spatial
