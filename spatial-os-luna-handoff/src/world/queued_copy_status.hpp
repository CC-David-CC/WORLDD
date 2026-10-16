#pragma once

#include "core/protocol.hpp"
#include "world_session.pb.h"

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <string>

namespace spatial::world_detail {

enum class QueuedReplyRevocationAction { CloseTransport, ReplaceWithDenial, PreserveDenial };

inline bool authenticated_workspace_copy_status_request(
        const identity::v1::WorldSessionRequest& request, bool authenticated) {
    return authenticated && request.has_action() &&
        request.action().has_workspace_copy_status();
}

inline QueuedReplyRevocationAction replace_queued_copy_status_frame(
        std::string& frame, bool queued_copy_status, std::uint64_t request_id,
        std::size_t sent, bool transport_write_attempted, bool already_denial,
        const std::chrono::steady_clock::time_point& output_deadline) {
    (void)output_deadline;
    if (already_denial) return QueuedReplyRevocationAction::PreserveDenial;
    if (!queued_copy_status || sent != 0 || transport_write_attempted || frame.size() < 4)
        return QueuedReplyRevocationAction::CloseTransport;

    const auto* header = reinterpret_cast<const unsigned char*>(frame.data());
    const std::uint32_t length = (std::uint32_t(header[0]) << 24) |
        (std::uint32_t(header[1]) << 16) | (std::uint32_t(header[2]) << 8) |
        std::uint32_t(header[3]);
    if (length > max_frame_bytes || frame.size() != std::size_t(length) + 4)
        return QueuedReplyRevocationAction::CloseTransport;

    identity::v1::WorldSessionReply queued_reply;
    if (!queued_reply.ParseFromArray(frame.data() + 4, static_cast<int>(length)) ||
            queued_reply.request_id() != request_id)
        return QueuedReplyRevocationAction::CloseTransport;

    identity::v1::WorldSessionReply denial;
    denial.set_protocol_version(1);
    denial.set_request_id(request_id);
    denial.mutable_error()->set_code(wire::UNAUTHORIZED);
    denial.mutable_error()->set_message("World session changed during operation");
    const auto payload = denial.SerializeAsString();
    if (payload.size() > max_frame_bytes)
        return QueuedReplyRevocationAction::CloseTransport;

    const auto replacement_length = static_cast<std::uint32_t>(payload.size());
    std::string replacement(4, '\0');
    replacement[0] = static_cast<char>(replacement_length >> 24);
    replacement[1] = static_cast<char>(replacement_length >> 16);
    replacement[2] = static_cast<char>(replacement_length >> 8);
    replacement[3] = static_cast<char>(replacement_length);
    replacement.append(payload);
    frame.swap(replacement);
    return QueuedReplyRevocationAction::ReplaceWithDenial;
}

} // namespace spatial::world_detail
