#pragma once
#include "process_scope.pb.h"
#include <array>
#include <chrono>
#include <filesystem>
#include <functional>
#include <memory>
#include <string>
#include <cstdint>
#include <vector>
#include <sys/types.h>

namespace spatial::supervisor {
void validate_budget(const v1::ProcessScopeBudget& budget);
// Owns one authority process subtree. Native cleanup and issuer retirement are separate.
class ProcessScope final {
public:
    static std::unique_ptr<ProcessScope> prepare(const std::filesystem::path& delegated_root,
                                               const v1::ProcessScopeBudget* budget = nullptr);
    // Reuses one named direct child owned by a durable broker identity.
    static std::unique_ptr<ProcessScope> prepare_owned(const std::filesystem::path& delegated_root,
        const std::string& scope_name);
    // Create one exact caller-reserved child cgroup. The name must be unique under
    // delegated_root and is validated as a single path component.
    static std::unique_ptr<ProcessScope> prepare_named(const std::filesystem::path& delegated_root,
                                               const std::string& scope_name,
                                               const v1::ProcessScopeBudget* budget = nullptr);
    // Stop only the exact recorded child scope below an identity-pinned parent.
    static bool stop_recovered(const std::filesystem::path&, std::uint64_t, std::uint64_t,
                               const v1::ProcessScopeIdentity&,
                               const std::function<void(const v1::ProcessScopeIdentity&)>& drained = {});
    static bool remove_reserved_empty(const std::filesystem::path&, const v1::ProcessScopeIdentity&,
                                      const std::string&);
    static bool remove_recovered_git_parent(const std::filesystem::path&, std::uint64_t, std::uint64_t,
                                            const v1::ProcessScopeIdentity&);
    static bool remove_reserved_git_parent(const std::filesystem::path&, std::uint64_t, std::uint64_t,
                                           const std::string&, const std::string&);
    static bool remove_unjournaled_empty_git_parent(const std::filesystem::path&, std::uint64_t, std::uint64_t);
    void start(const v1::AuthorityProcessLaunch& request, const std::array<int, 3>& streams,
               const std::vector<int>& inherited = {}, bool allow_test_security_evidence = false);
    static std::unique_ptr<ProcessScope> launch(const std::filesystem::path& delegated_root,
        const v1::AuthorityProcessLaunch& request, const std::array<int, 3>& streams,
        const std::vector<int>& inherited = {}, bool allow_test_security_evidence = false);
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
    // Attach only the held, owned payload before application exec.
    void attach_payload(pid_t payload);
    void begin_payload_freeze();
    bool payload_frozen() const;
    v1::PayloadQuiescenceReceipt payload_receipt() const;
    void thaw_payload();
    void request_stop();
    v1::ProcessScopeStopped complete_stop(std::chrono::steady_clock::time_point deadline,
        const std::function<void(const v1::ProcessScopeIdentity&)>& drained = {});
    const v1::ProcessScopeIdentity& identity() const;
    bool diagnostic_populated() const;
    bool diagnostic_child_reaped() const;
    v1::ProcessScopeStopped stop(const std::function<void(const v1::ProcessScopeIdentity&)>& drained = {});
private:
    static std::unique_ptr<ProcessScope> prepare_impl(const std::filesystem::path&, const v1::ProcessScopeBudget*, const std::string&);
    ProcessScope() = default;
    bool populated() const;
    bool child_exited() const;
    int parent_ = -1, scope_ = -1, pidfd_ = -1, payload_ = -1;
    bool freeze_owned_ = false;
    std::string frozen_inventory_;
    pid_t child_ = -1;
    bool reaped_ = false, removed_ = false, started_ = false, stop_requested_ = false, retained_scope_ = false;
    v1::ProcessScopeIdentity identity_;
};
}
