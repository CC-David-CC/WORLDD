#include "identity/native_seat_guardian_dispatch.hpp"
#include "core/protocol.hpp"
#include <openssl/rand.h>
#include <limits>
#include <stdexcept>
#include <utility>
namespace spatial::identity {
namespace {
void need(bool value,const char* message){if(!value)throw std::invalid_argument(message);}
std::string identifier(){
    std::string result(32,'\0');
    if(RAND_bytes(reinterpret_cast<unsigned char*>(result.data()),result.size())!=1)
        throw std::runtime_error("Cannot generate guardian registration identity");
    return result;
}
}
NativeSeatGuardianDispatch::NativeSeatGuardianDispatch(NativeSeatGuardian& guardian,std::string issuer,
        std::string incarnation,std::uint64_t generation)
    :guardian_(guardian),issuer_id_(std::move(issuer)),service_incarnation_(std::move(incarnation)),authority_generation_(generation){
    need(issuer_id_.size()==32 && service_incarnation_.size()==32 && authority_generation_>0,
        "Invalid guardian channel context");
}
guardian::v1::GuardianReply NativeSeatGuardianDispatch::dispatch(Role role,const guardian::v1::GuardianRequest& request){
    validate_message(request);
    need(role==Role::world || role==Role::issuer,"Invalid guardian channel role");
    need(request.protocol_version()==1 && request.request_id()>0,"Invalid guardian request envelope");
    auto& sequence=role==Role::world?world_sequence_:issuer_sequence_;
    need(request.request_id()>sequence,"Stale guardian request sequence");
    sequence=request.request_id();
    guardian::v1::GuardianReply reply;reply.set_protocol_version(1);reply.set_request_id(request.request_id());
    if(role==Role::world){
        need(request.has_register_seat() && request.register_seat().has_binding(),"World role requires seat registration");
        const auto& binding=request.register_seat().binding();
        need(binding.issuer_id()==issuer_id_ && binding.service_incarnation()==service_incarnation_ &&
             binding.authority_generation()==authority_generation_,"Seat registration differs from checked world context");
        const auto registration_id=guardian_.register_seat(request.register_seat(),identifier());
        reply.set_registration_id(registration_id);
        auto* receipt=reply.mutable_registration_receipt();
        receipt->set_operation_id(request.register_seat().operation_id());
        *receipt->mutable_binding()=binding;
        receipt->set_guardian_incarnation(service_incarnation_);
        receipt->set_registration_id(registration_id);
        receipt->set_issuer_security_revision(request.register_seat().issuer_security_revision());
    } else {
        need(request.has_fence() && request.fence().issuer_id()==issuer_id_,"Issuer role requires a matching fence");
        const auto remaining=guardian_.fence_step(request.fence());
        if(remaining>std::numeric_limits<std::uint32_t>::max())throw std::runtime_error("Excessive guardian obligations");
        reply.set_remaining_fences(static_cast<std::uint32_t>(remaining));
    }
    return reply;
}
}
