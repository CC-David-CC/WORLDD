#pragma once
#include "broker.pb.h"
#include "world/local_view_validation.hpp"
#include <stdexcept>
#include <cstdint>
#include <string>

namespace spatial {
inline void validate_observation_fence(const workspace::v1::AuthorizationFence& fence) {
    validate_message(fence);
    if(fence.incarnation().size()!=16 || !fence.epoch())
        throw std::invalid_argument("Invalid observation authority fence");
}
inline void validate_observation_view(const authority::v1::ActorRef& principal,
        const core::v1::ActorLocalViewIdentity& view, std::uint64_t presentation_generation) {
    validate_message(principal);validate_local_view_identity(view);
    if(principal.issuer_id().size()!=32 || principal.actor_id().size()!=32 || !presentation_generation)
        throw std::invalid_argument("Invalid observation actor or presentation identity");
    const auto hex=[](const std::string& bytes) {
        std::string out;constexpr char digits[]="0123456789abcdef";
        for(unsigned char byte:bytes){out+=digits[byte>>4];out+=digits[byte&15];}return out;
    };
    if(view.actor_id()!="identity:"+hex(principal.issuer_id())+":"+hex(principal.actor_id()))
        throw std::invalid_argument("Observation view does not match its actor principal");
}
inline void validate_observation_watch(const workspace::v1::ObservationWatch& watch) {
    validate_message(watch);
    validate_observation_view(watch.principal(),watch.view(),watch.presentation_generation());
    validate_observation_fence(watch.fence());
    if(watch.id().size()!=32)throw std::invalid_argument("Invalid observation reservation identity");
}
}
