#include "platform/presentation_owner.hpp"
#include "platform/render_process.hpp"
#include "platform/presentation_stream.hpp"
#include "platform/compositor_config.hpp"
#include "world/store.hpp"
#include <chrono>
#include <fcntl.h>
#include <map>
#include <poll.h>
#include <unistd.h>
namespace spatial {
namespace protocol=render::v1;
namespace {
void require(bool okay,const char* message){if(!okay)throw std::runtime_error(message);}
}
struct PresentationOwner::Impl {
    pid_t creator=getpid();
    std::unique_ptr<RenderProcess> process;
    ProcessHandle pin;
    std::unique_ptr<PresentationStream> control;
    std::map<std::uint64_t,std::uint64_t> geometry_sequences;
    std::uint64_t sequence=0;
    bool stopped=false;
    Impl(const std::filesystem::path& executable,const std::filesystem::path& state,int native){
        require(native>=3,"Missing host compositor configuration");
        CompositorConfig config(fcntl(native,F_DUPFD_CLOEXEC,3));
        process=std::make_unique<RenderProcess>(executable,state,config.display(),config.descriptor(),RenderProcess::Mode::Presentation);
        config.consume();pin=ProcessHandle::duplicate(process->pidfd(),process->pid());
        PresentationStream::File fd(fcntl(process->socket(),F_DUPFD_CLOEXEC,3));
        control.reset(new PresentationStream(fd.release(),creator,process->pid()));
        auto ready=receive();require(ready.request_id()==0 && ready.has_ready(),"Missing host compositor readiness");
    }
    void live() const {require(getpid()==creator && !stopped && control && pin.live(),"Host presentation ownership unavailable");}
    protocol::PresentationControlReply receive(){
        const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(10);
        try {
            while(true){
                live();require(std::chrono::steady_clock::now()<deadline,"Host presentation reply deadline exceeded");
                control->flush();
                if(auto frame=control->receive()){
                    protocol::PresentationControlReply reply;
                    require(frame->descriptors.empty() && reply.ParseFromString(frame->bytes) && reply.protocol_version()==1,
                        "Invalid host presentation reply");validate_message(reply);return reply;
                }
                pollfd waiters[]{{control->descriptor(),static_cast<short>(POLLIN|(control->pending_output()?POLLOUT:0)),0},{pin.descriptor(),POLLIN,0}};
                require(poll(waiters,2,10)>=0 || errno==EINTR,"Host presentation poll failed");
            }
        }catch(...){control.reset();throw;}
    }
    protocol::PresentationControlReply call(protocol::PresentationControlRequest request,const std::vector<int>& descriptors={}){
        live();require(sequence!=UINT64_MAX,"Host presentation sequence exhausted");
        request.set_protocol_version(1);request.set_request_id(++sequence);
        control->send(request.SerializeAsString(),descriptors);
        auto reply=receive();
        if(reply.request_id()!=sequence){control.reset();throw std::runtime_error("Host presentation reply identity mismatch");}
        if(reply.has_error())throw WorldError(reply.error().code(),reply.error().message().c_str());
        return reply;
    }
    protocol::PresentationState state(protocol::PresentationControlRequest request,const protocol::WorldPresentationBinding& binding,
        const std::vector<int>& descriptors={}){
        const bool attaching=request.has_attach(),selecting=request.has_select(),detaching=request.has_detach(),recovering=request.has_recover();
        auto reply=call(std::move(request),descriptors);
        const auto& result=reply.state();
        const bool valid=reply.has_state() && result.binding().SerializeAsString()==binding.SerializeAsString() &&
            result.stage()>=protocol::ATTACHED && result.stage()<=protocol::UNCERTAIN &&
            (!recovering || (result.stage()==protocol::RETIRED && !result.selected())) &&
            (!attaching || (result.stage()==protocol::ATTACHED && !result.selected())) &&
            (!selecting || (result.stage()==protocol::READY && result.selected())) &&
            (!detaching || ((result.stage()==protocol::RETIRED || result.stage()==protocol::UNCERTAIN) && !result.selected()));
        if(!valid){control.reset();throw std::runtime_error("Host presentation state mismatch");}
        return result;
    }
    protocol::GeometryPresentationState geometry_state(protocol::PresentationControlRequest request,
        const protocol::GeometryPresentationTarget& target, protocol::PresentationStage stage,
        bool selected, std::uint64_t sequence) {
        auto reply=call(std::move(request));
        if(!reply.has_geometry_state()) {
            control.reset();throw std::runtime_error("Missing geometry presentation state");
        }
        const auto& result=reply.geometry_state();
        if(result.target().SerializeAsString()!=target.SerializeAsString() || result.stage()!=stage ||
            result.selected()!=selected || result.last_view_sequence()!=sequence) {
            control.reset();throw std::runtime_error("Geometry presentation state mismatch");
        }
        return result;
    }
};
PresentationOwner::PresentationOwner(const std::filesystem::path& executable,const std::filesystem::path& state,int native)
    :impl_(std::make_unique<Impl>(executable,state,native)){}
PresentationOwner::~PresentationOwner()=default;
pid_t PresentationOwner::pid() const {impl_->live();return impl_->process->pid();}
int PresentationOwner::pidfd() const {impl_->live();return impl_->pin.descriptor();}
protocol::PresentationState PresentationOwner::attach(const protocol::WorldPresentationBinding& binding,int channel,const ProcessHandle& world,const workspace::v1::ControlOwner& owner,const supervisor::v1::ProcessScopeIdentity& process){
    require(world.live(),"World process exited before presentation attachment");
    protocol::PresentationControlRequest request;*request.mutable_attach()->mutable_binding()=binding;
    *request.mutable_attach()->mutable_owner()=owner;*request.mutable_attach()->mutable_process()=process;
    request.mutable_attach()->set_world_pid(world.pid());return impl_->state(std::move(request),binding,{channel,world.descriptor()});
}
protocol::PresentationState PresentationOwner::select(const protocol::WorldPresentationBinding& binding){
    protocol::PresentationControlRequest request;*request.mutable_select()=binding;return impl_->state(std::move(request),binding);
}
protocol::PresentationState PresentationOwner::inspect(const protocol::WorldPresentationBinding& binding){
    protocol::PresentationControlRequest request;*request.mutable_inspect()=binding;return impl_->state(std::move(request),binding);
}
protocol::PresentationState PresentationOwner::detach(const protocol::WorldPresentationBinding& binding){
    protocol::PresentationControlRequest request;*request.mutable_detach()=binding;return impl_->state(std::move(request),binding);
}
protocol::PresentationState PresentationOwner::recover(const protocol::WorldPresentationBinding& binding,
    const supervisor::v1::ProcessScopeStopped& process,const supervisor::v1::NativeScopeStopped& native){
    protocol::PresentationControlRequest request;auto* value=request.mutable_recover();
    *value->mutable_binding()=binding;*value->mutable_process_stopped()=process;*value->mutable_native_stopped()=native;
    return impl_->state(std::move(request),binding);
}
protocol::GeometryPresentationState PresentationOwner::attach_geometry(const protocol::GeometryPresentationTarget& target){
    protocol::PresentationControlRequest request;*request.mutable_attach_geometry()->mutable_target()=target;
    require(!impl_->geometry_sequences.contains(target.presentation_generation()),"Duplicate geometry presentation target");
    impl_->geometry_sequences.emplace(target.presentation_generation(),0);
    try { return impl_->geometry_state(std::move(request),target,protocol::ATTACHED,false,0); }
    catch (const WorldError&) { impl_->geometry_sequences.erase(target.presentation_generation());throw; }
}
protocol::GeometryPresentationState PresentationOwner::select_geometry(const protocol::GeometryPresentationTarget& target){
    protocol::PresentationControlRequest request;*request.mutable_select_geometry()=target;
    const auto found=impl_->geometry_sequences.find(target.presentation_generation());
    if(found==impl_->geometry_sequences.end())throw std::runtime_error("Unknown geometry presentation target");
    return impl_->geometry_state(std::move(request),target,protocol::READY,true,found->second);
}
protocol::GeometryPresentationState PresentationOwner::publish_geometry(const protocol::GeometryPresentationTarget& target,
    const identity::v1::ActorGeometryView& view){
    protocol::PresentationControlRequest request;auto* value=request.mutable_publish_geometry();
    *value->mutable_target()=target;*value->mutable_view()=view;
    const auto result=impl_->geometry_state(std::move(request),target,protocol::READY,true,view.sequence());
    impl_->geometry_sequences[target.presentation_generation()]=view.sequence();return result;
}
protocol::GeometryPresentationState PresentationOwner::retire_geometry(const protocol::GeometryPresentationTarget& target){
    protocol::PresentationControlRequest request;*request.mutable_retire_geometry()=target;
    const auto found=impl_->geometry_sequences.find(target.presentation_generation());
    if(found==impl_->geometry_sequences.end())throw std::runtime_error("Unknown geometry presentation target");
    auto result=impl_->geometry_state(std::move(request),target,protocol::RETIRED,false,found->second);
    impl_->geometry_sequences.erase(found);return result;
}
void PresentationOwner::clear_selection(){
    protocol::PresentationControlRequest request;request.mutable_clear_selection();
    const auto reply=impl_->call(std::move(request));
    if(!reply.has_selection_cleared() || !reply.selection_cleared()) {
        impl_->control.reset();throw std::runtime_error("Foreground clearing unconfirmed");
    }
}
void PresentationOwner::shutdown(){
    protocol::PresentationControlRequest request;request.mutable_shutdown();auto reply=impl_->call(std::move(request));
    require(reply.has_shutting_down() && reply.shutting_down(),"Compositor shutdown unconfirmed");impl_->stopped=true;impl_->control.reset();
}
}
