#include "identity/issuer_client.hpp"
#include <csignal>
#include <iostream>
#include <stdexcept>
#include <string>
#include <utility>

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    signal(SIGPIPE, SIG_IGN);
    try {
        spatial::identity::IssuerClient client(argv[1]);
        client.set_tick([] { throw std::runtime_error("diagnostic-secret-marker"); });
        spatial::identity::v1::IssuerRequest request;
        request.set_protocol_version(1); request.mutable_info();
        try { (void)client.call(std::move(request)); return 3; }
        catch (const std::runtime_error& error) {
            const auto diagnostic = client.failure_diagnostic();
            if (std::string(error.what()) != "diagnostic-secret-marker" || !client.failed() ||
                diagnostic.find("category=tick_callback") == std::string::npos ||
                diagnostic.find("elapsed_ms=") == std::string::npos ||
                diagnostic.find("timeout_ms=") == std::string::npos ||
                diagnostic.find("gap_ms=") == std::string::npos ||
                diagnostic.find("tick_ms=") == std::string::npos ||
                diagnostic.find("progress=") == std::string::npos ||
                diagnostic.find("diagnostic-secret-marker") != std::string::npos) return 4;
            std::cout << diagnostic << '\n';
            return 0;
        }
    } catch (...) { return 5; }
}
