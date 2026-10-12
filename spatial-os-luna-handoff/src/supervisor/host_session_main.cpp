#include "supervisor/session_coordinator.hpp"
#include "supervisor/host_actor_controller.hpp"
#include "world/actor_session_client.hpp"
#include "host_session.pb.h"
#include "actor_session_client.pb.h"
#include "identity/tls_support.hpp"
#include "core/protocol.hpp"
#include <google/protobuf/text_format.h>
#include <array>
#include <algorithm>
#include <cstddef>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <filesystem>
#include <fcntl.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>
#include <iostream>
#include <limits>
#include <map>
#include <set>
#include <stdexcept>

namespace {
using namespace spatial;
using namespace spatial::supervisor;
volatile sig_atomic_t stopped = 0;
void signal_stop(int) { stopped = 1; }
void need(bool value, const char* message) { if (!value) throw std::runtime_error(message); }

using Deadline = std::chrono::steady_clock::time_point;
constexpr auto exchange_limit = std::chrono::seconds(10);
bool read_exact(int fd, void* output, std::size_t size, Deadline& deadline, bool idle = false) {
    auto* bytes = static_cast<char*>(output);
    std::size_t received = 0;
    while (size) {
        pollfd p{fd, POLLIN, 0};
        const auto remaining = deadline-std::chrono::steady_clock::now();
        const bool waiting_for_first_byte = idle && received == 0;
        const auto left = std::chrono::duration_cast<std::chrono::milliseconds>(remaining).count();
        need(waiting_for_first_byte || remaining > std::chrono::steady_clock::duration::zero(),"Control exchange deadline expired");
        const int ready = poll(&p, 1, waiting_for_first_byte ? -1 : static_cast<int>(std::max<std::int64_t>(1,left)));
        if (ready < 0 && errno == EINTR) { if (stopped) return false; continue; }
        if (stopped) return false;
        need(ready > 0 && !(p.revents & (POLLERR | POLLNVAL)), "Control channel read timed out or failed");
        const auto n = recv(fd, bytes, size, 0);
        if (n == 0) { if (idle && received == 0) return false; throw std::runtime_error("EOF during control frame"); }
        if (n < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) continue;
        need(n > 0, "Control channel read failed");
        if (idle && received == 0) deadline = std::chrono::steady_clock::now()+exchange_limit;
        bytes += n; size -= static_cast<std::size_t>(n); received += static_cast<std::size_t>(n);
    }
    return true;
}
void write_exact(int fd, const void* input, std::size_t size, Deadline deadline) {
    const auto* bytes = static_cast<const char*>(input);
    while (size) {
        pollfd p{fd, POLLOUT, 0};
        int ready;
        const auto remaining=deadline-std::chrono::steady_clock::now();
        const auto left=std::chrono::duration_cast<std::chrono::milliseconds>(remaining).count();
        need(remaining>std::chrono::steady_clock::duration::zero(),"Control exchange deadline expired");
        ready = poll(&p, 1, static_cast<int>(std::max<std::int64_t>(1,left)));
        if (ready < 0 && errno == EINTR) { need(!stopped,"Control write interrupted by shutdown"); continue; }
        need(ready > 0 && !(p.revents & (POLLERR | POLLNVAL | POLLHUP)), "Control channel write timed out or failed");
        const auto n = send(fd, bytes, size, MSG_NOSIGNAL);
        if (n < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) continue;
        need(n > 0, "Control channel write failed");
        bytes += n; size -= static_cast<std::size_t>(n);
    }
}
bool read_request(int fd, spatial::supervisor::v1::HostControlRequest& request, Deadline& deadline) {
    std::array<unsigned char,4> head{};
    if (!read_exact(fd, head.data(), 1, deadline, true)) return false;
    need(read_exact(fd, head.data()+1, head.size()-1, deadline),"Truncated control header");
    const std::uint32_t size = (std::uint32_t(head[0])<<24) | (std::uint32_t(head[1])<<16) | (std::uint32_t(head[2])<<8) | head[3];
    need(size > 0 && size <= spatial::max_frame_bytes, "Invalid control frame length");
    std::string bytes(size, '\0');
    need(read_exact(fd, bytes.data(), bytes.size(), deadline), "Truncated control frame");
    need(request.ParseFromString(bytes), "Malformed control protobuf");
    spatial::validate_message(request);
    return true;
}
void write_reply(int fd, const spatial::supervisor::v1::HostControlReply& reply, Deadline deadline) {
    auto bytes = reply.SerializeAsString();
    need(bytes.size() <= spatial::max_frame_bytes, "Control response exceeds frame limit");
    const auto n = static_cast<std::uint32_t>(bytes.size());
    const std::array<unsigned char,4> h{static_cast<unsigned char>(n>>24),static_cast<unsigned char>(n>>16),static_cast<unsigned char>(n>>8),static_cast<unsigned char>(n)};
    write_exact(fd,h.data(),h.size(),deadline); write_exact(fd,bytes.data(),bytes.size(),deadline);
}
std::string config_contents(int fd) {
    struct stat st{}; need(fstat(fd,&st)==0 && S_ISREG(st.st_mode) && st.st_size > 0 && st.st_size <= 1024*1024,"Invalid sealed host configuration descriptor");
#ifdef F_GET_SEALS
    const int seals=fcntl(fd,F_GET_SEALS);
    need(seals>=0 && (seals&(F_SEAL_WRITE|F_SEAL_GROW|F_SEAL_SHRINK|F_SEAL_SEAL))==(F_SEAL_WRITE|F_SEAL_GROW|F_SEAL_SHRINK|F_SEAL_SEAL),"Host configuration descriptor is not sealed");
#else
    need(false,"Sealed host configuration descriptors are unsupported");
#endif
    std::string data(static_cast<std::size_t>(st.st_size),'\0');
    need(pread(fd,data.data(),data.size(),0)==static_cast<ssize_t>(data.size()),"Cannot read host configuration");
    return data;
}
void check_peer(int fd, pid_t expected) {
    sockaddr_un address{}, local{}; socklen_t alen=sizeof(address), llen=sizeof(local); int type=0; socklen_t tlen=sizeof(type); ucred peer{}; socklen_t plen=sizeof(peer);
    need(getpeername(fd,reinterpret_cast<sockaddr*>(&address),&alen)==0 && address.sun_family==AF_UNIX &&
         getsockname(fd,reinterpret_cast<sockaddr*>(&local),&llen)==0 && local.sun_family==AF_UNIX &&
         alen==offsetof(sockaddr_un,sun_path) && llen==offsetof(sockaddr_un,sun_path) &&
         getsockopt(fd,SOL_SOCKET,SO_TYPE,&type,&tlen)==0 && type==SOCK_STREAM &&
         getsockopt(fd,SOL_SOCKET,SO_PEERCRED,&peer,&plen)==0 && plen==sizeof(peer) && peer.pid==expected && peer.uid==geteuid(),
         "Control channel must be an unnamed private stream with the expected kernel peer");
}
void check_descriptor_aliases(int config_fd,int control_fd,const spatial::supervisor::v1::HostSessionConfig& c) {
    std::vector<int> descriptors{config_fd,control_fd,static_cast<int>(c.broker_native_fd()),static_cast<int>(c.compositor_native_fd())};
    for (const auto fd:c.broker().inherited_source_fds()) descriptors.push_back(static_cast<int>(fd));
    for (const auto& world:c.worlds()) {
        descriptors.push_back(static_cast<int>(world.native_fd()));
        descriptors.push_back(static_cast<int>(world.actor_listener_fd()));
        for (const auto fd:world.supervisor().inherited_source_fds()) descriptors.push_back(static_cast<int>(fd));
    }
    std::vector<struct stat> identities;
    for (const int fd:descriptors) {
        struct stat current{};
        need(fd>=3 && fstat(fd,&current)==0,"Missing inherited startup descriptor");
        for (const auto& previous:identities)
            need(previous.st_dev!=current.st_dev || previous.st_ino!=current.st_ino,
                 "Aliased startup, configuration, or control descriptor");
        identities.push_back(current);
    }
}
void validate_config(const spatial::supervisor::v1::HostSessionConfig& c) {
    spatial::validate_message(c);
    need(c.protocol_version()==1 && c.has_broker() && c.worlds_size()>=1 && c.worlds_size()<=16,"Unsupported or incomplete host configuration");
    std::set<std::uint32_t> descriptors{c.broker_native_fd(),c.compositor_native_fd()};
    need(c.broker_native_fd()>=3 && c.compositor_native_fd()>=3 && descriptors.size()==2,"Invalid or repeated host native descriptor");
    need(std::filesystem::path(c.supervisor_executable()).is_absolute() && std::filesystem::path(c.compositor_executable()).is_absolute() &&
         std::filesystem::path(c.compositor_state_path()).is_absolute() && std::filesystem::path(c.broker_config_path()).is_absolute(),"Host executable and state paths must be absolute");
    std::set<std::string> worlds;
    const auto validate_actor_profile=[](const std::string& path,const std::string& world) {
        spatial::identity::v1::ActorSessionClientTlsConfig tls;
        need(google::protobuf::TextFormat::ParseFromString(spatial::identity::tls_detail::load_config(path),&tls),"Malformed trusted actor TLS profile");
        spatial::validate_message(tls);
        need(tls.protocol_version()==1 && tls.expected_world_id()==world,"Actor TLS profile does not match its configured world");
    };
    for (const auto& w:c.worlds()) {
        need(!w.world_id().empty() && w.world_id().size()<=256 && worlds.insert(w.world_id()).second && w.has_supervisor() &&
             std::filesystem::path(w.private_config_path()).is_absolute() && std::filesystem::path(w.actor_tls_config()).is_absolute() && w.native_fd()>=3 && w.actor_listener_fd()>=3,
             "Invalid world registration configuration");
        need(descriptors.insert(w.native_fd()).second && descriptors.insert(w.actor_listener_fd()).second,"Repeated inherited world descriptor");
        validate_actor_profile(w.actor_tls_config(),w.world_id());
    }
    need(worlds.contains(c.initial_world_id()) && std::filesystem::path(c.initial_actor_tls_config()).is_absolute(),"Initial actor world is not configured");
    validate_actor_profile(c.initial_actor_tls_config(),c.initial_world_id());
}
}

