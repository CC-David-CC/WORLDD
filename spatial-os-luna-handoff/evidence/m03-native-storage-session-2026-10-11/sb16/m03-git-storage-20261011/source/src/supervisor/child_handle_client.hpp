#pragma once
#include "platform/process_handle.hpp"
#include "launch.pb.h"
#include <memory>

namespace spatial::supervisor {
// Runs in the coordinator which created the socket and retained the supervisor.
class ChildHandleClient final {
public:
    ChildHandleClient(int owned_socket, pid_t supervisor, int borrowed_supervisor_pidfd);
    ~ChildHandleClient();
    ChildHandleClient(const ChildHandleClient&) = delete;
    ChildHandleClient& operator=(const ChildHandleClient&) = delete;
    ProcessHandle broker(const v1::AuthorityLaunchRecord& expected);
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
