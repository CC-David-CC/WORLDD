#include "platform/render_worker.hpp"
#include <iostream>
#include <stdexcept>

// Thin protocol-test boundary. RenderWorker performs every compositor check.
int main(int argc, char** argv) {
    try {
        if (argc != 3) throw std::invalid_argument("Expected fixture compositor and state directory");
        std::string bytes;
        if (!spatial::read_frame(std::cin, bytes)) throw std::invalid_argument("Missing observation command");
        spatial::wire::RenderCommand request;
        if (!request.ParseFromString(bytes)) throw std::invalid_argument("Invalid observation command");
        spatial::validate_message(request);
        spatial::RenderWorker client(argv[1], argv[2], ":protocol-fixture");
        spatial::wire::RenderResult reply;
        reply.set_protocol_version(1); reply.set_request_id(request.request_id());
        *reply.mutable_visibility() = client.observe(request.scene(), request.width(), request.height(), request.include_pixels());
        spatial::write_frame(std::cout, reply);
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 2;
    }
}
