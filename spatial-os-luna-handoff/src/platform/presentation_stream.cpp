#include "platform/presentation_stream.hpp"
#include "core/protocol.hpp"
#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <deque>
#include <fcntl.h>
#include <stdexcept>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <utility>
namespace spatial {
namespace {
using Clock=std::chrono::steady_clock;
void require(bool okay,const char* message) { if(!okay)throw std::runtime_error(message); }
constexpr auto timeout=std::chrono::seconds(5);
}
PresentationStream::File::~File(){if(value>=0)close(value);}
PresentationStream::File::File(File&& other) noexcept:value(other.release()){}
PresentationStream::File& PresentationStream::File::operator=(File&& other) noexcept {
    if(this!=&other){if(value>=0)close(value);value=other.release();}return *this;
}
int PresentationStream::File::release(){return std::exchange(value,-1);}
struct PresentationStream::Impl {
    File socket;
    pid_t sender;
    Frame input;
    std::size_t expected=4,sent=0,queued=0;
    Clock::time_point input_deadline;
    struct Output {Frame frame;Clock::time_point deadline;};
    std::deque<Output> output;
    void invalidate() { socket=File{};input=Frame{};output.clear();sent=queued=0; }
    Impl(int fd,pid_t creator,pid_t sender):socket(fd),sender(sender) {
        require(fd>=3 && creator>1 && sender>1,"Invalid presentation channel identity");
        sockaddr_un address{};socklen_t length=sizeof(address);
        int type=0;socklen_t type_length=sizeof(type);ucred peer{};socklen_t peer_length=sizeof(peer);
        require(getpeername(fd,reinterpret_cast<sockaddr*>(&address),&length)==0 && address.sun_family==AF_UNIX &&
            getsockopt(fd,SOL_SOCKET,SO_TYPE,&type,&type_length)==0 && type==SOCK_STREAM &&
            getsockopt(fd,SOL_SOCKET,SO_PEERCRED,&peer,&peer_length)==0 && peer_length==sizeof(peer) &&
            peer.pid==creator && peer.uid==geteuid(),"Presentation socket creator mismatch");
        int enabled=1;const int flags=fcntl(fd,F_GETFL);
        require(flags>=0 && fcntl(fd,F_SETFL,flags|O_NONBLOCK)==0 && fcntl(fd,F_SETFD,FD_CLOEXEC)==0 &&
            setsockopt(fd,SOL_SOCKET,SO_PASSCRED,&enabled,sizeof(enabled))==0,"Cannot configure presentation channel");
    }
};
PresentationStream::PresentationStream(int fd,pid_t creator,pid_t sender) {
    File owned(fd);
    // Allocate before releasing descriptor ownership into the constructor.
    impl_.reset(new Impl(owned.release(),creator,sender));
}
PresentationStream::~PresentationStream()=default;
int PresentationStream::descriptor() const{return impl_->socket.value;}
bool PresentationStream::pending_output() const{return !impl_->output.empty();}
void PresentationStream::send(std::string bytes,const std::vector<int>& descriptors) {
    auto& state=*impl_;
    require(bytes.size()<=max_frame_bytes && descriptors.size()<=2 && state.output.size()<8 &&
        state.queued+bytes.size()+4<=2*max_frame_bytes,"Presentation output limit exceeded");
    Frame frame;const auto size=static_cast<std::uint32_t>(bytes.size());frame.bytes.resize(4);
    for(unsigned i=0;i<4;++i)frame.bytes[i]=static_cast<char>(size>>(24-8*i));
    frame.bytes+=bytes;
    for(int fd:descriptors){
        require(fd>=3,"Invalid presentation transfer descriptor");
        File copy(fcntl(fd,F_DUPFD_CLOEXEC,3));require(copy.value>=3,"Cannot retain presentation descriptor");
        frame.descriptors.push_back(std::move(copy));
    }
    const auto count=frame.bytes.size();
    state.output.push_back({std::move(frame),Clock::now()+timeout});state.queued+=count;
}
void PresentationStream::flush() try {
    auto& state=*impl_;std::size_t budget=65536;
    while(!state.output.empty() && budget){
        auto& front=state.output.front();
        require(Clock::now()<front.deadline,"Presentation output deadline exceeded");
        iovec data{front.frame.bytes.data()+state.sent,std::min(budget,front.frame.bytes.size()-state.sent)};
        alignas(cmsghdr) std::array<char,CMSG_SPACE(sizeof(int)*2)> ancillary{};
        msghdr message{};message.msg_iov=&data;message.msg_iovlen=1;
        if(!front.frame.descriptors.empty()){
            message.msg_control=ancillary.data();message.msg_controllen=CMSG_SPACE(sizeof(int)*front.frame.descriptors.size());
            auto* header=CMSG_FIRSTHDR(&message);header->cmsg_level=SOL_SOCKET;header->cmsg_type=SCM_RIGHTS;
            header->cmsg_len=CMSG_LEN(sizeof(int)*front.frame.descriptors.size());
            for(std::size_t i=0;i<front.frame.descriptors.size();++i)
                std::memcpy(CMSG_DATA(header)+i*sizeof(int),&front.frame.descriptors[i].value,sizeof(int));
        }
        const auto count=sendmsg(state.socket.value,&message,MSG_NOSIGNAL|MSG_DONTWAIT);
        if(count<0 && errno==EINTR)continue;
        if(count<0 && (errno==EAGAIN || errno==EWOULDBLOCK))return;
        require(count>0,"Presentation output closed");
        front.frame.descriptors.clear(); // The kernel retained them with the first written byte.
        state.sent+=static_cast<std::size_t>(count);budget-=static_cast<std::size_t>(count);
        if(state.sent==front.frame.bytes.size()){state.queued-=front.frame.bytes.size();state.output.pop_front();state.sent=0;}
    }
}
catch (...) { impl_->invalidate();throw; }
std::optional<PresentationStream::Frame> PresentationStream::receive() try {
    auto& state=*impl_;
    for(unsigned pass=0;pass<16;++pass){
        require(state.input.bytes.empty() || Clock::now()<state.input_deadline,"Presentation input deadline exceeded");
        if(state.input.bytes.size()==state.expected){
            if(state.expected==4){
                std::uint32_t size=0;for(unsigned i=0;i<4;++i)size=(size<<8)|static_cast<unsigned char>(state.input.bytes[i]);
                require(size<=max_frame_bytes,"Excessive presentation frame");state.expected=4+size;
            }
            if(state.input.bytes.size()==state.expected){
                Frame result=std::move(state.input);result.bytes.erase(0,4);state.input=Frame{};state.expected=4;return result;
            }
        }
        std::array<char,4096> bytes{};iovec data{bytes.data(),state.input.bytes.empty()?1:std::min(bytes.size(),state.expected-state.input.bytes.size())};
        alignas(cmsghdr) std::array<char,CMSG_SPACE(sizeof(int)*16)+CMSG_SPACE(sizeof(ucred))> ancillary{};
        msghdr message{};message.msg_iov=&data;message.msg_iovlen=1;message.msg_control=ancillary.data();message.msg_controllen=ancillary.size();
        const auto count=recvmsg(state.socket.value,&message,MSG_DONTWAIT|MSG_CMSG_CLOEXEC);
        if(count<0 && errno==EINTR)continue;
        if(count<0 && (errno==EAGAIN || errno==EWOULDBLOCK))return {};
        std::vector<File> received;unsigned credentials=0;ucred peer{};bool unexpected=false;
        for(auto* header=CMSG_FIRSTHDR(&message);header;header=CMSG_NXTHDR(&message,header)){
            if(header->cmsg_len<CMSG_LEN(0)){unexpected=true;continue;}
            if(header->cmsg_level==SOL_SOCKET && header->cmsg_type==SCM_RIGHTS){
                const auto size=header->cmsg_len-CMSG_LEN(0);if(size%sizeof(int))unexpected=true;
                for(std::size_t offset=0;offset+sizeof(int)<=size;offset+=sizeof(int)){
                    int fd;std::memcpy(&fd,CMSG_DATA(header)+offset,sizeof(fd));received.push_back(File(fd));
                }
            }else if(header->cmsg_level==SOL_SOCKET && header->cmsg_type==SCM_CREDENTIALS && header->cmsg_len==CMSG_LEN(sizeof(ucred))){
                ++credentials;std::memcpy(&peer,CMSG_DATA(header),sizeof(peer));
            }else unexpected=true;
        }
        require(count>0,"Presentation input closed or truncated");
        require(!(message.msg_flags&(MSG_TRUNC|MSG_CTRUNC)) && !unexpected && credentials==1 &&
            peer.pid==state.sender && peer.uid==geteuid(),"Presentation message sender mismatch");
        require(received.empty() || (state.input.bytes.empty() && state.input.descriptors.empty() && received.size()<=2),
            "Unexpected presentation descriptors");
        if(state.input.bytes.empty())state.input_deadline=Clock::now()+timeout;
        if(!received.empty())state.input.descriptors=std::move(received);
        state.input.bytes.append(bytes.data(),static_cast<std::size_t>(count));
    }
    return {};
}
catch (...) { impl_->invalidate();throw; }
}
