#pragma once
#include "control/actor_control.hpp"
#include "world/actor_transition_coordinator.hpp"
#include "scripted_actor.pb.h"
#include <map>

namespace spatial::control {
// One ordinary actor, one sequencer, one active world. No host management authority.
class ScriptedActor final {
public:
    explicit ScriptedActor(const v1::ScriptedActorConfig&);
    ~ScriptedActor();
    ScriptedActor(const ScriptedActor&)=delete;
    ScriptedActor& operator=(const ScriptedActor&)=delete;
    v1::ScriptedActorContext context() const;
    core::v1::ActorStateReply inspect(std::uint64_t);
    core::v1::ActorActionReply propose(std::uint64_t,const core::v1::ActorActionRequest&);
    identity::v1::ActorTransitionResult transition(std::uint64_t,const v1::ScriptedActorTransition&);
private:
    void require_current(std::uint64_t) const;
    void create_controls(std::uint64_t initial_sequence);
    std::map<std::string,std::filesystem::path> worlds_;
    std::string world_,actor_;
    std::uint64_t generation_=0;
    std::unique_ptr<ActorSessionClient> client_;
    std::unique_ptr<ActorControl> controls_;
};
}
