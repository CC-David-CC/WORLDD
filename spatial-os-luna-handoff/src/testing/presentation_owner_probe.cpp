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
        bool rejected=false;
        try{
            owner.attach(binding,pair[0],process,scope,identity);
            if(std::string(argv[4])=="select_stage")owner.select(binding);
        }catch(const std::exception& e){rejected=true;std::cout<<e.what()<<'\n';}
        close(pair[0]);close(pair[1]);
        if(!rejected)return 4;
        try{owner.inspect(binding);return 5;}
        catch(const std::exception& e){std::cout<<e.what()<<'\n';}
        return 0;
    }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 6;}
}
