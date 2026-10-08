#pragma once
#include "world/actors.hpp"
#include <functional>
#include <memory>
#include <vector>

namespace spatial {
// Actor-only channels are inherited unnamed local stream sockets.
// The supervisor distributes their peer descriptors outside workspace processes.
void prepare_actor_channel(int fd);
class IdentitySessions {
public:
    IdentitySessions(const std::filesystem::path& config, const std::vector<int>& channels,
                     WorldStore& world, Actors& actors, BrokerClient* broker = nullptr);
    ~IdentitySessions();
    IdentitySessions(const IdentitySessions&) = delete;
    IdentitySessions& operator=(const IdentitySessions&) = delete;
    void set_tick(std::function<void()> callback);
    void tick();
    void pump();
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
