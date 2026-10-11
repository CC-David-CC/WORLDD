#include "platform/broker.hpp"
#include <csignal>
#include <fcntl.h>
#include <iostream>
#include <unistd.h>

// Transport-only witness. BrokerProcess owns the real broker and storage service.
int main(int argc, char** argv) {
    try {
        const bool envelope = argc == 4 && std::string(argv[3]) == "--broker-envelope";
        if (argc != 3 && !envelope) throw std::invalid_argument("Expected state directory and broker process configuration");
        std::signal(SIGPIPE, SIG_IGN);
        const auto binary = std::filesystem::canonical("/proc/self/exe").parent_path();
        spatial::BrokerProcess process(binary / "workspaced", argv[1], false, argv[2]);
        spatial::BrokerClient client(fcntl(process.channel(), F_DUPFD_CLOEXEC, 3), process.pid());
        std::string frame;
        while (spatial::read_frame(std::cin, frame)) {
            spatial::wire::Request request;
            spatial::broker_wire::Request outer;
            if (envelope) {
                if (!outer.ParseFromString(frame) || !outer.has_management() || outer.protocol_version() != 1)
                    throw std::invalid_argument("Expected one broker management envelope");
                spatial::validate_message(outer); request = outer.management();
            } else if (!request.ParseFromString(frame)) throw std::invalid_argument("Invalid broker witness request");
            spatial::validate_message(request);
            if (request.protocol_version() != 1) throw std::invalid_argument("Invalid broker witness version");
            auto reply = client.call(request);
            reply.set_request_id(request.request_id());
            if (envelope) {
                spatial::broker_wire::Reply result; result.set_protocol_version(1); result.set_request_id(outer.request_id());
                *result.mutable_management() = std::move(reply); spatial::write_frame(std::cout, result);
            } else spatial::write_frame(std::cout, reply);
        }
        process.finish();
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "storage broker launch witness: " << error.what() << '\n'; return 2;
    }
}
