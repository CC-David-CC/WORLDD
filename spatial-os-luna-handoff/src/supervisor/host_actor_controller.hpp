#pragma once

#include "supervisor/session_coordinator.hpp"
#include "world/actor_transition_coordinator.hpp"
#include "host_actor_controller.pb.h"
#include <functional>
#include <map>
#include <mutex>
#include <optional>

namespace spatial::supervisor {

// Trusted host API. Do not expose this object or its registration methods to actors.
class HostActorController final {
public:
    using ChannelFactory = ActorTransitionCoordinator::DestinationFactory;
    using PresentationInspect = std::function<render::v1::PresentationState(const workspace::v1::ControlOwner&)>;
    using PresentationSelect = std::function<render::v1::PresentationState(const workspace::v1::ControlOwner&)>;
    HostActorController(SessionCoordinator& session, std::string current_world,
                        std::unique_ptr<ActorSessionClient> current_client);
    // Explicit fixture seam. Production callers must use the SessionCoordinator constructor.
    HostActorController(PresentationInspect inspect, PresentationSelect select, std::string current_world,
                        std::unique_ptr<ActorSessionClient> current_client);
    ~HostActorController();
    HostActorController(const HostActorController&) = delete;
    HostActorController& operator=(const HostActorController&) = delete;

    void register_world(std::string world_id, workspace::v1::ControlOwner retained_owner,
                        ChannelFactory actor_channel);
    void register_remote_world(std::string world_id, ChannelFactory actor_channel);
    identity::v1::HostActorTransitionResult transition(
        const identity::v1::HostActorTransitionRequest& request);

    // Each semantic operation is bounded by the client's transport deadline.
    // Callers must carry the generation observed with their actor context.
    identity::v1::WorldSessionReply inspect(std::uint64_t generation);
    identity::v1::WorldSessionReply action(std::uint64_t generation,
                                         const core::v1::ActorActionRequest& request);
    std::uint64_t generation() const;
    bool control_available() const;
    identity::v1::ActorGeometryView geometry_view(std::uint64_t generation);
    identity::v1::HostGeometryStatus geometry_status(std::uint64_t generation) const;
    void start_geometry_presentation();
    void pump_geometry_presentation();
    std::string current_world() const;

private:
    bool retire_geometry_locked() noexcept;
    void clear_foreground_locked();
    void refresh_geometry_locked();
    struct Destination { std::optional<workspace::v1::ControlOwner> owner; ChannelFactory channel; };
    SessionCoordinator* session_ = nullptr;
    std::optional<render::v1::GeometryPresentationTarget> geometry_target_;
    identity::v1::HostGeometryStatus geometry_status_;
    PresentationInspect inspect_;
    PresentationSelect select_;
    mutable std::mutex mutex_;
    std::map<std::string, Destination> destinations_;
    std::string current_world_;
    std::unique_ptr<ActorSessionClient> current_client_;
    std::unique_ptr<ActorSessionClient> quarantined_client_;
    std::uint64_t generation_ = 0;
    bool transitioning_ = false;
    bool control_fenced_ = false;
};
}
