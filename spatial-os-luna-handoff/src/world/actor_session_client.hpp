#pragma once
#include "world_session.pb.h"
#include <chrono>
#include <atomic>
#include <filesystem>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <utility>

namespace spatial {
// A validated, matching server error. This does not prove absence of effects.
class ActorSessionRejected : public std::runtime_error {
public:
    explicit ActorSessionRejected(core::v1::Error error)
        : std::runtime_error(error.message()), error_(std::move(error)) {}
    const core::v1::Error& error() const noexcept { return error_; }
private:
    core::v1::Error error_;
};
// A checked input rejection with confirmed cancellation. Prior input may have
// occurred. Only a fresh action is permitted; the rejected action is never replayed.
class ActorInputCancelled final : public ActorSessionRejected {
public:
    using ActorSessionRejected::ActorSessionRejected;
};
// Owner-thread setup returns a weak handle. A watchdog may request shutdown
// without waiting for an actor exchange. This is not a cancellation receipt.
class ActorSessionInterrupt final {
public:
    ~ActorSessionInterrupt();
    void request() const noexcept;
    bool requested() const noexcept { return requested_.load(std::memory_order_acquire); }
    ActorSessionInterrupt(const ActorSessionInterrupt&) = delete;
    ActorSessionInterrupt& operator=(const ActorSessionInterrupt&) = delete;
private:
    friend class ActorSessionClient;
    explicit ActorSessionInterrupt(int descriptor) noexcept : descriptor_(descriptor) {}
    bool retire() const noexcept;
    const int descriptor_;
    mutable std::mutex mutex_;
    mutable bool retired_ = false;
    mutable std::atomic<bool> requested_{false};
};
// Owns one private local stream or mutually authenticated TLS connection.
// Mutating errors and uncertain exchanges permanently fail it.
class ActorSessionClient {
public:
    ActorSessionClient(int descriptor, identity::v1::SignedCredential credential,
                       std::filesystem::path key_path, std::string expected_world,
                       std::chrono::milliseconds timeout = std::chrono::seconds(5));
    // Opens one bounded TLS 1.3 connection using a private textproto configuration.
    // The application must ignore SIGPIPE before construction and throughout this lifetime.
    // The library checks this contract. It never changes signal disposition.
    ActorSessionClient(std::filesystem::path tls_config_path);
    ~ActorSessionClient();
    ActorSessionClient(const ActorSessionClient&) = delete;
    ActorSessionClient& operator=(const ActorSessionClient&) = delete;
    identity::v1::WorldSessionReply authenticate(std::string room = {},
        std::string transition_id = {}, const core::v1::Pose* pose = nullptr);
    identity::v1::WorldSessionReply activate(std::string transition_id);
    identity::v1::WorldSessionReply depart(std::uint64_t sequence, std::string transition_id);
    identity::v1::WorldSessionReply inspect();
    identity::v1::WorldSessionReply action(const core::v1::ActorActionRequest& action);
    core::v1::ActorActionReply controlled_action(const core::v1::ActorActionRequest& action);
    // The callback returns only current movement intent. Exchanges send it as a
    // bounded one-way refresh while waiting for their matching reply.
    void set_motion_refresh(std::function<std::optional<core::v1::ActorInputRequest>()> callback);
    std::weak_ptr<const ActorSessionInterrupt> interrupt_handle();
    // Revoke old watchdog handles when retaining this channel in a new host context.
    // This sends no actor action and never reconnects or replays.
    std::weak_ptr<const ActorSessionInterrupt> rotate_interrupt_handle();
    identity::v1::ActorGeometryView geometry_view();
    // Immutable identity for the trusted local owner. This grants no actor rights.
    core::v1::ActorLocalViewIdentity local_view_identity() const;
    bool failed() const noexcept;
    const std::string& expected_world() const noexcept;
    std::string expected_actor_id() const;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
