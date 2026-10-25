#pragma once
#include "broker.pb.h"
#include <chrono>
#include <optional>
#include <sys/types.h>

namespace spatial::supervisor {
// Single-threaded coordinator transport. Takes ownership of the socket.
// The coordinator supplies a child PID from its retained supervisor handle.
// This class does not establish the endpoint-to-launch mapping.
class ControlAdmin final {
public:
    ControlAdmin(int socket, pid_t broker_child, int retained_broker_pidfd);
    ~ControlAdmin();
    ControlAdmin(const ControlAdmin&) = delete;
    ControlAdmin& operator=(const ControlAdmin&) = delete;
    workspace::v1::ControlSupervisorReply exchange(
        const workspace::v1::ControlSupervisorRequest&, int registration_endpoint = -1);
    // Only descriptor-free inspection and fencing have a continuation API.
    // Poll performs no blocking wait. An uncertain exchange retains ownership.
    std::uint64_t begin(const workspace::v1::ControlSupervisorRequest&);
    std::optional<workspace::v1::ControlSupervisorReply> poll(std::uint64_t);
    bool pending() const noexcept { return pending_.has_value(); }
private:
    using Clock = std::chrono::steady_clock;
    int socket_ = -1, pidfd_ = -1;
    pid_t creator_ = 0, broker_ = 0;
    struct Pending {
        workspace::v1::ControlSupervisorRequest request;
        Clock::time_point deadline;
        bool sent = false;
    };
    std::optional<Pending> pending_;
    std::optional<workspace::v1::ControlSupervisorReply> receive_reply(
        const workspace::v1::ControlSupervisorRequest&);
    void close_all();
    void wait(short events, Clock::time_point deadline);
};
}
