#include "platform/broker.hpp"
#include "world/store.hpp"
#include <array>
#include <cerrno>
#include <fcntl.h>
#include <iostream>
#include <poll.h>
#include <set>
#include <sys/socket.h>
#include <unistd.h>

// Thin test boundary. Every authority decision and broker frame uses BrokerClient.
int main(int argc, char** argv) {
    namespace pb = spatial::broker_wire;
    try {
        if (argc != 3) throw std::invalid_argument("Expected inherited broker descriptor and process identity");
        spatial::BrokerClient client(std::stoi(argv[1]), std::stoi(argv[2]));
        std::set<std::string> revoked_seen;
        const auto events = [&] {
            for (const auto& lease : client.poll_control()) {
                if (client.control_active(lease)) throw std::runtime_error("Revoked lease remains active");
                revoked_seen.insert(lease.id());
                pb::Reply reply; reply.set_protocol_version(1); *reply.mutable_control_revoked() = lease;
                spatial::write_frame(std::cout, reply); std::cout.flush();
            }
        };
        client.set_tick(events);
        if (fcntl(STDIN_FILENO, F_SETFL, fcntl(STDIN_FILENO, F_GETFL) | O_NONBLOCK) < 0)
            throw std::runtime_error("Cannot configure probe input");
        std::string input;
        for (;;) {
            events();
            if (input.size() >= 4) {
                const auto* h = reinterpret_cast<const unsigned char*>(input.data());
                const auto n = (std::uint32_t(h[0])<<24) | (std::uint32_t(h[1])<<16) | (std::uint32_t(h[2])<<8) | h[3];
                if (n > spatial::max_frame_bytes) throw std::runtime_error("Excessive probe frame");
                if (input.size() >= n+4) {
                    pb::Request request; pb::Reply reply;
                    if (!request.ParseFromArray(input.data()+4, static_cast<int>(n))) throw std::runtime_error("Invalid probe request");
                    input.erase(0,n+4); reply.set_protocol_version(1); reply.set_request_id(request.request_id());
                    try {
                        if (request.has_control_acquire()) *reply.mutable_control_lease() = client.control_acquire(request.control_acquire());
                        else if (request.has_control_release()) *reply.mutable_control_released() = client.control_release(request.control_release());
                        else if (request.has_application_access()) *reply.mutable_application_access() = client.application_access(request.application_access());
                        else if (request.has_management()) {
                            *reply.mutable_management() = client.call(request.management());
                            reply.mutable_management()->set_request_id(request.management().request_id());
                        } else if (request.has_grants()) *reply.mutable_grants() = client.grants(request.grants());
                        else if (request.has_delegate()) *reply.mutable_grant() = client.delegate(request.delegate());
                        else if (request.has_revoke()) *reply.mutable_grant() = client.revoke(request.revoke());
                        else if (request.has_bootstrap_bind()) *reply.mutable_binding() = client.bind_bootstrap(request.bootstrap_bind());
                        else if (request.has_bootstrap_revoke()) *reply.mutable_binding() = client.revoke_bootstrap(request.bootstrap_revoke());
                        else if (request.has_bootstrap_apply()) *reply.mutable_bootstrap() = client.apply_bootstrap(request.bootstrap_apply());
                        else if (request.has_launch()) {
                            const auto& r = request.launch();
                            *reply.mutable_launched() = client.launch(r.workspace(),r.actor(),r.command(),r.has_principal()?&r.principal():nullptr);
                        } else throw std::runtime_error("Unsupported probe command");
                    } catch (const spatial::WorldError& error) {
                        reply.mutable_error()->set_code(error.code); reply.mutable_error()->set_message(error.what());
                    }
                    events();
                    if (reply.has_control_lease() && client.control_active(reply.control_lease()) == revoked_seen.contains(reply.control_lease().id()))
                        throw std::runtime_error("Control validity disagrees with received revocation");
                    spatial::write_frame(std::cout,reply); std::cout.flush(); continue;
                }
            }
            pollfd fd{STDIN_FILENO,POLLIN,0};
            if (poll(&fd,1,10) < 0 && errno != EINTR) throw std::runtime_error("Probe poll failed");
            std::array<char,65536> bytes{}; const auto n = read(STDIN_FILENO,bytes.data(),bytes.size());
            if (!n) {
                if (!input.empty()) return 2;
                // Preserve the broker's normal half-close shutdown contract.
                const int broker_fd = std::stoi(argv[1]);
                if (shutdown(broker_fd,SHUT_WR) != 0) throw std::runtime_error("Cannot close probe broker input");
                const auto until = std::chrono::steady_clock::now()+std::chrono::seconds(3);
                while (std::chrono::steady_clock::now() < until) {
                    pollfd upstream{broker_fd,POLLIN,0}; poll(&upstream,1,10);
                    const auto remaining = read(broker_fd,bytes.data(),bytes.size());
                    if (!remaining) return 0;
                    if (remaining < 0 && errno != EINTR && errno != EAGAIN) throw std::runtime_error("Probe broker shutdown failed");
                }
                throw std::runtime_error("Probe broker shutdown deadline exceeded");
            }
            if (n < 0 && errno != EINTR && errno != EAGAIN) throw std::runtime_error("Probe read failed");
            if (n > 0) input.append(bytes.data(),static_cast<std::size_t>(n));
        }
    } catch (const std::exception& error) { std::cerr << "broker client probe: " << error.what() << '\n'; return 2; }
}
