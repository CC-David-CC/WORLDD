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
void exercise(const std::string& kind) {
        char name[]="/tmp/spatial-local-frame-client-XXXXXX";
        const auto directory=mkdtemp(name);check(directory,"Cannot create private client fixture directory");
        struct Remove {std::filesystem::path path;~Remove(){std::filesystem::remove_all(path);}} remove{directory};
        int sockets[2];check(socketpair(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0,sockets)==0,"Cannot create fixture channel");
        PresentationStream::File producer(sockets[0]), peer_fd(sockets[1]);
        PresentationStream::File process(static_cast<int>(syscall(SYS_pidfd_open,getpid(),0)));
        PresentationStream::File native(open("/dev/null",O_RDONLY|O_CLOEXEC));
        std::exception_ptr peer_error;
        std::jthread peer([fd=peer_fd.release(),&peer_error,&kind](std::stop_token stop) {
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
                if(kind=="viewport" || kind=="invalid-viewport") {
                    auto* viewport=reply.mutable_ready()->mutable_viewport();
                    viewport->set_width(kind=="viewport"?128:0);viewport->set_height(72);
                }
                stream.send(reply.SerializeAsString());
                stream.flush();
                if(kind=="invalid-viewport")return;
                auto next=receive();request.Clear();
                check(next.descriptors.empty() && request.ParseFromString(next.bytes) && request.has_command(),
                    "Unexpected frame command");
                if(kind=="denial") {
                    check(request.request_id()==3 && request.command().has_sync(),
                        "Denied preparation was transmitted or subsequent sequence was not retained");
                    reply.Clear();reply.set_protocol_version(1);reply.set_request_id(3);reply.mutable_snapshot();
                }else{
                    check(request.request_id()==2 && request.command().prepare_local_frame(),"Missing preparation request");
                    reply.Clear();reply.set_protocol_version(1);reply.set_request_id(2);
                    auto* frame=reply.mutable_frame();frame->set_frame_id(1);frame->set_xid(77);
                    frame->set_presentation_generation(kind=="missing-generation"?0:37);
                    stream.send(reply.SerializeAsString());stream.flush();
                    if(kind=="missing-generation")return;
                    auto committed=receive();request.Clear();
                    check(committed.descriptors.empty() && request.ParseFromString(committed.bytes) &&
                        request.request_id()==3 && request.command().has_commit_local_frame() &&
                        request.command().commit_local_frame().frame_id()==1,"Unexpected commitment request");
                    reply.set_request_id(3);
                    if(kind=="changed-generation")frame->set_presentation_generation(38);
                    if(kind=="changed-frame")frame->set_frame_id(2);
                }
                stream.send(reply.SerializeAsString());stream.flush();
            }catch(...){peer_error=std::current_exception();}
        });
        {
            std::unique_ptr<RenderWorker> retained;
            bool invalid_viewport=false;
            try {retained=std::make_unique<RenderWorker>(directory,":99",producer.value,process.value,getpid(),getpid(),native.value);}
            catch(const std::invalid_argument& error) {
                check(kind=="invalid-viewport" && std::string(error.what())=="Invalid or excessive frame dimensions",
                    "Unexpected readiness rejection");
                invalid_viewport=true;
            }
            check(invalid_viewport==(kind=="invalid-viewport"),"Incorrect readiness viewport outcome");
            if(invalid_viewport) {
                peer.join();if(peer_error)std::rethrow_exception(peer_error);
                return;
            }
            check(bool(retained),"Valid readiness did not produce a retained worker");
            auto& worker=*retained;
            if(kind=="viewport") {
                const auto viewport=worker.native_viewport();
                check(viewport.width()==128 && viewport.height()==72,"Native viewport was not preserved");
            }else{
                bool missing=false;
                try{(void)worker.native_viewport();}
                catch(const WorldError& error){missing=error.code==core::v1::UNAVAILABLE;}
                check(missing,"Missing native viewport was guessed or accepted");
            }
            const bool exact=kind=="exact" || kind=="viewport";
            core::v1::ActorLocalViewIdentity identity;
            identity.set_protocol_version(1);identity.set_world_id("world");identity.set_actor_id("human");
            identity.set_view_id(std::string(32,'v'));identity.set_authority_service_incarnation(std::string(32,'i'));
            identity.set_authority_generation(1);
            core::v1::RenderScene scene;
            scene.mutable_camera()->mutable_position();scene.mutable_camera()->mutable_orientation()->set_w(1);
            auto* config=scene.mutable_config();config->set_vertical_fov(1.5);config->set_near_clip(.01);config->set_far_clip(1000);
            config->mutable_background();config->mutable_barrier_color();
            if(kind=="denial") {
                bool rejected=false;
                try {
                    worker.prepare_local_frame(identity,scene,64,64,[] {
                        throw WorldError(core::v1::FORBIDDEN,"Actor observation was revoked before send");
                    });
                }catch(const WorldError& error){rejected=error.code==core::v1::FORBIDDEN;}
                check(rejected,"Pre-send authorization denial was not returned");
                worker.check_alive();(void)worker.synchronize();
            }else{
                if(kind=="missing-generation") {
                    bool rejected=false;
                    try{(void)worker.prepare_local_frame(identity,scene,64,64);}
                    catch(const std::runtime_error& error){rejected=std::string(error.what())=="Prepared compositor frame identity mismatch";}
                    check(rejected,"Missing producer generation was accepted");
                }else{
                    const auto prepared=worker.prepare_local_frame(identity,scene,64,64);
                    check(prepared.presentation_generation()==37,"Producer generation was not preserved");
                    bool rejected=false;
                    try {
                        const auto committed=worker.commit_local_frame(identity,prepared.frame_id());
                        check(exact && committed.SerializeAsString()==prepared.SerializeAsString(),
                            "Changed commitment receipt was accepted");
                    }catch(const std::runtime_error& error){
                        rejected=std::string(error.what())=="Committed compositor frame identity mismatch";
                        check(!exact && rejected,"Unexpected commitment error");
                    }
                    check(rejected==!exact,"Incorrect commitment outcome");
                    bool stale=false;
                    try{(void)worker.commit_local_frame(identity,prepared.frame_id());}
                    catch(const WorldError& error){stale=error.code==core::v1::STALE_TARGET;}
                    check(stale,"Consumed commitment remained replayable");
                }
            }
        }
        peer.join();if(peer_error)std::rethrow_exception(peer_error);
}
int main() {
    try {
        for(const std::string kind:{"denial","exact","missing-generation","changed-generation","changed-frame","viewport","invalid-viewport"}) {
            exercise(kind);
            std::cout<<"PASS "<<kind<<'\n';
        }
        return 0;
    }catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}
}
