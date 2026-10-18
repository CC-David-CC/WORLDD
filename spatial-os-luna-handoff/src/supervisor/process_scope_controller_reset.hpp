#pragma once
#include <algorithm>
#include <array>
#include <cctype>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace spatial::supervisor::detail {
// Build a reset command only for controllers this broker deliberately enables.
inline std::string stale_subtree_controller_disable_command(std::string_view raw) {
    constexpr std::array<std::string_view, 4> expected{"cpu", "memory", "pids", "io"};
    std::array<bool, expected.size()> enabled{};
    std::istringstream input{std::string(raw)};
    std::string name;
    while (input >> name) {
        const auto found = std::find(expected.begin(), expected.end(), name);
        if (found == expected.end())
            throw std::invalid_argument("Unexpected controller in owned cgroup subtree_control");
        const auto index = static_cast<std::size_t>(found - expected.begin());
        if (enabled[index])
            throw std::invalid_argument("Duplicate controller in owned cgroup subtree_control");
        enabled[index] = true;
    }
    if (!input.eof())
        throw std::invalid_argument("Malformed owned cgroup subtree_control");
    std::string command;
    for (std::size_t index = 0; index < expected.size(); ++index) {
        if (!enabled[index]) continue;
        if (!command.empty()) command.push_back(' ');
        command.push_back('-');
        command.append(expected[index]);
    }
    if (!command.empty()) command.push_back('\n');
    return command;
}

struct ParsedIoLimits {
    std::vector<std::string> devices;
    bool all_unlimited = true;
};

// Parse the kernel's io.max rows. Missing keys are unlimited; present values must
// be either "max" or decimal digits. Reject malformed, duplicate, or unknown data.
inline ParsedIoLimits parse_owned_io_limits(std::string_view raw) {
    constexpr std::array<std::string_view, 4> keys{"rbps", "wbps", "riops", "wiops"};
    std::istringstream input{std::string(raw)};
    std::string line;
    ParsedIoLimits parsed;
    std::set<std::string> devices;
    while (std::getline(input, line)) {
        std::istringstream fields(line);
        std::string device;
        if (!(fields >> device))
            throw std::invalid_argument("Malformed device in owned cgroup io.max");
        const auto colon = device.find(':');
        bool valid_device = colon != std::string::npos && colon > 0 && colon + 1 < device.size() &&
                            device.find(':', colon + 1) == std::string::npos;
        if (valid_device) {
            for (std::size_t index = 0; index < device.size(); ++index)
                if (index != colon && !std::isdigit(static_cast<unsigned char>(device[index]))) valid_device = false;
        }
        if (!valid_device || !devices.insert(device).second)
            throw std::invalid_argument("Invalid or duplicate device in owned cgroup io.max");
        parsed.devices.push_back(device);
        std::array<bool, keys.size()> seen{};
        std::string field;
        while (fields >> field) {
            const auto equal = field.find('=');
            if (equal == std::string::npos || equal == 0 || equal + 1 == field.size())
                throw std::invalid_argument("Malformed limit in owned cgroup io.max");
            const auto found = std::find(keys.begin(), keys.end(), field.substr(0, equal));
            if (found == keys.end())
                throw std::invalid_argument("Unexpected limit in owned cgroup io.max");
            const auto index = static_cast<std::size_t>(found - keys.begin());
            if (seen[index])
                throw std::invalid_argument("Duplicate limit in owned cgroup io.max");
            seen[index] = true;
            const auto value = std::string_view(field).substr(equal + 1);
            if (value != "max" && !std::all_of(value.begin(), value.end(),
                    [](unsigned char c) { return std::isdigit(c); }))
                throw std::invalid_argument("Invalid value in owned cgroup io.max");
            if (value != "max") parsed.all_unlimited = false;
        }
        if (!fields.eof())
            throw std::invalid_argument("Malformed owned cgroup io.max");
    }
    if (input.bad())
        throw std::invalid_argument("Malformed owned cgroup io.max");
    return parsed;
}

inline bool io_limits_are_unlimited(std::string_view raw) {
    return parse_owned_io_limits(raw).all_unlimited;
}

// Emit explicit unlimited values for every device with a persisted row.
inline std::string stale_io_limit_reset_commands(std::string_view raw) {
    const auto parsed = parse_owned_io_limits(raw);
    std::string commands;
    for (const auto& device : parsed.devices)
        commands += device + " rbps=max wbps=max riops=max wiops=max\n";
    return commands;
}
} // namespace spatial::supervisor::detail
