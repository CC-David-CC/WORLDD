#include "identity/native_seat_guardian_service.hpp"
#include <stdexcept>
#include <utility>
#include <sys/socket.h>
#include <unistd.h>
namespace spatial::identity {
namespace {
std::string checked_context(std::string issuer,int world_fd,int issuer_fd,pid_t world_sender,pid_t issuer_sender){
    if(world_fd<3 || issuer_fd<3 || world_fd==issuer_fd || world_sender<=1 || issuer_sender<=1 ||
       world_sender==issuer_sender || world_sender==getpid() || issuer_sender==getpid())
        throw std::invalid_argument("Guardian requires distinct external role channels");
    return issuer;
}
}
NativeSeatGuardianService::NativeSeatGuardianService(NativeSeatGuardian& guardian,std::string issuer,
        std::string incarnation,std::uint64_t generation,int world_fd,pid_t creator,pid_t world_sender,
        int issuer_fd,pid_t issuer_sender)
    :world_fd_(world_fd>=3?world_fd:-1),issuer_fd_(issuer_fd>=3 && issuer_fd!=world_fd?issuer_fd:-1),
     guardian_(guardian),
     dispatch_(guardian,checked_context(std::move(issuer),world_fd,issuer_fd,world_sender,issuer_sender),std::move(incarnation),generation),
     world_(world_fd_.release(),creator,world_sender),issuer_(issuer_fd_.release(),creator,issuer_sender) {}
NativeSeatGuardianService::~NativeSeatGuardianService(){fail();}
void NativeSeatGuardianService::announce(){
    if(failed_ || announced_)throw std::logic_error("Guardian service cannot announce");
    try {
        guardian::v1::GuardianReply ready;ready.set_protocol_version(1);ready.set_ready(true);
        world_.send(ready.SerializeAsString());issuer_.send(ready.SerializeAsString());
        announced_=true;
    } catch (...) {fail();throw;}
}
NativeSeatGuardianService::Progress NativeSeatGuardianService::pump(){
    if(failed_ || !announced_)throw std::logic_error("Guardian service is unavailable");
    try {
    Progress progress=Progress::idle;
    const auto handle=[&](PresentationStream& stream,NativeSeatGuardianDispatch::Role role){
        stream.flush();
        if(stream.pending_output())return;
        auto frame=stream.receive();if(!frame)return;
        if(!frame->descriptors.empty())throw std::invalid_argument("Unexpected guardian channel descriptors");
        guardian::v1::GuardianRequest request;
        if(!request.ParseFromString(frame->bytes))throw std::invalid_argument("Malformed guardian request");
        const auto reply=dispatch_.dispatch(role,request);
        stream.send(reply.SerializeAsString());stream.flush();
        if(role==NativeSeatGuardianDispatch::Role::issuer && reply.has_remaining_fences() && !reply.remaining_fences())
            progress=Progress::fence_complete;
        else if(progress==Progress::idle)progress=Progress::progress;
    };
    handle(world_,NativeSeatGuardianDispatch::Role::world);
    handle(issuer_,NativeSeatGuardianDispatch::Role::issuer);
    return progress;
    } catch (...) {fail();throw;}
}
bool NativeSeatGuardianService::pending_output() const{return world_.pending_output() || issuer_.pending_output();}
void NativeSeatGuardianService::flush(){
    if(failed_ || !announced_)throw std::logic_error("Guardian service is unavailable");
    try {world_.flush();issuer_.flush();} catch (...) {fail();throw;}
}
void NativeSeatGuardianService::fail() noexcept {
    failed_=true;
    guardian_.disconnect();
    if(world_.descriptor()>=0)(void)::shutdown(world_.descriptor(),SHUT_RDWR);
    if(issuer_.descriptor()>=0)(void)::shutdown(issuer_.descriptor(),SHUT_RDWR);
}
int NativeSeatGuardianService::world_descriptor() const{return failed_?-1:world_.descriptor();}
int NativeSeatGuardianService::issuer_descriptor() const{return failed_?-1:issuer_.descriptor();}
}
