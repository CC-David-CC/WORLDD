#pragma once
#include "core/protocol.hpp"
#include "native_seat_guardian.pb.h"
#include <stdexcept>

namespace spatial::identity {
// Shape validation only. Authentication, ownership, and revocation are checked
// separately by the participant and guardian. A binding never grants authority.
inline void validate_native_seat_binding(const guardian::v1::NativeSeatBinding& binding) {
    validate_message(binding);
    const auto& entity = binding.actor_entity_id();
    if (binding.issuer_id().size()!=32 || binding.actor_id().size()!=32 ||
        binding.credential_id().size()!=32 || entity.empty() || entity.size()>256 ||
        entity.find('\0')!=std::string::npos || !binding.actor_session_instance() ||
        binding.service_incarnation().size()!=32 || !binding.authority_generation() ||
        binding.native_server_id().size()!=32 || binding.compositor_credential_id().size()!=32 ||
        !binding.native_seat_id())
        throw std::invalid_argument("Invalid native seat guardian binding");
}
}