int main(int argc,char** argv) {
    std::unique_ptr<SessionCoordinator> session;
    std::unique_ptr<HostActorController> controller;
    int control=-1;
    bool operation_ok=false, cleanup_ok=true;
    bool service_start_attempted=false, presentation_start_attempted=false, presentation_started=false;
    try {
        need(argc==4,"Usage: spatial-host-session CONFIG_FD CONTROL_FD PARENT_PID");
        const int config_fd=std::stoi(argv[1]); control=std::stoi(argv[2]); const pid_t parent=std::stoi(argv[3]);
        need(config_fd>=3 && control>=3 && config_fd!=control && parent>0,"Invalid inherited descriptors");
        check_peer(control,parent);
        need(std::signal(SIGPIPE,SIG_IGN)!=SIG_ERR,"Cannot disable SIGPIPE");
        struct sigaction sa{}; sa.sa_handler=signal_stop; sigemptyset(&sa.sa_mask);
        need(sigaction(SIGTERM,&sa,nullptr)==0 && sigaction(SIGINT,&sa,nullptr)==0,"Cannot install bounded shutdown handlers");
        spatial::supervisor::v1::HostSessionConfig config;
        need(google::protobuf::TextFormat::ParseFromString(config_contents(config_fd),&config),"Malformed host textproto configuration"); validate_config(config);
        check_descriptor_aliases(config_fd,control,config);
        const int flags=fcntl(control,F_GETFL);
        need(flags>=0 && fcntl(control,F_SETFL,flags|O_NONBLOCK)==0,"Cannot set nonblocking control transport");
        service_start_attempted=true;
        session=std::make_unique<SessionCoordinator>(config.supervisor_executable(),config.broker_config_path(),config.broker(),config.broker_native_fd());
        presentation_start_attempted=true;
        session->start_presentation(config.compositor_executable(),config.compositor_state_path(),config.compositor_native_fd());
        presentation_started=true;
        std::map<std::string,spatial::workspace::v1::ControlOwner> by_world;
        for(const auto& world:config.worlds()) {
            auto owner=session->start_world(world.private_config_path(),world.supervisor(),world.native_fd(),world.actor_listener_fd(),0,world.world_id());
            by_world.emplace(world.world_id(),owner);
        }
        const auto selected=session->select_world(by_world.at(config.initial_world_id()));
        need(selected.stage()==spatial::render::v1::READY && selected.selected() &&
             selected.binding().world_id()==config.initial_world_id(),"Initial presentation selection was not confirmed");
        auto client=std::make_unique<spatial::ActorSessionClient>(config.initial_actor_tls_config());
        need(client->expected_world()==config.initial_world_id(),"Initial actor TLS profile names another world");
        client->authenticate();
        controller=std::make_unique<HostActorController>(*session,config.initial_world_id(),std::move(client));
        for(const auto& world:config.worlds()) {
            const auto id=world.world_id(); const auto path=world.actor_tls_config();
            controller->register_world(id,by_world.at(id),[path]{ return std::make_unique<spatial::ActorSessionClient>(path); });
        }
        std::uint64_t last_id=0;
        while(!stopped) {
            spatial::supervisor::v1::HostControlRequest request;
            Deadline deadline;
            if(!read_request(control,request,deadline)) break;
            spatial::supervisor::v1::HostControlReply reply; reply.set_protocol_version(1); reply.set_request_id(request.request_id()); reply.set_generation(controller->generation());
            if(request.protocol_version()!=1 || request.request_id()==0 || request.request_id()<=last_id || request.operation_case()==spatial::supervisor::v1::HostControlRequest::OPERATION_NOT_SET) { reply.set_detail("Invalid version, request ID, or operation"); }
            else {
                last_id=request.request_id();
                try {
                    if(request.has_inspect()) { auto result=controller->inspect(request.generation()); *reply.mutable_inspection()=result; reply.set_accepted(true); }
                    else if(request.has_action()) { auto result=controller->action(request.generation(),request.action()); *reply.mutable_action()=result; reply.set_accepted(true); }
                    else {
                        need(request.generation()==controller->generation(),"Stale host actor generation");
                        auto result=controller->transition(request.transition()); *reply.mutable_transition()=result;
                        reply.set_generation(controller->generation()); reply.set_accepted(result.actor_control_available()); reply.set_detail(result.detail());
                    }
                } catch(const std::exception& e) { reply.set_detail(e.what()); }
            }
            write_reply(control,reply,deadline);
        }
        operation_ok=true;
    } catch(const std::exception& error) { std::cerr<<"host session failed: "<<error.what()<<'\n'; }
    controller.reset(); // Release actor TLS channels before owned-process cleanup.
    if(service_start_attempted && !session) {
        cleanup_ok=false; std::cerr<<"broker construction cleanup remains uncertain\n";
    }
    if(presentation_start_attempted && !presentation_started) {
        cleanup_ok=false; std::cerr<<"presentation construction cleanup remains uncertain\n";
    }
    if(session) {
        try {
            for(const auto& error:session->stop_retained_worlds()) {
                cleanup_ok=false; std::cerr<<"retained world cleanup uncertain: "<<error<<'\n';
            }
        } catch(const std::exception& error) { cleanup_ok=false; std::cerr<<"retained world cleanup uncertain: "<<error.what()<<'\n'; }
        try {
            const auto broker=session->stop();
            need(broker.stage()==spatial::supervisor::v1::OWNED_SCOPES_STOPPED && broker.has_process_stopped() && broker.has_native_stopped(),
                 "Broker cleanup lacks checked receipts");
        } catch(const std::exception& error) { cleanup_ok=false; std::cerr<<"broker cleanup uncertain: "<<error.what()<<'\n'; }
        if(presentation_started) {
            try { session->stop_presentation(); }
            catch(const std::exception& error) { cleanup_ok=false; std::cerr<<"presentation cleanup uncertain: "<<error.what()<<'\n'; }
        }
    }
    if(control>=0) {
        spatial::supervisor::v1::HostControlReply reply; reply.set_protocol_version(1); reply.set_request_id(std::numeric_limits<std::uint64_t>::max()); reply.set_cleanup_complete(cleanup_ok);
        try { write_reply(control,reply,std::chrono::steady_clock::now()+exchange_limit); } catch(...) {}
        close(control);
    }
    return operation_ok && cleanup_ok?0:1;
}
