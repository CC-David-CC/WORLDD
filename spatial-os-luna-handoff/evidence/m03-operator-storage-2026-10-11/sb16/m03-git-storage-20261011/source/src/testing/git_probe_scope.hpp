#pragma once
#include "supervisor/process_scope.hpp"
#include <cstdlib>
#include <array>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <sstream>
#include <string>
#include <utility>
#include <sys/stat.h>
#include <sys/sysmacros.h>

inline std::unique_ptr<spatial::supervisor::ProcessScope>
git_probe_scope(const std::filesystem::path& repository) {
    const char* root = std::getenv("SPATIAL_TEST_CGROUP_ROOT");
    if (!root || !std::filesystem::path(root).is_absolute())
        throw std::runtime_error("Git probe requires a delegated cgroup root");
    struct stat info{};
    if (stat(repository.c_str(), &info) != 0 || !S_ISDIR(info.st_mode))
        throw std::runtime_error("Git probe repository is unavailable");
    const char* io_device = std::getenv("SPATIAL_TEST_IO_DEVICE");
    if (!io_device) throw std::runtime_error("Git probe requires a resolved block device for I/O limits");
    unsigned io_major = 0, io_minor = 0; char separator = 0; char extra = 0;
    std::istringstream parsed(io_device);
    if (!(parsed >> io_major >> separator >> io_minor) || separator != ':' || (parsed >> extra))
        throw std::runtime_error("Git probe received an invalid I/O device");
    spatial::supervisor::v1::ProcessScopeBudget budget;
    budget.set_memory_bytes(256ULL * 1024 * 1024);
    budget.set_swap_bytes(0);
    budget.set_processes(16);
    budget.set_cpu_microseconds(200000);
    auto* io = budget.add_io();
    io->set_major(io_major); io->set_minor(io_minor);
    io->set_read_bytes_per_second(64ULL * 1024 * 1024);
    io->set_write_bytes_per_second(16ULL * 1024 * 1024);
    auto scope = spatial::supervisor::ProcessScope::prepare(root, &budget);
    const auto path = std::filesystem::path(root) / scope->state().identity().scope_name();
    for (const auto& [name, expected] : std::array<std::pair<const char*, const char*>, 4>{{
             {"memory.max", "268435456"}, {"memory.swap.max", "0"},
             {"pids.max", "16"}, {"cpu.max", "200000 100000"}}}) {
        std::ifstream input(path / name); std::string value; std::getline(input, value);
        if (!input || value != expected) throw std::runtime_error("Git worker cgroup limit was not applied");
    }
    return scope;
}
