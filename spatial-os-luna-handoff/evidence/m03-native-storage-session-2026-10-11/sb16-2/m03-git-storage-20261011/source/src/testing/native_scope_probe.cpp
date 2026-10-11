#include "core/protocol.hpp"
#include "supervisor/native_scope.hpp"
#include <iostream>
#include <memory>
#include <unistd.h>

// Thin private driver. All native authority effects use the production class.
int main(int argc, char** argv) {
    namespace pb = spatial::supervisor::v1;
    std::unique_ptr<spatial::supervisor::NativeScope> scope;
    std::uint64_t sequence = 0;
    try {
        if (argc != 2) throw std::invalid_argument("Expected sealed native configuration descriptor");
        const int config_fd = std::stoi(argv[1]);
        scope = std::make_unique<spatial::supervisor::NativeScope>(config_fd);
        close(config_fd);
        pb::NativeScopeReply ready; ready.set_protocol_version(1); *ready.mutable_ready() = scope->ready();
        spatial::write_frame(std::cout, ready);
        std::string bytes;
        while (spatial::read_frame(std::cin, bytes)) {
            pb::NativeScopeRequest request;
            if (!request.ParseFromString(bytes)) throw std::invalid_argument("Invalid native scope request");
            spatial::validate_message(request);
            if (request.protocol_version() != 1 || request.request_id() <= sequence ||
                    (!request.has_inspect() && !request.has_stop())) throw std::invalid_argument("Invalid native scope context");
            sequence = request.request_id();
            pb::NativeScopeReply reply; reply.set_protocol_version(1); reply.set_request_id(sequence);
            if (request.has_stop()) *reply.mutable_stopped() = scope->stop();
            else *reply.mutable_ready() = scope->ready();
            spatial::write_frame(std::cout, reply);
        }
        scope->stop(); return 0;
    } catch (const std::exception& error) {
        pb::NativeScopeReply reply; reply.set_protocol_version(1); reply.set_request_id(sequence);
        reply.mutable_error()->set_code(scope ? pb::NativeScopeError::CLEANUP_UNCONFIRMED : pb::NativeScopeError::INVALID_ARGUMENT);
        reply.mutable_error()->set_message(error.what()); spatial::write_frame(std::cout, reply);
        std::cerr << "native scope probe: " << error.what() << '\n';
        if (!scope) return 2; // Preserve normal sanitizer teardown for rejected configurations.
        // Do not run a second uncertain cleanup attempt after a failed stop.
        _exit(2);
    }
}
