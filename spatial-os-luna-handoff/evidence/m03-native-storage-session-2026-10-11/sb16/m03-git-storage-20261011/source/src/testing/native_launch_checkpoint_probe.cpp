// Test-only driver for a failing authorization callback at the real native boundary.
#include "workspace/native_launch.hpp"
#include "core/protocol.hpp"
#include "world/store.hpp"
#include "security.pb.h"
#include <iostream>
#include <sys/stat.h>
#include <unistd.h>

int main(int argc, char** argv) {
    try {
        if (argc != 3) return 2;
        spatial::NativeLaunch native(std::stoi(argv[1]));
        const int denial = std::stoi(argv[2]);
        if (denial != 1 && denial != 2) return 2;
        auto keeper = native.issue("checkpoint-workspace", "keeper", [] {});
        int calls = 0; bool denied = false;
        try {
            native.issue("checkpoint-workspace", "tentative", [&] {
                if (++calls == denial) throw spatial::WorldError(spatial::wire::FORBIDDEN, "Fixture authority revoked");
            });
        } catch (const spatial::WorldError& error) {
            if (error.code != spatial::wire::FORBIDDEN) throw;
            denied = true;
        }
        if (!denied) return 3;
        native.check();
        struct stat info{};
        if (fstat(keeper->authority_fd(), &info) || info.st_size < 32) return 4;
        std::string cookie(32, '\0');
        if (pread(keeper->authority_fd(), cookie.data(), cookie.size(), info.st_size-32) != 32) return 4;
        spatial::security::v1::Reply reply; reply.set_protocol_version(1); reply.set_request_id(1);
        reply.mutable_credential()->set_cookie(cookie);
        spatial::write_frame(std::cout, reply);
        std::string bytes;
        if (!spatial::read_frame(std::cin, bytes)) return 5;
        spatial::wire::Request request;
        if (!request.ParseFromString(bytes)) return 5;
        spatial::validate_message(request);
        if (request.protocol_version() != 1 || !request.has_hello()) return 5;
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Native checkpoint probe: " << error.what() << '\n';
        return 2;
    }
}
