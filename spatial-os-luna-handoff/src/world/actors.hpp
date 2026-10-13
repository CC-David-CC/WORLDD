#pragma once
#include "world/store.hpp"
#include "core/navigation.hpp"
#include "grants.pb.h"
#include "broker.pb.h"
#include "geometry_view.pb.h"
#include <array>
#include <map>
#include <set>
#include <functional>

namespace spatial {
class Surfaces;
class RenderWorker;
class BrokerClient;
wire::RuntimeConfig load_runtime_config(const std::filesystem::path& path);

// One actor engine serves fixture and monotonic clocks through the same tick.
class Actors {
public:
    Actors(WorldStore& store, const wire::RuntimeConfig& config, bool manual_clock, Surfaces* surfaces = nullptr, RenderWorker* renderer = nullptr, BrokerClient* broker = nullptr);
    using Authority = std::function<bool(const std::string&, const std::vector<std::string>&)>;
    wire::ActorOpenReply open(const wire::ActorOpenRequest& request, bool require_observation_grant = false, Authority authority = {},
                              std::optional<spatial::authority::v1::ActorRef> principal = {}, bool local_application_output = true);
    void consume_sequence(const std::string& token, std::uint64_t sequence);
    void refresh_authority();
    wire::ActorStateReply inspect(const std::string& token);
    wire::ActorCloseReply close(const std::string& token);
    std::string depart(const std::string& token);
    wire::ActorActionReply act(const wire::ActorActionRequest& request);
    wire::RenderScene scene(const std::string& token, bool geometry_only = false);
    identity::v1::ActorGeometryView geometry_view(const std::string& token, const std::string& world_id,
        const std::string& actor_id, const std::string& view_id, std::uint64_t authority_generation,
        std::uint64_t sequence);
    bool application_observation_valid(const std::string& token);
    wire::WorldCheckpointReply checkpoint();
    wire::WorldCheckpointReply checkpoint_closed(const std::string& actor_id);
    wire::ClockReply advance(const wire::ClockAdvanceRequest& request);
    void tick(double monotonic_seconds);
    void flush_input();
    bool cancellation_settled() const;
    const wire::Entity* find(const std::string& id) const;
    bool room_in_use(const std::string& id) const;
    bool manual_clock() const { return manual_clock_; }
private:
    struct ObservedApplication {
        wire::SurfaceHandle handle;
        workspace::v1::ApplicationRef application;
    };
    struct ObjectHold {
        wire::Entity original;
        wire::Pose preview;
    };
    struct Actor {
        wire::Entity entity;
        std::string token;
        std::set<std::string> grants;
        Authority authority;
        std::optional<spatial::authority::v1::ActorRef> principal;
        bool require_observation_grant = false;
        bool local_application_output = true;
        std::array<double, 6> axes{};
        std::uint64_t sequence = 0;
        double expires = 0;
        wire::AimReply aim;
        wire::Vec3 direction;
        wire::NativeActorInput native;
        wire::AimHit capture;
        std::string lease;
        std::optional<workspace::v1::ApplicationControlLease> application_control;
        bool application_input = false;
        bool capture_motion_pending = false;
        bool interaction_reset_pending = false;
        bool close_pending = false;
        bool departure_pending = false;
        std::vector<ObservedApplication> observed_applications;
        std::optional<ObjectHold> object;
        wire::ErrorCode object_error = wire::NO_TARGET;
    };
    Actor& authenticated(const std::string& token);
    bool granted(const Actor&, const std::string& right, const std::vector<std::string>& components = {}) const;
    std::set<std::string> observable_applications(const Actor&, const std::vector<ObservedApplication>&);
    std::string selected_workspace(const Actor& actor);
    void step(double target);
    void refresh_native();
    void release_input(Actor& actor, bool close = false, bool keep_reservation = false);
    void poll_control();
    void drain_control_releases();
    void authorize_input(Actor& actor, const wire::SurfaceHandle& target);
    void finish_input(Actor& actor);
    void send_input(Actor& actor, const wire::RendererInputCommand& command);
    wire::AimHit input_target(Actor& actor, bool captured = true);
    std::string control_group(const wire::SurfaceRecord& surface) const;
    void route_input(Actor& actor, const wire::ActorActionRequest& request);
    void move_capture(Actor& actor, const wire::Vec3& direction);
    wire::ActorObservation observe(Actor& actor, const wire::ActorObserveRequest& request);
    void refresh_collisions();
    void object_action(Actor& actor, const wire::ActorActionRequest& request);
    void cancel_object(Actor& actor);
    void refresh_objects();
    std::uint64_t collision_revision_ = 0;
    std::map<std::string, RoomGeometry> collision_rooms_;
    WorldStore& store_;
    Surfaces* surfaces_;
    RenderWorker* renderer_;
    BrokerClient* broker_;
    std::map<std::string, std::string> leases_;
    std::vector<workspace::v1::ApplicationControlLease> control_releases_;
    bool flushing_input_ = false;
    std::map<std::string, std::string> object_leases_;
    wire::RuntimeConfig config_;
    bool manual_clock_;
    double now_ = 0;
    std::map<std::string, Actor> actors_;
    std::map<std::string, std::string> sessions_;
};
} // namespace spatial
