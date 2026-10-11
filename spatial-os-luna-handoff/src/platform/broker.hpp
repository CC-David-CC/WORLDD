#pragma once
#include "core/protocol.hpp"
#include "broker.pb.h"
#include <chrono>
#include <filesystem>
#include <functional>
#include <vector>
#include <map>
#include <optional>
#include <memory>
#include <sys/types.h>

namespace spatial {
namespace supervisor { class ProcessScope; class StorageOwnerLaunch; }
namespace broker_wire = workspace::v1;
// Lifecycle belongs to the session supervisor, not a world authority.
class BrokerProcess {
public:
    BrokerProcess(const std::filesystem::path& executable, const std::filesystem::path& state, bool fixture_faults = false,
                  const std::filesystem::path& resource_configuration = {});
    ~BrokerProcess();
    BrokerProcess(const BrokerProcess&) = delete;
    BrokerProcess& operator=(const BrokerProcess&) = delete;
    int channel() const { return channel_; }
    pid_t pid() const { return pid_; }
    void release_channel();
    // Half-close management, verify the actual child's successful exit, then stop owned scopes.
    void finish();
private:
    std::unique_ptr<supervisor::ProcessScope> scope_;
    std::unique_ptr<supervisor::StorageOwnerLaunch> storage_;
    int channel_ = -1, pidfd_ = -1;
    pid_t pid_ = -1;
    bool stopped_ = false;
};
class BrokerClient {
public:
    BrokerClient(int inherited_channel, pid_t pid);
    // Production coordinators supply the verified handle transferred by their retained supervisor.
    BrokerClient(int inherited_channel, pid_t pid, int borrowed_pidfd);
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
    authority::v1::BootstrapBinding bind_bootstrap(const broker_wire::BootstrapBindRequest&);
    authority::v1::BootstrapBinding revoke_bootstrap(const broker_wire::BootstrapRevokeRequest&);
    authority::v1::BootstrapReport apply_bootstrap(const broker_wire::BootstrapApplyRequest&);
    void set_tick(std::function<void()> tick) { tick_ = std::move(tick); }
    void check_alive() const;
    pid_t pid() const { return pid_; }
private:
    BrokerClient(int inherited_channel, pid_t pid, int borrowed_pidfd, bool retained);
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
