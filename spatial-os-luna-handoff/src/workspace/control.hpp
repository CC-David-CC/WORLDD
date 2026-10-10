#pragma once
#include "broker.pb.h"
#include <functional>
#include <map>
#include <set>

namespace spatial {
class ExecutionStore;
// Owners come from admitted private channels, never from actor request fields.
class ControlLeases {
public:
    using Lease = workspace::v1::ApplicationControlLease;
    ControlLeases(ExecutionStore& store, std::function<void(const std::string&, const Lease&)> revoked);
    Lease acquire(const workspace::v1::ApplicationControlRequest&, const std::string& owner = {});
    workspace::v1::ApplicationControlReleased release(const workspace::v1::ApplicationControlRelease&, const std::string& owner = {});
    void fence(const std::string& owner);
    void inspect(const std::string& owner, workspace::v1::ControlOwnerState&) const;
    // Uses already prepared issuer state. Performs no issuer exchange or process reaping.
    void tick();
    bool cancellation_settled() const;
    bool empty() const { return leases_.empty(); }
private:
    struct Reservation { Lease lease; std::string owner; bool revoked = false; };
    ExecutionStore& store_;
    std::function<void(const std::string&, const Lease&)> revoked_;
    std::map<std::string, Reservation> leases_;
    std::map<std::string, std::string> groups_;
    std::set<std::string> fenced_;
};
}
