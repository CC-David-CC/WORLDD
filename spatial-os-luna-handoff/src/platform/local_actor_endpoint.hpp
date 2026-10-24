#pragma once
#include "identity/tls_support.hpp"
#include "platform/process_handle.hpp"
#include <chrono>
#include <cerrno>
#include <charconv>
#include <climits>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fcntl.h>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <poll.h>
#include <utility>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

namespace spatial::local_actor {
inline void need(bool value,const char* message) {if(!value)throw std::runtime_error(message);}
inline std::uint64_t start_ticks(pid_t pid) {
    std::ifstream input("/proc/"+std::to_string(pid)+"/stat");std::string line;
    need(bool(std::getline(input,line)),"Local actor server process is unavailable");
    const auto end=line.rfind(')');need(end!=std::string::npos,"Invalid local actor server process identity");
    std::istringstream fields(line.substr(end+1));std::string value;
    for(unsigned index=0;index<=19;++index)need(bool(fields>>value),"Incomplete local actor server process identity");
    std::uint64_t result=0;const auto parsed=std::from_chars(value.data(),value.data()+value.size(),result);
    need(parsed.ec==std::errc{} && parsed.ptr==value.data()+value.size() && result>0,"Invalid local actor server start time");
    return result;
}
struct Path {
    std::filesystem::path path;
    identity::tls_detail::Fd parent;
    sockaddr_un address{};
    socklen_t size=0;
    explicit Path(const std::filesystem::path& supplied):path(supplied) {
        const auto name=path.string();
        need(path.is_absolute() && path.lexically_normal()==path && !path.filename().empty() &&
            name.find('\0')==std::string::npos && name.size()<sizeof(address.sun_path),"Invalid local actor socket path");
        const auto directory=path.parent_path();
        need(std::filesystem::canonical(directory)==directory,"Local actor socket directory cannot use symlinks");
        parent.reset(open(directory.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
        struct stat st{};need(parent.value>=3 && fstat(parent.value,&st)==0 && st.st_uid==geteuid() &&
            (st.st_mode&0777)==0700,"Local actor socket requires a private owned directory");
        address.sun_family=AF_UNIX;std::memcpy(address.sun_path,name.c_str(),name.size()+1);
        size=static_cast<socklen_t>(offsetof(sockaddr_un,sun_path)+name.size()+1);
    }
    struct stat entry() const {
        struct stat st{};need(fstatat(parent.value,path.filename().c_str(),&st,AT_SYMLINK_NOFOLLOW)==0,
            "Local actor socket entry is unavailable");
        need(S_ISSOCK(st.st_mode) && st.st_uid==geteuid() && (st.st_mode&0777)==0600,
            "Local actor socket must be an owned private socket");return st;
    }
};
class Listener final {
    Path path_;
    identity::tls_detail::Fd socket_;
    struct stat bound_{};
    bool bound=false;
    void cleanup() noexcept {
        socket_.reset();struct stat current{};
        if(bound && fstatat(path_.parent.value,path_.path.filename().c_str(),&current,AT_SYMLINK_NOFOLLOW)==0 &&
            S_ISSOCK(current.st_mode) && current.st_uid==geteuid() && current.st_dev==bound_.st_dev && current.st_ino==bound_.st_ino)
            (void)unlinkat(path_.parent.value,path_.path.filename().c_str(),0);
    }
public:
    explicit Listener(const std::filesystem::path& path):path_(path) {
        try {
            struct stat exists{};need(fstatat(path_.parent.value,path.filename().c_str(),&exists,AT_SYMLINK_NOFOLLOW)<0 &&
                errno==ENOENT,"Local actor endpoint already exists; checked recovery must select a fresh path");
            socket_.reset(socket(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC|SOCK_NONBLOCK,0));
            need(socket_.value>=3 && bind(socket_.value,reinterpret_cast<const sockaddr*>(&path_.address),path_.size)==0,
                "Cannot bind local actor endpoint");
            need(fstatat(path_.parent.value,path.filename().c_str(),&bound_,AT_SYMLINK_NOFOLLOW)==0 &&
                S_ISSOCK(bound_.st_mode) && bound_.st_uid==geteuid(),"Cannot pin local actor endpoint");bound=true;
            need(fchmodat(path_.parent.value,path.filename().c_str(),0600,0)==0,"Cannot protect local actor endpoint");
            (void)path_.entry();need(listen(socket_.value,16)==0,"Cannot listen on local actor endpoint");
        }catch(...){cleanup();throw;}
    }
    ~Listener(){cleanup();}
    int accept() {
        identity::tls_detail::Fd peer;
        do {peer.reset(accept4(socket_.value,nullptr,nullptr,SOCK_CLOEXEC|SOCK_NONBLOCK));}while(peer.value<0 && errno==EINTR);
        if(peer.value<0) {need(errno==EAGAIN || errno==EWOULDBLOCK,"Local actor accept failed");return -1;}
        ucred credentials{};socklen_t size=sizeof(credentials);
        if(getsockopt(peer.value,SOL_SOCKET,SO_PEERCRED,&credentials,&size)!=0 || size!=sizeof(credentials) ||
            credentials.uid!=geteuid() || credentials.pid<=1)return -1;
        return std::exchange(peer.value,-1);
    }
};
struct Connection {
    int channel_fd=-1;
    ProcessHandle server;
    Connection()=default;
    Connection(const Connection&)=delete;
    Connection(Connection&& other) noexcept:channel_fd(std::exchange(other.channel_fd,-1)),server(std::move(other.server)) {}
    ~Connection(){if(channel_fd>=0)close(channel_fd);}
};
inline Connection connect(const std::filesystem::path& supplied,std::uint64_t pid,std::uint64_t ticks,
        std::chrono::milliseconds timeout) {
    need(pid>1 && pid<=INT_MAX && ticks>0,"Local actor endpoint requires a pinned server identity");
    Path path(supplied);const auto before=path.entry();Connection result;
    result.server=ProcessHandle::take(static_cast<int>(syscall(SYS_pidfd_open,static_cast<pid_t>(pid),0)),static_cast<pid_t>(pid));
    need(start_ticks(static_cast<pid_t>(pid))==ticks,"Local actor server start time changed");
    result.channel_fd=socket(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC|SOCK_NONBLOCK,0);need(result.channel_fd>=3,"Cannot create local actor socket");
    const auto deadline=std::chrono::steady_clock::now()+timeout;
    if(::connect(result.channel_fd,reinterpret_cast<const sockaddr*>(&path.address),path.size)<0) {
        need(errno==EINPROGRESS,"Cannot connect local actor socket");
        for(;;) {
            const auto left=std::chrono::duration_cast<std::chrono::milliseconds>(deadline-std::chrono::steady_clock::now()).count();
            need(left>0 && result.server.live(),"Local actor connection deadline or server lifetime expired");
            pollfd item{result.channel_fd,POLLOUT,0};const int ready=poll(&item,1,static_cast<int>(left));
            if(ready<0 && errno==EINTR)continue;
            need(ready>0 && !(item.revents&(POLLERR|POLLHUP|POLLNVAL)),"Local actor socket connection failed");break;
        }
        int error=0;socklen_t size=sizeof(error);need(getsockopt(result.channel_fd,SOL_SOCKET,SO_ERROR,&error,&size)==0 && !error,
            "Local actor socket connection failed");
    }
    ucred peer{};socklen_t size=sizeof(peer);const auto after=path.entry();
    need(getsockopt(result.channel_fd,SOL_SOCKET,SO_PEERCRED,&peer,&size)==0 && size==sizeof(peer) &&
        peer.uid==geteuid() && peer.pid==static_cast<pid_t>(pid) && result.server.live() && start_ticks(peer.pid)==ticks &&
        before.st_dev==after.st_dev && before.st_ino==after.st_ino,"Local actor endpoint identity changed");
    return result;
}
class RetirementPin final {
    Path path_;
    struct stat pinned_{};
public:
    RetirementPin(const std::filesystem::path& path,const ProcessHandle& server):path_(path),pinned_(path_.entry()) {
        need(server.live(),"Cannot retain a stopped local actor endpoint");
        auto checked=connect(path,server.pid(),start_ticks(server.pid()),std::chrono::seconds(1));
        const auto after=path_.entry();need(server.live() && pinned_.st_dev==after.st_dev && pinned_.st_ino==after.st_ino,
            "Local actor endpoint changed before retention");
    }
    // The coordinator calls this only after validating both owned stop receipts.
    void retire() {
        struct stat current{};
        if(fstatat(path_.parent.value,path_.path.filename().c_str(),&current,AT_SYMLINK_NOFOLLOW)<0) {
            need(errno==ENOENT,"Local actor endpoint retirement could not inspect its entry");return;
        }
        need(S_ISSOCK(current.st_mode) && current.st_uid==geteuid() && current.st_dev==pinned_.st_dev && current.st_ino==pinned_.st_ino,
            "Local actor endpoint was replaced before retirement");
        need(unlinkat(path_.parent.value,path_.path.filename().c_str(),0)==0,"Local actor endpoint retirement failed");
    }
};
}
