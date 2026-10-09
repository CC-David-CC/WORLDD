#pragma once
#include "core/protocol.hpp"
#include "broker.pb.h"
#include <chrono>
#include <filesystem>
#include <functional>
#include <vector>
#include <map>
#include <optional>
#include <sys/types.h>

namespace spatial {
namespace broker_wire = workspace::v1;
// Lifecycle belongs to the session supervisor, not a world authority.
class BrokerProcess {
public:
    BrokerProcess(const std::filesystem::path& executable, const std::filesystem::path& state, bool fixture_faults = false);
    ~BrokerProcess();
    BrokerProcess(const BrokerProcess&) = delete;
    BrokerProcess& operator=(const BrokerProcess&) = delete;
    int channel() const { return channel_; }
    pid_t pid() const { return pid_; }
    void release_channel();
private:
    int channel_ = -1, pidfd_ = -1;
    pid_t pid_ = -1;
};
class BrokerClient {
public:
    BrokerClient(int inherited_channel, pid_t pid);
    ~BrokerClient();
    BrokerClient(const BrokerClient&) = delete;
    BrokerClient& operator=(const BrokerClient&) = delete;
    wire::Reply call(wire::Request request);
    broker_wire::AccessReply access(const std::string& workspace, const std::string& actor, const authority::v1::ActorRef* principal = nullptr);
    broker_wire::ApplicationAccessReply application_access(const broker_wire::ApplicationAccessRequest&);
    broker_wire::ApplicationControlLease control_acquire(const broker_wire::ApplicationControlRequest&);
    broker_wire::ApplicationControlReleased control_release(const broker_wire::ApplicationControlRelease&);
    // Nonblocking. Call while idle and during other component waits.
    std::vector<broker_wire::ApplicationControlLease> poll_control();
    // A local validity check, not a replacement for pumping the authority channel.
    bool control_active(const broker_wire::ApplicationControlLease&) const;
    bool busy() const { return pending_ != nullptr; }
    wire::ProcessLaunchReply launch(const std::string& workspace, const std::string& actor, const wire::ProcessLaunchRequest& command,
                                   const authority::v1::ActorRef* principal = nullptr);
    authority::v1::GrantPage grants(const broker_wire::GrantListRequest&);
    authority::v1::GrantRecord delegate(const broker_wire::GrantDelegateRequest&);
    authority::v1::GrantRecord revoke(const broker_wire::GrantRevokeRequest&);
    void set_tick(std::function<void()> tick) { tick_ = std::move(tick); }
    void check_alive() const;
    pid_t pid() const { return pid_; }
private:
    broker_wire::Reply exchange(broker_wire::Request request);
    void wait(short events);
    void pump();
    void revoked(const broker_wire::ApplicationControlLease&);
    void fail_channel();
    int channel_ = -1, pidfd_ = -1;
    pid_t pid_;
    std::uint64_t sequence_ = 0;
    std::function<void()> tick_ = [] {};
    std::chrono::steady_clock::time_point deadline_;
    std::chrono::steady_clock::time_point frame_started_;
    std::string incoming_;
    const broker_wire::Request* pending_ = nullptr;
    std::optional<broker_wire::Reply> response_;
    std::optional<broker_wire::ApplicationControlLease> early_revocation_;
    struct Reservation { broker_wire::ApplicationControlLease lease; bool revoked = false; };
    std::map<std::string, Reservation> controls_;
    std::vector<broker_wire::ApplicationControlLease> revocations_;
};
}
