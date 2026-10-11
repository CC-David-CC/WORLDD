#include "supervisor/storage_launch.hpp"
#include "core/protocol.hpp"
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <iterator>
#include <unistd.h>

int main(int argc, char** argv) {
    try {
        if (argc != 4) throw std::invalid_argument("Expected private policy, storage channel, and broker handle");
        std::ifstream file(argv[1], std::ios::binary);
        const std::string bytes((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
        spatial::workspace::v1::StorageOwnerLaunchConfig config;
        if (!file || bytes.size() > 65536 || !config.ParseFromString(bytes)) throw std::invalid_argument("Invalid launch probe policy");
        spatial::supervisor::StorageOwnerLaunch launch(config, config.volume_root(), std::stoi(argv[2]), std::stoi(argv[3]), STDERR_FILENO);
        // The launch class borrowed these inputs. The witness must not retain peer copies.
        close(std::stoi(argv[2])); close(std::stoi(argv[3]));
        std::string frame;
        while (spatial::read_frame(std::cin, frame)) {
            spatial::workspace::v1::StorageLaunchControl request;
            spatial::workspace::v1::StorageLaunchResult reply; reply.set_protocol_version(1); reply.set_unit(launch.unit());
            try {
                if (!request.ParseFromString(frame)) throw std::invalid_argument("Invalid launch control frame");
                spatial::validate_message(request); reply.set_request_id(request.request_id());
                if (request.protocol_version() != 1) throw std::invalid_argument("Invalid launch control version");
                if (request.has_stop() && request.stop()) { launch.stop(); reply.set_stopped(true); }
                else { launch.check_alive(); reply.set_running(true); }
            } catch (const std::exception& error) { reply.set_error(error.what()); }
            spatial::write_frame(std::cout, reply);
            if (reply.has_stopped()) return 0;
        }
        launch.stop(); return 0;
    } catch (const std::exception& error) { std::cerr << "storage launch probe: " << error.what() << '\n'; return 2; }
}
