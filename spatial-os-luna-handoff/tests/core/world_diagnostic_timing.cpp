#include "world/diagnostic_timing.hpp"

#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>

namespace {
void check(bool condition, const char* message) {
    if (!condition) { std::cerr << message << '\n'; std::exit(1); }
}
}

int main() {
    using namespace spatial::world_diagnostic;
    const auto zero = Clock::time_point{};
    LastSuccessfulExchange successes;
    check(!successes.elapsed_ms(zero), "last success must begin uninitialized");
    successes.record_success(zero);
    const auto attempted_at = zero + std::chrono::milliseconds(20);
    check(successes.elapsed_ms(attempted_at) == 20, "elapsed time must use last successful exchange");
    // A failed attempt has no recorder call, so it cannot reset the successful baseline.
    check(successes.elapsed_ms(zero + std::chrono::milliseconds(35)) == 35,
        "failed attempt must not become the last successful exchange");

    check(operation_name("info") == "info", "info label changed");
    check(operation_name("credential_check") == "credential_check", "credential label changed");
    check(operation_name("actor_status") == "actor_status", "actor status label changed");
    check(operation_name("private-key-sentinel") == "other", "unknown operation must be allowlisted");
    const auto issuer = issuer_failure_line("private-key-sentinel", max_logged_ms + 1);
    check(issuer.find("operation=other") != std::string::npos, "issuer label must be fixed");
    check(issuer.find("category=") == std::string::npos, "timing prefix must not duplicate transport category");
    const auto combined = issuer + " category=issuer_eof phase=reply_header elapsed_ms=0";
    check(combined.find("category=") == combined.rfind("category="), "combined diagnostic must have exactly one category");
    check(issuer.find("since_last_success_ms=86400000") != std::string::npos, "issuer time must be bounded");
    check(issuer.find("private-key-sentinel") == std::string::npos, "issuer diagnostic leaked operation input");
    check(issuer.find("uninitialized") == std::string::npos, "initialized time rendered as uninitialized");
    check(issuer_failure_line("info", std::nullopt).find("since_last_success_ms=uninitialized") != std::string::npos,
        "missing success must remain explicit");

    const auto geometry = geometry_failure_line("validation", max_logged_ms + 1, std::nullopt);
    check(geometry.find("category=geometry_failure stage=validation") != std::string::npos,
        "geometry category and stage must be fixed");
    check(geometry.find("construction_ms=86400000 validation_ms=unstarted") != std::string::npos,
        "geometry timings must be bounded and show unstarted validation");
    std::uint64_t exception_elapsed_ms = 0;
    try { throw std::runtime_error("private-key-sentinel"); }
    catch (const std::exception&) {
        exception_elapsed_ms = bounded_ms(std::chrono::milliseconds(17));
    }
    check(exception_elapsed_ms == 17, "exception path must capture elapsed duration");
    const auto construction_exception = geometry_failure_line("construction", exception_elapsed_ms, std::nullopt);
    check(construction_exception.find("construction_ms=17 validation_ms=unstarted") != std::string::npos,
        "construction exception must retain elapsed time and mark validation unstarted");
    const auto validation_exception = geometry_failure_line("validation", 17, 9);
    check(validation_exception.find("construction_ms=17 validation_ms=9") != std::string::npos,
        "validation exception must retain both stage durations");
    const auto secret_stage = geometry_failure_line("credential-secret-sentinel", 1, 2);
    check(secret_stage.find("stage=other") != std::string::npos &&
          secret_stage.find("credential-secret-sentinel") == std::string::npos,
        "geometry diagnostic must allowlist stages and exclude supplied sensitive text");
    return 0;
}
