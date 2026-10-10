#pragma once
#include "launch.pb.h"
#include <filesystem>
#include <memory>

namespace spatial { class BrokerClient; }
namespace spatial::supervisor {
// Owns broker management above individual world launches. No actor receives this API.
class SessionCoordinator final {
public:
    SessionCoordinator(const std::filesystem::path& supervisor, const std::filesystem::path& broker_config_path,
        v1::AuthoritySupervisorConfig broker_config, int borrowed_broker_native);
    ~SessionCoordinator();
    SessionCoordinator(const SessionCoordinator&) = delete;
    SessionCoordinator& operator=(const SessionCoordinator&) = delete;
    workspace::v1::ControlOwner start_world(const std::filesystem::path& private_config,
        v1::AuthoritySupervisorConfig world_config, int borrowed_native, int borrowed_actor_listener = -1);
    const v1::AuthorityLaunchRecord& world_launch(const workspace::v1::ControlOwner&) const;
    workspace::v1::ControlOwnerState inspect_world(const workspace::v1::ControlOwner&);
    core::v1::Reply world_call(const workspace::v1::ControlOwner&, const core::v1::Request&);
    v1::AuthorityLaunchRecord stop_world(const workspace::v1::ControlOwner&);
    BrokerClient& root();
    const v1::AuthorityLaunchRecord& broker_launch() const;
    v1::AuthorityLaunchRecord stop();
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
