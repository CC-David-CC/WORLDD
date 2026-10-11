#pragma once
#include "process_scope.pb.h"
#include <array>
#include <filesystem>
#include <memory>
#include <vector>
#include <sys/types.h>

namespace spatial::supervisor {
void validate_budget(const v1::ProcessScopeBudget& budget);
// Owns one authority process subtree. Native cleanup and issuer retirement are separate.
class ProcessScope final {
public:
    static std::unique_ptr<ProcessScope> prepare(const std::filesystem::path& delegated_root,
                                               const v1::ProcessScopeBudget* budget = nullptr);
    void start(const v1::AuthorityProcessLaunch& request, const std::array<int, 3>& streams,
               const std::vector<int>& inherited = {});
    static std::unique_ptr<ProcessScope> launch(const std::filesystem::path& delegated_root,
        const v1::AuthorityProcessLaunch& request, const std::array<int, 3>& streams,
        const std::vector<int>& inherited = {});
    ~ProcessScope();
    ProcessScope(const ProcessScope&) = delete;
    ProcessScope& operator=(const ProcessScope&) = delete;
    v1::ProcessScopeState state() const;
    bool exited_successfully() const;
    int exit_status() const;
    // Duplicates the retained live child handle. The caller must close the result.
    // This does not authorize an external transfer or prove process cleanup.
    int duplicate_child_pidfd() const;
    // The PID remains owned and unreaped. Never use it as a durable identity.
    pid_t direct_child_pid() const;
    // Apply reclaim pressure before a bounded worker reaches its hard memory limit.
    void throttle_memory(std::uint64_t bytes);
    void enable_children();
    v1::ProcessScopeStopped stop();
private:
    ProcessScope() = default;
    bool populated() const;
    bool child_exited() const;
    int parent_ = -1, scope_ = -1, pidfd_ = -1;
    pid_t child_ = -1;
    bool reaped_ = false, removed_ = false, started_ = false;
    v1::ProcessScopeIdentity identity_;
};
}
