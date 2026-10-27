#include "identity/native_seat_guardian_world_service.hpp"
#include <stdexcept>
#include <sys/socket.h>
#include <unistd.h>

namespace spatial::identity {
NativeSeatGuardianWorldService::NativeSeatGuardianWorldService(NativeSeatGuardian& guardian,std::string issuer,
        std::string incarnation,std::uint64_t generation,int fd,pid_t creator,pid_t sender)
    :world_fd_(fd),guardian_(guardian),dispatch_(guardian,std::move(issuer),std::move(incarnation),generation),
     world_(world_fd_.release(),creator,sender) {}
NativeSeatGuardianWorldService::~NativeSeatGuardianWorldService(){fail();}
void NativeSeatGuardianWorldService::announce(){
    if(failed_||announced_)throw std::logic_error("Guardian world service cannot announce");
    try {
        guardian::v1::GuardianReply ready;ready.set_protocol_version(1);ready.set_ready(true);
        world_.send(ready.SerializeAsString());world_.flush();announced_=true;
    } catch (...) {fail();throw;}
}
bool NativeSeatGuardianWorldService::pump(){
    if(failed_||!announced_)throw std::logic_error("Guardian world service is unavailable");
    try {
        world_.flush();
        auto frame=world_.receive();if(!frame)return false;
        if(!frame->descriptors.empty())throw std::invalid_argument("Unexpected guardian world descriptors");
        guardian::v1::GuardianRequest request;
        if(!request.ParseFromString(frame->bytes))throw std::invalid_argument("Malformed guardian world request");
        const auto reply=dispatch_.dispatch(NativeSeatGuardianDispatch::Role::world,request);
        world_.send(reply.SerializeAsString());world_.flush();return true;
    } catch (...) {fail();throw;}
}
void NativeSeatGuardianWorldService::fail() noexcept {
    failed_=true;guardian_.disconnect();
    if(world_.descriptor()>=0)(void)::shutdown(world_.descriptor(),SHUT_RDWR);
}
int NativeSeatGuardianWorldService::descriptor() const{return failed_?-1:world_.descriptor();}
}
