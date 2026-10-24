#pragma once
#include "core/protocol.hpp"
#include <stdexcept>

namespace spatial {
inline void validate_local_view_identity(const core::v1::ActorLocalViewIdentity& value) {
    validate_message(value);
    if (value.protocol_version() != 1 || value.world_id().empty() || value.world_id().size() > 256 ||
        value.world_id().find('\0') != std::string::npos || value.actor_id().empty() || value.actor_id().size() > 256 ||
        value.actor_id().find('\0') != std::string::npos || value.view_id().size() != 32 ||
        value.authority_service_incarnation().size() != 32 || value.authority_generation() == 0)
        throw std::invalid_argument("Invalid authenticated local actor view identity");
}
}
