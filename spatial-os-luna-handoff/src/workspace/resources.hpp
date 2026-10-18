#pragma once
#include "broker.pb.h"
#include "supervisor/process_scope.hpp"
#include <array>
#include <filesystem>
#include <memory>
#include <span>
#include <utility>

namespace spatial {
class FileOperationJournal;
void validate_resource_config(const workspace::v1::BrokerResourceConfig& configuration);
// Return an owned sealed descriptor. Bind only an empty-root operator template.
int seal_resource_config(const workspace::v1::BrokerResourceConfig& configuration,
                         const std::filesystem::path& owned_scope);
class WorkspaceResources;
class GitJobResources final {
public:
    ~GitJobResources();
    supervisor::ProcessScope& scope();
    const supervisor::v1::ProcessScopeIdentity& parent_identity() const;
private:
    friend class WorkspaceResources;
    struct Impl;
    explicit GitJobResources(std::unique_ptr<Impl> impl);
    std::unique_ptr<Impl> impl_;
};
class ApplicationResources final {
public:
    ~ApplicationResources();
    supervisor::ProcessScope& scope();
private:
    friend class WorkspaceResources;
    struct Impl;
    explicit ApplicationResources(std::unique_ptr<Impl> impl);
    std::unique_ptr<Impl> impl_;
};
// Owns immutable policy and the broker's nested workspace/actor resource groups.
class WorkspaceResources final {
public:
    explicit WorkspaceResources(const std::filesystem::path& configuration);
    // Consume this descriptor, including on rejection.
    explicit WorkspaceResources(int sealed_configuration, bool require_aggregate = false);
    ~WorkspaceResources();
    std::unique_ptr<ApplicationResources> prepare(const workspace::v1::LaunchRequest& request,
        const std::array<std::filesystem::path, 3>& writable_paths);
    std::unique_ptr<GitJobResources> prepare_git_job(std::span<const int> directory_handles,
                                                        const std::string& reserved_scope_name = {});
    const supervisor::v1::ProcessScopeIdentity& git_worker_parent_identity();
    std::filesystem::path git_worker_parent_path() const;
    std::filesystem::path broker_root_path() const;
    void set_git_worker_recovery_parent_path(const std::filesystem::path& path);
    void clear_git_worker_recovery_parent_path();
    void bind_git_worker_journal(FileOperationJournal* journal);
    // Activate cgroup controllers after durable copy-job recovery completes.
    void finish_initialization();
    std::pair<std::uint64_t,std::uint64_t> broker_root_identity() const;
    workspace::v1::WorkspaceCopyResourceProfile resolve_copy_profile(const std::string& name,const std::string& application_profile) const;
    void register_copy_workspace(const std::string& workspace,const workspace::v1::WorkspaceCopyResourceProfile&);
    bool has_profile(const std::string& name, const std::string& workspace = {}) const;
private:
    friend class ApplicationResources;
    friend class GitJobResources;
    struct Impl;
    std::shared_ptr<Impl> impl_;
};
}
