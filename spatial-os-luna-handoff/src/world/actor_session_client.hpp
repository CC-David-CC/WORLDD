#pragma once
#include "world_session.pb.h"
#include <chrono>
#include <filesystem>
#include <memory>

namespace spatial {
// Owns one private local stream or mutually authenticated TLS connection.
// Any uncertain exchange permanently fails it.
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
    bool failed() const noexcept;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
