#pragma once
#include "core/protocol.hpp"
#include "world_session.pb.h"
#include <cstdint>
#include <optional>
#include <stdexcept>
#include <string>

namespace spatial::world_detail {
struct SelectedMotionFrame { std::size_t offset, length; };

// Select only a complete refresh directly behind one checked key frame.
// The caller still performs all authority and movement checks before applying it.
inline std::optional<SelectedMotionFrame> motion_after_key(
        const std::string& input, std::size_t first_length,
        const identity::v1::WorldSessionRequest& first) {
    if(!first.has_action() && !first.has_control_action())return std::nullopt;
    const auto& action=first.has_action()?first.action():first.control_action();
    if(first.protocol_version()!=1 || !first.request_id() || !action.token().empty() ||
            !action.has_seq() || (!action.has_key() && !action.has_key_tap_batch()))return std::nullopt;
    try { validate_message(first); } catch (const std::invalid_argument&) { return std::nullopt; }
    if(first_length>max_frame_bytes || input.size()<first_length+4)return std::nullopt;
    const auto offset=first_length+4;
    if(input.size()-offset<4)return std::nullopt;
    const auto* next=reinterpret_cast<const unsigned char*>(input.data()+offset);
    const auto length=(std::uint32_t(next[0])<<24)|(std::uint32_t(next[1])<<16)|
        (std::uint32_t(next[2])<<8)|next[3];
    if(length>max_frame_bytes || input.size()-offset-4<length)return std::nullopt;
    identity::v1::WorldSessionRequest refresh;
    if(!refresh.ParseFromArray(input.data()+offset+4,static_cast<int>(length)) ||
            !refresh.has_motion_refresh() || refresh.protocol_version()!=1 ||
            refresh.request_id()<=first.request_id())return std::nullopt;
    try { validate_message(refresh); } catch (const std::invalid_argument&) { return std::nullopt; }
    return SelectedMotionFrame{offset,length};
}
}
