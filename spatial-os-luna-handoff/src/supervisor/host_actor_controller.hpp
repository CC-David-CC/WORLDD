#pragma once

#include "supervisor/session_coordinator.hpp"
#include "world/actor_transition_coordinator.hpp"
#include "host_actor_controller.pb.h"
#include "control/menu.hpp"
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
    // Enable before legacy actions or transitions. These objects never escape the trusted host.
    void start_controls(const control::v1::ControlProfiles&, control::ControlsMenu::Persist);
    core::v1::ActorActionReply propose(std::uint64_t generation, const core::v1::ActorActionRequest&);
    identity::v1::HostActorTransitionResult transition_proposed(std::uint64_t generation,
        const identity::v1::HostActorTransitionRequest&);
    control::v1::ControlStatus device_event(std::uint64_t generation, const control::v1::CapturedDeviceEvent&);
    control::v1::ControlStatus tick_controls(std::uint64_t generation);
    control::v1::ControlStatus control_status(std::uint64_t generation) const;
    control::v1::ControlMenuView controls_menu(std::uint64_t generation) const;
    // Obtain on the owner thread before exchanges. The watchdog only requests socket shutdown.
    std::weak_ptr<const ActorSessionInterrupt> interrupt_handle(std::uint64_t generation);
    std::uint64_t generation() const;
    bool control_available() const;
    identity::v1::ActorGeometryView geometry_view(std::uint64_t generation);
    identity::v1::HostGeometryStatus geometry_status(std::uint64_t generation) const;
    void start_geometry_presentation();
    void pump_geometry_presentation();
    std::string current_world() const;

private:
    identity::v1::HostActorTransitionResult transition_locked(const identity::v1::HostActorTransitionRequest&);
    void require_controls_locked(std::uint64_t generation) const;
    void create_controls_locked(const control::v1::ControlProfiles&, std::uint64_t initial_sequence);
    control::v1::ControlStatus control_status_locked() const;
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
    bool legacy_used_ = false, shared_enabled_ = false;
    std::uint64_t actor_sequence_ = 0, capture_generation_ = 0, capture_sequence_ = 0;
    control::ControlsMenu::Persist persist_controls_;
    std::unique_ptr<control::ActorControl> controls_;
    std::unique_ptr<control::DeviceAdapters> adapters_;
    std::unique_ptr<control::ControlsMenu> controls_menu_;
};
}
