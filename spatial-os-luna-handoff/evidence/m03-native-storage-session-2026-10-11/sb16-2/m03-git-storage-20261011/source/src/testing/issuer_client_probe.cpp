#include "identity/issuer_client.hpp"
#include <chrono>
#include <csignal>
#include <iostream>
#include <stdexcept>

// A thin caller checks cancellation in the production client library.
int main(int argc, char** argv) {
    signal(SIGPIPE, SIG_IGN);
    try {
        if (argc != 2) throw std::runtime_error("Expected a client configuration path");
        spatial::identity::IssuerClient client(argv[1]);
        const auto start = std::chrono::steady_clock::now(); unsigned ticks = 0;
        client.set_tick([&] {
            ++ticks;
            if (std::chrono::steady_clock::now()-start >= std::chrono::milliseconds(60))
                throw std::runtime_error("probe cancellation");
        });
        spatial::identity::v1::IssuerRequest request; request.set_protocol_version(1); request.mutable_info();
        try { client.call(request); throw std::logic_error("Cancellation did not interrupt the call"); }
        catch (const std::runtime_error& error) {
            if (std::string(error.what()) != "probe cancellation") throw;
        }
        if (!client.failed() || ticks < 2) throw std::runtime_error("Cancellation did not disable the client");
        const auto previous = ticks;
        try { client.call(request); throw std::logic_error("Failed client allowed another request"); }
        catch (const std::runtime_error&) {}
        if (ticks != previous) throw std::runtime_error("Failed client retried I/O");
        return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 2; }
}
