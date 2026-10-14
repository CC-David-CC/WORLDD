#pragma once
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>

namespace spatial {
inline constexpr std::string_view presentation_geometry_prefix = "workspace-presentation.v1/";

inline std::string presentation_geometry_id(std::string_view raw_id) {
    if (raw_id.size() != 16) throw std::invalid_argument("Presentation representation ID must be 16 bytes");
    constexpr char hex[] = "0123456789abcdef";
    std::string result(presentation_geometry_prefix);
    result.reserve(presentation_geometry_prefix.size() + 32);
    for (const unsigned char byte : raw_id) {
        result.push_back(hex[byte >> 4]);
        result.push_back(hex[byte & 0x0f]);
    }
    return result;
}

inline bool is_reserved_presentation_geometry_id(std::string_view id) {
    return id.starts_with(presentation_geometry_prefix);
}

inline std::optional<std::string> presentation_representation_id(std::string_view geometry_id) {
    if (!is_reserved_presentation_geometry_id(geometry_id) ||
        geometry_id.size() != presentation_geometry_prefix.size() + 32) return std::nullopt;
    std::string result(16, '\0');
    const auto nibble = [](char value) -> int {
        if (value >= '0' && value <= '9') return value - '0';
        if (value >= 'a' && value <= 'f') return value - 'a' + 10;
        return -1;
    };
    const auto suffix = geometry_id.substr(presentation_geometry_prefix.size());
    for (std::size_t i = 0; i < result.size(); ++i) {
        const int high = nibble(suffix[2 * i]), low = nibble(suffix[2 * i + 1]);
        if (high < 0 || low < 0) return std::nullopt;
        result[i] = static_cast<char>((high << 4) | low);
    }
    return result;
}
} // namespace spatial
