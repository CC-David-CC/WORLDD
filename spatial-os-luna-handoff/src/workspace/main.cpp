#include "workspace/store.hpp"
#include "workspace/authority.hpp"
#include "workspace/native_launch.hpp"
#include "workspace/control.hpp"
#include "world/store.hpp"
#include "build_id.hpp"
#include <csignal>
#include <iostream>
#include <sys/stat.h>
#include <unistd.h>
#include <array>
#include <cerrno>
#include <fcntl.h>
#include <poll.h>
#include <sstream>

namespace {
spatial::wire::Reply dispatch(const spatial::wire::Request& request, spatial::ExecutionStore& git) {
    namespace wire = spatial::wire;
    wire::Reply reply; reply.set_protocol_version(1); reply.set_request_id(request.request_id());
    try {
        spatial::validate_message(request);
        if (request.protocol_version() != 1) throw spatial::WorldError(wire::UNSUPPORTED_VERSION, "Unsupported broker protocol version");
        switch (request.operation_case()) {
        case wire::Request::kHello: {
            auto* hello = reply.mutable_hello(); hello->set_protocol("spatial-protobuf/1");
            hello->set_implementation("production"); hello->set_build_id(SPATIAL_BUILD_ID);
            hello->add_capabilities("workspace.git-create/v1");
            if (git.fixture_faults()) hello->add_capabilities("fixture.faults/v1");
            break;
        }
        case wire::Request::kRuntimeIdentities: reply.mutable_identities()->set_workspaced_pid(getpid()); break;
        case wire::Request::kFaultArm: *reply.mutable_fault_armed() = git.arm_fault(request.fault_arm()); break;
        case wire::Request::kGitRepoImport: *reply.mutable_repository() = git.import_repository(request.git_repo_import()); break;
        case wire::Request::kGitWorktreeCreate: *reply.mutable_worktree() = git.create(request.git_worktree_create()); break;
        case wire::Request::kGitOperation: *reply.mutable_git_operation() = git.operation(request.git_operation().operation_id()); break;
        case wire::Request::kGitStatus: *reply.mutable_git_status() = git.status(request.git_status().worktree()); break;
        case wire::Request::kGitWorktreeRemove: *reply.mutable_worktree_removed() = git.remove(request.git_worktree_remove()); break;
        case wire::Request::kWorkspaceCreate: *reply.mutable_workspace() = git.workspace_create(request.workspace_create()); break;
        case wire::Request::kWorkspaceGet: *reply.mutable_workspace() = git.workspace_get(request.workspace_get().id()); break;
        case wire::Request::kWorkspaceMemberSet: *reply.mutable_workspace_ack() = git.workspace_member(request.workspace_member_set()); break;
        case wire::Request::kProcessWait: *reply.mutable_process_wait() = git.wait(request.process_wait()); break;
        case wire::Request::OPERATION_NOT_SET: throw spatial::WorldError(wire::UNKNOWN_METHOD, "No recognized broker operation");
        default: throw spatial::WorldError(wire::UNIMPLEMENTED, "Broker operation is not implemented");
        }
    } catch (const spatial::WorldError& error) {
        reply.mutable_error()->set_code(error.code); reply.mutable_error()->set_message(error.what());
    } catch (const std::filesystem::filesystem_error&) {
        reply.mutable_error()->set_code(wire::STORAGE_ERROR);
        reply.mutable_error()->set_message("Workspace filesystem effect failed; inspect operation state and retained data");
    } catch (const std::invalid_argument& error) {
        reply.mutable_error()->set_code(wire::INVALID_ARGUMENT); reply.mutable_error()->set_message(error.what());
    } catch (const std::exception&) {
        reply.mutable_error()->set_code(wire::INTERNAL_ERROR); reply.mutable_error()->set_message("Broker operation failed internally");
    }
    return reply;
}
spatial::workspace::v1::Reply dispatch_broker(const spatial::workspace::v1::Request& request, spatial::ExecutionStore& store,
                                            spatial::WorkspaceAuthority* authority, spatial::ControlLeases& controls) {
    namespace broker = spatial::workspace::v1;
    namespace wire = spatial::wire;
    broker::Reply reply; reply.set_protocol_version(1); reply.set_request_id(request.request_id());
    try {
        spatial::validate_message(request);
        if (request.protocol_version() != 1) throw spatial::WorldError(wire::UNSUPPORTED_VERSION, "Unsupported private broker protocol version");
        if (request.has_management()) {
            if (request.management().request_id() != request.request_id()) throw std::invalid_argument("Broker management identity mismatch");
            *reply.mutable_management() = dispatch(request.management(), store);
        } else if (request.has_access()) *reply.mutable_access() = store.access(request.access());
        else if (request.has_launch()) *reply.mutable_launched() = store.launch(request.launch());
        else if (request.has_application_access()) *reply.mutable_application_access() = store.application_access(request.application_access());
        else if (request.has_control_acquire()) *reply.mutable_control_lease() = controls.acquire(request.control_acquire());
        else if (request.has_control_release()) *reply.mutable_control_released() = controls.release(request.control_release());
        else if (request.has_grants() || request.has_delegate() || request.has_revoke()) {
            if (!authority) throw spatial::WorldError(wire::UNIMPLEMENTED, "Persistent workspace authority is not configured");
            if (request.has_grants()) *reply.mutable_grants() = authority->list(request.grants());
            else if (request.has_delegate()) *reply.mutable_grant() = authority->delegate(request.delegate());
            else *reply.mutable_grant() = authority->revoke(request.revoke());
        }
        else throw spatial::WorldError(wire::UNKNOWN_METHOD, "No recognized private broker operation");
    } catch (const spatial::WorldError& error) {
        reply.mutable_error()->set_code(error.code); reply.mutable_error()->set_message(error.what());
    } catch (const std::invalid_argument& error) {
        reply.mutable_error()->set_code(wire::INVALID_ARGUMENT); reply.mutable_error()->set_message(error.what());
    } catch (const std::exception&) {
        reply.mutable_error()->set_code(wire::STORAGE_ERROR); reply.mutable_error()->set_message("Broker authority lookup failed");
    }
    return reply;
}
}
int main(int argc, char** argv) {
    try {
        std::filesystem::path state, authority_config, seed;
        unsigned migration = 0; bool fixture_faults = false;
        int native_fd = -1;
        for (int i = 1; i < argc; ++i) {
            const std::string_view argument(argv[i]);
            if (argument == "--state-dir" && state.empty() && i+1 < argc) state = argv[++i];
            else if (argument == "--authority-config" && authority_config.empty() && i+1 < argc) authority_config = argv[++i];
            else if (argument == "--init-authority" && seed.empty() && i+1 < argc) seed = argv[++i];
            else if (argument == "--fixture-faults" && !fixture_faults) fixture_faults = true;
            else if (argument == "--native-security-fd" && native_fd < 0 && i+1 < argc) {
                const std::string value(argv[++i]); std::size_t used = 0;
                native_fd = std::stoi(value, &used);
                if (used != value.size() || native_fd < 3) throw std::invalid_argument("Invalid native security descriptor");
            }
            else if (argument == "--migrate-v1" && !migration) migration = 1;
            else if (argument == "--migrate-v2" && !migration) migration = 2;
            else if (argument == "--migrate-v3" && !migration) migration = 3;
            else throw std::invalid_argument("Unknown or incomplete workspaced argument");
        }
        if (!state.is_absolute()) throw std::invalid_argument("Execution state directory must be absolute");
        if (((migration || !seed.empty()) && (fixture_faults || !authority_config.empty() || native_fd >= 0)) || (migration && !seed.empty()))
            throw std::invalid_argument("Provisioning and migration require an exclusive stopped broker");
        umask(0077); signal(SIGPIPE, SIG_IGN);
        // Destroy jobs before their authority callback target.
        std::unique_ptr<spatial::WorkspaceAuthority> authority;
        std::unique_ptr<spatial::NativeLaunch> native;
        spatial::ExecutionStore store(state, migration, fixture_faults);
        if (migration) { std::cerr << "Execution schema migration complete\n"; return 0; }
        if (!seed.empty()) { spatial::initialize_workspace_authority(seed, store); return 0; }
        if (!authority_config.empty()) {
            authority = std::make_unique<spatial::WorkspaceAuthority>(authority_config, store);
            store.set_authority(authority.get());
        }
        if (native_fd >= 0) {
            native = std::make_unique<spatial::NativeLaunch>(native_fd);
            store.set_native_launch(native.get());
        }
        // Keep the private management transport bounded while processing issuer events.
        for (const int fd : {STDIN_FILENO, STDOUT_FILENO}) {
            const int flags = fcntl(fd, F_GETFL);
            if (flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0) throw std::runtime_error("Cannot configure broker transport");
        }
        using Clock = std::chrono::steady_clock;
        auto input_started = Clock::now(), output_started = Clock::now();
        std::string input, output; std::size_t sent = 0; bool eof = false;
        bool transport_failed = false;
        const auto enqueue = [&](const spatial::workspace::v1::Reply& reply) {
            std::ostringstream framed; spatial::write_frame(framed, reply);
            const auto bytes = framed.str();
            if (output.size()+bytes.size() > 2*spatial::max_frame_bytes) {
                transport_failed = true; throw std::runtime_error("Workspace output queue exceeds limit");
            }
            if (output.empty()) { sent = 0; output_started = Clock::now(); }
            output += bytes;
        };
        const auto flush = [&] {
            if (transport_failed || (!output.empty() && Clock::now()-output_started > std::chrono::seconds(10))) {
                transport_failed = true; throw std::runtime_error("Workspace reply transport failed");
            }
            if (output.empty()) return;
            const auto count = write(STDOUT_FILENO, output.data()+sent, output.size()-sent);
            if (count < 0 && errno != EAGAIN && errno != EINTR) {
                transport_failed = true; throw std::runtime_error("Workspace reply failed");
            }
            if (count > 0) {
                sent += static_cast<std::size_t>(count);
                if (sent == output.size()) { output.clear(); sent = 0; }
            }
        };
        spatial::ControlLeases controls(store, [&](const auto& lease) {
            spatial::workspace::v1::Reply event; event.set_protocol_version(1);
            *event.mutable_control_revoked() = lease; enqueue(event);
        });
        struct ClearObserver {
            spatial::WorkspaceAuthority* authority;
            ~ClearObserver() { if (authority) authority->set_observer([] {}); }
        } clear_observer{authority.get()};
        // Issuer and process waits call this on the same broker thread.
        // Cached lease checks neither recurse into issuer I/O nor reap a borrowed job.
        if (authority) authority->set_observer([&] { controls.tick(); flush(); });
        const auto enforce = [&] {
            if (transport_failed) throw std::runtime_error("Workspace reply transport failed");
            if (native) native->check();
            if (authority) {
                authority->tick();
                if (authority->consume_changes()) store.reconcile_authority();
            }
            store.reap();
            controls.tick();
        };
        for (;;) {
            enforce();
            if (authority) { authority->keepalive(); enforce(); }
            const auto now = Clock::now();
            if ((!input.empty() && now-input_started > std::chrono::seconds(10)) ||
                    (!output.empty() && now-output_started > std::chrono::seconds(10)))
                throw std::runtime_error("Workspace transport deadline exceeded");
            if (output.empty() && input.size() >= 4) {
                const auto* header = reinterpret_cast<const unsigned char*>(input.data());
                const auto length = (std::uint32_t(header[0]) << 24) | (std::uint32_t(header[1]) << 16) | (std::uint32_t(header[2]) << 8) | header[3];
                if (length > spatial::max_frame_bytes) throw std::runtime_error("Excessive workspace frame");
                if (input.size() >= length+4) {
                    spatial::workspace::v1::Request request; spatial::workspace::v1::Reply reply;
                    if (!request.ParseFromArray(input.data()+4, static_cast<int>(length))) {
                        reply.set_protocol_version(1); reply.mutable_error()->set_code(spatial::wire::INVALID_ARGUMENT);
                        reply.mutable_error()->set_message("Malformed broker protobuf payload");
                    } else reply = dispatch_broker(request, store, authority.get(), controls);
                    enforce();
                    input.erase(0, length+4); input_started = Clock::now();
                    enqueue(reply);
                }
            }
            if (eof && output.empty()) {
                if (!input.empty()) throw std::runtime_error("Truncated workspace frame");
                return 0;
            }
            pollfd fds[2]{{!eof && output.empty() ? STDIN_FILENO : -1, POLLIN, 0},
                          {STDOUT_FILENO, static_cast<short>(output.empty() ? 0 : POLLOUT), 0}};
            const int ready = poll(fds, 2, 10);
            if (ready < 0 && errno == EINTR) continue;
            if (ready < 0 || ((fds[0].revents | fds[1].revents) & POLLNVAL)) throw std::runtime_error("Workspace transport poll failed");
            if (fds[1].revents & (POLLHUP | POLLERR)) throw std::runtime_error("Workspace supervisor disconnected");
            if (!output.empty() && (fds[1].revents & POLLOUT)) {
                flush();
            }
            if (!eof && output.empty() && (fds[0].revents & (POLLIN | POLLHUP | POLLERR))) {
                std::array<char, 65536> bytes{};
                const auto count = read(STDIN_FILENO, bytes.data(), std::min(bytes.size(), spatial::max_frame_bytes+4-input.size()));
                if (count < 0 && errno != EAGAIN && errno != EINTR) throw std::runtime_error("Workspace request failed");
                if (!count) eof = true;
                if (count > 0) {
                    if (input.empty()) input_started = Clock::now();
                    input.append(bytes.data(), static_cast<std::size_t>(count));
                }
            }
        }
    } catch (const std::exception& error) {
        std::cerr << "workspaced: " << error.what() << '\n'; return 2;
    }
}
