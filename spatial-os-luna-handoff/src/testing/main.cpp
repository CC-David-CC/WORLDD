#include "platform/worker.hpp"
#include <iostream>
#include <string_view>
#include <cstdlib>

int main(int argc, char** argv) {
    if (argc != 3 || std::string_view(argv[1]) != "--state-dir" || !std::filesystem::path(argv[2]).is_absolute()) {
        std::cerr << "Usage: spatial-test-host --state-dir ABSOLUTE_DIRECTORY\n"; return 2;
    }
    try {
        const auto executable = std::filesystem::read_symlink("/proc/self/exe").parent_path() / "worldd";
        const char* broker_resources = std::getenv("SPATIAL_TEST_BROKER_CONFIG");
        spatial::WorldWorker worker(executable, argv[2], broker_resources ? broker_resources : "");
        worker.proxy();
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "spatial-test-host: " << error.what() << '\n'; return 2;
    }
}
