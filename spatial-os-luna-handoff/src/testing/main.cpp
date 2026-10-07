#include "core/protocol.hpp"

#include <filesystem>
#include <iostream>
#include <string>

// This adapter owns framing and test state only. All request behavior lives in
// production modules. Future world/render requests forward to their workers.
int main(int argc, char** argv) {
    if (argc != 3 || std::string_view(argv[1]) != "--state-dir" ||
        !std::filesystem::path(argv[2]).is_absolute()) {
        std::cerr << "Usage: spatial-test-host --state-dir ABSOLUTE_DIRECTORY\n";
        return 2;
    }
    try {
        std::filesystem::create_directories(argv[2]);
        if (!std::filesystem::is_directory(argv[2])) return 2;
    } catch (const std::filesystem::filesystem_error&) {
        std::cerr << "Cannot open the test state directory\n";
        return 2;
    }
    std::string frame;
    bool oversized = false;
    char byte;
    while (std::cin.get(byte)) {
        if (byte == '\n') {
            const auto response = oversized
                ? spatial::error_response(nullptr, "INVALID_ARGUMENT", "Frame exceeds size limit")
                : spatial::handle_line(frame);
            std::cout << response.dump() << '\n' << std::flush;
            if (!std::cout) return 1;
            frame.clear();
            oversized = false;
        } else if (frame.size() < spatial::max_frame_bytes) {
            frame.push_back(byte);
        } else {
            oversized = true;
        }
    }
    if (!frame.empty() || oversized) {
        std::cout << spatial::error_response(nullptr, "INVALID_ARGUMENT", "Truncated JSON line").dump() << '\n';
    }
    return std::cin.bad() ? 1 : 0;
}
