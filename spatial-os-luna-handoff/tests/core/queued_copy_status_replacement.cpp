#include "world/queued_copy_status.hpp"

#include <chrono>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <google/protobuf/unknown_field_set.h>

namespace pb = spatial::identity::v1;

void require(bool condition, std::string_view message) {
    if (!condition) throw std::runtime_error(std::string(message));
}

std::string frame_reply(const pb::WorldSessionReply& reply) {
    const auto payload = reply.SerializeAsString();
    require(payload.size() <= spatial::max_frame_bytes, "Test reply exceeds frame limit");
    const auto length = static_cast<std::uint32_t>(payload.size());
    std::string frame(4, '\0');
    frame[0] = static_cast<char>(length >> 24);
    frame[1] = static_cast<char>(length >> 16);
    frame[2] = static_cast<char>(length >> 8);
    frame[3] = static_cast<char>(length);
    frame.append(payload);
    return frame;
}

pb::WorldSessionReply parse_frame(const std::string& frame) {
    require(frame.size() >= 4, "Framed reply is truncated");
    const auto* header = reinterpret_cast<const unsigned char*>(frame.data());
    const std::uint32_t length = (std::uint32_t(header[0]) << 24) |
        (std::uint32_t(header[1]) << 16) | (std::uint32_t(header[2]) << 8) |
        std::uint32_t(header[3]);
    require(frame.size() == std::size_t(length) + 4, "Framed reply length is invalid");
    pb::WorldSessionReply reply;
    require(reply.ParseFromArray(frame.data() + 4, static_cast<int>(length)),
        "Framed reply protobuf is invalid");
    return reply;
}

pb::WorldSessionRequest status_request(std::uint64_t request_id) {
    pb::WorldSessionRequest request;
    request.set_protocol_version(1);
    request.set_request_id(request_id);
    request.mutable_action()->mutable_workspace_copy_status()->set_protocol_version(1);
    return request;
}

