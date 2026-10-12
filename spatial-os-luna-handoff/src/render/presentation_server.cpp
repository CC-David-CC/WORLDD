#include "render/presentation_server.hpp"
#include "render/presentation_inputs.hpp"
#include "render/renderer.hpp"
#include "platform/presentation_stream.hpp"
#include "platform/process_handle.hpp"
#include "presentation.pb.h"
#include "world/store.hpp"
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
    ProcessHandle process;
    std::unique_ptr<PresentationStream> channel;
    protocol::PresentationStage stage=protocol::ATTACHED;
    std::uint64_t sequence=0;
};
struct Server {
    Renderer& renderer;
    PresentationStream control;
    pid_t owner;
    ProcessHandle owner_process;
    std::map<std::uint64_t,WorldChannel> worlds;
    std::uint64_t sequence=0,selected=0;
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
    void retire(WorldChannel& world){
        world.channel.reset();
        if(selected==world.binding.presentation_generation()){renderer.clear_presentation();selected=0;}
        if(world.stage==protocol::RETIRED)return;
        try {renderer.retire_input(world.binding);world.stage=protocol::RETIRED;}
        catch (...){world.stage=protocol::UNCERTAIN;}
    }
    void administrative(PresentationStream::Frame frame){
        protocol::PresentationControlRequest request;
        require(request.ParseFromString(frame.bytes),"Malformed presentation control frame");validate_message(request);
        require(request.protocol_version()==1 && request.request_id()>sequence,"Invalid presentation control sequence or version");
        sequence=request.request_id();
        protocol::PresentationControlReply reply;reply.set_protocol_version(1);reply.set_request_id(sequence);
        try {
            require(frame.descriptors.size()==(request.has_attach()?2U:0U),"Incorrect presentation control descriptor count");
            if(request.has_attach()){
                const auto& attach=request.attach();const auto& binding=attach.binding();
                PresentationInputs::validate_binding(binding);
                require(attach.world_pid()>1 && attach.world_pid()<=INT_MAX,"Invalid presentation world process");
                if(worlds.size()>=256)throw WorldError(wire::IN_USE,"Too many retained world channels");
                if(worlds.contains(binding.presentation_generation()))throw WorldError(wire::IN_USE,"Duplicate presentation binding");
                WorldChannel world;world.binding=binding;
                world.process=ProcessHandle::take(frame.descriptors[1].release(),static_cast<pid_t>(attach.world_pid()));
                world.channel.reset(new PresentationStream(frame.descriptors[0].release(),owner,world.process.pid()));
                auto [position,inserted]=worlds.emplace(binding.presentation_generation(),std::move(world));
                (void)inserted;
                try {renderer.reserve_input(binding);}
                catch (...){worlds.erase(position);throw;}
                *reply.mutable_state()=state(position->second);
            }else if(request.has_select()){
                auto& world=find(request.select());
                if(world.stage!=protocol::READY || !world.channel || !world.process.live())
                    throw WorldError(wire::UNAVAILABLE,"World presentation is not ready");
                if(selected!=world.binding.presentation_generation())renderer.clear_presentation();
                selected=world.binding.presentation_generation();*reply.mutable_state()=state(world);
            }else if(request.has_detach()){
                auto& world=find(request.detach());retire(world);*reply.mutable_state()=state(world);
                if(world.stage==protocol::RETIRED)worlds.erase(world.binding.presentation_generation());
            }else if(request.has_inspect()){
                auto& world=find(request.inspect());
                if(!world.process.live())retire(world);
                *reply.mutable_state()=state(world);
            }else if(request.has_shutdown()){
                if(!worlds.empty())throw WorldError(wire::IN_USE,"Detach world channels before compositor shutdown");
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
