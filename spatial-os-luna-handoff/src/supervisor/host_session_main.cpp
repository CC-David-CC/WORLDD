#include "supervisor/session_coordinator.hpp"
#include "supervisor/host_actor_controller.hpp"
#include "supervisor/host_actor_worker.hpp"
#include "platform/presentation_stream.hpp"
#include "control/native_capture_config.hpp"
#include "world/actor_session_client.hpp"
#include "host_session.pb.h"
#include "actor_session_client.pb.h"
#include "identity/tls_support.hpp"
#include "core/protocol.hpp"
#include <google/protobuf/text_format.h>
#include <array>
#include <algorithm>
#include <cstddef>
#include <climits>
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
    for (const auto fd:c.trusted_capture_fds()) descriptors.push_back(static_cast<int>(fd));
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
control::v1::ControlProfiles host_controls(const std::string& configured) {
    const std::filesystem::path path(configured);
    need(path.is_absolute() && !path.filename().empty() && std::filesystem::canonical(path.parent_path()) == path.parent_path(),
         "Control preference location must be absolute and canonical");
    struct stat parent{};
    need(lstat(path.parent_path().c_str(), &parent) == 0 && S_ISDIR(parent.st_mode) && parent.st_uid == geteuid() &&
         (parent.st_mode & 0777) == 0700, "Control preference directory must be private and owned");
    struct stat file{};
    if (lstat(path.c_str(), &file) == 0) return control::load_profiles(path);
    need(errno == ENOENT, "Cannot inspect control preferences");
    return control::default_profiles();
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
        need(w.initial_workspaces().empty()==!w.has_expected_initial_world_revision() && w.initial_workspaces_size()<=64,
            "Initial workspaces require an explicit expected world revision");
        if(w.has_expected_initial_world_revision())need(w.expected_initial_world_revision()>0,"Invalid initial workspace world revision");
        std::set<std::string> initial;
        for(const auto& workspace:w.initial_workspaces())need(!workspace.empty() && workspace.size()<=256 &&
            workspace.find('\0')==std::string::npos && initial.insert(workspace).second,"Invalid initial workspace registration");
    }
    need(c.remote_worlds_size()<=16,"Too many remote world registrations");
    for(const auto& remote:c.remote_worlds()) {
        need(!remote.world_id().empty() && remote.world_id().size()<=256 && worlds.insert(remote.world_id()).second &&
            std::filesystem::path(remote.actor_tls_config()).is_absolute(),"Invalid remote world registration");
        validate_actor_profile(remote.actor_tls_config(),remote.world_id());
    }
    need(worlds.contains(c.initial_world_id()) && std::filesystem::path(c.initial_actor_tls_config()).is_absolute(),"Initial actor world is not configured");
    validate_actor_profile(c.initial_actor_tls_config(),c.initial_world_id());
    if (c.shared_actor_controls()) (void)host_controls(c.controls_preferences_path());
    else need(c.controls_preferences_path().empty(), "Control preferences require shared actor controls");
    need(!c.enable_presentation_continuations() || c.shared_actor_controls(),
        "Presentation continuations require shared actor controls");
    need((!c.has_native_capture() && c.trusted_capture_fds().empty()) || c.shared_actor_controls(),
        "Capture requires shared actor controls");
    need(!c.has_native_capture() || c.trusted_capture_fds().empty(),"Native and inherited capture sources are mutually exclusive");
    need(c.trusted_capture_fds_size()<=64,"Too many inherited capture endpoints");
    if(c.has_native_capture())control::validate_native_capture(c.native_capture());
    for(const auto fd:c.trusted_capture_fds())need(fd>=3 && fd<=INT_MAX,"Invalid inherited capture descriptor");
}
}

