#include "workspace/storage_owner.hpp"
#include "core/protocol.hpp"
#include <fstream>
#include <iostream>
#include <stdexcept>

// Test-only stdin transport. Production must authenticate its private broker.
int main(int argc, char** argv) {
    namespace pb = spatial::workspace::v1;
    try {
        if (argc != 2) throw std::invalid_argument("Expected test-owned binary configuration");
        std::ifstream input(argv[1], std::ios::binary);
        pb::StorageOwnerConfig config;
        if (!input || !config.ParseFromIstream(&input)) throw std::invalid_argument("Invalid storage configuration");
        spatial::StorageOwner owner(std::move(config));
        std::string frame;
        while (spatial::read_frame(std::cin, frame)) {
            pb::StorageOwnerRequest request;
            if (!request.ParseFromString(frame)) throw std::invalid_argument("Invalid storage request");
            spatial::write_frame(std::cout, owner.handle(request));
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "storage-owner-probe: " << error.what() << '\n';
        return 1;
    }
}
