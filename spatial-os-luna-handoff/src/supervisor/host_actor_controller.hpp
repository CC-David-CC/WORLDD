#pragma once

#include "supervisor/session_coordinator.hpp"
#include "world/actor_transition_coordinator.hpp"
#include "host_actor_controller.pb.h"
#include <functional>
#include <map>
#include <mutex>

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
    identity::v1::HostActorTransitionResult transition(
        const identity::v1::HostActorTransitionRequest& request);

    // Each semantic operation is bounded by the client's transport deadline.
    // Callers must carry the generation observed with their actor context.
    identity::v1::WorldSessionReply inspect(std::uint64_t generation);
    identity::v1::WorldSessionReply action(std::uint64_t generation,
                                         const core::v1::ActorActionRequest& request);
    std::uint64_t generation() const;
    bool control_available() const;
    std::string current_world() const;

private:
    struct Destination { workspace::v1::ControlOwner owner; ChannelFactory channel; };
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
