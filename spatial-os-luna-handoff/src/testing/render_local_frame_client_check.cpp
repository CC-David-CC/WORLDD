#include "platform/render_worker.hpp"
#include "platform/presentation_stream.hpp"
#include "presentation.pb.h"
#include "world/store.hpp"
#include <chrono>
#include <exception>
#include <fcntl.h>
#include <iostream>
#include <poll.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <thread>
#include <unistd.h>

using namespace spatial;
namespace {
void check(bool okay, const char* message) { if (!okay) throw std::runtime_error(message); }
}
int main() {
    try {
        char name[]="/tmp/spatial-local-frame-client-XXXXXX";
        const auto directory=mkdtemp(name);check(directory,"Cannot create private client fixture directory");
        struct Remove {std::filesystem::path path;~Remove(){std::filesystem::remove_all(path);}} remove{directory};
        int sockets[2];check(socketpair(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0,sockets)==0,"Cannot create fixture channel");
        PresentationStream::File producer(sockets[0]), peer_fd(sockets[1]);
        PresentationStream::File process(static_cast<int>(syscall(SYS_pidfd_open,getpid(),0)));
        PresentationStream::File native(open("/dev/null",O_RDONLY|O_CLOEXEC));
        std::exception_ptr peer_error;
        std::jthread peer([fd=peer_fd.release(),&peer_error](std::stop_token stop) {
            try {
                PresentationStream stream(fd,getpid(),getpid());
                const auto receive=[&] {
                    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(5);
                    while(!stop.stop_requested() && std::chrono::steady_clock::now()<deadline) {
                        stream.flush();
                        if(auto packet=stream.receive())return std::move(*packet);
                        pollfd waiter{stream.descriptor(),POLLIN,0};poll(&waiter,1,10);
                    }
                    throw std::runtime_error("Fixture peer deadline or cancellation");
                };
                auto hello=receive();render::v1::PresentationWorldRequest request;
                check(request.ParseFromString(hello.bytes) && request.protocol_version()==1 && request.request_id()==1 &&
                    request.has_hello() && hello.descriptors.size()==1,"Unexpected shared producer hello");
                core::v1::RenderResult reply;reply.set_protocol_version(1);reply.set_request_id(1);reply.mutable_ready();
                stream.send(reply.SerializeAsString());
                auto next=receive();request.Clear();
                check(next.descriptors.empty() && request.ParseFromString(next.bytes) && request.request_id()==3 &&
                    request.has_command() && request.command().has_sync(),
                    "Denied preparation was transmitted or subsequent sequence was not retained");
                reply.Clear();reply.set_protocol_version(1);reply.set_request_id(3);reply.mutable_snapshot();
                stream.send(reply.SerializeAsString());stream.flush();
            }catch(...){peer_error=std::current_exception();}
        });
        {
            RenderWorker worker(directory,":99",producer.value,process.value,getpid(),getpid(),native.value);
            core::v1::ActorLocalViewIdentity identity;
            identity.set_protocol_version(1);identity.set_world_id("world");identity.set_actor_id("human");
            identity.set_view_id(std::string(32,'v'));identity.set_authority_service_incarnation(std::string(32,'i'));
            identity.set_authority_generation(1);
            core::v1::RenderScene scene;
            scene.mutable_camera()->mutable_position();scene.mutable_camera()->mutable_orientation()->set_w(1);
            auto* config=scene.mutable_config();config->set_vertical_fov(1.5);config->set_near_clip(.01);config->set_far_clip(1000);
            config->mutable_background();config->mutable_barrier_color();
            bool rejected=false;
            try {
                worker.prepare_local_frame(identity,scene,64,64,[] {
                    throw WorldError(core::v1::FORBIDDEN,"Actor observation was revoked before send");
                });
            }catch(const WorldError& error){rejected=error.code==core::v1::FORBIDDEN;}
            check(rejected,"Pre-send authorization denial was not returned");
            worker.check_alive();
            (void)worker.synchronize();
        }
        peer.join();if(peer_error)std::rethrow_exception(peer_error);
        std::cout<<"PASS definite-unsent-authorization-keeps-shared-producer\n";
        return 0;
    }catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}
}
