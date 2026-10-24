#include "control/device_stream.hpp"
#include <array>
#include <filesystem>
#include <fcntl.h>
#include <iostream>
#include <stdexcept>
#include <sys/socket.h>
#include <unistd.h>
namespace c=spatial::control;
namespace p=spatial::control::v1;
namespace {
void check(bool b,const char* s){if(!b)throw std::runtime_error(s);}
template<class F>void rejects(F f){bool rejected=false;try{f();}catch(const std::exception&){rejected=true;}check(rejected,"Invalid capture stream accepted");}
struct Pair {
    std::array<int,2> sockets{-1,-1};
    Pair(){check(socketpair(AF_UNIX,SOCK_SEQPACKET|SOCK_CLOEXEC,0,sockets.data())==0,"socketpair");}
    ~Pair(){for(int fd:sockets)if(fd>=0)close(fd);}
    int take(unsigned i){const int fd=sockets.at(i);sockets.at(i)=-1;return fd;}
};
p::DeviceEvent event(){p::DeviceEvent e;e.set_protocol_version(1);e.set_source_id("keyboard");e.set_source_generation(1);e.set_event_sequence(1);e.mutable_connected()->set_kind(p::KEYBOARD);return e;}
p::CapturedDeviceEvent packet(){p::CapturedDeviceEvent e;e.set_protocol_version(1);e.set_capture_generation(7);e.set_packet_sequence(1);e.set_monotonic_nanoseconds(1000000);*e.mutable_event()=event();return e;}
void raw_send(int fd,const p::CapturedDeviceEvent& p){const auto b=p.SerializeAsString();check(send(fd,b.data(),b.size(),MSG_NOSIGNAL)==static_cast<ssize_t>(b.size()),"raw packet send");}
void private_roundtrip(){Pair p;c::DeviceStream capture(p.take(0),getpid(),7,c::DeviceStream::Role::Capture),controller(p.take(1),getpid(),7,c::DeviceStream::Role::Controller);
    check(!controller.receive(),"Empty capture was not nonblocking");auto e=event();capture.emit(e,c::DeviceAdapters::Clock::time_point(std::chrono::seconds(10)));
    auto value=controller.receive();check(value&&value->capture_generation()==7&&value->packet_sequence()==1&&value->monotonic_nanoseconds()==10000000000&&value->event().SerializeAsString()==e.SerializeAsString(),"Capture changed packet identity or time");
    check((fcntl(controller.descriptor(),F_GETFL)&O_NONBLOCK)&&(fcntl(controller.descriptor(),F_GETFD)&FD_CLOEXEC),"Capture descriptor flags");
}
void invalid_identity_and_sequence(){for(const std::string fault:{"epoch","sequence","timestamp","version","event","unknown"}){
    Pair p;const int raw=p.sockets[0];c::DeviceStream receiver(p.take(1),getpid(),7,c::DeviceStream::Role::Controller);
    auto first=packet();raw_send(raw,first);check(bool(receiver.receive()),"Valid capture setup");auto bad=packet();bad.set_packet_sequence(2);
    if(fault=="epoch")bad.set_capture_generation(8);else if(fault=="sequence")bad.set_packet_sequence(1);else if(fault=="timestamp")bad.set_monotonic_nanoseconds(0);
    else if(fault=="version")bad.set_protocol_version(2);else if(fault=="event")bad.clear_event();else bad.GetReflection()->MutableUnknownFields(&bad)->AddVarint(100,1);
    raw_send(raw,bad);rejects([&]{receiver.receive();});check(receiver.failed(),"Invalid envelope left capture usable");rejects([&]{receiver.receive();});
}}
std::size_t fd_count(){std::size_t count=0;for(const auto& entry:std::filesystem::directory_iterator("/proc/self/fd")){(void)entry;++count;}return count;}
void ancillary_and_oversize(){for(bool ancillary:{false,true}){Pair p;const int raw=p.sockets[0];c::DeviceStream receiver(p.take(1),getpid(),7,c::DeviceStream::Role::Controller);
    const auto before=fd_count();
    if(ancillary){auto bytes=packet().SerializeAsString();iovec vector{bytes.data(),bytes.size()};alignas(cmsghdr)std::array<char,CMSG_SPACE(sizeof(int))> data{};
        msghdr message{};message.msg_iov=&vector;message.msg_iovlen=1;message.msg_control=data.data();message.msg_controllen=data.size();
        auto* header=CMSG_FIRSTHDR(&message);header->cmsg_level=SOL_SOCKET;header->cmsg_type=SCM_RIGHTS;header->cmsg_len=CMSG_LEN(sizeof(int));*reinterpret_cast<int*>(CMSG_DATA(header))=raw;
        check(sendmsg(raw,&message,MSG_NOSIGNAL)>0,"ancillary send");
    }else{std::string bytes(4097,'x');check(send(raw,bytes.data(),bytes.size(),MSG_NOSIGNAL)==static_cast<ssize_t>(bytes.size()),"oversize send");}
    rejects([&]{receiver.receive();});check(receiver.failed()&&fd_count()==before,"Invalid capture leaked a descriptor");
}}
void backpressure_is_terminal(){Pair p;c::DeviceStream sender(p.take(0),getpid(),7,c::DeviceStream::Role::Capture);unsigned count=0;
    for(;count<1024&&!sender.failed();++count){try{sender.emit(event(),c::DeviceAdapters::Clock::now());}catch(const std::exception&) {}}
    check(sender.failed()&&count<1024,"Capture output buffering was unbounded");rejects([&]{sender.emit(event(),c::DeviceAdapters::Clock::now());});
}
void wrong_peer_stream_and_eof(){
    {Pair p;const int fd=p.take(1);rejects([&]{c::DeviceStream s(fd,getpid()+1,7,c::DeviceStream::Role::Controller);});check(fcntl(fd,F_GETFD)==-1,"Constructor leaked refused peer");}
    {std::array<int,2> sockets{};check(socketpair(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0,sockets.data())==0,"stream pair");rejects([&]{c::DeviceStream s(sockets[0],getpid(),7,c::DeviceStream::Role::Controller);});close(sockets[1]);}
    {Pair p;c::DeviceStream receiver(p.take(1),getpid(),7,c::DeviceStream::Role::Controller);close(p.take(0));rejects([&]{receiver.receive();});check(receiver.failed(),"EOF did not fence capture");}
}
}
int main(){try{
    private_roundtrip();std::cout<<"PASS private-capture-roundtrip\n";
    invalid_identity_and_sequence();std::cout<<"PASS capture-epoch-sequence-envelope\n";
    ancillary_and_oversize();std::cout<<"PASS capture-ancillary-size-descriptor-safety\n";
    backpressure_is_terminal();std::cout<<"PASS capture-backpressure-terminal\n";
    wrong_peer_stream_and_eof();std::cout<<"PASS capture-peer-type-eof-refusal\n";
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
