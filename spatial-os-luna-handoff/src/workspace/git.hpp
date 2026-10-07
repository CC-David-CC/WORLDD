#pragma once
#include "core/protocol.hpp"
#include <filesystem>
#include <memory>

namespace spatial {
// A host execution registry. It has no room, world, or actor-session identity.
class WorkspaceGit {
public:
    explicit WorkspaceGit(const std::filesystem::path& state);
    ~WorkspaceGit();
    WorkspaceGit(const WorkspaceGit&) = delete;
    WorkspaceGit& operator=(const WorkspaceGit&) = delete;
    wire::RepositoryReply import_repository(const wire::RepositoryImportRequest& request);
    wire::WorktreeReply create(const wire::WorktreeCreateRequest& request);
    wire::GitOperationReply operation(const std::string& id);
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
