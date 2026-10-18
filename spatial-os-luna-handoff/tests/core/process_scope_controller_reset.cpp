#include "supervisor/process_scope_controller_reset.hpp"
#include <stdexcept>
#include <string>
#include <string_view>

namespace {
void expect_invalid(std::string_view input) {
    try {
        (void)spatial::supervisor::detail::stale_subtree_controller_disable_command(input);
    } catch (const std::invalid_argument&) { return; }
    throw std::runtime_error("invalid controller state was accepted");
}
}

namespace {
void expect_io_invalid(std::string_view input) {
    try { (void)spatial::supervisor::detail::parse_owned_io_limits(input); }
    catch (const std::invalid_argument&) { return; }
    throw std::runtime_error("invalid io.max state was accepted");
}
}

int main() {
    using spatial::supervisor::detail::stale_subtree_controller_disable_command;
    if (!stale_subtree_controller_disable_command("").empty()) return 1;
    if (stale_subtree_controller_disable_command("cpu memory pids io\n") !=
        "-cpu -memory -pids -io\n") return 2;
    if (stale_subtree_controller_disable_command("io cpu\n") != "-cpu -io\n") return 3;
    expect_invalid("cpu unexpected\n");
    expect_invalid("cpu cpu\n");
    expect_invalid("+cpu\n");

    using spatial::supervisor::detail::stale_io_limit_reset_commands;
    using spatial::supervisor::detail::io_limits_are_unlimited;
    if (!stale_io_limit_reset_commands("").empty() || !io_limits_are_unlimited("")) return 4;
    const std::string finite = "8:0 rbps=1048576 wbps=max\n259:2 riops=4 wiops=9\n";
    const auto reset = stale_io_limit_reset_commands(finite);
    if (reset != "8:0 rbps=max wbps=max riops=max wiops=max\n259:2 rbps=max wbps=max riops=max wiops=max\n") return 5;
    // Kernel io.max keeps device rows after all limits become unlimited.
    const std::string kernel_unlimited = "8:0 rbps=max wbps=max riops=max wiops=max\n259:2 rbps=max wbps=max riops=max wiops=max\n";
    if (!io_limits_are_unlimited(kernel_unlimited)) return 6;
    if (io_limits_are_unlimited(finite)) return 7;
    expect_io_invalid("8:0 rbps=1 nonsense=2\n");
    expect_io_invalid("8:0 rbps=1 rbps=2\n");
    expect_io_invalid("bad-device rbps=1\n");
    return 0;
}
