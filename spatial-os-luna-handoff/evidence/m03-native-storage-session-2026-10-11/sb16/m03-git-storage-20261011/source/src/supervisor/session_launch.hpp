#pragma once
#include "platform/process_handle.hpp"
#include "launch.pb.h"
#include <array>
#include <filesystem>
#include <memory>

namespace spatial::supervisor {
// Internal coordinator ownership of an actual authorized supervisor child.
class SessionLaunch final {
public:
    SessionLaunch(const std::filesystem::path& executable, const std::filesystem::path& private_config,
        const v1::AuthoritySupervisorConfig&, int borrowed_native, const std::array<int, 3>& streams,
        bool broker_handle_channel);
    ~SessionLaunch();
    SessionLaunch(const SessionLaunch&) = delete;
    SessionLaunch& operator=(const SessionLaunch&) = delete;
    void live() const;
    const v1::AuthorityLaunchRecord& record() const;
    pid_t supervisor_pid() const;
    int supervisor_pidfd() const;
    ProcessHandle broker_handle();
    workspace::v1::ControlOwner control_owner();
    // Exact retry returns the already checked receipt. Rejected child exits stay rejected.
    v1::AuthorityLaunchRecord stop();
    // Retained receipt and checked child exit only. A journal cannot supply this fact.
    bool checked_failure(const v1::AuthorityLaunchRecord& expected) const;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
