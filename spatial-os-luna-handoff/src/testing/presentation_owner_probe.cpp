#include "platform/presentation_owner.hpp"
#include <iostream>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <unistd.h>
int main(int argc,char** argv){
    if(argc!=5)return 2;
    try{
        spatial::PresentationOwner owner(argv[1],argv[2],std::stoi(argv[3]));
        spatial::render::v1::WorldPresentationBinding binding;
        binding.set_protocol_version(1);binding.set_world_id("owner-test");
        binding.set_service_incarnation(std::string(32,'i'));
        binding.set_authority_generation(1);binding.set_presentation_generation(1);
        auto process=spatial::ProcessHandle::take(static_cast<int>(syscall(SYS_pidfd_open,getpid(),0)),getpid());
        int pair[2];if(socketpair(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0,pair))return 3;
        spatial::workspace::v1::ControlOwner scope;
        spatial::supervisor::v1::ProcessScopeIdentity identity;
        const std::string mode=argv[4];
        spatial::render::v1::GeometryPresentationTarget geometry;
        geometry.set_protocol_version(1);geometry.set_world_id("geometry-test");geometry.set_actor_id("observer");
        geometry.set_geometry_view_id(std::string(32,'v'));geometry.set_authority_service_incarnation(std::string(32,'i'));
        geometry.set_authority_generation(1);geometry.set_presentation_generation(2);
        if(mode=="geometry_sequence"){
            owner.attach_geometry(geometry);owner.select_geometry(geometry);
            spatial::identity::v1::ActorGeometryView view;
            view.set_world_id(geometry.world_id());view.set_actor_id(geometry.actor_id());
            view.set_view_id(geometry.geometry_view_id());view.set_authority_service_incarnation(geometry.authority_service_incarnation());
            view.set_authority_generation(1);view.set_sequence(7);view.set_room("entry");
            view.mutable_camera()->mutable_position();view.mutable_camera()->mutable_orientation()->set_w(1);
            auto* config=view.mutable_config();config->set_vertical_fov(1.5);config->set_near_clip(.01);config->set_far_clip(1000);
            config->mutable_background();config->mutable_barrier_color();
            owner.publish_geometry(geometry,view);
            if(owner.select_geometry(geometry).last_view_sequence()!=7)return 7;
            if(owner.retire_geometry(geometry).last_view_sequence()!=7)return 8;
            owner.shutdown();close(pair[0]);close(pair[1]);std::cout<<"geometry sequence confirmed\n";return 0;
        }
        bool rejected=false;
        try{
            if(mode=="lost_geometry_attach" || mode=="missing_geometry_state")owner.attach_geometry(geometry);
            else owner.attach(binding,pair[0],process,scope,identity);
            if(std::string(argv[4])=="select_stage")owner.select(binding);
        }catch(const std::exception& e){rejected=true;std::cout<<e.what()<<'\n';}
        close(pair[0]);close(pair[1]);
        if(!rejected)return 4;
        try{
            if(mode=="lost_geometry_attach" || mode=="missing_geometry_state")owner.retire_geometry(geometry);
            else owner.inspect(binding);
            return 5;
        }
        catch(const std::exception& e){std::cout<<e.what()<<'\n';}
        return 0;
    }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 6;}
}
