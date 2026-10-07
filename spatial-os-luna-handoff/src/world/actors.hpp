#pragma once
#include "world/store.hpp"
#include <array>
#include <map>
#include <set>

namespace spatial {
wire::RuntimeConfig load_runtime_config(const std::filesystem::path& path);

// One actor engine serves fixture and monotonic clocks through the same tick.
class Actors {
public:
    Actors(WorldStore& store, const wire::RuntimeConfig& config, bool manual_clock);
    wire::ActorOpenReply open(const wire::ActorOpenRequest& request);
    wire::ActorStateReply inspect(const std::string& token);
    wire::ActorCloseReply close(const std::string& token);
    wire::ActorActionReply act(const wire::ActorActionRequest& request);
    wire::WorldCheckpointReply checkpoint();
    wire::ClockReply advance(const wire::ClockAdvanceRequest& request);
    void tick(double monotonic_seconds);
    const wire::Entity* find(const std::string& id) const;
    bool manual_clock() const { return manual_clock_; }
private:
    struct Actor {
        wire::Entity entity;
        std::string token;
        std::set<std::string> grants;
        std::array<double, 6> axes{};
        std::uint64_t sequence = 0;
        double expires = 0;
    };
    Actor& authenticated(const std::string& token);
    void step(double target);
    WorldStore& store_;
    wire::RuntimeConfig config_;
    bool manual_clock_;
    double now_ = 0;
    std::map<std::string, Actor> actors_;
    std::map<std::string, std::string> sessions_;
};
} // namespace spatial
