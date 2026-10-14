#include "workspace/store.hpp"
#include "workspace/authority.hpp"
#include "workspace/native_launch.hpp"
#include "workspace/resources.hpp"
#include "workspace/storage_client.hpp"
#include "workspace/control.hpp"
#include "workspace/control_channels.hpp"
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
                                            spatial::WorkspaceAuthority* authority, spatial::ControlLeases& controls,
                                            const std::string& owner = {}, const std::string& native_server = {},
                                            bool during_effect = false) {
    namespace broker = spatial::workspace::v1;
    namespace wire = spatial::wire;
    broker::Reply reply; reply.set_protocol_version(1); reply.set_request_id(request.request_id());
    try {
        spatial::validate_message(request);
        if (request.protocol_version() != 1) throw spatial::WorldError(wire::UNSUPPORTED_VERSION, "Unsupported private broker protocol version");
        // Transport validation precedes attribution on every private channel.
        if (!owner.empty() && request.has_control_acquire() && request.control_acquire().application().server_id() != native_server)
            throw spatial::WorldError(wire::FORBIDDEN, "Control owner native server mismatch");
        if (during_effect && !request.has_access() && !request.has_scoped_authority_check() && !request.has_authorization_fence() && !request.has_application_access() &&
                !request.has_control_acquire() && !request.has_control_release())
            throw spatial::WorldError(wire::BUSY, "Workspace effect is pending; retry execution after its checked result");
        if (request.has_management()) {
            if (request.management().request_id() != request.request_id()) throw std::invalid_argument("Broker management identity mismatch");
            *reply.mutable_management() = dispatch(request.management(), store);
        } else if (request.has_access()) *reply.mutable_access() = store.access(request.access());
        else if (request.has_scoped_authority_check()) {
            if (!authority) throw spatial::WorldError(wire::UNIMPLEMENTED, "Persistent workspace authority is not configured");
            *reply.mutable_scoped_authority_check() = store.scoped_authority_check(request.scoped_authority_check());
        }
        else if (request.has_authorization_fence()) *reply.mutable_authorization_fence() = store.authorization_fence(request.authorization_fence());
        else if (request.has_launch()) *reply.mutable_launched() = store.launch(request.launch());
        else if (request.has_application_access()) *reply.mutable_application_access() = store.application_access(request.application_access());
        else if (request.has_workspace_copy_begin()) {
            const auto& operation=request.workspace_copy_begin();
            if(!operation.has_principal() || !operation.has_request())
                throw spatial::WorldError(wire::FORBIDDEN,"Workspace copy requires authenticated actor identity and request");
            *reply.mutable_workspace_copy_status()=store.workspace_copy_begin(operation.request(),operation.principal());
        }
        else if (request.has_workspace_copy_status()) {
            const auto& operation=request.workspace_copy_status();
            if(!operation.has_principal() || !operation.has_request())
                throw spatial::WorldError(wire::FORBIDDEN,"Workspace copy status requires authenticated actor identity");
            *reply.mutable_workspace_copy_status()=store.workspace_copy_status(operation.request(),operation.principal());
        }
        else if (request.has_control_acquire()) *reply.mutable_control_lease() = controls.acquire(request.control_acquire(), owner);
        else if (request.has_control_release()) *reply.mutable_control_released() = controls.release(request.control_release(), owner);
        else if (request.has_bootstrap_bind() || request.has_bootstrap_revoke() || request.has_bootstrap_apply()) {
            if (!authority) throw spatial::WorldError(wire::UNIMPLEMENTED, "Persistent workspace authority is not configured");
            if (request.has_bootstrap_bind()) *reply.mutable_binding() = authority->bind_bootstrap(request.bootstrap_bind());
            else if (request.has_bootstrap_revoke()) *reply.mutable_binding() = authority->revoke_bootstrap(request.bootstrap_revoke());
            else *reply.mutable_bootstrap() = authority->apply_bootstrap(request.bootstrap_apply());
        }
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
    } catch (const std::exception& error) {
        std::cerr << "broker private operation failed: " << error.what() << '\n';
        reply.mutable_error()->set_code(wire::STORAGE_ERROR); reply.mutable_error()->set_message("Broker authority lookup failed");
    }
    return reply;
}
}
int main(int argc, char** argv) {
    try {
        std::filesystem::path state, authority_config, seed, resource_config;
        unsigned migration = 0; bool fixture_faults = false, production = false;
        int native_fd = -1, resource_fd = -1, storage_fd = -1;
        int control_fd = -1, control_peer = -1;
        for (int i = 1; i < argc; ++i) {
            const std::string_view argument(argv[i]);
            if (argument == "--state-dir" && state.empty() && i+1 < argc) state = argv[++i];
            else if (argument == "--authority-config" && authority_config.empty() && i+1 < argc) authority_config = argv[++i];
            else if (argument == "--resource-config" && resource_config.empty() && resource_fd < 0 && i+1 < argc) resource_config = argv[++i];
            else if (argument == "--resource-config-fd" && resource_config.empty() && resource_fd < 0 && i+1 < argc) {
                const std::string value(argv[++i]); std::size_t used = 0;
                resource_fd = std::stoi(value, &used);
                if (used != value.size() || resource_fd < 3) throw std::invalid_argument("Invalid resource policy descriptor");
            }
            else if (argument == "--storage-fd" && storage_fd < 0 && i+1 < argc) {
                const std::string value(argv[++i]); std::size_t used = 0; storage_fd = std::stoi(value, &used);
                if (used != value.size() || storage_fd < 3) throw std::invalid_argument("Invalid storage owner descriptor");
            }
            else if (argument == "--init-authority" && seed.empty() && i+1 < argc) seed = argv[++i];
            else if (argument == "--fixture-faults" && !fixture_faults) fixture_faults = true;
            else if (argument == "--production" && !production) production = true;
            else if (argument == "--native-security-fd" && native_fd < 0 && i+1 < argc) {
                const std::string value(argv[++i]); std::size_t used = 0;
                native_fd = std::stoi(value, &used);
                if (used != value.size() || native_fd < 3) throw std::invalid_argument("Invalid native security descriptor");
            }
            else if ((argument == "--control-supervisor-fd" || argument == "--control-supervisor-pid") && i+1 < argc) {
                int& target = argument == "--control-supervisor-fd" ? control_fd : control_peer;
                if (target != -1) throw std::invalid_argument("Repeated control supervisor argument");
                const std::string value(argv[++i]); std::size_t used = 0;
                target = std::stoi(value, &used);
                if (used != value.size() || target < 3) throw std::invalid_argument("Invalid control supervisor argument");
            }
            else if (argument == "--migrate-v1" && !migration) migration = 1;
            else if (argument == "--migrate-v2" && !migration) migration = 2;
            else if (argument == "--migrate-v3" && !migration) migration = 3;
            else if (argument == "--migrate-v4" && !migration) migration = 4;
            else if (argument == "--migrate-v5" && !migration) migration = 5;
            else if (argument == "--migrate-v6" && !migration) migration = 6;
            else if (argument == "--migrate-v7" && !migration) migration = 7;
            else throw std::invalid_argument("Unknown or incomplete workspaced argument");
        }
        if (!state.is_absolute()) throw std::invalid_argument("Execution state directory must be absolute");
        if (production && (resource_fd < 0 || storage_fd < 0 || fixture_faults || migration || !seed.empty()))
            throw std::invalid_argument("Production broker requires sealed resources and storage ownership without fixture faults");
        if ((control_fd < 0) != (control_peer < 0) || (control_fd >= 0 && control_fd == native_fd))
            throw std::invalid_argument("Control supervisor requires a separate descriptor and peer");
        if (resource_fd >= 0 && (resource_fd == native_fd || resource_fd == control_fd))
            throw std::invalid_argument("Resource policy requires a separate descriptor");
        if (storage_fd >= 0 && (storage_fd == native_fd || storage_fd == control_fd || storage_fd == resource_fd))
            throw std::invalid_argument("Storage owner requires a separate descriptor");
        if (((migration || !seed.empty()) && (fixture_faults || !authority_config.empty() || !resource_config.empty() || resource_fd >= 0 || native_fd >= 0 || control_fd >= 0 || storage_fd >= 0)) || (migration && !seed.empty()))
            throw std::invalid_argument("Provisioning and migration require an exclusive stopped broker");
        umask(0077); signal(SIGPIPE, SIG_IGN);
        // Destroy jobs before their authority callback target.
        std::unique_ptr<spatial::WorkspaceAuthority> authority;
        std::unique_ptr<spatial::NativeLaunch> native;
        std::unique_ptr<spatial::WorkspaceResources> resources;
        if (!resource_config.empty()) resources = std::make_unique<spatial::WorkspaceResources>(resource_config);
        if (resource_fd >= 0) resources = std::make_unique<spatial::WorkspaceResources>(resource_fd, production);
        std::unique_ptr<spatial::StorageClient> storage;
        if (storage_fd >= 0) storage = std::make_unique<spatial::StorageClient>(storage_fd, state);
        spatial::ExecutionStore store(state, migration, fixture_faults, storage.get(), resources.get());
        store.set_resources(resources.get());
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
        std::unique_ptr<spatial::ControlChannels> channels;
        spatial::ControlLeases controls(store, [&](const std::string& owner, const auto& lease) {
            if (!owner.empty()) { channels->revoked(owner, lease); return; }
            spatial::workspace::v1::Reply event; event.set_protocol_version(1);
            *event.mutable_control_revoked() = lease; enqueue(event);
        });
        if (control_fd >= 0) channels = std::make_unique<spatial::ControlChannels>(control_fd, control_peer, controls);
        struct ClearObserver {
            spatial::WorkspaceAuthority* authority;
            spatial::ExecutionStore& store;
            ~ClearObserver() {
                store.set_observer({});
                if (authority) authority->set_observer([] {});
            }
        } clear_observer{authority.get(), store};
        // Issuer and process waits call this on the same broker thread.
        // Cached lease checks neither recurse into issuer I/O nor reap a borrowed job.
        if (authority) authority->set_observer([&] {
            controls.tick(); flush();
            if (channels) channels->administrative();
        });
        const auto enforce = [&] {
            if (transport_failed) throw std::runtime_error("Workspace reply transport failed");
            if (storage) storage->check_alive();
            if (native) native->check();
            if (authority) {
                authority->tick();
                if (authority->consume_changes()) store.reconcile_authority();
            }
            store.reap();
            controls.tick();
            // Reconciliation and reaping can destroy credentials and lose their native cleanup reply.
            // Check that result before this iteration can acknowledge issuer revocation.
            if (native) native->check();
        };
        if (storage) storage->set_observer(enforce);
        bool serving_effect = false;
        const auto serve_effect = [&] {
            if (!channels || serving_effect) return;
            struct Guard {
                bool& busy;
                explicit Guard(bool& value) : busy(value) { busy = true; }
                ~Guard() { busy = false; }
            } guard(serving_effect);
            // These callbacks run only during filesystem and Git waits, never while borrowing an application job.
            // Keep release acknowledgements and checked access available without nesting storage effects.
            channels->serve([&](const auto& request, const std::string& owner, const std::string& native_server) {
                const auto cancellation = authority ? authority->cancellation_sequence() : 0;
                auto reply = dispatch_broker(request, store, authority.get(), controls, owner, native_server, true);
                enforce();
                if (authority && !reply.has_error() && cancellation != authority->cancellation_sequence() &&
                        !request.has_control_release()) {
                    reply.Clear(); reply.set_protocol_version(1); reply.set_request_id(request.request_id());
                    reply.mutable_error()->set_code(spatial::wire::UNAUTHORIZED);
                    reply.mutable_error()->set_message("Issuer revocation invalidated the in-flight broker result");
                }
                return reply;
            });
        };
        store.set_observer([&] {
            serve_effect(); enforce();
            // This explicit effect wait is outside issuer callbacks. Do not recursively refresh from tick().
            if (authority) { authority->keepalive(); enforce(); }
            serve_effect();
        });
        for (;;) {
            enforce();
            store.advance_workspace_copies();
            enforce();
            if (authority) { authority->keepalive(); enforce(); }
            if (channels) channels->serve([&](const auto& request, const std::string& owner, const std::string& native_server) {
                const auto cancellation = authority ? authority->cancellation_sequence() : 0;
                auto reply = dispatch_broker(request, store, authority.get(), controls, owner, native_server);
                enforce();
                if (authority && !reply.has_error() && cancellation != authority->cancellation_sequence() &&
                        !request.has_management() && !request.has_control_release()) {
                    reply.Clear(); reply.set_protocol_version(1); reply.set_request_id(request.request_id());
                    reply.mutable_error()->set_code(spatial::wire::UNAUTHORIZED);
                    reply.mutable_error()->set_message("Issuer revocation invalidated the in-flight broker result");
                }
                return reply;
            });
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
                    const auto cancellation = authority ? authority->cancellation_sequence() : 0;
                    if (!request.ParseFromArray(input.data()+4, static_cast<int>(length))) {
                        reply.set_protocol_version(1); reply.mutable_error()->set_code(spatial::wire::INVALID_ARGUMENT);
                        reply.mutable_error()->set_message("Malformed broker protobuf payload");
                    } else reply = dispatch_broker(request, store, authority.get(), controls);
                    enforce();
                    if (authority && !reply.has_error() && cancellation != authority->cancellation_sequence() &&
                            !request.has_management() && !request.has_control_release()) {
                        reply.Clear(); reply.set_protocol_version(1); reply.set_request_id(request.request_id());
                        reply.mutable_error()->set_code(spatial::wire::UNAUTHORIZED);
                        reply.mutable_error()->set_message("Issuer revocation invalidated the in-flight broker result");
                    }
                    input.erase(0, length+4); input_started = Clock::now();
                    enqueue(reply);
                }
            }
            if (eof && output.empty()) {
                if (!input.empty()) throw std::runtime_error("Truncated workspace frame");
                store.stop_all();
                if (authority) {
                    if (!controls.empty()) throw std::runtime_error("Workspace control release remains unresolved");
                    authority->shutdown(enforce);
                }
                return 0;
            }
            if (authority) authority->settle(output.empty() && (!channels || !channels->pending_output()) && controls.cancellation_settled());
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