int main(int argc,char** argv) {
    std::unique_ptr<SessionCoordinator> session;
    std::unique_ptr<HostActorController> controller;
    std::unique_ptr<HostActorWorker> actor_worker;
    std::vector<PresentationStream::File> capture_pool;
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
        for(const auto fd:config.trusted_capture_fds())control::DeviceStream::validate_endpoint(static_cast<int>(fd),parent);
        for(const auto fd:config.trusted_capture_fds()) {
            capture_pool.emplace_back(static_cast<int>(fd));
            const int descriptor_flags=fcntl(static_cast<int>(fd),F_GETFD);
            need(descriptor_flags>=0 && fcntl(static_cast<int>(fd),F_SETFD,descriptor_flags|FD_CLOEXEC)==0,
                "Cannot isolate inherited capture endpoint from child processes");
        }
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
            if(!world.initial_workspaces().empty()) {
                spatial::core::v1::Request request;request.set_protocol_version(1);request.mutable_world_info();
                const auto info=session->world_call(owner,request);
                need(info.has_world_info() && info.world_info().world_id()==world.world_id() &&
                    info.world_info().world_revision()==world.expected_initial_world_revision(),
                    "Initial workspace setup world revision changed");
                for(const auto& workspace:world.initial_workspaces()) {
                    request.clear_world_info();auto* available=request.mutable_workspace_available();
                    available->set_id(workspace);available->set_available(true);
                    const auto checked=session->world_call(owner,request);
                    if(checked.has_error())throw spatial::ActorSessionRejected(checked.error());
                    need(checked.has_workspace_ack() && checked.workspace_ack().accepted(),"Initial workspace availability was not confirmed");
                }
            }
        }
        if(by_world.contains(config.initial_world_id())) {
            const auto retained=session->inspect_presentation(by_world.at(config.initial_world_id()));
            need(retained.stage()==spatial::render::v1::READY &&
                 retained.binding().world_id()==config.initial_world_id(),"Initial retained presentation was not ready");
            // A local producer is not selected before actor observation authority.
            // The authenticated geometry target owns foreground publication.
        }
        auto client=std::make_unique<spatial::ActorSessionClient>(config.initial_actor_tls_config());
        need(client->expected_world()==config.initial_world_id(),"Initial actor TLS profile names another world");
        client->authenticate();
        controller=std::make_unique<HostActorController>(*session,config.initial_world_id(),std::move(client));
        if (config.shared_actor_controls()) {
            const auto path = config.controls_preferences_path();
            controller->start_controls(host_controls(path), [path](const auto& profiles) { control::save_profiles(path, profiles); });
        }
        for(const auto& world:config.worlds()) {
            const auto id=world.world_id(); const auto path=world.actor_tls_config();
            controller->register_world(id,by_world.at(id),[path]{ return std::make_unique<spatial::ActorSessionClient>(path); });
        }
        for(const auto& world:config.remote_worlds()) {
            const auto id=world.world_id(); const auto path=world.actor_tls_config();
            controller->register_remote_world(id,[path]{ return std::make_unique<spatial::ActorSessionClient>(path); });
        }
        if(config.enable_presentation_continuations())controller->enable_presentation_continuations();
        controller->start_geometry_presentation();
        controller->synchronize_controls_presentation();
        if(config.shared_actor_controls()) {
            HostActorWorker::CaptureStart start;
            HostActorWorker::CaptureStop stop;
            if(config.has_native_capture()) {
                start=[&session,native=config.native_capture()](const auto& binding) {
                    int pair[2];need(socketpair(AF_UNIX,SOCK_SEQPACKET|SOCK_CLOEXEC,0,pair)==0,"Cannot create private native capture pair");
                    PresentationStream::File consumer(pair[0]),producer(pair[1]);
                    auto stream=std::make_unique<control::DeviceStream>(consumer.release(),getpid(),binding->capture_generation,
                        control::DeviceStream::Role::Controller);
                    session->start_capture(binding->capture_generation,native,producer.value);
                    return HostActorWorker::CaptureEndpoint{std::move(stream),0};
                };
                stop=[&session](const auto& binding){session->stop_capture(binding->capture_generation);};
            } else if(!capture_pool.empty()) {
                start=[&capture_pool,parent,index=std::size_t{0}](const auto& binding) mutable {
                    need(index<capture_pool.size(),"Trusted capture endpoint pool exhausted");
                    const auto slot=++index;
                    auto stream=std::make_unique<control::DeviceStream>(capture_pool[slot-1].release(),parent,
                        binding->capture_generation,control::DeviceStream::Role::Controller);
                    return HostActorWorker::CaptureEndpoint{std::move(stream),static_cast<std::uint32_t>(slot)};
                };
                stop=[](const auto&){}; // No physical grab; closing the exact receiver retires this slot.
            }
            actor_worker=std::make_unique<HostActorWorker>(*controller,std::move(start),std::move(stop));
        }
        std::uint64_t last_id=0;
        while(!stopped) {
            need(!actor_worker || !actor_worker->failed(),"Actor worker terminated unexpectedly");
            pollfd foreground{control,POLLIN,0};
            const int foreground_ready=poll(&foreground,1,100);
            if(foreground_ready<0 && errno==EINTR)continue;
            need(foreground_ready>=0,"Host foreground poll failed");
            if(foreground_ready==0) {
                if(!actor_worker)controller->pump_geometry_presentation();
                continue;
            }
            spatial::supervisor::v1::HostControlRequest request;
            Deadline deadline;
            if(!read_request(control,request,deadline)) break;
            auto dispatch=[&] {
                spatial::supervisor::v1::HostControlReply reply; reply.set_protocol_version(1); reply.set_request_id(request.request_id()); reply.set_generation(controller->generation());
                if(request.protocol_version()!=1 || request.request_id()==0 || request.request_id()<=last_id || request.operation_case()==spatial::supervisor::v1::HostControlRequest::OPERATION_NOT_SET) { reply.set_detail("Invalid version, request ID, or operation"); }
                else {
                    last_id=request.request_id();
                    try {
                        if(request.has_inspect()) { auto result=controller->inspect(request.generation()); *reply.mutable_inspection()=result; reply.set_accepted(true); }
                        else if(request.has_geometry_status()) { *reply.mutable_geometry_status()=controller->geometry_status(request.generation()); reply.set_accepted(true); }
                        else if(request.has_action()) { auto result=controller->action(request.generation(),request.action()); *reply.mutable_action()=result; reply.set_accepted(true); }
                        else if(request.has_propose()) { *reply.mutable_proposed()=controller->propose(request.generation(),request.propose()); reply.set_accepted(true); }
                        else if(request.has_controls_status()) { *reply.mutable_controls()=actor_worker ? actor_worker->control_status(request.generation()) : controller->control_status(request.generation()); reply.set_accepted(true); }
                        else if(request.has_captured_device()) {
                            if(actor_worker) {
                                const auto binding=actor_worker->capture_binding();
                                need(binding && binding->actor_generation==request.generation(),"Stale human capture actor generation");
                                *reply.mutable_controls()=actor_worker->device_event(binding,request.captured_device());
                            } else *reply.mutable_controls()=controller->device_event(request.generation(),request.captured_device());
                            reply.set_accepted(true);
                        }
                        else if(request.has_controls_menu()) { *reply.mutable_menu()=controller->controls_menu(request.generation()); reply.set_accepted(true); }
                        else if(request.has_controls_tick()) { *reply.mutable_controls()=controller->tick_controls(request.generation()); reply.set_accepted(true); }
                        else if(request.has_transition() || request.has_transition_proposal()) {
                            need(request.generation()==controller->generation(),"Stale host actor generation");
                            auto result=request.has_transition_proposal() ?
                                (actor_worker ? actor_worker->transition_proposed(request.generation(),request.transition_proposal()) :
                                                controller->transition_proposed(request.generation(),request.transition_proposal())) :
                                                                          controller->transition(request.transition());
                            *reply.mutable_transition()=result;
                            reply.set_generation(controller->generation()); reply.set_accepted(result.actor_control_available()); reply.set_detail(result.detail());
                        } else throw std::invalid_argument("Unsupported host control operation");
                    } catch(const std::exception& e) { reply.set_detail(e.what()); }
                }
                try {controller->synchronize_controls_presentation();}
                catch(const std::exception& error){reply.set_accepted(false);reply.set_detail(error.what());}
                return reply;
            };
            const bool cancellation=(request.has_propose() && request.propose().has_stop()) || request.has_transition_proposal();
            auto urgency=actor_worker ? actor_worker->capture_binding() : HostActorWorker::Binding{};
            if(!urgency || urgency->actor_generation!=request.generation() ||
                (!request.has_captured_device() && !cancellation) ||
                (request.has_captured_device() && urgency->capture_generation!=request.captured_device().capture_generation()))
                urgency.reset();
            auto reply=actor_worker ? actor_worker->call(dispatch,urgency,cancellation) : dispatch();
            write_reply(control,reply,deadline);
        }
        operation_ok=true;
    } catch(const std::exception& error) { std::cerr<<"host session failed: "<<error.what()<<'\n'; }
    if(actor_worker && !actor_worker->shutdown()) {cleanup_ok=false;std::cerr<<"Native capture cleanup remains unconfirmed\n";}
    actor_worker.reset(); // Stop exchanges before returning coordinator ownership to this thread.
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
