#pragma once
#include "launch.pb.h"
#include <array>
#include <filesystem>
#include <memory>
#include <optional>
#include <vector>

namespace spatial::supervisor {
// Owns one launch record and its live kernel/native handles.
// Reopening an unresolved record does not reconstruct handles or permit execution.
class AuthorityLaunch final {
public:
    explicit AuthorityLaunch(const std::filesystem::path& private_directory);
    ~AuthorityLaunch();
    AuthorityLaunch(const AuthorityLaunch&) = delete;
    AuthorityLaunch& operator=(const AuthorityLaunch&) = delete;
    v1::AuthorityLaunchView prepare(const std::filesystem::path& delegated_root,
        const v1::AuthorityLaunchBinding& binding, int sealed_native_config);
    v1::AuthorityLaunchRecord start(const v1::AuthorityProcessLaunch& launch,
        const std::array<int, 3>& streams, const std::vector<int>& inherited = {});
    // The coordinator retains the real service connection; callers cannot supply its identity.
    void attach(const v1::AuthorityParticipantAttach& request);
    std::optional<identity::v1::ParticipantUpdate> poll_participant();
    v1::AuthorityLaunchRecord authorize(const v1::AuthorityLaunchAuthorize& request);
    void submit_participant(identity::v1::ParticipantCommand command);
    identity::v1::AuthorityParticipantRuntimeConfig prepare_channel(std::uint32_t target_fd);
    // The supervisor event loop pumps this channel. Failure requires checked cleanup.
    void pump_channel();
    // True only after an explicit release and exit of the retained direct child.
    bool child_completed() const;
    v1::AuthorityLaunchRecord inspect() const;
    // Registration identity from retained live ownership. This does not prove activation or cleanup.
    workspace::v1::ControlOwner control_owner() const;
    // Private coordinator query. Caller owns the duplicate; serialized identity is insufficient.
    int duplicate_broker_pidfd(const v1::AuthorityLaunchRecord& expected) const;
    int duplicate_world_pidfd(const v1::AuthorityLaunchRecord& expected) const;
    v1::AuthorityLaunchRecord stop();
    // Fence the exact authorized generation. This does not retire issuer obligations.
    v1::AuthorityLaunchRecord fence();
    // Exceptional shutdown only. Checked graceful release needs no fence.
    void fence_after_failure();
private:
    friend class SessionCoordinator;
    friend class SessionBroker;
    // Only the retained coordinator may persist its verified dependent cleanup.
    v1::AuthorityDependentCleanup record_control_recovery(const v1::AuthorityLaunchRecord& expected_world,
        const v1::AuthorityLaunchRecord& retained_broker, const workspace::v1::ControlOwnerState& recovered);
    v1::AuthorityDependentCleanup record_stopped_broker_recovery(const v1::AuthorityLaunchRecord& expected_world,
        const v1::AuthorityLaunchRecord& checked_broker);
    v1::AuthorityDependentCleanup persist_dependent_cleanup(const v1::AuthorityDependentCleanup& value);
    identity::v1::AuthorityRetirementState retire_control_recovery(const v1::AuthorityDependentCleanup& expected,
        const v1::AuthorityLaunchAuthorize& original_config, bool checked_supervisor_failure);
    identity::v1::AuthorityRetirementState retire_broker_cleanup(const v1::AuthorityLaunchRecord& expected,
        const v1::AuthorityLaunchAuthorize& original_config, bool checked_supervisor_failure);
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
