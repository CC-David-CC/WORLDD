#pragma once
#include "broker.pb.h"
#include <functional>
#include <map>

namespace spatial {
class ExecutionStore;
// One private supervisor connection owns these ephemeral reservations.
class ControlLeases {
public:
    using Lease = workspace::v1::ApplicationControlLease;
    ControlLeases(ExecutionStore& store, std::function<void(const Lease&)> revoked);
    Lease acquire(const workspace::v1::ApplicationControlRequest&);
    workspace::v1::ApplicationControlReleased release(const workspace::v1::ApplicationControlRelease&);
    // Uses already prepared issuer state. Performs no issuer exchange or process reaping.
    void tick();
private:
    struct Reservation { Lease lease; bool revoked = false; };
    ExecutionStore& store_;
    std::function<void(const Lease&)> revoked_;
    std::map<std::string, Reservation> leases_;
    std::map<std::string, std::string> groups_;
};
}
