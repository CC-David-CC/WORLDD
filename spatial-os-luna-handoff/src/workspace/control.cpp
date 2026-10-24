#include "workspace/control.hpp"
#include "workspace/store.hpp"
#include "workspace/observation_watch.hpp"
#include "workspace/control_owner.hpp"
#include "world/store.hpp"
#include <cerrno>
#include <sys/random.h>
#include <vector>
#include <optional>

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
ControlLeases::ControlLeases(ExecutionStore& store, std::function<void(const std::string&, const Lease&)> revoked,
        std::function<void(const std::string&, const Observation&)> observation_revoked)
    : store_(store), revoked_(std::move(revoked)), observation_revoked_(std::move(observation_revoked)) {}
void ControlLeases::check_snapshot_budget(const std::string& owner,std::size_t added_bytes) const {
    if(owner.empty())return;
    std::size_t bytes=added_bytes;
    for(const auto& [id,item]:leases_){(void)id;if(item.owner==owner)bytes+=item.lease.ByteSizeLong()+16;}
    for(const auto& [id,item]:observations_){(void)id;if(item.owner==owner)bytes+=item.watch.ByteSizeLong()+16;}
    if(bytes>60000)throw WorldError(wire::RESOURCE_EXHAUSTED,"Control owner snapshot limit reached");
}
ControlLeases::Observation ControlLeases::observe(const pb::ObservationWatchAcquire& request,const std::string& owner) {
    validate_message(request);
    validate_observation_view(request.principal(),request.view(),request.presentation_generation());
    validate_observation_fence(request.expected());
    if(!observation_revoked_)throw WorldError(wire::UNIMPLEMENTED,"Observation notification is unavailable");
    if(fenced_.contains(owner))throw WorldError(wire::FORBIDDEN,"Observation owner is fenced");
    if(!owner.empty()) {
        pb::ControlOwner binding;
        if(!binding.ParseFromString(owner))throw std::invalid_argument("Invalid retained observation owner");
        validate_control_owner(binding);
        if(request.principal().issuer_id()!=binding.issuer_id() ||
            request.view().authority_service_incarnation()!=binding.launch().service_incarnation() ||
            request.view().authority_generation()!=binding.launch().generation())
            throw WorldError(wire::FORBIDDEN,"Observation view does not match its retained owner");
    }
    pb::AuthorizationFenceRequest query;*query.mutable_principal()=request.principal();
    const auto current=store_.authorization_fence(query);
    tick();
    if(fenced_.contains(owner))throw WorldError(wire::FORBIDDEN,"Observation owner was fenced during preparation");
    if(current.SerializeAsString()!=request.expected().SerializeAsString() ||
        store_.observation_fence_snapshot().SerializeAsString()!=current.SerializeAsString())
        throw WorldError(wire::REVISION_CONFLICT,"Observation authority changed before reservation");
    for(const auto& [id,item]:observations_) {
        (void)id;
        if(item.owner==owner && item.watch.view().SerializeAsString()==request.view().SerializeAsString() &&
            item.watch.presentation_generation()==request.presentation_generation()) {
            if(item.revoked)throw WorldError(wire::BUSY,"Retire the invalidated observation before replacement");
            return item.watch;
        }
    }
    if(observations_.size()>=256)throw WorldError(wire::RESOURCE_EXHAUSTED,"Observation reservation limit reached");
    Observation watch;*watch.mutable_principal()=request.principal();*watch.mutable_view()=request.view();
    watch.set_presentation_generation(request.presentation_generation());*watch.mutable_fence()=current;
    do {watch.set_id(identifier());}while(observations_.contains(watch.id()) || leases_.contains(watch.id()));
    check_snapshot_budget(owner,watch.ByteSizeLong()+16);
    observations_.emplace(watch.id(),ObservationReservation{watch,owner});return watch;
}
pb::ObservationWatchReleased ControlLeases::release_observation(const pb::ObservationWatchRelease& request,const std::string& owner) {
    validate_message(request);validate_observation_view(request.principal(),request.view(),request.presentation_generation());
    if(request.id().size()!=32)throw std::invalid_argument("Invalid observation release identity");
    if(fenced_.contains(owner))throw WorldError(wire::FORBIDDEN,"Observation owner is fenced");
    const auto found=observations_.find(request.id());
    if(found==observations_.end())throw WorldError(wire::NOT_FOUND,"Observation reservation does not exist");
    const auto& watch=found->second.watch;
    if(found->second.owner!=owner || watch.principal().SerializeAsString()!=request.principal().SerializeAsString() ||
        watch.view().SerializeAsString()!=request.view().SerializeAsString() ||
        watch.presentation_generation()!=request.presentation_generation())
        throw WorldError(wire::FORBIDDEN,"Observation release does not match its retained view");
    observations_.erase(found);pb::ObservationWatchReleased result;result.set_id(request.id());return result;
}
pb::ObservationBarrierReply ControlLeases::observation_barrier(const pb::ObservationBarrierRequest& request) {
    validate_message(request);validate_observation_fence(request.fence());
    const auto current=store_.observation_fence_snapshot();
    if(current.incarnation()!=request.fence().incarnation() || current.epoch()<request.fence().epoch())
        throw WorldError(wire::STALE_TARGET,"Observation barrier does not match this authority");
    tick();bool settled=true;
    for(const auto& [id,item]:observations_) {
        (void)id;
        if(item.revoked || fenced_.contains(item.owner) || item.watch.fence().epoch()<request.fence().epoch())settled=false;
    }
    pb::ObservationBarrierReply reply;*reply.mutable_fence()=request.fence();reply.set_settled(settled);return reply;
}
ControlLeases::Lease ControlLeases::acquire(const pb::ApplicationControlRequest& request, const std::string& owner) {
    if (fenced_.contains(owner)) throw WorldError(wire::FORBIDDEN, "Control owner is fenced");
    Lease lease;
    if (request.has_principal()) *lease.mutable_principal() = request.principal();
    if (request.has_application()) *lease.mutable_application() = request.application();
    if (!store_.application_access(query(lease)).applications(0).control())
        throw WorldError(wire::FORBIDDEN, "Application control authority is required");
    tick();
    // Authority refresh can pump supervisor fencing while acquisition is in flight.
    if (fenced_.contains(owner)) throw WorldError(wire::FORBIDDEN, "Control owner is fenced");
    const auto group = lease.application().SerializeAsString();
    const auto previous = groups_.find(group);
    if (previous != groups_.end()) {
        const auto& current = leases_.at(previous->second);
        if (!current.revoked && current.owner == owner && current.lease.principal().SerializeAsString() == lease.principal().SerializeAsString())
            return current.lease;
        throw WorldError(wire::BUSY, "Application control reservation requires release");
    }
    if (leases_.size() >= 256) throw WorldError(wire::RESOURCE_EXHAUSTED, "Application control reservation limit reached");
    do { lease.set_id(identifier()); } while (leases_.contains(lease.id()) || observations_.contains(lease.id()));
    check_snapshot_budget(owner,lease.ByteSizeLong()+16);
    leases_.emplace(lease.id(), Reservation{lease, owner}); groups_.emplace(group, lease.id()); return lease;
}
pb::ApplicationControlReleased ControlLeases::release(const pb::ApplicationControlRelease& request, const std::string& owner) {
    if (fenced_.contains(owner)) throw WorldError(wire::FORBIDDEN, "Control owner is fenced");
    if (request.id().size() != 32 || !request.has_principal() || request.principal().issuer_id().size() != 32 ||
            request.principal().actor_id().size() != 32) throw std::invalid_argument("Invalid control release identity");
    const auto found = leases_.find(request.id());
    if (found == leases_.end()) throw WorldError(wire::NOT_FOUND, "Control reservation does not exist");
    if (found->second.owner != owner || found->second.lease.principal().SerializeAsString() != request.principal().SerializeAsString())
        throw WorldError(wire::FORBIDDEN, "Control reservation belongs to another actor");
    groups_.erase(found->second.lease.application().SerializeAsString()); leases_.erase(found);
    pb::ApplicationControlReleased result; result.set_id(request.id()); return result;
}
void ControlLeases::fence(const std::string& owner) {
    if (owner.empty()) throw std::invalid_argument("Cannot fence the root management owner");
    fenced_.insert(owner);
}
void ControlLeases::inspect(const std::string& owner, pb::ControlOwnerState& state) const {
    state.clear_reservations();state.clear_observation_watches();
    state.set_channel_fenced(fenced_.contains(owner));
    for (const auto& [id, reservation] : leases_) {
        (void)id;
        if (reservation.owner == owner) *state.add_reservations() = reservation.lease;
    }
    for(const auto& [id,item]:observations_){(void)id;if(item.owner==owner)*state.add_observation_watches()=item.watch;}
}
bool ControlLeases::cancellation_settled() const {
    for (const auto& [id, reservation] : leases_) {
        (void)id;
        if (reservation.revoked || fenced_.contains(reservation.owner)) return false;
    }
    for(const auto& [id,item]:observations_){(void)id;if(item.revoked || fenced_.contains(item.owner))return false;}
    return true;
}
void ControlLeases::recover(const std::string& owner, pb::ControlRecoveryReceipt& receipt) {
    if (owner.empty() || !fenced_.contains(owner))
        throw WorldError(wire::FORBIDDEN, "Control recovery requires a fenced managed owner");
    if (!receipt.released().empty() || !receipt.observation_released().empty())
        throw std::invalid_argument("Recovery receipt must start empty");
    std::vector<decltype(observations_)::iterator> observed;
    for(auto item=observations_.begin();item!=observations_.end();++item) {
        if(item->second.owner!=owner)continue;
        const auto& cleared=receipt.cleanup().retired_presentation();
        if(!receipt.cleanup().has_retired_presentation() ||
            cleared.world_id()!=item->second.watch.view().world_id() ||
            cleared.presentation_generation()!=item->second.watch.presentation_generation())
            throw WorldError(wire::FORBIDDEN,"Observation recovery requires exact checked presentation retirement");
        *receipt.add_observation_released()=item->second.watch;observed.push_back(item);
    }
    std::vector<std::pair<decltype(leases_)::iterator, decltype(groups_)::iterator>> pending;
    pending.reserve(leases_.size());
    for (auto lease = leases_.begin(); lease != leases_.end(); ++lease) {
        if (lease->second.owner != owner) continue;
        const auto group = groups_.find(lease->second.lease.application().SerializeAsString());
        if (group == groups_.end() || group->second != lease->first)
            throw std::runtime_error("Control recovery reservation index mismatch");
        *receipt.add_released() = lease->second.lease;
        pending.emplace_back(lease, group);
    }
    // Complete every allocation and invariant check before the first erase.
    // No authority refresh, callback, or reentrant request can interleave this commit.
    for (const auto& [lease, group] : pending) { groups_.erase(group); leases_.erase(lease); }
    for(const auto& item:observed)observations_.erase(item);
}
void ControlLeases::tick() {
    if(!observations_.empty()) {
        std::optional<pb::AuthorizationFence> current;
        try{current=store_.observation_fence_snapshot();}catch(const WorldError&){}
        for(auto& [id,item]:observations_) {
            (void)id;
            if(item.revoked)continue;
            if(!current || current->SerializeAsString()!=item.watch.fence().SerializeAsString()) {
                item.revoked=true;observation_revoked_(item.owner,item.watch);
            }
        }
    }
    for (auto& [id, reservation] : leases_) {
        (void)id;
        if (reservation.revoked) continue;
        bool allowed = false;
        try { allowed = store_.application_access(query(reservation.lease), false).applications(0).control(); }
        catch (const WorldError&) { /* Unavailable authority cannot retain control. */ }
        if (!allowed) {
            // Keep the group reserved until the trusted world releases native input.
            reservation.revoked = true; revoked_(reservation.owner, reservation.lease);
        }
    }
}
}
