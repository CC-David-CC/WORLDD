#pragma once
#include "world/actors.hpp"
#include <functional>
#include <memory>
#include <vector>

namespace spatial {
// Local actor channels are inherited unnamed sockets. Remote channels use the explicit TLS listener.
// The supervisor distributes their peer descriptors outside workspace processes.
void prepare_actor_channel(int fd);
class IdentitySessions {
public:
    IdentitySessions(const std::filesystem::path& config, const std::vector<int>& channels,
                     WorldStore& world, Actors& actors, BrokerClient* broker = nullptr,
                     int listener = -1, const std::filesystem::path& tls_configuration = {});
    ~IdentitySessions();
    IdentitySessions(const IdentitySessions&) = delete;
    IdentitySessions& operator=(const IdentitySessions&) = delete;
    void set_tick(std::function<void()> callback);
    void tick();
    void pump();
    // Call only from the outer scheduler, after handlers and native cancellation finish.
    void settle();
    void shutdown();
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
