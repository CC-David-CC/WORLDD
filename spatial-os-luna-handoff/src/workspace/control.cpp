#include "workspace/control.hpp"
#include "workspace/store.hpp"
#include "world/store.hpp"
#include <cerrno>
#include <sys/random.h>

namespace spatial {
namespace {
namespace pb = workspace::v1;
std::string identifier() {
    std::string result(32, '\0'); std::size_t used = 0;
    while (used < result.size()) {
        const auto count = getrandom(result.data()+used, result.size()-used, 0);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) throw std::runtime_error("Cannot obtain control lease entropy");
        used += static_cast<std::size_t>(count);
    }
    return result;
}
pb::ApplicationAccessRequest query(const pb::ApplicationControlLease& lease) {
    pb::ApplicationAccessRequest result;
    if (lease.has_principal()) *result.mutable_principal() = lease.principal();
    *result.add_applications() = lease.application(); return result;
}
}
ControlLeases::ControlLeases(ExecutionStore& store, std::function<void(const Lease&)> revoked)
    : store_(store), revoked_(std::move(revoked)) {}
ControlLeases::Lease ControlLeases::acquire(const pb::ApplicationControlRequest& request) {
    Lease lease;
    if (request.has_principal()) *lease.mutable_principal() = request.principal();
    if (request.has_application()) *lease.mutable_application() = request.application();
    if (!store_.application_access(query(lease)).applications(0).control())
        throw WorldError(wire::FORBIDDEN, "Application control authority is required");
    tick();
    const auto group = lease.application().SerializeAsString();
    const auto previous = groups_.find(group);
    if (previous != groups_.end()) {
        const auto& current = leases_.at(previous->second);
        if (!current.revoked && current.lease.principal().SerializeAsString() == lease.principal().SerializeAsString())
            return current.lease;
        throw WorldError(wire::BUSY, "Application control reservation requires release");
    }
    if (leases_.size() >= 256) throw WorldError(wire::RESOURCE_EXHAUSTED, "Application control reservation limit reached");
    do { lease.set_id(identifier()); } while (leases_.contains(lease.id()));
    leases_.emplace(lease.id(), Reservation{lease}); groups_.emplace(group, lease.id()); return lease;
}
pb::ApplicationControlReleased ControlLeases::release(const pb::ApplicationControlRelease& request) {
    if (request.id().size() != 32 || !request.has_principal() || request.principal().issuer_id().size() != 32 ||
            request.principal().actor_id().size() != 32) throw std::invalid_argument("Invalid control release identity");
    const auto found = leases_.find(request.id());
    if (found == leases_.end()) throw WorldError(wire::NOT_FOUND, "Control reservation does not exist");
    if (found->second.lease.principal().SerializeAsString() != request.principal().SerializeAsString())
        throw WorldError(wire::FORBIDDEN, "Control reservation belongs to another actor");
    groups_.erase(found->second.lease.application().SerializeAsString()); leases_.erase(found);
    pb::ApplicationControlReleased result; result.set_id(request.id()); return result;
}
void ControlLeases::tick() {
    for (auto& [id, reservation] : leases_) {
        (void)id;
        if (reservation.revoked) continue;
        bool allowed = false;
        try { allowed = store_.application_access(query(reservation.lease), false).applications(0).control(); }
        catch (const WorldError&) { /* Unavailable authority cannot retain control. */ }
        if (!allowed) {
            // Keep the group reserved until the trusted world releases native input.
            reservation.revoked = true; revoked_(reservation.lease);
        }
    }
}
}
