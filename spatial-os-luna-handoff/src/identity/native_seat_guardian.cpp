#include "identity/native_seat_guardian.hpp"
#include "identity/native_seat_binding.hpp"
#include "x11/input.hpp"
#include <stdexcept>

namespace spatial::identity {
NativeSeatGuardian::NativeSeatGuardian(NativeSeatLedger& ledger,NativeInput& native):ledger_(ledger),native_(native) {}
void NativeSeatGuardian::check_native(const guardian::v1::NativeSeatBinding& binding) const {
    validate_native_seat_binding(binding);
    if(!native_.scoped_connected() || binding.native_server_id()!=native_.server_id() ||
       binding.compositor_credential_id()!=native_.credential_id())
        throw std::runtime_error("Native guardian connection differs from the registered seat");
}
std::string NativeSeatGuardian::register_seat(const guardian::v1::SeatRegistration& request,const std::string& id){
    if(!request.has_binding())throw std::invalid_argument("Missing native seat binding");
    check_native(request.binding());
    const auto result=ledger_.register_seat(request,id);
    native_.guard_seat(request.binding().native_seat_id());
    check_native(request.binding());
    return result;
}
void NativeSeatGuardian::disconnect() noexcept {native_.disconnect();}
std::size_t NativeSeatGuardian::fence_step(const guardian::v1::NativeFenceIntent& intent){
    if(!native_.scoped_connected())throw std::runtime_error("Native guardian connection is unavailable");
    ledger_.prepare_fence(intent);
    const auto item=ledger_.first_outstanding(intent.intent_id());
    if(!item)return 0;
    const auto& binding=item->binding;
    check_native(binding);
    if(binding.issuer_id()!=intent.issuer_id() ||
       (intent.target_kind()==guardian::v1::REVOCATION_ACTOR && binding.actor_id()!=intent.target_id()) ||
       (intent.target_kind()==guardian::v1::REVOCATION_CREDENTIAL && binding.credential_id()!=intent.target_id()))
        throw std::runtime_error("Native fence obligation differs from its intent");
    native_.revoke_seat(binding.native_seat_id());
    ledger_.record_confirmed_fence(intent.intent_id(),item->registration_id);
    if(!native_.scoped_connected())throw std::runtime_error("Native guardian connection was lost");
    return ledger_.outstanding_count(intent.intent_id());
}
}
