#pragma once

#include "core/protocol.hpp"
#include <cstddef>

namespace spatial {

// Generated protobuf sizing of a one-element envelope includes the repeated
// field tag, length prefix, and the entity's encoded payload.
inline std::size_t snapshot_entity_field_size(const wire::Entity& entity) {
    wire::WorldSnapshotReply envelope;
    *envelope.add_entities() = entity;
    return envelope.ByteSizeLong();
}

// Keep the running count within the allowed bound without overflowing size_t.
inline bool add_snapshot_bytes(std::size_t& current, std::size_t contribution,
                               std::size_t limit) {
    if (current > limit || contribution > limit - current) return false;
    current += contribution;
    return true;
}

} // namespace spatial