int main() {
    constexpr std::uint64_t request_id = 72;
    const auto request = status_request(request_id);
    const bool status_request = spatial::world_detail::authenticated_workspace_copy_status_request(
        request, true);
    require(status_request, "Authenticated status request was not classified");
    require(!spatial::world_detail::authenticated_workspace_copy_status_request(request, false),
        "Unauthenticated status request was classified");

    pb::WorldSessionReply old_reply;
    old_reply.set_protocol_version(1);
    old_reply.set_request_id(request_id);
    old_reply.mutable_error()->set_code(spatial::wire::FORBIDDEN);
    old_reply.mutable_error()->set_message("private copy status detail");
    old_reply.GetReflection()->MutableUnknownFields(&old_reply)->AddLengthDelimited(91, "sensitive-pixels");
    const auto old_frame = frame_reply(old_reply);
    auto deadline = std::chrono::steady_clock::time_point(std::chrono::seconds(500));
    const auto original_deadline = deadline;

    auto replaceable = old_frame;
    require(spatial::world_detail::replace_queued_copy_status_frame(
            replaceable, status_request, request_id, 0, false, false, deadline) ==
            spatial::world_detail::QueuedReplyRevocationAction::ReplaceWithDenial,
        "Never-attempted copy status reply was not replaced");
    const auto denial = parse_frame(replaceable);
    require(denial.request_id() == request_id, "Replacement lost request correlation");
    require(denial.has_error() && denial.error().code() == spatial::wire::UNAUTHORIZED,
        "Replacement is not a typed unauthorized denial");
    require(denial.error().message() == "World session changed during operation",
        "Replacement denial message differs from the production contract");
    pb::WorldSessionReply expected_denial;
    expected_denial.set_protocol_version(1);
    expected_denial.set_request_id(request_id);
    expected_denial.mutable_error()->set_code(spatial::wire::UNAUTHORIZED);
    expected_denial.mutable_error()->set_message("World session changed during operation");
    require(denial.SerializeAsString() == expected_denial.SerializeAsString(),
        "Replacement retained metadata, pixels, unknown fields, or prior payload");
    require(replaceable.find("private copy status detail") == std::string::npos,
        "Replacement retained the prior error payload");
    require(deadline == original_deadline, "Replacement refreshed the original I/O deadline");
    auto repeated = replaceable;
    require(spatial::world_detail::replace_queued_copy_status_frame(
            repeated, false, request_id, 3, true, true, deadline) ==
            spatial::world_detail::QueuedReplyRevocationAction::PreserveDenial &&
            repeated == replaceable,
        "Repeated invalidation changed the fresh denial frame");

    auto attempted_tls = old_frame;
    require(spatial::world_detail::replace_queued_copy_status_frame(
            attempted_tls, status_request, request_id, 0, true, false, deadline) ==
            spatial::world_detail::QueuedReplyRevocationAction::CloseTransport &&
            attempted_tls == old_frame,
        "TLS write attempt with zero sent bytes did not preserve close behavior");
    auto partial_local = old_frame;
    require(spatial::world_detail::replace_queued_copy_status_frame(
            partial_local, status_request, request_id, 1, true, false, deadline) ==
            spatial::world_detail::QueuedReplyRevocationAction::CloseTransport &&
            partial_local == old_frame,
        "Partially sent local frame did not preserve close behavior");
    auto fully_sent = old_frame;
    require(spatial::world_detail::replace_queued_copy_status_frame(
            fully_sent, status_request, request_id, old_frame.size(), true, false, deadline) ==
            spatial::world_detail::QueuedReplyRevocationAction::CloseTransport &&
            fully_sent == old_frame,
        "Fully sent frame did not preserve close behavior");

    pb::WorldSessionRequest observation;
    observation.set_request_id(request_id);
    observation.mutable_action()->mutable_observe();
    require(!spatial::world_detail::authenticated_workspace_copy_status_request(observation, true),
        "Observation request was classified as copy status");
    pb::WorldSessionRequest geometry;
    geometry.set_request_id(request_id);
    geometry.mutable_geometry_view();
    require(!spatial::world_detail::authenticated_workspace_copy_status_request(geometry, true),
        "Geometry request was classified as copy status");
    auto protected_reply = old_frame;
    require(spatial::world_detail::replace_queued_copy_status_frame(
            protected_reply, false, request_id, 0, false, false, deadline) ==
            spatial::world_detail::QueuedReplyRevocationAction::CloseTransport &&
            protected_reply == old_frame,
        "A protected non-status reply did not preserve close behavior");

    auto mismatched = old_frame;
    require(spatial::world_detail::replace_queued_copy_status_frame(
            mismatched, status_request, request_id + 1, 0, false, false, deadline) ==
            spatial::world_detail::QueuedReplyRevocationAction::CloseTransport &&
            mismatched == old_frame,
        "Mismatched queued request ID did not preserve close behavior");
    auto malformed = std::string("bad");
    require(spatial::world_detail::replace_queued_copy_status_frame(
            malformed, status_request, request_id, 0, false, false, deadline) ==
            spatial::world_detail::QueuedReplyRevocationAction::CloseTransport && malformed == "bad",
        "Malformed queued frame did not preserve close behavior");
    auto invalid_protobuf = std::string("\0\0\0\1\xff", 5);
    const auto invalid_protobuf_before = invalid_protobuf;
    require(spatial::world_detail::replace_queued_copy_status_frame(
            invalid_protobuf, status_request, request_id, 0, false, false, deadline) ==
            spatial::world_detail::QueuedReplyRevocationAction::CloseTransport &&
            invalid_protobuf == invalid_protobuf_before,
        "Invalid queued protobuf did not preserve close behavior");
    auto length_mismatch = old_frame;
    length_mismatch[3] = static_cast<char>(static_cast<unsigned char>(length_mismatch[3]) - 1);
    const auto length_mismatch_before = length_mismatch;
    require(spatial::world_detail::replace_queued_copy_status_frame(
            length_mismatch, status_request, request_id, 0, false, false, deadline) ==
            spatial::world_detail::QueuedReplyRevocationAction::CloseTransport &&
            length_mismatch == length_mismatch_before,
        "Mismatched queued frame length did not preserve close behavior");

    auto oversized = std::string(4, '\0');
    const auto oversized_length = static_cast<std::uint32_t>(spatial::max_frame_bytes + 1);
    oversized[0] = static_cast<char>(oversized_length >> 24);
    oversized[1] = static_cast<char>(oversized_length >> 16);
    oversized[2] = static_cast<char>(oversized_length >> 8);
    oversized[3] = static_cast<char>(oversized_length);
    const auto oversized_before = oversized;
    require(spatial::world_detail::replace_queued_copy_status_frame(
            oversized, status_request, request_id, 0, false, false, deadline) ==
            spatial::world_detail::QueuedReplyRevocationAction::CloseTransport &&
            oversized == oversized_before,
        "Oversized queued frame did not preserve close behavior");

    std::cout << "queued copy status replacement cases passed\n";
    return 0;
}
