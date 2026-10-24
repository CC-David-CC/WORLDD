#include "supervisor/session_coordinator.hpp"
#include "supervisor/session_broker.hpp"
#include "supervisor/session_launch.hpp"
#include "supervisor/launch.hpp"
#include "supervisor/control_admin.hpp"
#include "platform/broker.hpp"
#include "platform/presentation_owner.hpp"
#include "platform/local_actor_endpoint.hpp"
#include "identity/participant_stream.hpp"
#include "identity/tls_support.hpp"
#include "world/geometry_view_validation.hpp"
#include "world/local_view_validation.hpp"
#include "world/actor_session_client.hpp"
#include "core/scene.hpp"
#include "core/geometry.hpp"
#include "world/store.hpp"
#include "world_session.pb.h"
#include <google/protobuf/text_format.h>
#include <map>
#include <cmath>
#include <iostream>
#include <vector>
#include <fcntl.h>
#include <netinet/in.h>
#include <poll.h>
#include <sys/socket.h>

namespace spatial::supervisor {
namespace {
using File = identity::tls_detail::Fd;
using Clock = std::chrono::steady_clock;
void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
int retain_actor_listener(int borrowed) {
    require(borrowed >= 3, "Actor listener must be above stderr");
    File retained(fcntl(borrowed, F_DUPFD_CLOEXEC, 3));
    require(retained.value >= 3, "Cannot retain actor listener");
    sockaddr_storage address{}; socklen_t size = sizeof(address);
    int accepting = 0, type = 0, protocol = 0; socklen_t option = sizeof(int);
    require(getsockname(retained.value, reinterpret_cast<sockaddr*>(&address), &size) == 0 &&
        (address.ss_family == AF_INET || address.ss_family == AF_INET6) &&
        getsockopt(retained.value, SOL_SOCKET, SO_ACCEPTCONN, &accepting, &option) == 0 && accepting &&
        getsockopt(retained.value, SOL_SOCKET, SO_TYPE, &type, &option) == 0 && type == SOCK_STREAM &&
        getsockopt(retained.value, SOL_SOCKET, SO_PROTOCOL, &protocol, &option) == 0 && protocol == IPPROTO_TCP,
        "Session actor transport requires a listening TCP socket");
    // Private coordinator endpoints are UNIX sockets. No duplicate of one can pass this check.
    return std::exchange(retained.value, -1);
}
struct Pair {
    File parent, child;
    Pair() {
        int pair[2]; require(socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, pair) == 0, "Cannot create world session channel");
        parent.reset(pair[0]); child.reset(pair[1]);
    }
};
void validate_local_presentation(const core::v1::ActorLocalViewState& result,
    const render::v1::LocalActorPresentationTarget& target,std::uint32_t width,std::uint32_t height) {
    validate_render_extent(result.viewport().width(),result.viewport().height());
    require(std::isfinite(result.vertical_fov()) && result.vertical_fov()>0 &&
        result.vertical_fov()<std::acos(-1.0),"Invalid local actor projection field of view");
    require(result.bound() && result.presented() && result.identity().SerializeAsString()==target.identity().SerializeAsString() && result.frame_id()>0 &&
        result.world_revision()>0 && (!width || (result.viewport().width()==width && result.viewport().height()==height)) &&
        !result.room().empty() && result.room().size()<=256 && result.room().find('\0')==std::string::npos &&
        result.presentation_generation()==target.binding().presentation_generation() && result.camera().has_position() &&
        result.camera().has_orientation(),"Local actor presentation receipt does not match its target");
    const auto& p=result.camera().position();const auto& q=result.camera().orientation();
    validate_pose({{p.x(),p.y(),p.z()},{q.x(),q.y(),q.z(),q.w()}});
}
struct World {
    struct LocalTarget {
        render::v1::LocalActorPresentationTarget target;
        bool attested=false;
    };
    std::unique_ptr<SessionLaunch> supervisor;
    std::unique_ptr<identity::ParticipantStream> management;
    workspace::v1::ControlOwner owner;
    v1::AuthorityLaunchRecord record;
    v1::AuthorityLaunchAuthorize authorization;
    std::optional<v1::AuthorityDependentCleanup> dependent;
    std::optional<identity::v1::AuthorityRetirementState> retirement;
    std::filesystem::path journal_directory;
    std::vector<std::unique_ptr<File>> actors;
    render::v1::WorldPresentationBinding presentation;
    std::map<std::string,LocalTarget> local_targets;
    void invalidate_local_attestations() {
        for(auto& [key,target]:local_targets){(void)key;target.attested=false;}
    }
    bool presentation_attached=false,presentation_retired=false,world_identity_checked=false;
    bool registered = false, stopped = false, failed = false;
    std::uint64_t sequence = 0;
    struct Exchange {
        std::uint64_t token, caller_id;
        Clock::time_point deadline;
    };
    std::optional<Exchange> exchange;
    std::unique_ptr<local_actor::RetirementPin> local_endpoint;
    struct LocalFrame {
        SessionCoordinator::LocalPresentationTicket ticket;
        render::v1::LocalActorPresentationTarget target;
        enum Stage { Bind, Select, Present } stage = Bind;
        std::uint32_t width = 0, height = 0;
        std::optional<std::uint64_t> world_token, selection_token;
        Clock::time_point deadline;
        bool cancelled = false;
    };
    std::optional<LocalFrame> local_frame;
};
}
struct SessionCoordinator::Impl {
    const pid_t creator = getpid();
    std::filesystem::path executable;
    SessionBroker broker;
    std::unique_ptr<PresentationOwner> presentation;
    std::map<std::uint64_t,render::v1::GeometryPresentationTarget> geometry_targets;
    std::uint64_t presentation_generation=0;
    std::uint64_t local_operation_sequence=0;
    // Reverse destruction closes world scopes before broker root ownership.
    std::map<std::string, std::unique_ptr<World>> worlds;
    std::uint64_t sequence = 0;
    Impl(const std::filesystem::path& executable, const std::filesystem::path& path,
        v1::AuthoritySupervisorConfig config, int native)
        : executable(executable), broker(executable, path, std::move(config), native) {}
    void owned() const { require(getpid() == creator, "Session coordinator ownership lost"); }
    void live() const { owned(); broker.check_alive(); }
    World& find_owned(const workspace::v1::ControlOwner& owner) const {
        owned(); validate_message(owner);
        auto found = worlds.find(owner.launch().service_incarnation());
        require(found != worlds.end() && found->second->registered &&
            found->second->owner.SerializeAsString() == owner.SerializeAsString(), "Control owner is not a retained session world");
        return *found->second;
    }
    World& find(const workspace::v1::ControlOwner& owner) const { live(); return find_owned(owner); }
    void exchange_failed(World& world) {
        world.failed=true;world.invalidate_local_attestations();
        try {administrative(world.owner,-1,true);}
        catch(const std::exception& error){std::cerr<<"Failed world fence unresolved: "<<error.what()<<'\n';}
    }
    std::uint64_t begin_world(World& world,const core::v1::Request& supplied,Clock::time_point deadline) {
        require(!world.failed && !world.stopped && world.management,"Session world is not available");
        require(!world.exchange,"World management exchange already in progress");
        validate_message(supplied);require(supplied.protocol_version()==1,"Unsupported world request version");
        require(world.sequence!=UINT64_MAX,"World request sequence exhausted");
        auto request=supplied;request.set_request_id(++world.sequence);
        world.exchange=World::Exchange{request.request_id(),supplied.request_id(),deadline};
        try {world.supervisor->live();world.management->send(request.SerializeAsString());}
        catch(...){exchange_failed(world);throw;}
        return request.request_id();
    }
    std::optional<core::v1::Reply> poll_world(World& world,std::uint64_t token) {
        require(world.exchange && world.exchange->token==token,"Stale world management operation");
        try {
            live();world.supervisor->live();
            require(!world.failed && !world.stopped && world.management,"Session world is not available");
            require(Clock::now()<world.exchange->deadline,"Session world response timed out");
            world.management->flush();
            auto bytes=world.management->receive();if(!bytes)return {};
            core::v1::Reply reply;require(reply.ParseFromString(*bytes),"Malformed world reply");validate_message(reply);
            require(reply.protocol_version()==1 && reply.request_id()==token,"World reply correlation failed");
            reply.set_request_id(world.exchange->caller_id);world.exchange.reset();return reply;
        }catch(...){exchange_failed(world);throw;}
    }
    World::LocalFrame& frame(World& world,const SessionCoordinator::LocalPresentationTicket& ticket) {
        require(world.local_frame && world.local_frame->ticket.serial==ticket.serial &&
            world.local_frame->ticket.presentation_generation==ticket.presentation_generation &&
            world.local_frame->ticket.owner.SerializeAsString()==ticket.owner.SerializeAsString() &&
            world.local_frame->ticket.identity.SerializeAsString()==ticket.identity.SerializeAsString(),
            "Stale local presentation operation");
        return *world.local_frame;
    }
    // A synchronous menu receipt may share the compositor channel. Drain only
    // selection; leave world-frame scheduling to its explicit continuation owner.
    void drain_local_selection(World& world) {
        if(!world.local_frame || !world.local_frame->selection_token)return;
        auto& frame=*world.local_frame;
        try {while(!presentation->poll_select_local_actor(*frame.selection_token))::poll(nullptr,0,2);}
        catch(const WorldError&){frame.selection_token.reset();throw;}
        frame.selection_token.reset();frame.stage=World::LocalFrame::Present;
    }
    void retire_stopped_presentation(World& world) {
        require(presentation && world.presentation_attached && world.stopped,
            "Presentation recovery requires stopped retained world ownership");
        world.invalidate_local_attestations();
        if(world.presentation_retired)return;
        const auto checked=world.supervisor->stop();
        require(checked.SerializeAsString()==world.record.SerializeAsString() && checked.stage()==v1::OWNED_SCOPES_STOPPED &&
            checked.has_process_stopped() && checked.has_native_stopped(),
            "Presentation recovery requires checked supervisor receipts");
        if(world.local_frame) {
            world.local_frame->cancelled=true;
            // The stopped producer cannot finish a world request. Its checked
            // receipts permit recovery, but the live compositor still owns its
            // submitted selection. Consume that reply before reusing its stream.
            try {drain_local_selection(world);}
            catch(const WorldError& error) {
                // A correlated typed denial has retired the selection exchange.
                // Transport or validation failures remain uncertain and propagate.
                std::cerr<<"Stopped-world selection denied before recovery: "<<error.what()<<'\n';
            }
        }
        presentation->recover(world.presentation,checked.process_stopped(),checked.native_stopped());
        const auto retired=presentation->detach(world.presentation);
        require(retired.stage()==render::v1::RETIRED,"Presentation retirement remains unconfirmed");
        world.presentation_retired=true;
        // Drop obsolete tickets only after exact process/native stop and
        // compositor retirement. An unconfirmed recovery retains its obligations.
        world.local_frame.reset();world.exchange.reset();
    }
    workspace::v1::ControlOwnerState administrative(const workspace::v1::ControlOwner& owner, int endpoint = -1,
        bool fence = false, const v1::AuthorityLaunchRecord* cleanup = nullptr) {
        live(); require(sequence != UINT64_MAX, "Session administrative request sequence exhausted");
        workspace::v1::ControlSupervisorRequest request;
        request.set_protocol_version(1); request.set_request_id(++sequence);
        if (cleanup) {
            *request.mutable_recover()->mutable_owner() = owner;
            *request.mutable_recover()->mutable_process_stopped() = cleanup->process_stopped();
            *request.mutable_recover()->mutable_native_stopped() = cleanup->native_stopped();
            const auto& world=find_owned(owner);
            if(world.presentation_attached) {
                require(world.presentation_retired,"Recover broker observations only after checked presentation retirement");
                auto* retired=request.mutable_recover()->mutable_retired_presentation();
                retired->set_world_id(world.presentation.world_id());
                retired->set_presentation_generation(world.presentation.presentation_generation());
            }
        }
        else if (endpoint >= 3) *request.mutable_register_() = owner;
        else if (fence) *request.mutable_fence() = owner;
        else *request.mutable_inspect() = owner;
        const auto reply = broker.administrative().exchange(request, endpoint);
        require(reply.has_state(), reply.has_error() ? reply.error().message().c_str() : "Missing administrative state");
        return reply.state(); // ControlAdmin already validated the complete reply against this exact request.
    }
};
SessionCoordinator::SessionCoordinator(const std::filesystem::path& executable, const std::filesystem::path& path,
    v1::AuthoritySupervisorConfig config, int native)
    : impl_(std::make_unique<Impl>(executable, path, std::move(config), native)) {}
