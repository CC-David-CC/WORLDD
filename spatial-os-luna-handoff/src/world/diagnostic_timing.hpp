#pragma once

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>

namespace spatial::world_diagnostic {

using Clock = std::chrono::steady_clock;
constexpr std::uint64_t max_logged_ms = 86'400'000;

inline std::uint64_t bounded_ms(Clock::duration duration) {
    const auto count = std::chrono::duration_cast<std::chrono::milliseconds>(duration).count();
    if (count <= 0) return 0;
    return std::min<std::uint64_t>(static_cast<std::uint64_t>(count), max_logged_ms);
}

class LastSuccessfulExchange {
public:
    void record_success(Clock::time_point when) { last_success_ = when; }
    std::optional<std::uint64_t> elapsed_ms(Clock::time_point now) const {
        if (!last_success_) return std::nullopt;
        return bounded_ms(now - *last_success_);
    }
private:
    std::optional<Clock::time_point> last_success_;
};

inline constexpr std::string_view operation_name(std::string_view kind) {
    if (kind == "info") return "info";
    if (kind == "credential_check") return "credential_check";
    if (kind == "actor_status") return "actor_status";
    return "other";
}

inline std::string issuer_failure_line(std::string_view operation, std::optional<std::uint64_t> since_success_ms) {
    std::ostringstream out;
    out << "worldd: issuer exchange failed operation=" << operation_name(operation)
        << " since_last_success_ms=";
    if (since_success_ms) out << std::min(*since_success_ms, max_logged_ms);
    else out << "uninitialized";
    return out.str();
}

inline std::string geometry_failure_line(std::string_view stage, std::uint64_t construction_ms,
        std::optional<std::uint64_t> validation_ms) {
    const auto safe_stage = stage == "construction" || stage == "validation" ? stage : "other";
    std::ostringstream out;
    out << "worldd: geometry failed category=geometry_failure stage=" << safe_stage
        << " construction_ms=" << std::min(construction_ms, max_logged_ms) << " validation_ms=";
    if (validation_ms) out << std::min(*validation_ms, max_logged_ms);
    else out << "unstarted";
    return out.str();
}

} // namespace spatial::world_diagnostic
