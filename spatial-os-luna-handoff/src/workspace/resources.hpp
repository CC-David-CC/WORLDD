#pragma once
#include "broker.pb.h"
#include "supervisor/process_scope.hpp"
#include <array>
#include <filesystem>
#include <memory>

namespace spatial {
void validate_resource_config(const workspace::v1::BrokerResourceConfig& configuration);
// Return an owned sealed descriptor. Bind only an empty-root operator template.
int seal_resource_config(const workspace::v1::BrokerResourceConfig& configuration,
                         const std::filesystem::path& owned_scope);
class WorkspaceResources;
class GitJobResources final {
public:
    ~GitJobResources();
    supervisor::ProcessScope& scope();
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
    explicit WorkspaceResources(int sealed_configuration);
    ~WorkspaceResources();
    std::unique_ptr<ApplicationResources> prepare(const workspace::v1::LaunchRequest& request,
        const std::array<std::filesystem::path, 3>& writable_paths);
    std::unique_ptr<GitJobResources> prepare_git_job();
private:
    friend class ApplicationResources;
    friend class GitJobResources;
    struct Impl;
    std::shared_ptr<Impl> impl_;
};
}