SessionCoordinator::~SessionCoordinator() = default;
void SessionCoordinator::start_presentation(const std::filesystem::path& executable,const std::filesystem::path& path,int native){
    auto& state=*impl_;state.live();
    require(!state.presentation && state.worlds.empty(),"Start presentation before any world launches");
    state.presentation=std::make_unique<PresentationOwner>(executable,path,native);
}
render::v1::PresentationState SessionCoordinator::select_world(const workspace::v1::ControlOwner& owner){
    auto& state=*impl_;auto& world=state.find(owner);
    require(state.presentation && world.presentation_attached && world.world_identity_checked && !world.stopped && !world.failed,
        "World presentation is not available");world.supervisor->live();
    return state.presentation->select(world.presentation);
}
render::v1::PresentationState SessionCoordinator::inspect_presentation(const workspace::v1::ControlOwner& owner){
    auto& state=*impl_;auto& world=state.find_owned(owner);
    require(state.presentation && world.presentation_attached && !world.presentation_retired,"No retained world presentation");
    return state.presentation->inspect(world.presentation);
}
render::v1::GeometryPresentationTarget SessionCoordinator::attach_geometry(const identity::v1::ActorGeometryView& view){
    auto& state=*impl_;state.live();require(bool(state.presentation),"No host presentation owner");
    validate_geometry_view(view);require(state.presentation_generation!=UINT64_MAX,"Presentation generation exhausted");
    render::v1::GeometryPresentationTarget target;target.set_protocol_version(1);target.set_world_id(view.world_id());
    target.set_actor_id(view.actor_id());target.set_geometry_view_id(view.view_id());
    target.set_authority_service_incarnation(view.authority_service_incarnation());target.set_authority_generation(view.authority_generation());
    target.set_presentation_generation(++state.presentation_generation);
    require(state.geometry_targets.emplace(target.presentation_generation(),target).second,"Duplicate geometry generation");
    try {
        const auto attached=state.presentation->attach_geometry(target);
        require(attached.stage()==render::v1::ATTACHED,"Unexpected geometry attachment state");
    } catch (const WorldError&) {
        // A checked server rejection confirms that registration had no effect.
        state.geometry_targets.erase(target.presentation_generation());
        throw;
    }
    // Transport or malformed-reply failures retain attempted ownership for cleanup.
    return target;
}
render::v1::GeometryPresentationState SessionCoordinator::select_geometry(const render::v1::GeometryPresentationTarget& target){
    auto& state=*impl_;state.live();require(bool(state.presentation),"No host presentation owner");
    const auto found=state.geometry_targets.find(target.presentation_generation());require(found!=state.geometry_targets.end() && found->second.SerializeAsString()==target.SerializeAsString(),"Unknown geometry target");
    return state.presentation->select_geometry(target);
}
render::v1::GeometryPresentationState SessionCoordinator::publish_geometry(const render::v1::GeometryPresentationTarget& target,
    const identity::v1::ActorGeometryView& view){auto& state=*impl_;state.live();require(bool(state.presentation),"No host presentation owner");
    const auto found=state.geometry_targets.find(target.presentation_generation());require(found!=state.geometry_targets.end() && found->second.SerializeAsString()==target.SerializeAsString(),"Unknown geometry target");
    return state.presentation->publish_geometry(target,view);}
