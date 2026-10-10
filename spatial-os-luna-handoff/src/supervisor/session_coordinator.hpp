#pragma once
#include "launch.pb.h"
#include <filesystem>
#include <memory>

namespace spatial { class BrokerClient; }
namespace spatial::supervisor {
// Owns broker management above individual world launches. No actor receives this API.
// The calling process must ignore SIGPIPE, as required by its issuer transport.
class SessionCoordinator final {
public:
    SessionCoordinator(const std::filesystem::path& supervisor, const std::filesystem::path& broker_config_path,
        v1::AuthoritySupervisorConfig broker_config, int borrowed_broker_native);
    ~SessionCoordinator();
    SessionCoordinator(const SessionCoordinator&) = delete;
    SessionCoordinator& operator=(const SessionCoordinator&) = delete;
    workspace::v1::ControlOwner start_world(const std::filesystem::path& private_config,
        v1::AuthoritySupervisorConfig world_config, int borrowed_native, int borrowed_actor_listener = -1,
        unsigned local_actor_channels = 0);
    // Transfers one actor-only endpoint to the caller, which must close it. No caller FD is admitted.
    int take_actor_channel(const workspace::v1::ControlOwner&, unsigned index);
    const v1::AuthorityLaunchRecord& world_launch(const workspace::v1::ControlOwner&) const;
    workspace::v1::ControlOwnerState inspect_world(const workspace::v1::ControlOwner&);
    core::v1::Reply world_call(const workspace::v1::ControlOwner&, const core::v1::Request&);
    v1::AuthorityLaunchRecord stop_world(const workspace::v1::ControlOwner&);
    workspace::v1::ControlOwnerState recover_world(const workspace::v1::ControlOwner&);
    identity::v1::AuthorityRetirementState retire_world(const workspace::v1::ControlOwner&);
    BrokerClient& root();
    const v1::AuthorityLaunchRecord& broker_launch() const;
    v1::AuthorityLaunchRecord stop();
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
