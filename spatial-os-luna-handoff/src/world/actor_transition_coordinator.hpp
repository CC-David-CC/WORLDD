#pragma once

#include "world/actor_session_client.hpp"
#include "actor_transition.pb.h"
#include <functional>
#include <memory>

namespace spatial {
class ActorTransitionCoordinator {
public:
    using DestinationFactory = std::function<std::unique_ptr<ActorSessionClient>()>;
    struct Outcome {
        identity::v1::ActorTransitionResult result;
        // Present only after activation was confirmed. Caller owns the active client.
        std::unique_ptr<ActorSessionClient> destination;
    };

    // The factory must return a fresh client for destination_world. Destroying it
    // closes an abandoned prepared channel. The source client remains caller-owned.
    static Outcome transition(ActorSessionClient& source, std::string source_world,
                              std::string destination_world,
                              std::string transition_id, std::uint64_t departure_sequence,
                              DestinationFactory make_destination,
                              std::string destination_room = {},
                              const core::v1::Pose* destination_pose = nullptr);
};
}
