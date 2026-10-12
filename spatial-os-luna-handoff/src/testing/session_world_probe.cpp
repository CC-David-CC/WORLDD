#include "supervisor/session_coordinator.hpp"
#include "platform/broker.hpp"
#include "identity/tls_support.hpp"
#include <google/protobuf/text_format.h>
#include <iostream>
#include <sys/wait.h>
#include <unistd.h>

#ifdef SPATIAL_HOST_ACTOR_NATIVE_RUNNER
#include "host_actor_native_checks.hpp"
#endif

// Transport-only fixture for the production coordinator and real broker/world children.
int main(int argc, char** argv) {
    try {
#ifdef SPATIAL_HOST_ACTOR_NATIVE_RUNNER
        if (argc != 12) throw std::runtime_error("Expected native host controller fixture arguments");
        signal(SIGPIPE, SIG_IGN);
        const unsigned actor_channels = 2;
        const bool two_worlds = true;
#else
        const unsigned actor_channels = 0;
        const bool two_worlds = argc == 11;
        if (argc != 5 && argc != 6 && argc != 8 && argc != 11) throw std::runtime_error("Expected broker and world configs with sealed native descriptors and optional actor listener");
#endif
        const auto binary = std::filesystem::canonical("/proc/self/exe").parent_path();
        const auto load = [](const char* path) {
            spatial::supervisor::v1::AuthoritySupervisorConfig config;
            if (!google::protobuf::TextFormat::ParseFromString(spatial::identity::tls_detail::load_config(path), &config))
                throw std::runtime_error("Malformed session fixture configuration");
            return config;
        };
        auto session=std::make_unique<spatial::supervisor::SessionCoordinator>(binary / "spatial-authority-supervisor",
            std::string(argv[1]) + ".owned", load(argv[1]), std::stoi(argv[2]));
        const auto report = [](const spatial::supervisor::v1::AuthorityLaunchRecord& record) {
            spatial::supervisor::v1::AuthorityLaunchReply reply; reply.set_protocol_version(1);
            *reply.mutable_view()->mutable_record() = record; spatial::write_frame(std::cout, reply);
        };
        if(argc>=8)session->start_presentation(binary/"spatialwm",argv[6],std::stoi(argv[5]));
        if(argc>=8){
            const pid_t child=fork();if(child<0)throw std::runtime_error("Cannot fork owner-copy check");
            if(child==0){session.reset();_exit(0);}
            int status=0;if(waitpid(child,&status,0)!=child || !WIFEXITED(status) || WEXITSTATUS(status)!=0)
                throw std::runtime_error("Forked owner-copy destruction failed");
        }
        report(session->broker_launch());
        spatial::core::v1::Request hello; hello.set_protocol_version(1); hello.set_request_id(44); hello.mutable_hello();
        spatial::workspace::v1::ControlOwner owner;
        try {
            owner = session->start_world(std::string(argv[3]) + ".owned", load(argv[3]), std::stoi(argv[4]),
                argc == 6 ? std::stoi(argv[5]) : -1,actor_channels,argc>=8?argv[7]:"");
            if (argc == 6) close(std::stoi(argv[5]));
        } catch (const std::exception& error) {
            std::cerr << "world start rejected: " << error.what() << '\n';
            spatial::write_frame(std::cout, session->root().call(hello)); report(session->stop()); return 2;
        }
        if(argc>=8){
            const auto selected=session->select_world(owner);
            if(!selected.selected() || selected.stage()!=spatial::render::v1::READY)throw std::runtime_error("Host world selection failed");
        }
        report(session->world_launch(owner));
        spatial::supervisor::v1::AuthorityLaunchReply binding; binding.set_protocol_version(1);
        *binding.mutable_control_owner() = owner; spatial::write_frame(std::cout, binding);
        spatial::core::v1::Request identities; identities.set_protocol_version(1); identities.set_request_id(45); identities.mutable_runtime_identities();
        spatial::write_frame(std::cout, session->world_call(owner, identities));
        spatial::workspace::v1::ControlOwner second;
        if(two_worlds){
            second=session->start_world(std::string(argv[8])+".owned",load(argv[8]),std::stoi(argv[9]),-1,actor_channels,argv[10]);
            const auto b=session->select_world(second);const auto a=session->select_world(owner);
            if(!a.selected() || !b.selected() || session->inspect_presentation(second).selected())
                throw std::runtime_error("Independent world selection failed");
            report(session->world_launch(second));
            spatial::supervisor::v1::AuthorityLaunchReply binding;binding.set_protocol_version(1);
            *binding.mutable_control_owner()=second;spatial::write_frame(std::cout,binding);
            spatial::write_frame(std::cout,session->world_call(second,identities));
        }
#ifdef SPATIAL_HOST_ACTOR_NATIVE_RUNNER
        const auto denied_config = native_setting("SPATIAL_HOST_ACTOR_DENIED_CONFIG");
        const auto denied = session->start_world(std::string(denied_config) + ".owned", load(denied_config),
            std::stoi(argv[11]), -1, 1, native_setting("SPATIAL_HOST_ACTOR_DENIED_WORLD"));
        host_actor_native_checks(*session, owner, second, denied);
#endif
        std::string bytes; spatial::workspace::v1::ControlSupervisorRequest request;
        if (!spatial::read_frame(std::cin, bytes) || !request.ParseFromString(bytes) || (!request.has_inspect() && !request.has_fence()))
            throw std::runtime_error("Expected fixture owner inspection");
        if (request.has_fence()) {
            spatial::workspace::v1::ControlSupervisorReply state; state.set_protocol_version(1); state.set_request_id(request.request_id());
            *state.mutable_state() = session->inspect_world(request.fence()); spatial::write_frame(std::cout, state);
            spatial::supervisor::v1::AuthorityLaunchReply cleanup; cleanup.set_protocol_version(1);
            try { *cleanup.mutable_view()->mutable_record() = session->stop_world(owner); }
            catch (const std::exception& error) { cleanup.mutable_error()->set_message(error.what()); }
            spatial::write_frame(std::cout, cleanup);
            spatial::write_frame(std::cout, session->root().call(hello));
            if (cleanup.has_view()) report(session->stop());
            return 0;
        }
        spatial::workspace::v1::ControlSupervisorReply response;
        response.set_protocol_version(1); response.set_request_id(request.request_id());
        try { *response.mutable_state() = session->inspect_world(request.inspect()); }
        catch (const std::exception& error) {
            response.mutable_error()->set_code(spatial::core::v1::INVALID_ARGUMENT); response.mutable_error()->set_message(error.what());
        }
        spatial::write_frame(std::cout, response);
        spatial::write_frame(std::cout, session->world_call(owner, hello));
        report(session->stop_world(owner));
        if(two_worlds){
            if(!session->select_world(second).selected())throw std::runtime_error("Second world lost presentation after first stop");
            spatial::write_frame(std::cout,session->world_call(second,identities));
            report(session->stop_world(second));
        }
        spatial::write_frame(std::cout, session->root().call(hello));
#ifdef SPATIAL_HOST_ACTOR_NATIVE_RUNNER
        report(session->stop_world(denied));
#endif
        if(argc>=8)session->stop_presentation();
        report(session->stop()); return 0;
    } catch (const std::exception& error) { std::cerr << "session world rejected: " << error.what() << '\n'; return 2; }
}
