#pragma once
#include "world/store.hpp"
#include "core/navigation.hpp"
#include <array>
#include <map>
#include <set>

namespace spatial {
class Surfaces;
class RenderWorker;
wire::RuntimeConfig load_runtime_config(const std::filesystem::path& path);

// One actor engine serves fixture and monotonic clocks through the same tick.
class Actors {
public:
    Actors(WorldStore& store, const wire::RuntimeConfig& config, bool manual_clock, Surfaces* surfaces = nullptr, RenderWorker* renderer = nullptr);
    wire::ActorOpenReply open(const wire::ActorOpenRequest& request);
    wire::ActorStateReply inspect(const std::string& token);
    wire::ActorCloseReply close(const std::string& token);
    wire::ActorActionReply act(const wire::ActorActionRequest& request);
    wire::RenderScene scene(const std::string& token);
    wire::WorldCheckpointReply checkpoint();
    wire::ClockReply advance(const wire::ClockAdvanceRequest& request);
    void tick(double monotonic_seconds);
    void flush_input();
    const wire::Entity* find(const std::string& id) const;
    bool room_in_use(const std::string& id) const;
    bool manual_clock() const { return manual_clock_; }
private:
    struct Actor {
        wire::Entity entity;
        std::string token;
        std::set<std::string> grants;
        std::array<double, 6> axes{};
        std::uint64_t sequence = 0;
        double expires = 0;
        wire::AimReply aim;
        wire::Vec3 direction;
        wire::NativeActorInput native;
        wire::AimHit capture;
        std::string lease;
        bool capture_motion_pending = false;
        bool interaction_reset_pending = false;
    };
    Actor& authenticated(const std::string& token);
    void step(double target);
    void refresh_native();
    void release_input(Actor& actor, bool close = false);
    wire::AimHit input_target(Actor& actor, bool captured = true);
    std::string control_group(const wire::SurfaceRecord& surface) const;
    void route_input(Actor& actor, const wire::ActorActionRequest& request);
    void move_capture(Actor& actor, const wire::Vec3& direction);
    wire::ActorObservation observe(Actor& actor, const wire::ActorObserveRequest& request);
    void refresh_collisions();
    std::uint64_t collision_revision_ = 0;
    std::map<std::string, RoomGeometry> collision_rooms_;
    WorldStore& store_;
    Surfaces* surfaces_;
    RenderWorker* renderer_;
    std::map<std::string, std::string> leases_;
    wire::RuntimeConfig config_;
    bool manual_clock_;
    double now_ = 0;
    std::map<std::string, Actor> actors_;
    std::map<std::string, std::string> sessions_;
};
} // namespace spatial
