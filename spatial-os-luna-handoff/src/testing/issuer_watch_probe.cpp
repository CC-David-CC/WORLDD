#include "identity/issuer_watch.hpp"
#include <chrono>
#include <csignal>
#include <iostream>
#include <stdexcept>
#include <thread>

// Do not consume updates. Verify the production queue reports overflow as failure.
int main(int argc, char** argv) {
    signal(SIGPIPE, SIG_IGN);
    try {
        if (argc != 2) throw std::runtime_error("Expected a client configuration path");
        spatial::identity::IssuerWatcher watcher(argv[1], 0);
        const auto deadline = std::chrono::steady_clock::now()+std::chrono::seconds(3);
        while (!watcher.failed() && std::chrono::steady_clock::now() < deadline)
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        const auto update = watcher.poll();
        if (!watcher.failed() || !update || !update->has_error() ||
                update->error().code() != spatial::core::v1::RESOURCE_EXHAUSTED || watcher.poll())
            throw std::runtime_error("Queue overflow did not discard stale updates and report permanent failure");
        return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 2; }
}
