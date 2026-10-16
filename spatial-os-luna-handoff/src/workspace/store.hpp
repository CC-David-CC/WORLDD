#pragma once
#include "core/protocol.hpp"
#include "broker.pb.h"
#include "workspace/copy_journal.hpp"
#include <filesystem>
#include <functional>
#include <memory>

namespace spatial {
class WorkspaceAuthority;
class NativeLaunch;
class WorkspaceResources;
class StorageClient;
// A host execution registry. It has no room, world, or actor-session identity.
class ExecutionStore {
public:
    explicit ExecutionStore(const std::filesystem::path& state, unsigned migrate_from = 0, bool fixture_faults = false,
                            StorageClient* storage = nullptr, WorkspaceResources* resources = nullptr);
    ~ExecutionStore();
    ExecutionStore(const ExecutionStore&) = delete;
    ExecutionStore& operator=(const ExecutionStore&) = delete;
    CopyJournal& copy_journal();
    const CopyJournal& copy_journal() const;
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
    workspace::v1::ScopedAuthorityCheckReply scoped_authority_check(const workspace::v1::ScopedAuthorityCheckRequest& request);
    workspace::copy::v1::WorkspaceCopyStatus workspace_copy_begin(
        const workspace::copy::v1::WorkspaceCopyRequest&, const authority::v1::ActorRef&);
    workspace::copy::v1::WorkspaceCopyStatus workspace_copy_status(
        const workspace::copy::v1::WorkspaceCopyStatusRequest&, const authority::v1::ActorRef&);
    workspace::v1::AuthorizationFence authorization_fence(const workspace::v1::AuthorizationFenceRequest& request);
    workspace::v1::ApplicationAccessReply application_access(const workspace::v1::ApplicationAccessRequest& request, bool refresh = true);
    void set_authority(WorkspaceAuthority* authority);
    void set_native_launch(NativeLaunch* native);
    void set_resources(WorkspaceResources* resources);
    void set_observer(std::function<void()> observer);
    void reconcile_authority();
    void reap();
    void advance_workspace_copies();
    void stop_all();
    wire::ProcessLaunchReply launch(const workspace::v1::LaunchRequest& request);
    wire::ProcessWaitReply wait(const wire::ProcessWaitRequest& request);
private:
    void revalidate_copy(const CopyJournal::Operation&, bool query_owner = true);
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
