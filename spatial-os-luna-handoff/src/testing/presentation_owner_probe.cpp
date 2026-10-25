#include "platform/presentation_owner.hpp"
#include <iostream>
#include <chrono>
#include <poll.h>
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
        if(mode.starts_with("local_")){
            close(pair[0]);close(pair[1]);
            spatial::render::v1::LocalActorPresentationTarget target;*target.mutable_binding()=binding;
            auto* view=target.mutable_identity();view->set_protocol_version(1);view->set_world_id(binding.world_id());
            view->set_actor_id("human");view->set_view_id(std::string(32,'v'));
            view->set_authority_service_incarnation(binding.service_incarnation());view->set_authority_generation(1);
            if(mode.starts_with("local_retire_")) {
                using Clock=std::chrono::steady_clock;owner.select_local_actor(target);
                auto wait=[&](auto poll,std::uint64_t token) {
                    const auto deadline=Clock::now()+std::chrono::seconds(3);unsigned ticks=0;
                    while(Clock::now()<deadline) {
                        const auto before=Clock::now();const bool done=poll(token);
                        if(Clock::now()-before>std::chrono::milliseconds(25))throw std::runtime_error("Retirement poll blocked");
                        ++ticks;if(done)return ticks;::poll(nullptr,0,1);
                    }
                    throw std::runtime_error("Retirement polling did not finish");
                };
                bool rejected=false;
                try {
                    const auto token=owner.begin_retire_local_actor(target);
                    if(!owner.pending())return 30;
                    bool busy=false,stale=false;
                    try{owner.inspect(binding);}catch(const std::exception& error){busy=std::string(error.what())=="Host presentation exchange already in progress";}
                    try{owner.poll_retire_local_actor(token+1);}catch(const std::exception& error){stale=std::string(error.what())=="Stale local actor retirement operation";}
                    if(!busy || !stale)return 31;
                    const auto ticks=wait([&](auto id){return owner.poll_retire_local_actor(id).has_value();},token);
                    if(ticks<50 || owner.pending())return 32;
                    spatial::control::v1::ControlMenuView menu;menu.set_protocol_version(1);
                    auto output=owner.begin_present_controls(7,menu);
                    wait([&](auto id){return owner.poll_present_controls(id);},output);
                    auto capture=owner.begin_stop_capture(7);
                    wait([&](auto id){return owner.poll_stop_capture(id);},capture);
                    if(mode!="local_retire_success")return 33;
                    owner.shutdown();std::cout<<"retirement continuations confirmed\n";
                }catch(const std::exception& error){rejected=true;std::cout<<error.what()<<'\n';}
                if(mode=="local_retire_success" && rejected)return 34;
                if(mode!="local_retire_success" && !rejected)return 35;
                if(mode=="local_retire_denied")owner.shutdown();
                try{owner.inspect(binding);return 36;}
                catch(const std::exception& error){std::cout<<error.what()<<'\n';}
                return 0;
            }
            if(mode.starts_with("local_async_")) {
                using Clock=std::chrono::steady_clock;
                const auto token=owner.begin_select_local_actor(target);
                bool busy=false,stale=false;
                try{owner.inspect(binding);}catch(const std::exception& error){busy=std::string(error.what())=="Host presentation exchange already in progress";}
                try{owner.poll_select_local_actor(token+1);}catch(const std::exception& error){stale=std::string(error.what())=="Stale local actor selection operation";}
                if(!busy || !stale)return 20;
                const auto started=Clock::now();unsigned ticks=0;bool rejected=false,done=false;
                while(Clock::now()-started<std::chrono::seconds(3)) {
                    const auto before=Clock::now();
                    try{done=owner.poll_select_local_actor(token).has_value();}
                    catch(const std::exception& error){std::cout<<error.what()<<'\n';rejected=true;break;}
                    if(Clock::now()-before>std::chrono::milliseconds(25))return 21;
                    ++ticks;if(done)break;::poll(nullptr,0,1);
                }
                if(ticks<50)return 22;
                if(mode=="local_async_success" || mode=="local_async_denied") {
                    if((mode=="local_async_success" && !done) || (mode=="local_async_denied" && !rejected))return 23;
                    owner.retire_local_actor(target);owner.shutdown();
                }else if(!rejected)return 24;
                std::cout<<"nonblocking polls confirmed "<<ticks<<'\n';
                try{owner.inspect(binding);return 25;}
                catch(const std::exception& error){std::cout<<error.what()<<'\n';}
                return 0;
            }
            bool rejected=false;
            try{
                owner.select_local_actor(target);owner.retire_local_actor(target);
                if(mode!="local_success")return 10;
                owner.shutdown();std::cout<<"local view sequence confirmed\n";
            }catch(const std::exception& e){rejected=true;std::cout<<e.what()<<'\n';}
            if(mode=="local_success" && rejected)return 11;
            try{owner.inspect(binding);return 12;}
            catch(const std::exception& e){std::cout<<e.what()<<'\n';}
            return 0;
        }
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
