#include "world/actors.hpp"
#include "world/surfaces.hpp"
#include "world/workspace_presentations.hpp"
#include "platform/broker.hpp"
#include "core/scene.hpp"
#include "core/geometry.hpp"
#include <openssl/rand.h>
#include <algorithm>
#include <array>
#include <cmath>

namespace spatial {
namespace {
void need(bool value,const char* message) {if(!value)throw WorldError(wire::INVALID_ARGUMENT,message);}
void identifier(const std::string& value) {
    need(!value.empty() && value.size()<=256 && value.find('\0')==std::string::npos,"Invalid application identity");
}
std::string entropy() {
    std::string value(32,'\0');
    if(RAND_bytes(reinterpret_cast<unsigned char*>(value.data()),value.size())!=1)
        throw std::runtime_error("Cannot obtain application discovery entropy");
    return value;
}
std::string slot_identity() {
    const auto bytes=entropy();std::string result="application-";
    constexpr char digits[]="0123456789abcdef";
    for(unsigned char value:bytes) {result+=digits[value>>4];result+=digits[value&15];}
    return result;
}
bool segment_hits_box(Vec3 first,Vec3 last,const wire::Entity& box) {
    first=inverse_point(scene_pose(box.pose()),first);last=inverse_point(scene_pose(box.pose()),last);
    const auto& bounds=box.bounds();const Vec3 low{bounds.min().x(),bounds.min().y(),bounds.min().z()};
    const Vec3 high{bounds.max().x(),bounds.max().y(),bounds.max().z()};double begin=0,end=1;
    for(unsigned axis=0;axis<3;++axis) {
        const auto delta=last[axis]-first[axis];
        if(std::abs(delta)<1e-12) {if(first[axis]<low[axis] || first[axis]>high[axis])return false;continue;}
        auto a=(low[axis]-first[axis])/delta,b=(high[axis]-first[axis])/delta;if(a>b)std::swap(a,b);
        begin=std::max(begin,a);end=std::min(end,b);if(begin>end)return false;
    }
    return true;
}
bool panel_hits_box(const Pose& panel,double width,double height,const wire::Entity& box) {
    const std::array<Vec3,4> corners{{transform_point(panel,{-width/2,-height/2,0}),
        transform_point(panel,{width/2,-height/2,0}),transform_point(panel,{width/2,height/2,0}),
        transform_point(panel,{-width/2,height/2,0})}};
    for(unsigned i=0;i<4;++i)if(segment_hits_box(corners[i],corners[(i+1)%4],box))return true;
    const auto& low=box.bounds().min();const auto& high=box.bounds().max();std::array<Vec3,8> vertices;
    for(unsigned i=0;i<8;++i)vertices[i]=inverse_point(panel,transform_point(scene_pose(box.pose()),
        {i&1?high.x():low.x(),i&2?high.y():low.y(),i&4?high.z():low.z()}));
    for(unsigned i=0;i<8;++i)for(unsigned axis=0;axis<3;++axis)if(!(i&(1u<<axis))) {
        const auto& a=vertices[i];const auto& b=vertices[i|(1u<<axis)];const auto delta=b[2]-a[2];
        if(std::abs(delta)<1e-12)continue;
        const auto t=-a[2]/delta;
        if(t>=0 && t<=1 && std::abs(a[0]+t*(b[0]-a[0]))<=width/2 &&
                std::abs(a[1]+t*(b[1]-a[1]))<=height/2)return true;
    }
    return false;
}
}

bool Actors::application_slot_authorized(const Actor& actor,const wire::SurfaceRecord& surface,bool use) const {
    if(!surface.has_slot())return true;
    try {
        const auto slot=store_.get(surface.slot());if(!slot.has_application_slot())return true;
        const std::vector<std::string> scope{surface.room(),slot.id()};
        return actor.principal && !surface.room().empty() && granted(actor,"observe",scope) &&
            granted(actor,"representation.view",scope) && (!use || granted(actor,"representation.use",scope));
    }catch(const WorldError& error) {if(error.code==wire::NOT_FOUND)return false;throw;}
}
bool Actors::held_application_slot_authorized(const Actor& actor) const {
    if(!surfaces_ || !actor.native.has_focus())return true;
    try {return application_slot_authorized(actor,surfaces_->get(actor.native.focus().id()),true);}
    catch(const WorldError& error) {if(error.code==wire::NOT_FOUND || error.code==wire::STALE_TARGET)return false;throw;}
}
wire::ActorApplicationSurfacesReply Actors::discover_application(Actor& actor,
        const wire::ActorApplicationSurfacesRequest& request) {
    need(request.protocol_version()==1,"Unsupported application discovery version");identifier(request.application_id());
    if(!actor.local_application_output)throw WorldError(wire::UNIMPLEMENTED,"Remote application surfaces are not supported");
    if(!actor.principal || !broker_ || !surfaces_ || !granted(actor,"observe"))
        throw WorldError(wire::FORBIDDEN,"Application observation authority required");
    const auto workspace=selected_workspace(actor);if(workspace.empty())throw WorldError(wire::NO_WORKSPACE,"No selected workspace");
    if(!actor.principal || actor.token.empty())throw WorldError(wire::FORBIDDEN,"Application discovery context was closed");
    const auto token=actor.token,principal=actor.principal->SerializeAsString();
    const auto current=[&] {return actor.token==token && actor.principal && actor.principal->SerializeAsString()==principal &&
        !actor.close_pending && !actor.departure_pending && actor.local_application_output &&
        store_.selected_workspace(actor.entity.id())==workspace && store_.workspace_available(workspace) && granted(actor,"observe");};
    std::vector<broker_wire::AuthorizationFence> fences;
    for(const auto right:{authority::v1::WORKSPACE_ACCESS,authority::v1::APPLICATION_OBSERVE}) {
        const auto decision=workspace_check(actor,workspace,right,
            right==authority::v1::APPLICATION_OBSERVE ? std::vector<std::string>{"applications",request.application_id()} : std::vector<std::string>{},&fences);
        if(!decision || !decision->allowed() || !current())throw WorldError(wire::FORBIDDEN,"Application observation authority denied");
    }
    refresh_native();const auto candidates=surfaces_->application_surfaces(workspace,request.application_id());
    if(!current())throw WorldError(wire::FORBIDDEN,"Application discovery context changed");
    if(candidates.size()>64)throw WorldError(wire::RESOURCE_EXHAUSTED,"Application surface discovery exceeds its bound");
    broker_wire::ApplicationAccessRequest access;*access.mutable_principal()=*actor.principal;
    std::map<std::string,int> groups;std::vector<ObservedApplication> identities;
    for(const auto& surface:candidates) {
        if(!surface.room().empty() && surface.room()!=actor.entity.room())continue;
        if(!application_slot_authorized(actor,surface,false))continue;
        wire::SurfaceHandle handle;handle.set_id(surface.id());handle.set_generation(surface.generation());
        const auto app=surfaces_->application(handle);if(!app)continue;
        if(!groups.contains(app->SerializeAsString())) {
            groups.emplace(app->SerializeAsString(),access.applications_size());*access.add_applications()=*app;
        }
        identities.push_back({handle,*app});
    }
    if(access.applications_size()) {
        const auto checked=broker_->application_access(access);
        broker_wire::AuthorizationFence stamp;stamp.set_incarnation(checked.incarnation());stamp.set_epoch(checked.epoch());fences.push_back(stamp);
        for(const auto& decision:checked.applications())if(!decision.observe())
            throw WorldError(wire::FORBIDDEN,"Application observation authority denied");
    }
    refresh_native();validate_authority_fence(actor,fences);
    if(!current())throw WorldError(wire::FORBIDDEN,"Application discovery context changed");
    wire::ActorApplicationSurfacesReply reply;reply.set_workspace_id(workspace);reply.set_application_id(request.application_id());
    std::map<std::string,ObservedApplication> receipts;
    for(const auto& identity:identities) {
        const auto actual=surfaces_->application(identity.handle);const auto surface=surfaces_->get(identity.handle.id());
        if(!actual || actual->SerializeAsString()!=identity.application.SerializeAsString() ||
                (!surface.room().empty() && surface.room()!=actor.entity.room()) || !application_slot_authorized(actor,surface,false))
            throw WorldError(wire::STALE_TARGET,"Application changed during discovery");
        const auto receipt=entropy();if(!receipts.emplace(receipt,identity).second)throw std::runtime_error("Discovery entropy collision");
        auto* value=reply.add_surfaces();value->set_discovery_id(receipt);*value->mutable_surface()=identity.handle;
        *value->mutable_pixels()=surface.pixels();
        if(surface.has_slot()) {value->set_slot_id(surface.slot());value->set_slot_revision(store_.get(surface.slot()).revision());}
    }
    if(!current())throw WorldError(wire::FORBIDDEN,"Application discovery context changed before publication");
    actor.application_discoveries=std::move(receipts);return reply;
}
wire::ActorApplicationPlaceReply Actors::place_application(Actor& actor,const wire::ActorApplicationPlaceRequest& request) {
    need(request.protocol_version()==1 && request.operation_id().size()==16 && request.discovery_id().size()==32 &&
        request.has_transform() && request.has_size() && request.expected_world_revision()>0,"Invalid application placement request");
    const auto plane=scene_pose(request.transform());const auto width=request.size().x(),height=request.size().y();
    need(std::isfinite(width) && std::isfinite(height) && width>=.1 && height>=.1 && width<=8 && height<=8,"Invalid application panel dimensions");
    if(!actor.local_application_output)throw WorldError(wire::UNIMPLEMENTED,"Remote application placement is not supported");
    if(!actor.principal || !broker_ || !surfaces_)throw WorldError(wire::FORBIDDEN,"Application placement authority required");
    const auto found=actor.application_discoveries.find(request.discovery_id());
    if(found==actor.application_discoveries.end())throw WorldError(wire::FORBIDDEN,"Application discovery receipt is unavailable");
    const auto identity=found->second;const auto workspace=selected_workspace(actor);
    if(!actor.principal || actor.token.empty())throw WorldError(wire::FORBIDDEN,"Application placement context was closed");
    if(workspace.empty() || workspace!=identity.application.workspace())throw WorldError(wire::FORBIDDEN,"Application workspace context changed");
    const auto room=request.has_room_id()?request.room_id():actor.entity.room();identifier(room);
    if(room!=actor.entity.room())throw WorldError(wire::NOT_VISIBLE,"Application placement requires the current room");
    const auto token=actor.token,principal=actor.principal->SerializeAsString(),pose=actor.entity.pose().SerializeAsString();
    const auto current=[&] {return actor.token==token && actor.principal && actor.principal->SerializeAsString()==principal &&
        !actor.close_pending && !actor.departure_pending && actor.local_application_output && actor.entity.room()==room &&
        actor.entity.pose().SerializeAsString()==pose && store_.selected_workspace(actor.entity.id())==workspace &&
        store_.workspace_available(workspace) && granted(actor,"observe",{room}) && granted(actor,"presentation.create",{room});};
    if(!current())throw WorldError(wire::FORBIDDEN,"World application placement authority required");
    wire::Entity entity;entity.set_id(slot_identity());entity.set_kind(wire::SURFACE_SLOT);entity.set_label("Application");
    entity.set_room(room);*entity.mutable_pose()=request.transform();*entity.mutable_size()=request.size();
    auto* binding=entity.mutable_application_slot();binding->set_workspace(workspace);binding->set_application(identity.application.application());
    binding->set_server_id(identity.application.server_id());binding->set_surface_id(identity.handle.id());
    const auto digest=request_digest(request),operation_principal=actor.principal->issuer_id()+actor.principal->actor_id();
    const auto placement_reply=[&](const wire::Entity& placed) {
        wire::ActorApplicationPlaceReply reply;reply.set_slot_id(placed.id());reply.set_slot_revision(placed.revision());reply.set_room_id(placed.room());
        *reply.mutable_transform()=placed.pose();*reply.mutable_size()=placed.size();*reply.mutable_surface()=identity.handle;
        reply.set_world_revision(placed.world_revision());return reply;
    };
    std::vector<broker_wire::AuthorizationFence> fences;
    for(const auto right:{authority::v1::WORKSPACE_ACCESS,authority::v1::APPLICATION_OBSERVE,
                          authority::v1::WORKSPACE_PRESENTATION_ATTACH}) {
        const auto components=right==authority::v1::APPLICATION_OBSERVE
            ? std::vector<std::string>{"applications",identity.application.application()}
            : std::vector<std::string>{};
        const auto checked=workspace_check(actor,workspace,right,components,&fences);
        if(!checked || !checked->allowed() || !current())throw WorldError(wire::FORBIDDEN,"Workspace attachment authority denied");
    }
    // A retry returns an already committed receipt. Keep the current actor and
    // workspace fences, but do not require the application process to remain
    // observable after its placement has committed.
    if(const auto replay=store_.application_slot_operation(entity,request.operation_id(),digest,operation_principal)) {
        validate_authority_fence(actor,fences);
        if(!current())throw WorldError(wire::FORBIDDEN,"Application placement context changed before replay publication");
        return placement_reply(*replay);
    }
    broker_wire::ApplicationAccessRequest access;*access.mutable_principal()=*actor.principal;*access.add_applications()=identity.application;
    const auto checked=broker_->application_access(access);
    if(checked.applications_size()!=1 || !checked.applications(0).observe())throw WorldError(wire::FORBIDDEN,"Application observation authority denied");
    broker_wire::AuthorizationFence stamp;stamp.set_incarnation(checked.incarnation());stamp.set_epoch(checked.epoch());fences.push_back(stamp);
    refresh_native();const auto actual=surfaces_->application(identity.handle);
    if(!actual || actual->SerializeAsString()!=identity.application.SerializeAsString())throw WorldError(wire::STALE_TARGET,"Application discovery is stale");
    const auto camera=scene_pose(actor.entity.pose());
    if(std::hypot(plane.p[0]-camera.p[0],plane.p[1]-camera.p[1],plane.p[2]-camera.p[2])>config_.interaction_reach())
        throw WorldError(wire::OUT_OF_REACH,"Application placement exceeds reach");
    const auto normal=transform_point(Pose{{0,0,0},plane.q},{0,0,1});double facing=0;
    for(unsigned axis=0;axis<3;++axis)facing+=(camera.p[axis]-plane.p[axis])*normal[axis];
    need(facing>1e-6,"Application placement must face the actor");
    validate_presentation_placement(room,request.transform());
    const auto snapshot=store_.snapshot();
    for(const auto& entity:snapshot.entities())if(entity.kind()==wire::BARRIER && entity.room()==room &&
            (panel_hits_box(plane,width,height,entity) || segment_hits_box(camera.p,plane.p,entity)))
        throw WorldError(wire::INVALID_ARGUMENT,"Application placement intersects or crosses a room collider");
    validate_authority_fence(actor,fences);
    if(!current())throw WorldError(wire::FORBIDDEN,"Application placement context changed");
    const auto confirmed=surfaces_->application(identity.handle);
    if(!confirmed || confirmed->SerializeAsString()!=identity.application.SerializeAsString())
        throw WorldError(wire::STALE_TARGET,"Application changed before placement commit");
    const auto placed=store_.create_application_slot(entity,request.expected_world_revision(),request.operation_id(),digest,
        operation_principal);
    return placement_reply(placed);
}
} // namespace spatial
