#include "identity/issuer_client.hpp"
#include "core/protocol.hpp"
#include <csignal>
#include <iostream>
#include <sys/stat.h>

int main(int argc, char** argv) {
    namespace identity = spatial::identity::v1;
    umask(0077); signal(SIGPIPE, SIG_IGN);
    try {
        if (argc != 3 || std::string_view(argv[1]) != "--config")
            throw std::invalid_argument("Usage: spatial-issuer-client --config ABSOLUTE_FILE");
        spatial::identity::IssuerClient client(argv[2]);
        std::string bytes;
        while (spatial::read_frame(std::cin, bytes)) {
            identity::IssuerRequest request; identity::IssuerReply reply; reply.set_protocol_version(1);
            if (!request.ParseFromString(bytes)) {
                reply.mutable_error()->set_code(spatial::wire::INVALID_ARGUMENT);
                reply.mutable_error()->set_message("Malformed issuer request payload");
            } else {
                reply.set_request_id(request.request_id());
                try {
                    spatial::validate_message(request);
                    if (request.protocol_version() != 1) {
                        reply.mutable_error()->set_code(spatial::wire::UNSUPPORTED_VERSION);
                        reply.mutable_error()->set_message("Unsupported issuer protocol version");
                    } else reply = client.call(std::move(request));
                } catch (const std::invalid_argument& error) {
                    if (client.failed()) throw;
                    reply.mutable_error()->set_code(spatial::wire::INVALID_ARGUMENT); reply.mutable_error()->set_message(error.what());
                }
            }
            spatial::write_frame(std::cout, reply);
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "spatial-issuer-client: " << error.what() << '\n'; return 2;
    }
}
