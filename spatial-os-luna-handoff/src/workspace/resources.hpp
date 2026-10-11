#pragma once
#include "broker.pb.h"
#include "supervisor/process_scope.hpp"
#include <array>
#include <filesystem>
#include <memory>

namespace spatial {
class WorkspaceResources;
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
    ~WorkspaceResources();
    std::unique_ptr<ApplicationResources> prepare(const workspace::v1::LaunchRequest& request,
        const std::array<std::filesystem::path, 3>& writable_paths);
private:
    friend class ApplicationResources;
    struct Impl;
    std::shared_ptr<Impl> impl_;
};
}
