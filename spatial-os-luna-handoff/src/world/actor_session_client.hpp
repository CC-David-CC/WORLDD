#pragma once
#include "world_session.pb.h"
#include <chrono>
#include <filesystem>
#include <memory>
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
    identity::v1::ActorGeometryView geometry_view();
    bool failed() const noexcept;
    const std::string& expected_world() const noexcept;
    std::string expected_actor_id() const;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
