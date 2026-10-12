#include "supervisor/session_coordinator.hpp"
#include "platform/broker.hpp"
#include "identity/tls_support.hpp"
#include "session_probe.pb.h"
#include <google/protobuf/text_format.h>
#include <iostream>
#include <sys/socket.h>
#include <cstring>
#include <csignal>
#include <set>

// Forward management to the real coordinator. Application actions use its local actor channel.
int main(int argc, char** argv) {
    try {
        if (std::signal(SIGPIPE, SIG_IGN) == SIG_ERR) throw std::runtime_error("Cannot disable issuer transport SIGPIPE");
        if (argc < 7) throw std::runtime_error("Expected broker/world configuration, native inputs, actor listener, and actor delivery socket");
        std::set<int> replacement_native;
        for (int index = 7; index < argc; ++index) {
            const int fd = std::stoi(argv[index]);
            if (fd < 3 || !replacement_native.insert(fd).second)
                throw std::runtime_error("Invalid replacement fixture descriptor");
        }
        const auto binary = std::filesystem::canonical("/proc/self/exe").parent_path();
        const auto load = [](const char* path) {
            spatial::supervisor::v1::AuthoritySupervisorConfig config;
            if (!google::protobuf::TextFormat::ParseFromString(spatial::identity::tls_detail::load_config(path), &config))
                throw std::runtime_error("Malformed session fixture configuration");
            return config;
        };
        spatial::supervisor::SessionCoordinator coordinator(binary / "spatial-authority-supervisor",
            std::string(argv[1]) + ".owned", load(argv[1]), std::stoi(argv[2]));
        const auto report = [](const spatial::supervisor::v1::AuthorityLaunchRecord& record) {
            spatial::supervisor::v1::AuthorityLaunchReply reply; reply.set_protocol_version(1);
            *reply.mutable_view()->mutable_record() = record; spatial::write_frame(std::cout, reply);
        };
        report(coordinator.broker_launch());
        auto owner = coordinator.start_world(std::string(argv[3]) + ".owned", load(argv[3]),
            std::stoi(argv[4]), std::stoi(argv[5]), 1);
        close(std::stoi(argv[5])); report(coordinator.world_launch(owner));
        spatial::identity::tls_detail::Fd delivery(std::stoi(argv[6]));
        const auto transfer_actor = [&](const spatial::workspace::v1::ControlOwner& target) {
            spatial::identity::tls_detail::Fd actor(coordinator.take_actor_channel(target, 0));
            spatial::supervisor::v1::AuthorityLaunchReply actor_binding; actor_binding.set_protocol_version(1);
            *actor_binding.mutable_control_owner() = target;
            auto actor_packet = actor_binding.SerializeAsString(); iovec data{actor_packet.data(), actor_packet.size()};
            alignas(cmsghdr) char ancillary[CMSG_SPACE(sizeof(int))]{};
            msghdr message{}; message.msg_iov = &data; message.msg_iovlen = 1;
            message.msg_control = ancillary; message.msg_controllen = sizeof(ancillary);
            auto* header = CMSG_FIRSTHDR(&message); header->cmsg_level = SOL_SOCKET;
            header->cmsg_type = SCM_RIGHTS; header->cmsg_len = CMSG_LEN(sizeof(int));
            std::memcpy(CMSG_DATA(header), &actor.value, sizeof(int));
            if (sendmsg(delivery.value, &message, MSG_NOSIGNAL) != static_cast<ssize_t>(actor_packet.size()))
                throw std::runtime_error("Cannot transfer fixture actor endpoint");
        };
        transfer_actor(owner);
        spatial::supervisor::v1::AuthorityLaunchReply binding; binding.set_protocol_version(1);
        *binding.mutable_control_owner() = owner; spatial::write_frame(std::cout, binding);
        spatial::core::v1::Request identities; identities.set_protocol_version(1); identities.set_request_id(45);
        identities.mutable_runtime_identities(); spatial::write_frame(std::cout, coordinator.world_call(owner, identities));
        std::string bytes;
        while (spatial::read_frame(std::cin, bytes)) {
            spatial::testing::v1::SessionProbeRequest request;
            spatial::testing::v1::SessionProbeReply reply; reply.set_protocol_version(1);
            bool stopped = false;
            try {
                if (!request.ParseFromString(bytes)) throw std::runtime_error("Malformed coordinator test request");
                reply.set_request_id(request.request_id()); spatial::validate_message(request);
                if (request.protocol_version() != 1) throw std::runtime_error("Unsupported coordinator test version");
                if (request.has_world()) *reply.mutable_result() = coordinator.world_call(owner, request.world());
                else if (request.has_owned_world())
                    *reply.mutable_result() = coordinator.world_call(request.owned_world().owner(), request.owned_world().request());
                else if (request.has_start_world()) {
                    const auto& start = request.start_world();
                    if (!std::filesystem::path(start.configuration_path()).is_absolute() ||
                        !replacement_native.erase(start.native_fd()))
                        throw std::runtime_error("Unknown replacement fixture configuration");
                    spatial::identity::tls_detail::Fd native(start.native_fd());
                    spatial::identity::tls_detail::Fd listener;
                    if (start.has_actor_listener_fd()) {
                        if (!replacement_native.erase(start.actor_listener_fd()))
                            throw std::runtime_error("Unknown replacement actor listener descriptor");
                        listener.reset(start.actor_listener_fd());
                    }
                    owner = coordinator.start_world(start.configuration_path() + ".owned",
                        load(start.configuration_path().c_str()), native.value, listener.value, 1);
                    transfer_actor(owner);
                    *reply.mutable_started()->mutable_owner() = owner;
                    *reply.mutable_started()->mutable_record() = coordinator.world_launch(owner);
                }
                else if (request.has_broker()) *reply.mutable_result() = coordinator.root().call(request.broker());
                else if (request.has_inspect()) *reply.mutable_owner() = coordinator.inspect_world(request.inspect());
                else if (request.has_stop_world()) *reply.mutable_cleanup() = coordinator.stop_world(request.stop_world());
                else if (request.has_recover_world()) *reply.mutable_owner() = coordinator.recover_world(request.recover_world());
                else if (request.has_retire_world()) *reply.mutable_retirement() = coordinator.retire_world(request.retire_world());
                else if (request.has_stop_broker()) *reply.mutable_cleanup() = coordinator.stop_broker();
                else if (request.has_retire_broker()) *reply.mutable_retirement() = coordinator.retire_broker();
                else if (request.has_recover_stopped_broker_world())
                    *reply.mutable_dependent() = coordinator.recover_world_after_broker_stop(request.recover_stopped_broker_world());
                else if (request.has_stop_session()) { *reply.mutable_cleanup() = coordinator.stop(); stopped = true; }
                else throw std::runtime_error("Missing coordinator test operation");
            } catch (const std::exception& error) {
                reply.mutable_error()->set_code(spatial::core::v1::INVALID_ARGUMENT);
                reply.mutable_error()->set_message(error.what());
            }
            spatial::write_frame(std::cout, reply);
            if (stopped) break;
        }
        for (int fd : replacement_native) close(fd);
        return 0;
    } catch (const std::exception& error) { std::cerr << "session application fixture: " << error.what() << '\n'; return 2; }
}
