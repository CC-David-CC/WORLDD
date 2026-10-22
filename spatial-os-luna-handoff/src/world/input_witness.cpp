#include "world/input_witness.hpp"
#include "world/actors.hpp"
#include "platform/render_worker.hpp"
#include "world_input_witness_v2.pb.h"
#include <array>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <fcntl.h>
#include <iostream>
#include <stdexcept>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
namespace spatial {
namespace {
constexpr std::size_t maximum=4096;
std::uint64_t monotonic_ns() {
    const auto value=std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
    if(value<=0)throw std::runtime_error("Invalid witness monotonic clock");
    return static_cast<std::uint64_t>(value);
}
bool selector(const std::string& value){return !value.empty() && value.size()<=256 && value.find('\0')==std::string::npos;}
}
InputWitness::InputWitness(int fd):fd_(fd),parent_(getppid()) {
    try {
        sockaddr_un address{};socklen_t length=sizeof(address);
        int type=0;socklen_t type_length=sizeof(type);ucred peer{};socklen_t peer_length=sizeof(peer);
        if(fd<3 || parent_<=1 || getpeername(fd,reinterpret_cast<sockaddr*>(&address),&length)!=0 || address.sun_family!=AF_UNIX ||
           getsockopt(fd,SOL_SOCKET,SO_TYPE,&type,&type_length)!=0 || type!=SOCK_SEQPACKET ||
           getsockopt(fd,SOL_SOCKET,SO_PEERCRED,&peer,&peer_length)!=0 || peer_length!=sizeof(peer) ||
           peer.pid!=parent_ || peer.uid!=geteuid() || peer.gid!=getegid())
            throw std::invalid_argument("Input witness requires a parent-created Unix seqpacket endpoint");
        int enabled=1;const int flags=fcntl(fd,F_GETFL),descriptor_flags=fcntl(fd,F_GETFD);
        if(flags<0 || descriptor_flags<0 || fcntl(fd,F_SETFL,flags|O_NONBLOCK)!=0 ||
           fcntl(fd,F_SETFD,descriptor_flags|FD_CLOEXEC)!=0 || setsockopt(fd,SOL_SOCKET,SO_PASSCRED,&enabled,sizeof(enabled))!=0)
            throw std::runtime_error("Cannot secure input witness endpoint");
    }catch(...){close_channel();throw;}
}
InputWitness::~InputWitness(){close_channel();}
void InputWitness::close_channel(){if(fd_>=0){close(fd_);fd_=-1;}}
void InputWitness::pump(WorldStore& world,Actors& actors,RenderWorker* renderer) {
    if(fd_<0)return;
    try {
        std::array<char,maximum> bytes{};
        alignas(cmsghdr) std::array<char,CMSG_SPACE(sizeof(ucred))+CMSG_SPACE(16*sizeof(int))> ancillary{};
        iovec data{bytes.data(),bytes.size()};msghdr message{};
        message.msg_iov=&data;message.msg_iovlen=1;message.msg_control=ancillary.data();message.msg_controllen=ancillary.size();
        const auto count=recvmsg(fd_,&message,MSG_DONTWAIT|MSG_CMSG_CLOEXEC);
        if(count<0 && (errno==EAGAIN || errno==EWOULDBLOCK || errno==EINTR))return;
        bool valid=count>0 && !(message.msg_flags&(MSG_TRUNC|MSG_CTRUNC));unsigned credentials=0;
        for(auto* header=CMSG_FIRSTHDR(&message);header;header=CMSG_NXTHDR(&message,header)){
            if(header->cmsg_level==SOL_SOCKET && header->cmsg_type==SCM_RIGHTS && header->cmsg_len>=CMSG_LEN(0)){
                const auto size=header->cmsg_len-CMSG_LEN(0);
                for(std::size_t i=0;i+sizeof(int)<=size;i+=sizeof(int)){
                    int fd;std::memcpy(&fd,CMSG_DATA(header)+i,sizeof(fd));close(fd);
                }
                valid=false;
            }else if(header->cmsg_level==SOL_SOCKET && header->cmsg_type==SCM_CREDENTIALS && header->cmsg_len==CMSG_LEN(sizeof(ucred))){
                ucred peer{};std::memcpy(&peer,CMSG_DATA(header),sizeof(peer));++credentials;
                valid=valid && peer.pid==parent_ && peer.uid==geteuid() && peer.gid==getegid();
            }else valid=false;
        }
        if(!valid || credentials!=1 || consumed_)throw std::runtime_error("Input witness transport refused");
        consumed_=true;
        testing::v2::WorldInputWitnessRequestV2 request;
        if(!request.ParseFromArray(bytes.data(),static_cast<int>(count)))throw std::runtime_error("Input witness protobuf refused");
        validate_message(request);
        if(request.SerializeAsString()!=std::string(bytes.data(),static_cast<std::size_t>(count)))
            throw std::runtime_error("Input witness noncanonical request refused");
        testing::v2::WorldInputWitnessReplyV2 reply;
        reply.set_protocol_version(2);reply.set_session_id(request.session_id());reply.set_arm_id(request.arm_id());
        reply.set_world_id(world.info().world_id());reply.set_worldd_pid(static_cast<std::uint32_t>(getpid()));
        try {
            const auto now=monotonic_ns();
            if(request.protocol_version()!=2 || request.session_id().size()!=16 || request.arm_id().size()!=16 ||
               !selector(request.old_actor_entity_id()) || !selector(request.peer_actor_entity_id()) ||
               request.old_actor_entity_id()==request.peer_actor_entity_id() || !request.has_expected_application() ||
               !selector(request.expected_application().workspace_id()) || !selector(request.expected_application().application_id()) ||
               request.expected_server_id().size()!=32 || !request.expected_presentation_generation() ||
               request.deadline_monotonic_ns()<=now || request.deadline_monotonic_ns()-now>10000000000ULL)
                throw WorldError(wire::INVALID_ARGUMENT,"Invalid input witness selectors");
            if(!renderer)throw WorldError(wire::UNAVAILABLE,"Input witness compositor is unavailable");
            workspace::v1::ApplicationRef app;app.set_workspace(request.expected_application().workspace_id());
            app.set_application(request.expected_application().application_id());app.set_server_id(request.expected_server_id());
            const auto old=actors.input_witness_binding(request.old_actor_entity_id(),app);
            const auto peer=actors.input_witness_binding(request.peer_actor_entity_id(),app);
            if(!actors.input_witness_current(old) || !actors.input_witness_current(peer) ||
               old.fence.SerializeAsString()!=peer.fence.SerializeAsString() ||
               old.principal.SerializeAsString()==peer.principal.SerializeAsString() || old.actor_instance_id==peer.actor_instance_id ||
               old.native.native_seat_id()==peer.native.native_seat_id() || old.native.server_id()!=peer.native.server_id() ||
               old.native.presentation_generation()!=request.expected_presentation_generation() ||
               peer.native.presentation_generation()!=request.expected_presentation_generation() ||
               monotonic_ns()>=request.deadline_monotonic_ns())
                throw WorldError(wire::STALE_TARGET,"Input witness context changed");
            for(const auto* value:{&old,&peer}){
                auto* output=reply.add_actors();output->set_actor_entity_id(value->actor_entity_id);
                output->set_pointer_device(value->native.pointer_device());output->set_keyboard_device(value->native.keyboard_device());
                auto* binding=output->mutable_binding();binding->set_session_id(request.session_id());binding->set_arm_id(request.arm_id());
                binding->mutable_actor()->set_issuer_id(value->principal.issuer_id());binding->mutable_actor()->set_actor_id(value->principal.actor_id());
                binding->set_world_actor_id(value->actor_instance_id);binding->set_native_seat_id(value->native.native_seat_id());
                binding->set_presentation_generation(value->native.presentation_generation());
                *binding->mutable_application()=request.expected_application();
            }
            reply.set_compositor_pid(static_cast<std::uint32_t>(renderer->pid()));reply.set_native_server_id(old.native.server_id());
        }catch(const WorldError& error){reply.clear_actors();reply.set_error_code(error.code);}
        catch(const std::invalid_argument&){reply.clear_actors();reply.set_error_code(wire::INVALID_ARGUMENT);}
        catch(const std::exception&){reply.clear_actors();reply.set_error_code(wire::UNAVAILABLE);}
        const auto payload=reply.SerializeAsString();
        if(payload.size()>maximum)throw std::runtime_error("Input witness reply bound exceeded");
        ssize_t sent;
        do {sent=send(fd_,payload.data(),payload.size(),MSG_NOSIGNAL|MSG_DONTWAIT);}while(sent<0 && errno==EINTR);
        if(sent!=static_cast<ssize_t>(payload.size()))throw std::runtime_error("Input witness reply refused");
        close_channel();
    }catch(const std::exception& error){std::cerr<<"worldd: "<<error.what()<<'\n';close_channel();}
}
}
