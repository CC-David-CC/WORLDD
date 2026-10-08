#pragma once
#include "core/protocol.hpp"
#include "broker.pb.h"
#include <filesystem>
#include <memory>

namespace spatial {
class WorkspaceAuthority;
class NativeLaunch;
// A host execution registry. It has no room, world, or actor-session identity.
class ExecutionStore {
public:
    explicit ExecutionStore(const std::filesystem::path& state, unsigned migrate_from = 0, bool fixture_faults = false);
    ~ExecutionStore();
    ExecutionStore(const ExecutionStore&) = delete;
    ExecutionStore& operator=(const ExecutionStore&) = delete;
    bool fixture_faults() const;
    wire::FaultArmReply arm_fault(const wire::FaultArmRequest& request);
    wire::RepositoryReply import_repository(const wire::RepositoryImportRequest& request);
    wire::WorktreeReply create(const wire::WorktreeCreateRequest& request);
    wire::GitStatusReply status(const std::string& id);
    wire::WorktreeRemoveReply remove(const wire::WorktreeRemoveRequest& request);
    wire::GitOperationReply operation(const std::string& id);
    wire::WorkspaceReply workspace_create(const wire::WorkspaceCreateRequest& request);
    wire::WorkspaceReply workspace_get(const std::string& id);
    wire::WorkspaceAck workspace_member(const wire::WorkspaceMemberRequest& request);
    workspace::v1::AccessReply access(const workspace::v1::AccessRequest& request, bool fresh = true);
    workspace::v1::ApplicationAccessReply application_access(const workspace::v1::ApplicationAccessRequest& request);
    void set_authority(WorkspaceAuthority* authority);
    void set_native_launch(NativeLaunch* native);
    void reconcile_authority();
    void reap();
    wire::ProcessLaunchReply launch(const workspace::v1::LaunchRequest& request);
    wire::ProcessWaitReply wait(const wire::ProcessWaitRequest& request);
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
