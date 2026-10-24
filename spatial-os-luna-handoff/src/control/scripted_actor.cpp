#include "control/scripted_actor.hpp"
#include "core/geometry.hpp"
#include "core/protocol.hpp"
#include "world/control_action.hpp"
#include <limits>
#include <stdexcept>

namespace spatial::control {
namespace {
void need(bool value,const char* detail){if(!value)throw std::invalid_argument(detail);}
bool identifier(const std::string& value) {
    return !value.empty() && value.size()<=256 && value.find('\0')==std::string::npos;
}
}
ScriptedActor::ScriptedActor(const v1::ScriptedActorConfig& config) {
    validate_message(config);
    need(config.protocol_version()==1 && identifier(config.initial_world_id()) &&
        config.worlds_size()>0 && config.worlds_size()<=64,"Invalid scripted actor configuration");
    for(const auto& entry:config.worlds()) {
        const auto& path=entry.tls_config_path();
        need(identifier(entry.world_id()) && !path.empty() && path.size()<=4096 &&
            path.find('\0')==std::string::npos && std::filesystem::path(path).is_absolute(),
            "Invalid scripted actor destination configuration");
        need(worlds_.emplace(entry.world_id(),path).second,"Duplicate scripted actor world");
    }
    const auto initial=worlds_.find(config.initial_world_id());
    need(initial!=worlds_.end(),"Initial scripted actor world is not configured");
    world_=initial->first;
    client_=std::make_unique<ActorSessionClient>(initial->second);
    need(client_->expected_world()==world_,"Scripted actor initial world mismatch");
    (void)client_->authenticate();
    actor_=client_->expected_actor_id();
    create_controls(0);
}
ScriptedActor::~ScriptedActor(){controls_.reset();client_.reset();}
v1::ScriptedActorContext ScriptedActor::context() const {
    v1::ScriptedActorContext result;
    result.set_generation(generation_);result.set_world_id(world_);result.set_actor_id(actor_);
    result.set_available(client_ && !client_->failed() && controls_ && controls_->available());
    return result;
}
void ScriptedActor::require_current(std::uint64_t generation) const {
    need(generation==generation_ && client_ && !client_->failed() && controls_ && controls_->available(),
        "Unavailable or stale scripted actor context");
}
void ScriptedActor::create_controls(std::uint64_t initial_sequence) {
    need(client_ && !client_->failed() && !controls_,"Cannot create scripted actor action owner");
    const auto generation=generation_;
    controls_=std::make_unique<ActorControl>(ActorContext{world_,actor_,generation},
        [this,generation](const auto& request) {
            need(generation==generation_ && client_,"Retired scripted actor action callback");
            if(!actor_input::reversible(request))return client_->action(request);
            identity::v1::WorldSessionReply result;result.set_protocol_version(1);
            *result.mutable_action()=client_->controlled_action(request);return result;
        },[this,generation] {
            need(generation==generation_ && client_,"Retired scripted actor inspection callback");
            return client_->inspect();
        },[this,generation] {if(generation==generation_)client_.reset();},initial_sequence);
}
core::v1::ActorStateReply ScriptedActor::inspect(std::uint64_t generation) {
    require_current(generation);return controls_->inspect(generation);
}
core::v1::ActorActionReply ScriptedActor::propose(std::uint64_t generation,const core::v1::ActorActionRequest& proposal) {
    require_current(generation);return controls_->propose(generation,proposal);
}
identity::v1::ActorTransitionResult ScriptedActor::transition(std::uint64_t generation,
        const v1::ScriptedActorTransition& proposal) {
    require_current(generation);validate_message(proposal);
    need(proposal.transition_id().size()==32 && identifier(proposal.destination_world_id()) &&
        proposal.destination_world_id()!=world_ && proposal.destination_room().size()<=256 &&
        proposal.destination_room().find('\0')==std::string::npos,"Invalid scripted actor transition");
    const auto destination=worlds_.find(proposal.destination_world_id());
    need(destination!=worlds_.end(),"Scripted actor destination is not configured");
    if(proposal.has_destination_pose()) {
        const auto& pose=proposal.destination_pose();
        need(pose.has_position() && pose.has_orientation(),"Incomplete scripted actor destination pose");
        validate_pose({{pose.position().x(),pose.position().y(),pose.position().z()},
            {pose.orientation().x(),pose.orientation().y(),pose.orientation().z(),pose.orientation().w()}});
    }
    need(generation_<std::numeric_limits<std::uint64_t>::max(),"Scripted actor generation exhausted");
    // Stop uses the same sequencer. Only its checked receipt permits departure.
    controls_->cancel(generation);
    const auto departure=controls_->reserve_sequence(generation);
    const auto source=world_;
    ++generation_; // Old destruction callbacks cannot close an adopted or retained client.
    controls_.reset();
    try {
        auto outcome=ActorTransitionCoordinator::transition(*client_,source,destination->first,
            proposal.transition_id(),departure,[path=destination->second] {
                return std::make_unique<ActorSessionClient>(path);
            },proposal.destination_room(),proposal.has_destination_pose()?&proposal.destination_pose():nullptr);
        if(outcome.result.destination_activation_confirmed() && outcome.destination) {
            client_=std::move(outcome.destination);world_=destination->first;create_controls(0);
        } else if(!outcome.result.destination_preparation_confirmed() && !client_->failed()) {
            create_controls(departure);
        } else client_.reset();
        return outcome.result;
    } catch(...) {client_.reset();controls_.reset();throw;}
}
}
