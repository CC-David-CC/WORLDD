// Test-only linker wrappers exercise OpenSSL's documented retry outcomes.
// TLS authentication and recovery reads still use real OpenSSL over a socket pair.
#include "world/actor_tls.hpp"
#include <openssl/err.h>
#include <chrono>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <sys/socket.h>

namespace {
int injected_error = 0;
SSL* pending_error = nullptr;
unsigned injections = 0;
void check(bool valid, const char* message) { if (!valid) throw std::runtime_error(message); }
using Clock = std::chrono::steady_clock;
using namespace std::chrono_literals;
}
extern "C" int __real_SSL_read_ex(SSL*, void*, size_t, size_t*);
extern "C" int __real_SSL_get_error(const SSL*, int);
extern "C" int __wrap_SSL_read_ex(SSL* ssl, void* buffer, size_t size, size_t* count) {
    if (injected_error && SSL_is_server(ssl)) {
        pending_error=ssl; ++injections; *count=0; return 0;
    }
    return __real_SSL_read_ex(ssl, buffer, size, count);
}
extern "C" int __wrap_SSL_get_error(const SSL* ssl, int result) {
    if (pending_error==ssl && result==0) { pending_error=nullptr; return injected_error; }
    return __real_SSL_get_error(ssl,result);
}
int main(int argc, char** argv) {
    try {
        check(argc==3,"Expected certificate directory and case");
        const std::filesystem::path root=argv[1]; const std::string mode=argv[2];
        auto server=spatial::identity::tls_detail::make_context(true,root/"server.crt",root/"server.key",root/"client.crt");
        auto context=spatial::identity::tls_detail::make_context(false,root/"client.crt",root/"client.key",root/"server.crt");
        int sockets[2];check(socketpair(AF_UNIX,SOCK_STREAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0,sockets)==0,"socketpair");
        spatial::identity::tls_detail::Fd server_fd(sockets[0]),client_fd(sockets[1]);
        spatial::ActorTlsPeer peer(server_fd.value,server.get(),80ms,2000ms);
        std::unique_ptr<SSL,decltype(&SSL_free)> client(SSL_new(context.get()),SSL_free);
        check(client && SSL_set_fd(client.get(),client_fd.value)==1 && SSL_set1_host(client.get(),"server.test")==1,"client TLS setup");
        bool connected=false;const auto deadline=Clock::now()+3s;
        while ((!connected || !peer.ready()) && Clock::now()<deadline) {
            if (!connected) {
                ERR_clear_error();const int result=SSL_connect(client.get());
                if(result==1)connected=true;
                else {const auto error=SSL_get_error(client.get(),result);check(error==SSL_ERROR_WANT_READ || error==SSL_ERROR_WANT_WRITE,"client handshake");}
            }
            check(peer.handshake()>=0,"server handshake");std::this_thread::sleep_for(1ms);
        }
        check(connected && peer.ready(),"real TLS handshake deadline");
        std::string input;
        if(mode=="authenticated-idle") {
            check(peer.read(input)==0,"idle read");std::this_thread::sleep_for(120ms);
            check(!peer.expired(true),"Authenticated idle actor expired");
        } else {
            size_t sent=0;check(SSL_write_ex(client.get(),"X",1,&sent)==1 && sent==1,"real TLS record write");
            injected_error=SSL_ERROR_WANT_WRITE;
            check(peer.read(input)==0 && peer.read_needs_write() && injections==1,"retry was not injected");
            if(mode=="retry-recovers") {
                injected_error=0;check(peer.read(input)==1 && input=="X","real TLS read did not recover");
                std::this_thread::sleep_for(120ms);check(!peer.expired(true),"Completed retry retained deadline");
            } else if(mode=="retry-wants-read") {
                injected_error=SSL_ERROR_WANT_READ;check(peer.read(input)==0 && !peer.read_needs_write(),"read retry transition");
                std::this_thread::sleep_for(120ms);check(peer.expired(true),"Read retry transition lost deadline");
            } else if(mode=="retry-progress") {
                std::this_thread::sleep_for(50ms);check(peer.read(input)==0 && injections==2,"second retry not exercised");
                std::this_thread::sleep_for(50ms);check(peer.expired(true),"Retry progress reset deadline");
            } else {
                check(mode=="retry-stalls","Unknown case");std::this_thread::sleep_for(120ms);
                check(peer.expired(true),"Stalled read-to-write retry has no deadline");
            }
        }
        std::cout<<mode<<" PASS injections="<<injections<<'\n';return 0;
    } catch(const std::exception& error) {std::cerr<<"FAIL "<<error.what()<<'\n';return 1;}
}
