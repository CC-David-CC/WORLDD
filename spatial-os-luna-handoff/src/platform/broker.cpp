#include "platform/broker.hpp"
#include "platform/process_handle.hpp"
#include "world/store.hpp"
#include "authority/store.hpp"
#include "workspace/resources.hpp"
#include "supervisor/storage_launch.hpp"
#include "identity/tls_support.hpp"
#include <google/protobuf/text_format.h>
#include <set>
#include <array>
#include <algorithm>
#include <cerrno>
#include <csignal>
#include <fcntl.h>
#include <iostream>
#include <poll.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>
#include <utility>

namespace spatial {
namespace {
bool bootstrap_id(const std::string& id) { return !id.empty() && id.size() <= 128 && id.find('\0') == std::string::npos; }
bool bootstrap_terms(const authority::v1::GrantTerms& terms, const std::string& workspace) {
    try { authority::validate_permissions(terms); }
    catch (const std::invalid_argument&) { return false; }
    return terms.scope().domain().kind() == authority::v1::WORKSPACE && terms.scope().domain().id() == workspace;
}
bool bootstrap_binding(const authority::v1::BootstrapBinding& binding, const std::string& workspace) {
    return binding.id().size() == 32 && binding.parent_id().size() == 32 &&
        binding.authorizer().issuer_id().size() == 32 && binding.authorizer().actor_id().size() == 32 &&
        binding.selector().issuer_id().size() == 32 && bootstrap_id(binding.selector().profile_id()) &&
        bootstrap_id(binding.selector().template_id()) && binding.selector().profile_revision() > 0 &&
        binding.selector().profile_revision() <= INT64_MAX && binding.created_revision() > 0 &&
        binding.created_revision() <= INT64_MAX && binding.revoked_revision() <= INT64_MAX &&
        (!binding.revoked_revision() || binding.revoked_revision() > binding.created_revision()) &&
        !binding.terms().has_subject() && bootstrap_terms(binding.terms(), workspace);
}
bool bootstrap_report(const authority::v1::BootstrapReport& report, const broker_wire::BootstrapApplyRequest& request) {
    identity::v1::ActorCredential claims;
    if (!claims.ParseFromString(request.credential().content().payload())) return false;
    if (claims.issuer_id() != request.principal().issuer_id() || claims.actor_id() != request.principal().actor_id() ||
        report.issuer_id() != request.principal().issuer_id() || report.issuer_id().size() != 32 ||
        report.profile_id() != claims.bootstrap_profile() || !bootstrap_id(report.profile_id()) ||
        report.profile_revision() != claims.bootstrap_profile_revision() || report.profile_revision() == 0 ||
        report.profile_revision() > INT64_MAX || report.domain().kind() != authority::v1::WORKSPACE ||
        report.domain().id() != request.workspace() || report.results_size() > 32) return false;
    if (report.has_error()) return report.results_size() == 0 && report.error().code() != wire::ERROR_CODE_UNSPECIFIED;
    std::set<std::string> templates, grants;
    for (const auto& result : report.results()) {
        if (!bootstrap_id(result.template_id()) || !templates.insert(result.template_id()).second) return false;
        if (result.has_error()) { if (result.error().code() == wire::ERROR_CODE_UNSPECIFIED) return false; continue; }
        if (!result.has_grant()) return false;
        const auto& record = result.grant();
        if (record.protocol_version() != 1 || record.id().size() != 32 || !grants.insert(record.id()).second ||
            record.parent_id().size() != 32 || record.issuer().issuer_id().size() != 32 || record.issuer().actor_id().size() != 32 ||
            record.terms().subject().SerializeAsString() != request.principal().SerializeAsString() ||
            record.created_revision() == 0 || record.created_revision() > INT64_MAX || record.revoked_revision() > INT64_MAX ||
            (record.revoked_revision() && record.revoked_revision() <= record.created_revision()) ||
            !bootstrap_terms(record.terms(), request.workspace())) return false;
    }
    return true;
}
bool valid_control_lease(const broker_wire::ApplicationControlLease& lease) {
    return lease.id().size() == 32 && lease.principal().issuer_id().size() == 32 &&
        lease.principal().actor_id().size() == 32 && lease.application().server_id().size() == 32 &&
        !lease.application().workspace().empty() && !lease.application().application().empty();
}
}
BrokerProcess::BrokerProcess(const std::filesystem::path& executable, const std::filesystem::path& state, bool fixture_faults,
                             const std::filesystem::path& resource_configuration) {
    if (!resource_configuration.empty()) {
        broker_wire::BrokerProcessConfig config;
        if (!google::protobuf::TextFormat::ParseFromString(identity::tls_detail::load_config(resource_configuration), &config))
            throw std::invalid_argument("Invalid broker process configuration");
        validate_message(config);
        if (config.protocol_version() != 1 || !config.has_resources() || !config.resources().delegated_cgroup().empty())
            throw std::invalid_argument("Broker process requires an unbound resource template");
        validate_resource_config(config.resources());
        if (config.require_confinement() && (!config.has_storage_owner() || fixture_faults || config.resources().protocol_version() != 2))
            throw std::invalid_argument("Production broker launch requires aggregate resources and storage ownership without fixture faults");
        if (config.has_storage_owner()) supervisor::validate_storage_launch(config.storage_owner(), state);
        identity::tls_detail::Fd storage_owner, storage_child;
        if (config.has_storage_owner()) {
            int storage[2];
            if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, storage) != 0)
                throw std::runtime_error("Cannot create private storage channel");
            storage_owner.reset(storage[0]); storage_child.reset(storage[1]);
            const int enabled = 1;
            for (const int fd : storage)
                if (setsockopt(fd, SOL_SOCKET, SO_PASSCRED, &enabled, sizeof(enabled)) != 0)
                    throw std::runtime_error("Cannot require storage sender credentials");
        }
        int pair[2];
        if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, pair) != 0)
            throw std::runtime_error("Cannot create broker channel");
        identity::tls_detail::Fd parent(pair[0]), child(pair[1]);
        scope_ = supervisor::ProcessScope::prepare(config.delegated_cgroup());
        identity::tls_detail::Fd policy(seal_resource_config(config.resources(),
            std::filesystem::path(config.delegated_cgroup()) / scope_->state().identity().scope_name()));
        supervisor::v1::AuthorityProcessLaunch request;
        request.set_executable(executable.string());
        request.add_arguments("--state-dir"); request.add_arguments(state.string());
        if (config.require_confinement()) request.add_arguments("--production");
        if (fixture_faults) request.add_arguments("--fixture-faults");
        request.add_arguments("--resource-config-fd"); request.add_arguments("3"); request.add_inherited_fds(3);
        std::vector<int> inherited{policy.value};
        if (config.has_storage_owner()) {
            request.add_arguments("--storage-fd"); request.add_arguments("4"); request.add_inherited_fds(4);
            inherited.push_back(storage_child.value);
        }
        scope_->start(request, {child.value, child.value, STDERR_FILENO}, inherited);
        pid_ = scope_->direct_child_pid();
        identity::tls_detail::Fd retained(scope_->duplicate_child_pidfd());
        storage_child.reset();
        if (config.has_storage_owner())
            storage_ = std::make_unique<supervisor::StorageOwnerLaunch>(config.storage_owner(), state,
                storage_owner.value, retained.value, STDERR_FILENO);
        pidfd_ = std::exchange(retained.value, -1);
        channel_ = std::exchange(parent.value, -1);
        return;
    }
    int pair[2];
    if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, pair) != 0) throw std::runtime_error("Cannot create broker channel");
    const auto parent = getpid(); pid_ = fork();
    if (pid_ == 0) {
        if (prctl(PR_SET_PDEATHSIG, SIGKILL) != 0 || getppid() != parent) _exit(126);
        if (dup2(pair[1], STDIN_FILENO) < 0 || dup2(pair[1], STDOUT_FILENO) < 0) _exit(126);
        close(pair[0]); close(pair[1]);
        if (fixture_faults) execl(executable.c_str(), executable.c_str(), "--state-dir", state.c_str(),
                                "--fixture-faults", static_cast<char*>(nullptr));
        if (fixture_faults) _exit(127);
        execl(executable.c_str(), executable.c_str(), "--state-dir", state.c_str(), static_cast<char*>(nullptr)); _exit(127);
    }
    close(pair[1]); channel_ = pair[0];
    if (pid_ < 0) { close(channel_); throw std::runtime_error("Cannot fork workspace authority"); }
    pidfd_ = static_cast<int>(syscall(SYS_pidfd_open, pid_, 0));
    if (pidfd_ < 0) {
        close(channel_); kill(pid_, SIGKILL); while (waitpid(pid_, nullptr, 0) < 0 && errno == EINTR) {}
        throw std::runtime_error("Cannot monitor workspace authority");
    }
}
void BrokerProcess::release_channel() { if (channel_ >= 0) { close(channel_); channel_ = -1; } }
void BrokerProcess::finish() {
    if (!scope_ || channel_ < 0 || stopped_) throw std::logic_error("Broker finish requires retained scope and channel ownership");
    if (shutdown(channel_, SHUT_WR) != 0) throw std::runtime_error("Cannot finish broker management input");
    pollfd child{pidfd_, POLLIN, 0}; int ready;
    do { ready = poll(&child, 1, 5000); } while (ready < 0 && errno == EINTR);
    if (ready <= 0 || !scope_->exited_successfully()) throw std::runtime_error("Broker did not exit successfully after management EOF");
    scope_->stop(); stopped_ = true; scope_.reset();
    release_channel(); close(pidfd_); pidfd_ = -1;
    if (storage_) storage_->stop();
}
BrokerProcess::~BrokerProcess() {
    if (stopped_) return;
    release_channel();
    pollfd fd{pidfd_, POLLIN, 0}; int ready;
    do { ready = poll(&fd, 1, 1000); } while (ready < 0 && errno == EINTR);
    if (scope_) {
        // The scope owns reaping and checked descendant cleanup.
        scope_.reset(); close(pidfd_);
        if (storage_) {
            try { storage_->stop(); }
            catch (const std::exception& error) { std::cerr << "Broker storage cleanup uncertain: " << error.what() << '\n'; }
        }
        return;
    }
    if (ready <= 0) kill(pid_, SIGKILL);
    while (waitpid(pid_, nullptr, 0) < 0 && errno == EINTR) {}
    close(pidfd_);
}
BrokerClient::BrokerClient(int inherited_channel, pid_t pid) : BrokerClient(inherited_channel, pid, -1, false) {}
BrokerClient::BrokerClient(int inherited_channel, pid_t pid, int borrowed_pidfd) : BrokerClient(inherited_channel, pid, borrowed_pidfd, true) {}
BrokerClient::BrokerClient(int inherited_channel, pid_t pid, int borrowed_pidfd, bool retained) : channel_(inherited_channel), pid_(pid) {
    if (channel_ <= STDERR_FILENO || pid_ <= 1) throw std::invalid_argument("Invalid inherited broker identity");
    try {
        if (retained && borrowed_pidfd < 3) throw std::invalid_argument("Missing retained broker process handle");
        pidfd_ = retained ? ProcessHandle::duplicate(borrowed_pidfd, pid).release() :
            static_cast<int>(syscall(SYS_pidfd_open, pid_, 0));
        const auto flags = fcntl(channel_, F_GETFL);
        if (pidfd_ < 0 || flags < 0 || fcntl(channel_, F_SETFL, flags | O_NONBLOCK) < 0 ||
            fcntl(channel_, F_SETFD, FD_CLOEXEC) < 0) throw std::runtime_error("Cannot configure broker channel");
        wire::Request request; request.mutable_runtime_identities();
        const auto ready = call(request);
        if (!ready.has_identities() || ready.identities().workspaced_pid() != static_cast<std::uint64_t>(pid_))
            throw std::runtime_error("Workspace authority identity mismatch");
    } catch (...) { if (channel_ >= 0) close(channel_); if (pidfd_ >= 0) close(pidfd_); throw; }
}
BrokerClient::~BrokerClient() { if (channel_ >= 0) close(channel_); close(pidfd_); }
void BrokerClient::check_alive() const {
    if (channel_ < 0) throw std::runtime_error("Workspace channel failed");
    pollfd fd{pidfd_, POLLIN, 0};
    const auto result = poll(&fd, 1, 0);
    if (result < 0 && errno == EINTR) return;
    if (result < 0 || fd.revents) throw std::runtime_error("Workspace authority exited");
}
void BrokerClient::wait(short events) {
    while (true) {
        tick_();
        if (std::chrono::steady_clock::now() >= deadline_) throw std::runtime_error("Workspace authority deadline exceeded");
        pump();
        if ((events & POLLIN) && response_) return;
        pollfd fds[2]{{channel_, events, 0}, {pidfd_, POLLIN, 0}};
        const auto ready = poll(fds, 2, 10);
        if (ready < 0 && errno == EINTR) continue;
        if (ready < 0 || fds[1].revents || (fds[0].revents & (POLLHUP | POLLERR | POLLNVAL)))
            throw std::runtime_error("Workspace authority channel closed");
        if (fds[0].revents & events) return;
    }
}
void BrokerClient::fail_channel() {
    if (channel_ >= 0) close(channel_);
    channel_ = -1;
}
void BrokerClient::revoked(const broker_wire::ApplicationControlLease& lease) {
    if (!valid_control_lease(lease)) throw std::runtime_error("Invalid control revocation identity");
    const auto found = controls_.find(lease.id());
    if (found != controls_.end()) {
        if (found->second.revoked || found->second.lease.SerializeAsString() != lease.SerializeAsString())
            throw std::runtime_error("Invalid or repeated control revocation");
        found->second.revoked = true;
    } else {
        if (!pending_ || !pending_->has_control_acquire() || early_revocation_ || lease.id().size() != 32 ||
            lease.principal().SerializeAsString() != pending_->control_acquire().principal().SerializeAsString() ||
            lease.application().SerializeAsString() != pending_->control_acquire().application().SerializeAsString())
            throw std::runtime_error("Revocation does not match a control reservation");
        early_revocation_ = lease;
    }
    if (revocations_.size() >= 256) throw std::runtime_error("Control notification queue exceeds limit");
    revocations_.push_back(lease);
}
void BrokerClient::pump() {
    check_alive();
    if (!incoming_.empty() && std::chrono::steady_clock::now()-frame_started_ > std::chrono::seconds(10))
        throw std::runtime_error("Broker frame deadline exceeded");
    unsigned frames = 0; std::size_t received = 0;
    for (;;) {
        while (incoming_.size() >= 4) {
            const auto* h = reinterpret_cast<const unsigned char*>(incoming_.data());
            const auto size = (std::uint32_t(h[0])<<24) | (std::uint32_t(h[1])<<16) | (std::uint32_t(h[2])<<8) | h[3];
            if (size > max_frame_bytes) throw std::runtime_error("Broker reply exceeds limit");
            if (incoming_.size() < size+4) break;
            broker_wire::Reply reply;
            if (!reply.ParseFromArray(incoming_.data()+4, static_cast<int>(size)) || reply.protocol_version() != 1)
                throw std::runtime_error("Invalid broker reply");
            validate_message(reply);
            incoming_.erase(0,size+4); frame_started_ = std::chrono::steady_clock::now();
            if (reply.has_control_revoked()) {
                if (reply.request_id() != 0) throw std::runtime_error("Invalid revocation request identity");
                revoked(reply.control_revoked());
            } else {
                if (!pending_ || response_ || reply.request_id() != pending_->request_id())
                    throw std::runtime_error("Unexpected broker reply identity");
                response_ = std::move(reply);
            }
            if (++frames == 64) return;
        }
        if (received >= 262144) return;
        std::array<char,65536> bytes{};
        const auto n = read(channel_, bytes.data(), std::min(bytes.size(), max_frame_bytes+4-incoming_.size()));
        if (n < 0 && errno == EINTR) continue;
        if (n < 0 && errno == EAGAIN) return;
        if (n <= 0) throw std::runtime_error("Broker authority channel closed");
        if (incoming_.empty()) frame_started_ = std::chrono::steady_clock::now();
        incoming_.append(bytes.data(),static_cast<std::size_t>(n)); received += static_cast<std::size_t>(n);
    }
}
broker_wire::Reply BrokerClient::exchange(broker_wire::Request request) {
    if (pending_) throw std::logic_error("Recursive broker exchange is not allowed");
    request.set_protocol_version(1); request.set_request_id(++sequence_);
    if (request.has_management()) {
        request.mutable_management()->set_protocol_version(1);
        request.mutable_management()->set_request_id(sequence_);
    }
    const auto payload = request.SerializeAsString();
    if (payload.size() > max_frame_bytes) throw std::invalid_argument("Broker frame exceeds limit");
    broker_wire::Reply reply;
    pending_ = &request; response_.reset(); early_revocation_.reset();
    struct ClearPending {
        BrokerClient& client;
        ~ClearPending() { client.pending_ = nullptr; client.response_.reset(); client.early_revocation_.reset(); }
    } clear{*this};
    try {
        const auto size = static_cast<std::uint32_t>(payload.size());
        const std::array<char, 4> header{static_cast<char>(size >> 24), static_cast<char>(size >> 16), static_cast<char>(size >> 8), static_cast<char>(size)};
        std::string bytes(header.data(), header.size()); bytes += payload;
        auto timeout = std::chrono::seconds(10);
        if (request.has_management()) {
            const auto& management = request.management();
            if (management.has_git_repo_import()) timeout = std::chrono::minutes(5);
            else if (management.has_git_worktree_create() || management.has_git_status() ||
                     management.has_git_worktree_remove() || management.has_git_operation())
                timeout = std::chrono::seconds(60);
        }
        deadline_ = std::chrono::steady_clock::now() + timeout;
        std::size_t sent = 0;
        while (sent < bytes.size()) {
            wait(POLLOUT);
            const auto n = send(channel_, bytes.data() + sent, bytes.size() - sent, MSG_NOSIGNAL);
            if (n < 0 && (errno == EAGAIN || errno == EINTR)) continue;
            if (n <= 0) throw std::runtime_error("Cannot send broker request");
            sent += static_cast<std::size_t>(n);
        }
        while (!response_) { pump(); if (!response_) wait(POLLIN); }
        reply = std::move(*response_); response_.reset();
        if (reply.has_error() && early_revocation_) throw std::runtime_error("Acquisition denial followed a new lease revocation");
        if (!reply.has_error()) {
            bool valid = (request.has_access() && reply.has_access()) || (request.has_launch() && reply.has_launched());
            if (request.has_control_acquire() && reply.has_control_lease()) {
                const auto& lease = reply.control_lease(); const auto& expected = request.control_acquire();
                valid = valid_control_lease(lease) &&
                    lease.principal().SerializeAsString() == expected.principal().SerializeAsString() &&
                    lease.application().SerializeAsString() == expected.application().SerializeAsString();
                if (early_revocation_) valid = valid && early_revocation_->SerializeAsString() == lease.SerializeAsString();
                const auto previous = controls_.find(lease.id());
                if (previous != controls_.end()) valid = valid &&
                    previous->second.lease.SerializeAsString() == lease.SerializeAsString();
                for (const auto& [id, control] : controls_)
                    if (id != lease.id() && control.lease.application().SerializeAsString() == lease.application().SerializeAsString()) valid = false;
                if (valid && previous == controls_.end()) {
                    if (controls_.size() >= 256) throw std::runtime_error("Control reservation limit exceeded");
                    controls_.emplace(lease.id(),Reservation{lease,early_revocation_.has_value()});
                }
            }
            if (request.has_control_release() && reply.has_control_released()) {
                const auto& expected = request.control_release(); const auto found = controls_.find(expected.id());
                valid = reply.control_released().id() == expected.id() && found != controls_.end() &&
                    found->second.lease.principal().SerializeAsString() == expected.principal().SerializeAsString();
                if (valid) controls_.erase(found);
            }
            if (request.has_application_access() && reply.has_application_access()) {
                const auto& expected = request.application_access().applications();
                const auto& received = reply.application_access().applications();
                valid = expected.size() == received.size();
                for (int i = 0; valid && i < expected.size(); ++i) {
                    const auto& item = received.Get(i);
                    valid = item.has_application() && item.application().SerializeAsString() == expected.Get(i).SerializeAsString() &&
                        item.has_observe() && item.has_control() && (!item.control() || item.observe());
                }
            }
            if (request.has_grants() && reply.has_grants()) {
                valid = reply.grants().grants_size() <= 16 && reply.grants().revision() <= INT64_MAX &&
                    (reply.grants().next_after().empty() || reply.grants().next_after().size() == 32);
                for (const auto& record : reply.grants().grants())
                    valid = valid && record.protocol_version() == 1 && record.id().size() == 32 &&
                        record.terms().subject().SerializeAsString() == request.grants().principal().SerializeAsString() &&
                        record.terms().scope().domain().kind() == authority::v1::WORKSPACE &&
                        record.terms().scope().domain().id() == request.grants().workspace();
            }
            if (request.has_delegate() && reply.has_grant())
                valid = reply.grant().protocol_version() == 1 && reply.grant().id().size() == 32 &&
                    reply.grant().parent_id() == request.delegate().parent_id() &&
                    reply.grant().issuer().SerializeAsString() == request.delegate().principal().SerializeAsString() &&
                    reply.grant().terms().SerializeAsString() == request.delegate().terms().SerializeAsString();
            if (request.has_revoke() && reply.has_grant())
                valid = reply.grant().protocol_version() == 1 && reply.grant().id() == request.revoke().id() &&
                    reply.grant().revoked_revision() > 0 && reply.grant().terms().scope().domain().kind() == authority::v1::WORKSPACE &&
                    reply.grant().terms().scope().domain().id() == request.revoke().workspace();
            if (request.has_bootstrap_bind() && reply.has_binding()) {
                const auto& expected = request.bootstrap_bind(); const auto& binding = reply.binding();
                valid = bootstrap_binding(binding, expected.workspace()) && expected.has_expected_revision() &&
                    expected.expected_revision() < INT64_MAX && binding.created_revision() == expected.expected_revision()+1 &&
                    !binding.revoked_revision() && binding.parent_id() == expected.parent_id() &&
                    binding.selector().SerializeAsString() == expected.selector().SerializeAsString() &&
                    binding.authorizer().SerializeAsString() == expected.principal().SerializeAsString();
            }
            if (request.has_bootstrap_revoke() && reply.has_binding()) {
                const auto& expected = request.bootstrap_revoke(); const auto& binding = reply.binding();
                valid = bootstrap_binding(binding, expected.workspace()) && expected.has_expected_revision() &&
                    expected.expected_revision() < INT64_MAX && binding.id() == expected.id() && binding.revoked_revision() &&
                    binding.created_revision() <= expected.expected_revision() && binding.revoked_revision() <= expected.expected_revision()+1;
            }
            if (request.has_bootstrap_apply() && reply.has_bootstrap())
                valid = bootstrap_report(reply.bootstrap(), request.bootstrap_apply());
            if (request.has_scoped_authority_check() && reply.has_scoped_authority_check())
                valid = reply.scoped_authority_check().revision() <= INT64_MAX;
            if (request.has_management() && reply.has_management()) {
                const auto& command = request.management(); const auto& result = reply.management();
                valid = result.protocol_version() == 1 && result.request_id() == sequence_ &&
                    (result.has_error() || (command.has_hello() && result.has_hello()) || (command.has_runtime_identities() && result.has_identities()) ||
                     (command.has_fault_arm() && result.has_fault_armed() && result.fault_armed().has_armed() && result.fault_armed().armed()) ||
                     (command.has_git_repo_import() && result.has_repository()) || (command.has_git_worktree_create() && result.has_worktree()) ||
                     (command.has_git_operation() && result.has_git_operation()) || (command.has_git_status() && result.has_git_status()) ||
                     (command.has_git_worktree_remove() && result.has_worktree_removed()) ||
                     ((command.has_workspace_create() || command.has_workspace_get()) && result.has_workspace()) ||
                     (command.has_workspace_member_set() && result.has_workspace_ack()) ||
                     (command.has_process_wait() && result.has_process_wait()));
            }
            if (!valid) throw std::runtime_error("Broker reply has the wrong outcome");
        }
    } catch (...) { fail_channel(); throw; }
    if (reply.has_error()) throw WorldError(reply.error().code(), reply.error().message().c_str());
    return reply;
}
wire::Reply BrokerClient::call(wire::Request request) {
    broker_wire::Request envelope; *envelope.mutable_management() = std::move(request);
    const auto reply = exchange(envelope).management();
    if (reply.has_error()) throw WorldError(reply.error().code(), reply.error().message().c_str());
    return reply;
}
broker_wire::AccessReply BrokerClient::access(const std::string& workspace, const std::string& actor, const authority::v1::ActorRef* principal) {
    broker_wire::Request request; request.mutable_access()->set_workspace(workspace); request.mutable_access()->set_actor(actor);
    if (principal) *request.mutable_access()->mutable_principal() = *principal;
    return exchange(request).access();
}
broker_wire::ScopedAuthorityCheckReply BrokerClient::scoped_authority_check(const broker_wire::ScopedAuthorityCheckRequest& command) {
    broker_wire::Request request; *request.mutable_scoped_authority_check() = command;
    return exchange(request).scoped_authority_check();
}
broker_wire::ApplicationAccessReply BrokerClient::application_access(const broker_wire::ApplicationAccessRequest& command) {
    broker_wire::Request request; *request.mutable_application_access() = command;
    return exchange(request).application_access();
}
broker_wire::ApplicationControlLease BrokerClient::control_acquire(const broker_wire::ApplicationControlRequest& command) {
    broker_wire::Request request; *request.mutable_control_acquire() = command; return exchange(request).control_lease();
}
broker_wire::ApplicationControlReleased BrokerClient::control_release(const broker_wire::ApplicationControlRelease& command) {
    broker_wire::Request request; *request.mutable_control_release() = command; return exchange(request).control_released();
}
std::vector<broker_wire::ApplicationControlLease> BrokerClient::poll_control() {
    try { pump(); } catch (...) { fail_channel(); throw; }
    auto result = std::move(revocations_); revocations_.clear(); return result;
}
bool BrokerClient::control_active(const broker_wire::ApplicationControlLease& lease) const {
    const auto found = controls_.find(lease.id());
    return channel_ >= 0 && found != controls_.end() && !found->second.revoked &&
        found->second.lease.SerializeAsString() == lease.SerializeAsString();
}
wire::ProcessLaunchReply BrokerClient::launch(const std::string& workspace, const std::string& actor, const wire::ProcessLaunchRequest& command,
                                            const authority::v1::ActorRef* principal) {
    broker_wire::Request request; auto* launch = request.mutable_launch();
    launch->set_workspace(workspace); launch->set_actor(actor); *launch->mutable_command() = command;
    if (principal) *launch->mutable_principal() = *principal;
    return exchange(request).launched();
}
authority::v1::GrantPage BrokerClient::grants(const broker_wire::GrantListRequest& command) {
    broker_wire::Request request; *request.mutable_grants() = command; return exchange(request).grants();
}
authority::v1::GrantRecord BrokerClient::delegate(const broker_wire::GrantDelegateRequest& command) {
    broker_wire::Request request; *request.mutable_delegate() = command; return exchange(request).grant();
}
authority::v1::GrantRecord BrokerClient::revoke(const broker_wire::GrantRevokeRequest& command) {
    broker_wire::Request request; *request.mutable_revoke() = command; return exchange(request).grant();
}
authority::v1::BootstrapBinding BrokerClient::bind_bootstrap(const broker_wire::BootstrapBindRequest& command) {
    broker_wire::Request request; *request.mutable_bootstrap_bind() = command; return exchange(request).binding();
}
authority::v1::BootstrapBinding BrokerClient::revoke_bootstrap(const broker_wire::BootstrapRevokeRequest& command) {
    broker_wire::Request request; *request.mutable_bootstrap_revoke() = command; return exchange(request).binding();
}
authority::v1::BootstrapReport BrokerClient::apply_bootstrap(const broker_wire::BootstrapApplyRequest& command) {
    broker_wire::Request request; *request.mutable_bootstrap_apply() = command; return exchange(request).bootstrap();
}
}
