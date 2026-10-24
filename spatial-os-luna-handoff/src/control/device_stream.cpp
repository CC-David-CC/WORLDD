#include "control/device_stream.hpp"
#include "core/protocol.hpp"
#include <array>
#include <cerrno>
#include <cstddef>
#include <fcntl.h>
#include <limits>
#include <stdexcept>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

namespace spatial::control {
namespace {
void need(bool b,const char* s){if(!b)throw std::runtime_error(s);}
constexpr std::size_t maximum_packet=4096;
void private_peer(int fd,pid_t expected){
    sockaddr_un local{},peer{};socklen_t local_size=sizeof(local),peer_size=sizeof(peer);
    int type=0;socklen_t type_size=sizeof(type);ucred credentials{};socklen_t credentials_size=sizeof(credentials);
    need(expected>1&&fd>2&&getsockname(fd,reinterpret_cast<sockaddr*>(&local),&local_size)==0&&
        getpeername(fd,reinterpret_cast<sockaddr*>(&peer),&peer_size)==0&&local.sun_family==AF_UNIX&&peer.sun_family==AF_UNIX&&
        local_size==offsetof(sockaddr_un,sun_path)&&peer_size==offsetof(sockaddr_un,sun_path)&&
        getsockopt(fd,SOL_SOCKET,SO_TYPE,&type,&type_size)==0&&type==SOCK_SEQPACKET&&
        getsockopt(fd,SOL_SOCKET,SO_PEERCRED,&credentials,&credentials_size)==0&&credentials_size==sizeof(credentials)&&
        credentials.pid==expected&&credentials.uid==geteuid(),"Device stream requires its configured private kernel peer");
}
}
DeviceStream::DeviceStream(int fd,pid_t peer,std::uint64_t generation,Role role):descriptor_(fd),generation_(generation),role_(role){
    try{
        need(generation>0,"Missing device capture generation");private_peer(fd,peer);
        const int flags=fcntl(fd,F_GETFL);const int descriptor_flags=fcntl(fd,F_GETFD);
        need(flags>=0&&descriptor_flags>=0&&fcntl(fd,F_SETFL,flags|O_NONBLOCK)==0&&
            fcntl(fd,F_SETFD,descriptor_flags|FD_CLOEXEC)==0,"Cannot configure nonblocking private capture");
        // Bound kernel buffering. A producer cannot queue input indefinitely.
        int bytes=32768;need(setsockopt(fd,SOL_SOCKET,SO_SNDBUF,&bytes,sizeof(bytes))==0,"Cannot bound capture buffering");
    }catch(...){if(fd>2)close(fd);throw;}
}
DeviceStream::~DeviceStream(){fence();close(descriptor_);}
void DeviceStream::fence() noexcept {if(!failed_){failed_=true;(void)shutdown(descriptor_,SHUT_RDWR);}}
void DeviceStream::emit(const v1::DeviceEvent& event,DeviceAdapters::Clock::time_point captured){
    need(!failed_&&role_==Role::Capture,"Capture stream is unavailable or has the wrong role");
    try{
        validate_message(event);
        const auto ticks=std::chrono::duration_cast<std::chrono::nanoseconds>(captured.time_since_epoch()).count();
        need(ticks>0&&sequence_<std::numeric_limits<std::uint64_t>::max(),"Invalid capture timestamp or exhausted sequence");
        v1::CapturedDeviceEvent packet;packet.set_protocol_version(1);packet.set_capture_generation(generation_);
        packet.set_packet_sequence(++sequence_);packet.set_monotonic_nanoseconds(static_cast<std::uint64_t>(ticks));*packet.mutable_event()=event;
        const auto bytes=packet.SerializeAsString();need(bytes.size()<=maximum_packet,"Device capture packet exceeds its bound");
        ssize_t sent;do{sent=send(descriptor_,bytes.data(),bytes.size(),MSG_DONTWAIT|MSG_NOSIGNAL);}while(sent<0&&errno==EINTR);
        need(sent==static_cast<ssize_t>(bytes.size()),"Capture delivery failed or its bounded queue filled");
    }catch(...){fence();throw;}
}
std::optional<v1::CapturedDeviceEvent> DeviceStream::receive(){
    need(!failed_&&role_==Role::Controller,"Controller stream is unavailable or has the wrong role");
    try{
        std::array<char,maximum_packet> bytes{};alignas(cmsghdr) std::array<char,CMSG_SPACE(8*sizeof(int))> control{};
        iovec vector{bytes.data(),bytes.size()};msghdr message{};message.msg_iov=&vector;message.msg_iovlen=1;
        message.msg_control=control.data();message.msg_controllen=control.size();
        ssize_t size;do{size=recvmsg(descriptor_,&message,MSG_DONTWAIT|MSG_CMSG_CLOEXEC);}while(size<0&&errno==EINTR);
        if(size<0&&(errno==EAGAIN||errno==EWOULDBLOCK))return {};
        // Close unexpected transferred descriptors before rejecting ancillary data.
        bool ancillary=false;
        for(auto* c=CMSG_FIRSTHDR(&message);c;c=CMSG_NXTHDR(&message,c)){
            ancillary=true;
            if(c->cmsg_level==SOL_SOCKET&&c->cmsg_type==SCM_RIGHTS&&c->cmsg_len>=CMSG_LEN(0)){
                const auto count=(c->cmsg_len-CMSG_LEN(0))/sizeof(int);const auto* fds=reinterpret_cast<const int*>(CMSG_DATA(c));
                for(std::size_t i=0;i<count;++i)close(fds[i]);
            }
        }
        need(size>0&&!ancillary&&!(message.msg_flags&(MSG_TRUNC|MSG_CTRUNC)),"Device stream closed or received an invalid packet");
        v1::CapturedDeviceEvent packet;need(packet.ParseFromArray(bytes.data(),static_cast<int>(size)),"Malformed device capture protobuf");
        validate_message(packet);
        need(packet.protocol_version()==1&&packet.capture_generation()==generation_&&packet.has_event()&&
            packet.packet_sequence()>sequence_&&packet.monotonic_nanoseconds()>0&&
            packet.monotonic_nanoseconds()<=static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max()),"Stale or mismatched device capture envelope");
        sequence_=packet.packet_sequence();return packet;
    }catch(...){fence();throw;}
}
}
