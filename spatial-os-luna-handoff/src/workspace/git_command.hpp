#pragma once
#include "supervisor/process_scope.hpp"
#include <chrono>
#include <filesystem>
#include <string>
#include <utility>
#include <vector>
namespace spatial {
struct GitResult { int status; std::string output; };
struct GitMount {
    std::filesystem::path path;
    bool writable = false;
    std::vector<std::filesystem::path> hidden_directories;
    GitMount() = default;
    GitMount(std::filesystem::path value, bool write,
             std::vector<std::filesystem::path> hidden = {})
        : path(std::move(value)), writable(write), hidden_directories(std::move(hidden)) {}
};
// Run one administrative Git operation in its owned namespace and cgroup.
GitResult git_command(supervisor::ProcessScope& scope, const std::filesystem::path& directory,
                      const std::vector<std::string>& arguments, const std::vector<GitMount>& mounts,
                      std::chrono::seconds timeout = std::chrono::seconds(60));
}
