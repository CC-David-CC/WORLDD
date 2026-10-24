#include <spnav.h>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <future>
#include <iostream>
#include <stdexcept>
#include <string>
#include <fcntl.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

namespace {
bool fail_socket=false,config_exists=false,allow_fixture=false;
std::string fixture_path;
void check(bool value,const char* message){if(!value)throw std::runtime_error(message);}
std::size_t descriptors(){return std::distance(std::filesystem::directory_iterator("/proc/self/fd"),std::filesystem::directory_iterator{});}
struct File {int fd=-1;~File(){if(fd>=0)close(fd);}};
}
extern "C" int __real_socket(int,int,int);
extern "C" int __real_connect(int,const sockaddr*,socklen_t);
extern "C" FILE* __real_fopen(const char*,const char*);
extern "C" int __wrap_socket(int domain,int type,int protocol) {
    if(fail_socket && domain==AF_UNIX){errno=EMFILE;return -1;}
    return __real_socket(domain,type,protocol);
}
extern "C" int __wrap_connect(int fd,const sockaddr* address,socklen_t length) {
    if(allow_fixture && address->sa_family==AF_UNIX &&
       fixture_path==reinterpret_cast<const sockaddr_un*>(address)->sun_path)
        return __real_connect(fd,address,length);
    errno=ENOENT;return -1;
}
extern "C" FILE* __wrap_fopen(const char* path,const char* mode) {
    if(std::strcmp(path,"/etc/spnavrc")!=0)return __real_fopen(path,mode);
    if(!config_exists){errno=ENOENT;return nullptr;}
    FILE* file=std::tmpfile();
    if(file){std::fputs("socket = /missing-spnav-cleanup-fixture.sock\n",file);std::rewind(file);}
    return file;
}
namespace {
void repeated_failure(bool socket_failure,bool configuration) {
    fail_socket=socket_failure;config_exists=configuration;
    const auto before=descriptors();
    for(unsigned i=0;i<32;++i) {
        check(spnav_open()==-1 && spnav_fd()==-1,"Failed native open retained a connection");
        check(spnav_close()==-1,"Failed native open pretended to own a connection");
        check(descriptors()==before,"Failed native open leaked a descriptor or configuration file");
    }
    fail_socket=false;config_exists=false;
}
void success_after_failure() {
    char directory[]="/tmp/spatial-spnav-cleanup-XXXXXX";
    check(mkdtemp(directory)!=nullptr,"Cannot create private daemon fixture directory");
    struct Cleanup {std::string directory,path;~Cleanup(){unlink(path.c_str());rmdir(directory.c_str());}} cleanup{directory,std::string(directory)+"/daemon.sock"};
    fixture_path=cleanup.path;
    const char* previous=std::getenv("SPNAV_SOCKET");const bool had_previous=previous;
    const std::string saved=previous?previous:"";
    struct Environment {bool present;std::string value;~Environment(){if(present)setenv("SPNAV_SOCKET",value.c_str(),1);else unsetenv("SPNAV_SOCKET");}} environment{had_previous,saved};
    check(setenv("SPNAV_SOCKET",fixture_path.c_str(),1)==0,"Cannot select private daemon fixture");
    File listener{socket(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0)};
    sockaddr_un address{};address.sun_family=AF_UNIX;
    check(fixture_path.size()<sizeof(address.sun_path),"Private daemon path too long");
    std::memcpy(address.sun_path,fixture_path.c_str(),fixture_path.size()+1);
    check(listener.fd>=0 && bind(listener.fd,reinterpret_cast<sockaddr*>(&address),sizeof(address))==0 && listen(listener.fd,1)==0,
        "Cannot listen for the private daemon fixture");
    const auto before=descriptors();allow_fixture=true;
    auto server=std::async(std::launch::async,[&] {
        pollfd waiting{listener.fd,POLLIN,0};check(poll(&waiting,1,1500)==1,"Native client never connected");
        File client{accept4(listener.fd,nullptr,nullptr,SOCK_CLOEXEC)};check(client.fd>=0,"Native daemon fixture accept failed");
        int request=0;std::size_t received=0;
        while(received<sizeof(request)) {
            pollfd input{client.fd,POLLIN,0};check(poll(&input,1,1500)==1,"Native handshake did not arrive");
            const auto size=read(client.fd,reinterpret_cast<char*>(&request)+received,sizeof(request)-received);
            check(size>0,"Native handshake closed early");received+=static_cast<std::size_t>(size);
        }
        const int version=1;check(write(client.fd,&version,sizeof(version))==static_cast<ssize_t>(sizeof(version)),"Native fixture handshake reply failed");
        pollfd input{client.fd,POLLIN,0};check(poll(&input,1,1500)==1,"Native client did not close");
        char byte;check(read(client.fd,&byte,1)==0,"Native close left its connection open");
    });
    const int opened=spnav_open();
    const int flags=spnav_fd()>=0?fcntl(spnav_fd(),F_GETFD):-1;
    const bool identity=opened==0 && spnav_protocol()==1 && spnav_fd()>=0 && flags>=0 && (flags&FD_CLOEXEC);
    const int closed=spnav_close();server.get();allow_fixture=false;
    check(identity && closed==0 && spnav_fd()==-1 && descriptors()==before,"Recovery after a failed native open lost cleanup or close-on-exec");
}
}
int main() {
    try {
        repeated_failure(true,false);std::cout<<"PASS spnav-socket-failure-resource-cleanup\n";
        repeated_failure(false,false);std::cout<<"PASS spnav-connection-failure-resource-cleanup\n";
        repeated_failure(false,true);std::cout<<"PASS spnav-config-file-failure-resource-cleanup\n";
        success_after_failure();std::cout<<"PASS spnav-successful-recovery-after-failure\n";
    }catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}
}
