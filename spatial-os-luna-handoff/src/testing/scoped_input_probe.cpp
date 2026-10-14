#include "x11/input.hpp"
#include "core/protocol.hpp"
#include <iostream>

// Two production input owners in one process. Revoking one must preserve the other.
int main(int argc, char** argv) {
    try {
        if (argc != 3) throw std::invalid_argument("Expected two sealed configuration descriptors");
        spatial::NativeInput input(std::stoi(argv[1])), survivor(std::stoi(argv[2]));
        spatial::native_input::Request open; open.mutable_open();
        const auto opened = survivor.call(open);
        if (opened.code() != spatial::native_input::OK || !opened.seat())
            throw std::runtime_error("Independent input owner did not open");
        spatial::wire::RenderResult ready; ready.set_protocol_version(1); ready.mutable_ready();
        spatial::write_frame(std::cout, ready);
        std::string bytes;
        while (spatial::read_frame(std::cin, bytes)) {
            spatial::native_input::Request request;
            if (!request.ParseFromString(bytes)) throw std::invalid_argument("Malformed input request");
            spatial::wire::RenderResult reply;
            reply.set_protocol_version(1); reply.set_request_id(request.request_id());
            reply.mutable_ready(); // Successful envelope precedes the unchanged native reply.
            spatial::native_input::Reply result;
            try { result = input.exchange(request); }
            catch (const std::exception& error) {
                reply.mutable_error()->set_code(spatial::wire::UNAVAILABLE);
                reply.mutable_error()->set_message(error.what());
            }
            spatial::native_input::Request status; status.mutable_status()->set_id(opened.seat());
            if (survivor.call(status).code() != spatial::native_input::OK)
                throw std::runtime_error("Independent input owner stopped");
            spatial::write_frame(std::cout, reply);
            if (!reply.has_error()) spatial::write_frame(std::cout, result);
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "scoped input probe: " << error.what() << '\n'; return 2;
    }
}
