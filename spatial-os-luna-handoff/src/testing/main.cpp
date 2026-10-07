#include "core/protocol.hpp"
#include <filesystem>
#include <iostream>
#include <string>

// Thin adapter: state-directory setup and the production protobuf boundary.
// All geometry, validation, and dispatch run in the shared production library.
int main(int argc, char** argv) {
    if (argc != 3 || std::string_view(argv[1]) != "--state-dir" ||
        !std::filesystem::path(argv[2]).is_absolute()) {
        std::cerr << "Usage: spatial-test-host --state-dir ABSOLUTE_DIRECTORY\n";
        return 2;
    }
    try {
        std::filesystem::create_directories(argv[2]);
        if (!std::filesystem::is_directory(argv[2])) return 2;
        std::string frame;
        while (spatial::read_frame(std::cin, frame)) {
            spatial::write_frame(std::cout, spatial::handle_frame(frame));
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 2;
    }
}
