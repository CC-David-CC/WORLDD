#include "workspace/storage_client.hpp"
#include "core/protocol.hpp"
#include <iostream>
#include <stdexcept>

// Thin test transport over the production authenticated client.
int main(int argc, char** argv) {
    namespace pb = spatial::workspace::v1;
    try {
        if (argc != 3 || std::cin.get() != '!') throw std::invalid_argument("Expected operator startup gate");
        spatial::StorageClient client(std::stoi(argv[1]), argv[2]);
        std::string frame;
        while (spatial::read_frame(std::cin, frame)) {
            pb::StorageOwnerRequest request;
            if (!request.ParseFromString(frame)) throw std::invalid_argument("Invalid storage test request");
            pb::StorageOwnerReply reply; reply.set_protocol_version(1); reply.set_request_id(request.request_id());
            *reply.mutable_state() = client.call(std::move(request));
            spatial::write_frame(std::cout, reply);
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "storage-client-probe: " << error.what() << '\n';
        return 1;
    }
}
