#pragma once
#include "world_session.pb.h"
#include <chrono>
#include <filesystem>
#include <memory>

namespace spatial {
// Owns one inherited private AF_UNIX stream. Any uncertain exchange permanently fails it.
class ActorSessionClient {
public:
    ActorSessionClient(int descriptor, identity::v1::SignedCredential credential,
                       std::filesystem::path key_path, std::string expected_world,
                       std::chrono::milliseconds timeout = std::chrono::seconds(5));
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
