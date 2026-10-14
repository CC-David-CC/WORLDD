#include "render/presentation_server.hpp"
#include "render/presentation_inputs.hpp"
#include "render/renderer.hpp"
#include "platform/presentation_stream.hpp"
#include "platform/process_handle.hpp"
#include "presentation.pb.h"
#include "world/store.hpp"
#include "world/geometry_view_validation.hpp"
#include "workspace/control_owner.hpp"
#include <cerrno>
#include <climits>
#include <map>
#include <poll.h>
#include <vector>
#include <sys/syscall.h>
#include <unistd.h>
namespace spatial {
namespace {
namespace protocol=render::v1;
using Binding=protocol::WorldPresentationBinding;
void require(bool okay,const char* message){if(!okay)throw std::invalid_argument(message);}
ProcessHandle retain_owner(pid_t owner) {
    require(getppid()==owner,"Presentation owner must launch the compositor");
    auto process=ProcessHandle::take(static_cast<int>(syscall(SYS_pidfd_open,owner,0)),owner);
    require(getppid()==owner,"Presentation owner exited during startup");
    return process;
}
struct WorldChannel {
    Binding binding;
    workspace::v1::ControlOwner scope_owner;
    supervisor::v1::ProcessScopeIdentity scope_process;
    ProcessHandle process;
    std::unique_ptr<PresentationStream> channel;
    protocol::PresentationStage stage=protocol::ATTACHED;
    std::uint64_t sequence=0;
};
struct GeometryTarget {
    protocol::GeometryPresentationTarget target;
    protocol::PresentationStage stage=protocol::ATTACHED;
    std::uint64_t last_sequence=0;
};
struct Server {
    Renderer& renderer;
    PresentationStream control;
    pid_t owner;
    ProcessHandle owner_process;
    std::map<std::uint64_t,WorldChannel> worlds;
    std::map<std::uint64_t,GeometryTarget> geometry_targets;
    std::uint64_t sequence=0,selected=0,selected_geometry=0;
    bool shutdown=false;
    Server(Renderer& renderer,int fd,pid_t owner):renderer(renderer),control(fd,owner,owner),owner(owner),owner_process(retain_owner(owner)){}
    WorldChannel& find(const Binding& binding){
        PresentationInputs::validate_binding(binding);
        auto found=worlds.find(binding.presentation_generation());
        if(found==worlds.end() || found->second.binding.SerializeAsString()!=binding.SerializeAsString())
            throw WorldError(wire::STALE_TARGET,"Presentation binding is not attached");
        return found->second;
    }
    protocol::PresentationState state(const WorldChannel& world) const {
        protocol::PresentationState result;*result.mutable_binding()=world.binding;result.set_stage(world.stage);
        result.set_selected(selected==world.binding.presentation_generation());return result;
    }
    static void validate_geometry_target(const protocol::GeometryPresentationTarget& target) {
        validate_message(target);
        require(target.protocol_version()==1 && !target.world_id().empty() && target.world_id().size()<=256 &&
            target.world_id().find('\0')==std::string::npos && !target.actor_id().empty() && target.actor_id().size()<=256 &&
            target.actor_id().find('\0')==std::string::npos && target.geometry_view_id().size()==32 &&
            target.authority_service_incarnation().size()==32 && target.authority_generation()>0 &&
            target.presentation_generation()>0,"Invalid geometry presentation target");
    }
    GeometryTarget& find_geometry(const protocol::GeometryPresentationTarget& target) {
        validate_geometry_target(target);
        const auto found=geometry_targets.find(target.presentation_generation());
        if(found==geometry_targets.end() || found->second.target.SerializeAsString()!=target.SerializeAsString())
            throw WorldError(wire::STALE_TARGET,"Geometry presentation target is retired or mismatched");
        return found->second;
    }
    protocol::GeometryPresentationState geometry_state(const GeometryTarget& target) const {
        protocol::GeometryPresentationState result;*result.mutable_target()=target.target;result.set_stage(target.stage);
        result.set_selected(selected_geometry==target.target.presentation_generation());
        result.set_last_view_sequence(target.last_sequence);return result;
    }
    void retire(WorldChannel& world){
        world.channel.reset();
        if(selected==world.binding.presentation_generation()){renderer.clear_presentation();selected=0;}
        if(world.stage==protocol::RETIRED)return;
        try {renderer.retire_input(world.binding);world.stage=protocol::RETIRED;}
        catch (...){world.stage=protocol::UNCERTAIN;}
    }
    void administrative(PresentationStream::Frame frame){
        protocol::PresentationControlRequest request;
        require(request.ParseFromString(frame.bytes),"Malformed presentation control frame");
        require(request.protocol_version()==1 && request.request_id()>sequence,"Invalid presentation control sequence or version");
        protocol::PresentationControlReply reply;reply.set_protocol_version(1);reply.set_request_id(request.request_id());
        try {
            validate_message(request);
            require(frame.descriptors.size()==(request.has_attach()?2U:0U),"Incorrect presentation control descriptor count");
            sequence=request.request_id();
            if(request.has_attach()){
                const auto& attach=request.attach();const auto& binding=attach.binding();
                PresentationInputs::validate_binding(binding);
                require(attach.world_pid()>1 && attach.world_pid()<=INT_MAX,"Invalid presentation world process");
                if(worlds.size()+geometry_targets.size()>=256)throw WorldError(wire::IN_USE,"Too many retained presentation targets");
                if(worlds.contains(binding.presentation_generation()) || geometry_targets.contains(binding.presentation_generation()))
                    throw WorldError(wire::IN_USE,"Duplicate presentation binding");
                WorldChannel world;world.binding=binding;
                require(attach.has_owner()==attach.has_process(),"Incomplete presentation supervisor scope");
                if(attach.has_owner()){
                    workspace::v1::ControlOwnerRecovery shape;*shape.mutable_owner()=attach.owner();
                    *shape.mutable_process_stopped()->mutable_identity()=attach.process();
                    *shape.mutable_native_stopped()->mutable_identity()=attach.owner().native();validate_control_recovery(shape);
                    require(attach.owner().launch().service_incarnation()==binding.service_incarnation() &&
                        attach.owner().launch().generation()==binding.authority_generation(),"Presentation supervisor generation mismatch");
                    world.scope_owner=attach.owner();world.scope_process=attach.process();
                }
                world.process=ProcessHandle::take(frame.descriptors[1].release(),static_cast<pid_t>(attach.world_pid()));
                world.channel.reset(new PresentationStream(frame.descriptors[0].release(),owner,world.process.pid()));
                auto [position,inserted]=worlds.emplace(binding.presentation_generation(),std::move(world));
                (void)inserted;
                try {renderer.reserve_input(binding,attach.has_owner()?&attach.owner().native():nullptr);}
                catch (...){worlds.erase(position);throw;}
                *reply.mutable_state()=state(position->second);
            }else if(request.has_attach_geometry()){
                const auto& target=request.attach_geometry().target();validate_geometry_target(target);
                if(worlds.size()+geometry_targets.size()>=256)throw WorldError(wire::IN_USE,"Too many retained presentation targets");
                if(worlds.contains(target.presentation_generation()) || geometry_targets.contains(target.presentation_generation()))
                    throw WorldError(wire::IN_USE,"Duplicate geometry presentation target");
                GeometryTarget entry;entry.target=target;
                auto [position,inserted]=geometry_targets.emplace(target.presentation_generation(),std::move(entry));
                if(!inserted)throw WorldError(wire::IN_USE,"Duplicate geometry presentation target");
                *reply.mutable_geometry_state()=geometry_state(position->second);
            }else if(request.has_select_geometry()){
                auto& target=find_geometry(request.select_geometry());
                if(target.stage!=protocol::ATTACHED && target.stage!=protocol::READY)
                    throw WorldError(wire::UNAVAILABLE,"Geometry presentation target is not available");
                if(selected_geometry!=target.target.presentation_generation() || selected!=0)renderer.clear_presentation();
                selected=0;selected_geometry=target.target.presentation_generation();target.stage=protocol::READY;
                *reply.mutable_geometry_state()=geometry_state(target);
            }else if(request.has_publish_geometry()){
                const auto& value=request.publish_geometry();auto& target=find_geometry(value.target());
                if(target.stage!=protocol::READY || selected_geometry!=target.target.presentation_generation())
                    throw WorldError(wire::FORBIDDEN,"Geometry target is not selected");
                validate_geometry_view(value.view());
                const auto& view=value.view();
                require(view.world_id()==target.target.world_id() && view.actor_id()==target.target.actor_id() &&
                    view.view_id()==target.target.geometry_view_id() &&
                    view.authority_service_incarnation()==target.target.authority_service_incarnation() &&
                    view.authority_generation()==target.target.authority_generation(),
                    "Geometry view context does not match the registered target");
                require(view.sequence()>target.last_sequence,"Stale geometry view sequence");
                renderer.draw_geometry(view);target.last_sequence=view.sequence();
                *reply.mutable_geometry_state()=geometry_state(target);
            }else if(request.has_retire_geometry()){
                auto& target=find_geometry(request.retire_geometry());
                if(selected_geometry==target.target.presentation_generation()){
                    renderer.clear_presentation();selected_geometry=0;
                }
                target.stage=protocol::RETIRED;
                *reply.mutable_geometry_state()=geometry_state(target);
                geometry_targets.erase(target.target.presentation_generation());
            }else if(request.has_select()){
                auto& world=find(request.select());
                if(world.stage!=protocol::READY || !world.channel || !world.process.live())
                    throw WorldError(wire::UNAVAILABLE,"World presentation is not ready");
                if(selected!=world.binding.presentation_generation() || selected_geometry!=0)renderer.clear_presentation();
                selected_geometry=0;selected=world.binding.presentation_generation();*reply.mutable_state()=state(world);
            }else if(request.has_detach()){
                auto& world=find(request.detach());retire(world);*reply.mutable_state()=state(world);
                if(world.stage==protocol::RETIRED)worlds.erase(world.binding.presentation_generation());
            }else if(request.has_inspect()){
                auto& world=find(request.inspect());
                if(!world.process.live())retire(world);
                *reply.mutable_state()=state(world);
            }else if(request.has_recover()){
                const auto& recovery=request.recover();auto& world=find(recovery.binding());
                require(world.scope_owner.has_native() && !world.process.live(),"Presentation recovery requires a stopped retained world");
                require(recovery.has_process_stopped() && recovery.has_native_stopped() &&
                    recovery.process_stopped().identity().SerializeAsString()==world.scope_process.SerializeAsString() &&
                    recovery.native_stopped().identity().SerializeAsString()==world.scope_owner.native().SerializeAsString(),
                    "Presentation recovery receipts do not match the attached scopes");
                retire(world);
                if(world.stage!=protocol::RETIRED){renderer.confirm_input_retired(world.binding);world.stage=protocol::RETIRED;}
                *reply.mutable_state()=state(world);
            }else if(request.has_clear_selection()){
                renderer.clear_presentation();selected=0;selected_geometry=0;
                reply.set_selection_cleared(true);
            }else if(request.has_shutdown()){
                if(!worlds.empty() || !geometry_targets.empty())throw WorldError(wire::IN_USE,"Retire presentation targets before compositor shutdown");
                shutdown=true;reply.set_shutting_down(true);
            }else throw std::invalid_argument("Missing presentation control operation");
        }catch(const WorldError& error){reply.mutable_error()->set_code(error.code);reply.mutable_error()->set_message(error.what());}
        catch(const std::invalid_argument& error){reply.mutable_error()->set_code(wire::INVALID_ARGUMENT);reply.mutable_error()->set_message(error.what());}
        catch(const std::exception& error){reply.mutable_error()->set_code(wire::UNAVAILABLE);reply.mutable_error()->set_message(error.what());}
        control.send(reply.SerializeAsString());
    }
    void command(WorldChannel& world,PresentationStream::Frame frame){
        protocol::PresentationWorldRequest request;
        require(request.ParseFromString(frame.bytes),"Malformed world presentation frame");validate_message(request);
        require(request.protocol_version()==1 && request.request_id()>world.sequence,"Invalid world presentation sequence or version");
        world.sequence=request.request_id();
        require(frame.descriptors.size()==((request.has_hello() || (request.has_command() && request.command().capture()))?1U:0U),"Incorrect world presentation descriptor count");
        wire::RenderResult reply;reply.set_protocol_version(1);reply.set_request_id(world.sequence);
        try {
            if(request.has_hello()){
                if(world.stage!=protocol::ATTACHED)throw WorldError(wire::IN_USE,"World presentation already initialized");
                renderer.initialize_input(world.binding,frame.descriptors[0].release());
                world.stage=protocol::READY;*reply.mutable_ready()=renderer.ready();
            }else if(request.has_command()){
                if(world.stage!=protocol::READY)throw WorldError(wire::UNAVAILABLE,"World presentation is not initialized");
                const auto& value=request.command();
                require(value.protocol_version()==1 && value.request_id()==world.sequence &&
                    int(value.has_scene())+int(value.has_sync())+int(value.has_input())==1,"Invalid bound render command");
                require(!value.capture() || value.has_scene(),"Capture requires a scene");
                require(!value.observe() || (value.has_scene() && !value.capture()),"Invalid bound observation operation");
                require(!value.include_pixels() || value.observe(),"Pixel image requires bound observation");
                if(value.has_sync())*reply.mutable_snapshot()=renderer.synchronize(world.binding);
                else if(value.has_input())*reply.mutable_input()=renderer.input(world.binding,value.input());
                else if(value.observe())*reply.mutable_visibility()=renderer.observe(value);
                else {
                    if(selected!=world.binding.presentation_generation())throw WorldError(wire::FORBIDDEN,"World is not selected for presentation");
                    *reply.mutable_frame()=renderer.draw(value,value.capture()?frame.descriptors[0].value:-1);
                }
            }else throw std::invalid_argument("Missing world presentation operation");
        }catch(const WorldError& error){reply.mutable_error()->set_code(error.code);reply.mutable_error()->set_message(error.what());}
        catch(const std::invalid_argument& error){reply.mutable_error()->set_code(wire::INVALID_ARGUMENT);reply.mutable_error()->set_message(error.what());}
        catch(const std::exception& error){reply.mutable_error()->set_code(wire::UNAVAILABLE);reply.mutable_error()->set_message(error.what());}
        world.channel->send(reply.SerializeAsString());
    }
    void run(){
        protocol::PresentationControlReply ready;ready.set_protocol_version(1);*ready.mutable_ready()=renderer.ready();
        control.send(ready.SerializeAsString());
        while(true){
            require(owner_process.live(),"Presentation owner exited");
            renderer.events();control.flush();
            if(shutdown && !control.pending_output())return;
            if(!shutdown && !control.pending_output())if(auto frame=control.receive())administrative(std::move(*frame));
            for(auto& [generation,world]:worlds){
                (void)generation;if(!world.channel)continue;
                if(!world.process.live()){retire(world);continue;}
                try {
                    world.channel->flush();
                    if(!world.channel->pending_output())if(auto frame=world.channel->receive())command(world,std::move(*frame));
                }catch(const std::exception&){retire(world);}
            }
            std::vector<pollfd> descriptors{{control.descriptor(),static_cast<short>(POLLIN|(control.pending_output()?POLLOUT:0)),0},
                {renderer.connection(),POLLIN,0},{owner_process.descriptor(),POLLIN,0}};
            for(const auto& [generation,world]:worlds){
                (void)generation;if(!world.channel)continue;
                descriptors.push_back({world.channel->descriptor(),static_cast<short>(POLLIN|(world.channel->pending_output()?POLLOUT:0)),0});
                descriptors.push_back({world.process.descriptor(),POLLIN,0});
            }
            if(poll(descriptors.data(),descriptors.size(),10)<0 && errno!=EINTR)throw std::runtime_error("Presentation poll failed");
        }
    }
};
}
void serve_presentations(Renderer& renderer,int fd,pid_t owner){Server server(renderer,fd,owner);server.run();}
}
