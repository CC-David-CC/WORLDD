#include "supervisor/control_reply.hpp"
#include "core/protocol.hpp"
#include <iostream>
#include <stdexcept>

int main() {
    try {
        std::string bytes;
        spatial::workspace::v1::ControlSupervisorRequest request;
        spatial::workspace::v1::ControlSupervisorReply reply;
        if (!spatial::read_frame(std::cin, bytes) || !request.ParseFromString(bytes))
            throw std::invalid_argument("Expected control request frame");
        if (!spatial::read_frame(std::cin, bytes) || !reply.ParseFromString(bytes))
            throw std::invalid_argument("Expected control reply frame");
        spatial::supervisor::validate_control_reply(request, reply);
        spatial::write_frame(std::cout, reply);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "control reply rejected: " << error.what() << '\n';
        return 2;
    }
}
