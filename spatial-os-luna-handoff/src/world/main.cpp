#include "world/store.hpp"
#include <sys/stat.h>
#include <iostream>

int main(int argc, char** argv) {
    if (argc != 3 || std::string_view(argv[1]) != "--state-dir" || !std::filesystem::path(argv[2]).is_absolute()) {
        std::cerr << "Usage: worldd --state-dir ABSOLUTE_DIRECTORY\n"; return 2;
    }
    // Inherited stdio is a trusted management channel. No listener or actor API.
    umask(0077);
    try {
        spatial::WorldStore world(argv[2]);
        std::string frame;
        while (spatial::read_frame(std::cin, frame))
            spatial::write_frame(std::cout, spatial::handle_frame(frame, &world));
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "worldd: " << error.what() << '\n'; return 2;
    }
}