render::v1::GeometryPresentationState SessionCoordinator::retire_geometry(const render::v1::GeometryPresentationTarget& target){
    auto& state=*impl_;state.live();require(bool(state.presentation),"No host presentation owner");
    const auto found=state.geometry_targets.find(target.presentation_generation());require(found!=state.geometry_targets.end() && found->second.SerializeAsString()==target.SerializeAsString(),"Unknown geometry target");
    auto result=state.presentation->retire_geometry(target);state.geometry_targets.erase(found);return result;
}
void SessionCoordinator::recover_presentation(const workspace::v1::ControlOwner& owner){
    auto& state=*impl_;auto& world=state.find_owned(owner);
    state.retire_stopped_presentation(world);
}
core::v1::ActorLocalViewState SessionCoordinator::present_local_actor(const workspace::v1::ControlOwner& owner,
        const core::v1::ActorLocalViewIdentity& identity,std::uint32_t width,std::uint32_t height) {
    auto& state=*impl_;auto& world=state.find(owner);
    require(!world.local_frame,"Local presentation continuation owns the world channel");
    validate_local_view_identity(identity);
    if (width || height) validate_render_extent(width,height);
    require(state.presentation && world.presentation_attached && world.world_identity_checked &&
        !world.stopped && !world.failed && identity.world_id()==world.presentation.world_id() &&
        identity.authority_service_incarnation()==world.presentation.service_incarnation() &&
        identity.authority_generation()==world.presentation.authority_generation(),"Local actor does not match the retained producer");
    render::v1::LocalActorPresentationTarget target;
    *target.mutable_binding()=world.presentation;*target.mutable_identity()=identity;
    const auto key=identity.SerializeAsString();
    require(world.local_targets.contains(key) || world.local_targets.size()<256,"Too many retained local actor targets");
    // Attempted ownership and confirmed attestation have distinct lifetimes.
    auto& retained=world.local_targets.try_emplace(key,World::LocalTarget{target,false}).first->second;
    try {
        core::v1::Request request;request.set_protocol_version(1);
        auto* operation=request.mutable_actor_local_view();operation->set_protocol_version(1);*operation->mutable_identity()=identity;
        if(!retained.attested) {
            operation->mutable_bind();
            const auto bound=world_call(owner,request);
            if (bound.has_error()) throw ActorSessionRejected(bound.error());
            require(bound.has_actor_local_view() && bound.actor_local_view().bound() &&
                bound.actor_local_view().identity().SerializeAsString()==key,"Local actor binding was not confirmed");
            retained.attested=true;
        }
        // Selection and presentation still run their current authority checks every frame.
        state.presentation->select_local_actor(target);
        operation->mutable_present()->set_width(width);operation->mutable_present()->set_height(height);
        const auto reply=world_call(owner,request);
        if (reply.has_error()) throw ActorSessionRejected(reply.error());
        require(reply.has_actor_local_view(),"Local actor presentation receipt is missing");
        const auto& result=reply.actor_local_view();
        validate_local_presentation(result,target,width,height);
        return result;
    }catch(...) {retained.attested=false;throw;}
}
SessionCoordinator::LocalPresentationTicket SessionCoordinator::begin_present_local_actor(
    const workspace::v1::ControlOwner& owner,const core::v1::ActorLocalViewIdentity& identity,
    std::uint32_t width,std::uint32_t height) {
    auto& state=*impl_;auto& world=state.find(owner);validate_local_view_identity(identity);
    if(width || height)validate_render_extent(width,height);
    require(!world.local_frame && !world.exchange,"Local presentation operation already in progress");
    require(state.presentation && world.presentation_attached && world.world_identity_checked &&
        !world.stopped && !world.failed && identity.world_id()==world.presentation.world_id() &&
        identity.authority_service_incarnation()==world.presentation.service_incarnation() &&
        identity.authority_generation()==world.presentation.authority_generation(),
        "Local actor does not match the retained producer");
    require(state.local_operation_sequence!=UINT64_MAX,"Local presentation operation sequence exhausted");
    render::v1::LocalActorPresentationTarget target;
    *target.mutable_binding()=world.presentation;*target.mutable_identity()=identity;
    const auto key=identity.SerializeAsString();
    require(world.local_targets.contains(key) || world.local_targets.size()<256,"Too many retained local actor targets");
    auto& retained=world.local_targets.try_emplace(key,World::LocalTarget{target,false}).first->second;
    LocalPresentationTicket ticket{++state.local_operation_sequence,world.presentation.presentation_generation(),owner,identity};
    World::LocalFrame frame;frame.ticket=ticket;frame.target=target;frame.width=width;frame.height=height;
    frame.stage=retained.attested?World::LocalFrame::Select:World::LocalFrame::Bind;
    frame.deadline=Clock::now()+std::chrono::seconds(10);world.local_frame=std::move(frame);
    return ticket;
}
SessionCoordinator::LocalPresentationProgress SessionCoordinator::local_presentation_progress(
        const LocalPresentationTicket& ticket) const {
    auto& world=impl_->find_owned(ticket.owner);const auto& frame=impl_->frame(world,ticket);
    const auto stage=frame.stage==World::LocalFrame::Bind?LocalPresentationStage::bind:
        frame.stage==World::LocalFrame::Select?LocalPresentationStage::select:LocalPresentationStage::present;
    return {stage,bool(frame.world_token || frame.selection_token),frame.cancelled};
}
std::optional<core::v1::ActorLocalViewState> SessionCoordinator::poll_present_local_actor(
        const LocalPresentationTicket& ticket,unsigned maximum_steps) {
    require(maximum_steps>=1 && maximum_steps<=3,"Invalid local presentation poll budget");
    auto& state=*impl_;auto& world=state.find(ticket.owner);auto& frame=state.frame(world,ticket);
    require(!frame.cancelled,"Local presentation operation was cancelled");
    try {
        require(Clock::now()<frame.deadline,"Local presentation operation timed out");
        require(!world.failed && !world.stopped && world.presentation.presentation_generation()==ticket.presentation_generation,
            "Local presentation producer changed");
        for(unsigned step=0;step<maximum_steps;++step) {
            if(frame.stage==World::LocalFrame::Select) {
                if(!frame.selection_token)frame.selection_token=state.presentation->begin_select_local_actor(frame.target);
                try {
                    if(!state.presentation->poll_select_local_actor(*frame.selection_token))return {};
                }catch(const WorldError&){frame.selection_token.reset();throw;}
                frame.selection_token.reset();frame.stage=World::LocalFrame::Present;continue;
            }
            if(!frame.world_token) {
                core::v1::Request request;request.set_protocol_version(1);
                auto* operation=request.mutable_actor_local_view();operation->set_protocol_version(1);
                *operation->mutable_identity()=frame.target.identity();
                if(frame.stage==World::LocalFrame::Bind)operation->mutable_bind();
                else {operation->mutable_present()->set_width(frame.width);operation->mutable_present()->set_height(frame.height);}
                frame.world_token=state.begin_world(world,request,frame.deadline);
            }
            auto reply=state.poll_world(world,*frame.world_token);if(!reply)return {};
            frame.world_token.reset();
            if(reply->has_error())throw ActorSessionRejected(reply->error());
            require(reply->has_actor_local_view(),"Local actor presentation receipt is missing");
            const auto& result=reply->actor_local_view();
            if(frame.stage==World::LocalFrame::Bind) {
                require(result.bound() && result.identity().SerializeAsString()==ticket.identity.SerializeAsString(),
                    "Local actor binding was not confirmed");
                world.local_targets.at(ticket.identity.SerializeAsString()).attested=true;
                frame.stage=World::LocalFrame::Select;continue;
            }
            validate_local_presentation(result,frame.target,frame.width,frame.height);
            auto completed=result;world.local_frame.reset();return completed;
        }
        return {};
    }catch(...){
        if(world.local_frame)world.local_frame->cancelled=true;
        world.invalidate_local_attestations();throw;
    }
}
void SessionCoordinator::cancel_present_local_actor(const LocalPresentationTicket& ticket) {
    auto& state=*impl_;auto& world=state.find_owned(ticket.owner);auto& frame=state.frame(world,ticket);
    frame.cancelled=true;world.invalidate_local_attestations();
    // Draining may confirm effects already sent. It never starts the next stage.
    if(frame.world_token) {
        while(!state.poll_world(world,*frame.world_token))::poll(nullptr,0,2);
        frame.world_token.reset();
    }
    state.drain_local_selection(world);world.local_frame.reset();
}
void SessionCoordinator::retire_local_actor(const workspace::v1::ControlOwner& owner,
        const core::v1::ActorLocalViewIdentity& identity) {
    auto& state=*impl_;auto& world=state.find(owner);validate_local_view_identity(identity);
    if(world.local_frame)cancel_present_local_actor(world.local_frame->ticket);
    const auto key=identity.SerializeAsString();const auto found=world.local_targets.find(key);
    if (found==world.local_targets.end()) return;
    found->second.attested=false;
    require(bool(state.presentation),"Local actor retirement requires its retained compositor");
    state.presentation->retire_local_actor(found->second.target);
    core::v1::Request request;request.set_protocol_version(1);
    auto* operation=request.mutable_actor_local_view();operation->set_protocol_version(1);
    *operation->mutable_identity()=identity;operation->mutable_release();
    const auto reply=world_call(owner,request);
    if (reply.has_error()) {
        require(reply.error().code()==core::v1::FORBIDDEN || reply.error().code()==core::v1::UNAUTHORIZED,
            "Local actor release was not confirmed");
    }else require(reply.has_actor_local_view() && !reply.actor_local_view().bound() &&
        reply.actor_local_view().identity().SerializeAsString()==key,"Local actor release receipt mismatch");
    const auto controls=state.administrative(owner);
    for (const auto& watch:controls.observation_watches())
        require(watch.view().SerializeAsString()!=key,"Local actor observation release remains pending");
    world.local_targets.erase(found);
}
void SessionCoordinator::clear_presentation(){
    auto& state=*impl_;state.live();require(bool(state.presentation),"No host presentation owner");
    state.presentation->clear_selection();
}
core::v1::PixelExtent SessionCoordinator::native_viewport() const {
    auto& state=*impl_;state.live();require(bool(state.presentation),"No host presentation owner");
    return state.presentation->native_viewport();
}
void SessionCoordinator::present_controls(std::uint64_t generation,const control::v1::ControlMenuView& view){
    auto& state=*impl_;state.live();require(bool(state.presentation),"No host presentation owner");
    for(auto& [key,world]:state.worlds){(void)key;state.drain_local_selection(*world);}
    state.presentation->present_controls(generation,view);
}
void SessionCoordinator::stop_presentation(){
    auto& state=*impl_;state.owned();require(bool(state.presentation),"No host presentation owner");
    for(const auto& [key,world]:state.worlds){
        (void)key;require(!world->presentation_attached || world->presentation_retired,"World presentation cleanup remains unconfirmed");
    }
    while(!state.geometry_targets.empty()) {
        const auto target=state.geometry_targets.begin()->second;
        state.presentation->retire_geometry(target);state.geometry_targets.erase(state.geometry_targets.begin());
    }
    state.presentation->shutdown();state.presentation.reset();
}
void SessionCoordinator::start_capture(std::uint64_t generation,const control::v1::NativeCaptureConfig& config,int endpoint){
    auto& state=*impl_;state.live();require(bool(state.presentation),"No host presentation owner");
    state.presentation->start_capture(generation,config,endpoint);
}
void SessionCoordinator::stop_capture(std::uint64_t generation){
    auto& state=*impl_;state.owned();require(bool(state.presentation),"No host presentation owner");
    state.presentation->stop_capture(generation);
}
workspace::v1::ControlOwner SessionCoordinator::start_world(const std::filesystem::path& path,
    v1::AuthoritySupervisorConfig config, int native, int actor_listener, unsigned local_actor_channels,const std::string& world_id) {
    auto& state = *impl_; state.live(); validate_message(config);
    require(config.has_compositor_native_target_fd() && !config.has_broker_native_target_fd() &&
        config.inherited_source_fds_size() == 0 && config.launch().inherited_fds_size() == 0,
        "Session world requires an unmapped compositor launch configuration");
    require(config.binding().issuer_id() == state.broker.launch().binding().issuer_id(), "World issuer differs from the configured session issuer");
    require(bool(state.presentation)==!world_id.empty(),"Shared presentation requires the explicit world identity");
    require(world_id.size()<=256 && world_id.find('\0')==std::string::npos,"Invalid presentation world identity");
    const auto key = config.binding().service_incarnation();
    require(key.size() == 32 && !state.worlds.contains(key) && state.worlds.size() < 256, "Duplicate or excessive session world launch");
    for (const auto& arg : config.launch().arguments())
        require(arg != "--workspace-fd" && arg != "--workspace-pid" && arg != "--control-supervisor-fd" && arg != "--control-supervisor-pid" && arg != "--presentation-fd" && arg != "--presentation-pidfd" &&
            arg != "--presentation-pid" && arg != "--presentation-owner-pid",
            "Session owns world broker endpoint arguments");
    unsigned identity_configs = 0, tls_configs = 0;
    std::filesystem::path local_actor_path;
    for (int i = 0; i < config.launch().arguments_size(); ++i) {
        const auto& arg = config.launch().arguments(i);
        require(arg != "--actor-fd" && arg != "--actor-listen-fd", "Session owns world actor endpoint arguments");
        if (arg == "--identity-config" || arg == "--actor-tls-config") {
            require(i + 1 < config.launch().arguments_size() &&
                std::filesystem::path(config.launch().arguments(i + 1)).is_absolute(), "Actor configuration requires an absolute path");
            if (arg == "--identity-config") {
                ++identity_configs;
                identity::v1::WorldIdentityConfig profile;
                require(google::protobuf::TextFormat::ParseFromString(identity::tls_detail::load_config(config.launch().arguments(i+1)),&profile),
                    "Malformed world identity profile");
                validate_message(profile);local_actor_path=profile.local_actor_socket_path();
            } else ++tls_configs;
        }
    }
    require(local_actor_channels <= 32, "Excessive local actor channels");
    require(identity_configs == ((actor_listener != -1 || local_actor_channels) ? 1U : 0U),
        "Actor transport and identity configuration are required together");
    require(tls_configs == (actor_listener != -1 ? 1U : 0U),
        "Actor listener, identity configuration, and TLS configuration are required together");
    File listener;
    if (actor_listener != -1) listener.reset(retain_actor_listener(actor_listener));
    int target = 255;
    while (target == static_cast<int>(config.participant_target_fd()) || target == static_cast<int>(config.compositor_native_target_fd())) --target;
    const auto next_target = [&] {
        do { --target; } while (target == static_cast<int>(config.participant_target_fd()) ||
            target == static_cast<int>(config.compositor_native_target_fd()));
        return target;
    };
    Pair endpoint, channel;
    std::unique_ptr<Pair> presentation_channel;
    if(state.presentation){
        presentation_channel=std::make_unique<Pair>();int enabled=1;
        require(setsockopt(presentation_channel->parent.value,SOL_SOCKET,SO_PASSCRED,&enabled,sizeof(enabled))==0 &&
            setsockopt(presentation_channel->child.value,SOL_SOCKET,SO_PASSCRED,&enabled,sizeof(enabled))==0,
            "Cannot authenticate world presentation channel");
    }
    config.add_inherited_source_fds(endpoint.child.value); config.mutable_launch()->add_inherited_fds(target);
    config.mutable_launch()->add_arguments("--workspace-fd"); config.mutable_launch()->add_arguments(std::to_string(target));
    config.mutable_launch()->add_arguments("--workspace-pid"); config.mutable_launch()->add_arguments(std::to_string(state.broker.broker_pid()));
    if (listener.value >= 3) {
        config.add_inherited_source_fds(listener.value); config.mutable_launch()->add_inherited_fds(next_target());
        config.mutable_launch()->add_arguments("--actor-listen-fd"); config.mutable_launch()->add_arguments(std::to_string(target));
    }
    if(state.presentation){
        config.add_inherited_source_fds(presentation_channel->child.value);config.mutable_launch()->add_inherited_fds(next_target());
        config.mutable_launch()->add_arguments("--presentation-fd");config.mutable_launch()->add_arguments(std::to_string(target));
        config.add_inherited_source_fds(state.presentation->pidfd());config.mutable_launch()->add_inherited_fds(next_target());
        config.mutable_launch()->add_arguments("--presentation-pidfd");config.mutable_launch()->add_arguments(std::to_string(target));
        config.mutable_launch()->add_arguments("--presentation-pid");config.mutable_launch()->add_arguments(std::to_string(state.presentation->pid()));
        config.mutable_launch()->add_arguments("--presentation-owner-pid");config.mutable_launch()->add_arguments(std::to_string(state.creator));
    }
    std::vector<std::unique_ptr<Pair>> actors;
    for (unsigned index = 0; index < local_actor_channels; ++index) {
        auto actor = std::make_unique<Pair>();
        config.add_inherited_source_fds(actor->child.value); config.mutable_launch()->add_inherited_fds(next_target());
        config.mutable_launch()->add_arguments("--actor-fd"); config.mutable_launch()->add_arguments(std::to_string(target));
        actors.push_back(std::move(actor));
    }
    auto [position, inserted] = state.worlds.emplace(key, std::make_unique<World>());
    require(inserted, "Session world insertion failed");
    auto& world = *position->second;
    world.journal_directory = config.journal_directory();
    world.authorization = config.authorization();
    try {
        world.supervisor = std::make_unique<SessionLaunch>(state.executable, path, config, native,
            std::array<int, 3>{channel.child.value, channel.child.value, STDERR_FILENO}, bool(state.presentation));
        endpoint.child.reset(); channel.child.reset(); listener.reset();
        world.actors.reserve(actors.size());
        for (auto& actor : actors) {
            auto client = std::make_unique<File>();
            client->reset(std::exchange(actor->parent.value, -1));
            actor->child.reset();
            world.actors.push_back(std::move(client));
        }
        world.record = world.supervisor->record();
        require(world.record.native().server_id() == state.broker.launch().native().server_id(), "World and broker have different native server identities");
        world.owner = world.supervisor->control_owner();
        if(state.presentation){
            require(state.presentation_generation!=UINT64_MAX,"Presentation generation exhausted");
            auto& binding=world.presentation;binding.set_protocol_version(1);binding.set_world_id(world_id);
            binding.set_service_incarnation(world.owner.launch().service_incarnation());
            binding.set_authority_generation(world.owner.launch().generation());binding.set_presentation_generation(++state.presentation_generation);
            auto process=world.supervisor->world_handle();
            world.presentation_attached=true; // Retain possible attachment even if its reply is lost.
            const auto attached=state.presentation->attach(binding,presentation_channel->parent.value,process,world.owner,world.record.process());
            require(attached.stage()==render::v1::ATTACHED,"Unexpected world attachment state");
            presentation_channel.reset();
        }
        world.management = std::make_unique<identity::ParticipantStream>(std::exchange(channel.parent.value, -1), state.creator);
        state.administrative(world.owner, endpoint.parent.value);
        endpoint.parent.reset();
        world.registered = true;
        if(state.presentation){
            core::v1::Request request;request.set_protocol_version(1);request.mutable_world_info();
            const auto reply=world_call(world.owner,request);
            require(reply.has_world_info() && reply.world_info().world_id()==world_id,"Presented world identity differs from launch request");
            world.world_identity_checked=true;
        }
        if(!local_actor_path.empty()) {
            auto process=world.supervisor->world_handle();
            world.local_endpoint=std::make_unique<local_actor::RetirementPin>(local_actor_path,process);
        }
        return world.owner;
    } catch (...) {
        world.failed = true; world.invalidate_local_attestations();
        // Preserve the exact launch entry. An uncertain registration must not be replaced with a new identity.
        if (world.owner.has_launch()) {
            try { state.administrative(world.owner, -1, true); }
            catch (const std::exception& error) { std::cerr << "World registration fence unresolved: " << error.what() << '\n'; }
        }
        if (world.supervisor) {
            try { world.record = world.supervisor->stop(); world.stopped = true; }
            catch (const std::exception& error) { std::cerr << "World registration cleanup unresolved: " << error.what() << '\n'; }
        }
        world.management.reset();
        world.actors.clear();
        throw;
    }
}
int SessionCoordinator::take_actor_channel(const workspace::v1::ControlOwner& owner, unsigned index) {
    auto& world = impl_->find(owner);
    require(!world.failed && !world.stopped, "Session world is not available");
    world.supervisor->live();
    require(index < world.actors.size() && world.actors[index]->value >= 3, "Actor channel is unavailable or already transferred");
    return std::exchange(world.actors[index]->value, -1);
}
const v1::AuthorityLaunchRecord& SessionCoordinator::world_launch(const workspace::v1::ControlOwner& owner) const {
    return impl_->find(owner).record;
}
workspace::v1::ControlOwnerState SessionCoordinator::inspect_world(const workspace::v1::ControlOwner& owner) {
    impl_->find(owner); return impl_->administrative(owner);
}
core::v1::Reply SessionCoordinator::world_call(const workspace::v1::ControlOwner& owner,const core::v1::Request& supplied) {
    auto& state=*impl_;auto& world=state.find(owner);
    require(!world.local_frame,"Local presentation continuation owns the world channel");
    const auto token=state.begin_world(world,supplied,Clock::now()+std::chrono::seconds(10));
    for(;;){if(auto reply=state.poll_world(world,token))return std::move(*reply);::poll(nullptr,0,2);}
}
v1::AuthorityLaunchRecord SessionCoordinator::stop_world(const workspace::v1::ControlOwner& owner) {
    auto& state = *impl_; auto& world = state.find_owned(owner);
    require(!world.stopped, "Session world already stopped");
    world.invalidate_local_attestations();
    // Stop its input path before native/process cleanup. Reservations remain pending separate selective recovery.
    try { state.administrative(owner, -1, true); }
    catch (const std::exception& error) {
        std::cerr << "World broker fence unconfirmed; stopping owned scopes: " << error.what() << '\n';
    }
    world.record = world.supervisor->stop(); world.stopped = true; world.management.reset(); world.actors.clear();
    if(world.local_endpoint)world.local_endpoint->retire();
    if(world.presentation_attached)recover_presentation(owner);
    return world.record;
}
BrokerClient& SessionCoordinator::root() { impl_->live(); return impl_->broker.root(); }
workspace::v1::ControlOwnerState SessionCoordinator::recover_world(const workspace::v1::ControlOwner& owner) {
    auto& state = *impl_; auto& world = state.find(owner);
    // Stopped state requires SessionLaunch to validate both owned cleanup receipts.
    require(world.stopped && world.record.stage() == v1::OWNED_SCOPES_STOPPED,
        "Control recovery requires checked world cleanup");
    const auto recovered = state.administrative(owner, -1, false, &world.record);
    // Broker release can precede a persistence error. Exact retry uses its retained receipt.
    AuthorityLaunch journal(world.journal_directory);
    world.dependent = journal.record_control_recovery(world.record, state.broker.launch(), recovered);
    return recovered;
}
identity::v1::AuthorityRetirementState SessionCoordinator::retire_world(const workspace::v1::ControlOwner& owner) {
    auto& world = impl_->find_owned(owner);
    require(world.stopped && world.record.stage() == v1::OWNED_SCOPES_STOPPED && world.dependent,
        "Retirement requires retained durable world cleanup");
    AuthorityLaunch journal(world.journal_directory);
    auto result = journal.retire_control_recovery(*world.dependent, world.authorization,
        world.supervisor->checked_failure(world.record));
    require(!world.retirement || world.retirement->SerializeAsString() == result.SerializeAsString(),
        "Saved retirement differs from retained issuer result");
    world.retirement = result;
    return result;
}
v1::AuthorityDependentCleanup SessionCoordinator::recover_world_after_broker_stop(const workspace::v1::ControlOwner& owner) {
    auto& state = *impl_; auto& world = state.find_owned(owner);
    require(world.stopped && world.record.stage() == v1::OWNED_SCOPES_STOPPED,
        "Dependency recovery requires checked world cleanup");
    const auto& broker = state.broker.cleanup();
    AuthorityLaunch journal(world.journal_directory);
    world.dependent = journal.record_stopped_broker_recovery(world.record, broker);
    return *world.dependent;
}
const v1::AuthorityLaunchRecord& SessionCoordinator::broker_launch() const { impl_->live(); return impl_->broker.launch(); }
v1::AuthorityLaunchRecord SessionCoordinator::stop_broker() { impl_->owned(); return impl_->broker.stop(); }
identity::v1::AuthorityRetirementState SessionCoordinator::retire_broker() { impl_->owned(); return impl_->broker.retire(); }
v1::AuthorityLaunchRecord SessionCoordinator::stop() {
    auto& state = *impl_; state.owned();
    for (const auto& [key, world] : state.worlds) {
        (void)key; require(world->stopped || !world->supervisor, "Stop retained world scopes before the session broker");
    }
    return state.broker.stop();
}
std::vector<std::string> SessionCoordinator::stop_retained_worlds() {
    auto& state = *impl_; state.owned();
    std::vector<std::string> unresolved;
    for (auto& [key, entry] : state.worlds) {
        constexpr char digits[] = "0123456789abcdef";
        std::string label; label.reserve(key.size() * 2);
        for (const unsigned char byte : key) {
            label.push_back(digits[byte >> 4]); label.push_back(digits[byte & 15]);
        }
        auto& world = *entry;
        world.invalidate_local_attestations();
        std::string registered_stop_error;
        if (world.registered && world.supervisor && !world.stopped) {
            try { (void)stop_world(world.owner); }
            catch (const std::exception& error) {
                registered_stop_error = error.what();
            }
        }
        if (world.supervisor && !world.stopped) {
            if (world.owner.has_launch()) {
                try { state.administrative(world.owner, -1, true); }
                catch (const std::exception& error) {
                    // The supervisor stop still must be attempted. Its receipt does not prove the fence.
                    unresolved.push_back(label + ": broker fence: " + error.what());
                }
            }
            try {
                world.record = world.supervisor->stop();
                require(world.record.stage() == v1::OWNED_SCOPES_STOPPED &&
                    world.record.has_process_stopped() && world.record.has_native_stopped(),
                    "Retained world lacks checked stop receipts");
                world.stopped = true;
                world.management.reset(); world.actors.clear();
            } catch (const std::exception& error) {
                unresolved.push_back(label + ": world stop: " + error.what());
            }
        }
        if (!world.supervisor) {
            // SessionLaunch construction can fail after a child starts. Its constructor
            // cannot return ownership or a checked receipt in that case.
            unresolved.push_back(label + ": launch construction has no checked stop receipt");
        }
        if(world.local_endpoint) {
            try {
                require(world.stopped && world.record.stage()==v1::OWNED_SCOPES_STOPPED &&
                    world.record.has_process_stopped() && world.record.has_native_stopped(),"Local endpoint retirement requires checked world stop receipts");
                world.local_endpoint->retire();
            }catch(const std::exception& error){unresolved.push_back(label+": local actor endpoint: "+error.what());}
        }
        if (world.presentation_attached && !world.presentation_retired) {
            try {
                state.retire_stopped_presentation(world);
            } catch (const std::exception& error) {
                unresolved.push_back(label + ": presentation: " + error.what());
            }
        }
        if (!registered_stop_error.empty() && (!world.stopped ||
            (world.presentation_attached && !world.presentation_retired)))
            unresolved.push_back(label + ": registered world stop: " + registered_stop_error);
    }
    return unresolved;
}
}
