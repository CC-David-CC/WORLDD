#include "supervisor/session_broker.hpp"
#include "supervisor/session_launch.hpp"
#include "supervisor/launch.hpp"
#include "supervisor/control_admin.hpp"
#include "platform/broker.hpp"
#include "identity/tls_support.hpp"
#include <sys/socket.h>

namespace spatial::supervisor {
namespace {
using File = identity::tls_detail::Fd;
void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
struct Pair {
    File parent, child;
    Pair() {
        int pair[2]; require(socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, pair) == 0, "Cannot create session root channel");
        parent.reset(pair[0]); child.reset(pair[1]);
    }
};
}
struct SessionBroker::Impl {
    pid_t owner = getpid();
    std::unique_ptr<SessionLaunch> launch;
    ProcessHandle broker;
    std::unique_ptr<BrokerClient> root;
    std::unique_ptr<ControlAdmin> administrative;
    std::filesystem::path journal_directory;
    v1::AuthorityLaunchAuthorize authorization;
    std::optional<v1::AuthorityLaunchRecord> cleanup;
    std::optional<identity::v1::AuthorityRetirementState> retirement;
    void live() const { require(owner == getpid() && bool(launch), "Session broker ownership lost"); launch->live(); }
    void start(const std::filesystem::path& executable, const std::filesystem::path& path,
               v1::AuthoritySupervisorConfig config, int native) {
        validate_message(config);
        journal_directory = config.journal_directory(); authorization = config.authorization();
        require(config.has_broker_native_target_fd() && !config.has_compositor_native_target_fd() &&
            config.inherited_source_fds_size() == 0 && config.launch().inherited_fds_size() == 0,
            "Session broker requires an unmapped supervised broker configuration");
        for (const auto& arg : config.launch().arguments())
            require(arg != "--control-supervisor-fd" && arg != "--control-supervisor-pid", "Session owns broker administrative arguments");
        int target = 255;
        while (target == static_cast<int>(config.participant_target_fd()) || target == static_cast<int>(config.broker_native_target_fd()) ||
               (config.has_broker_resource_target_fd() && target == static_cast<int>(config.broker_resource_target_fd())) ||
               (config.has_broker_storage_target_fd() && target == static_cast<int>(config.broker_storage_target_fd()))) --target;
        int pair[2]; require(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, pair) == 0, "Cannot create session administrative channel");
        File admin_parent(pair[0]), admin_child(pair[1]); Pair channel;
        config.add_inherited_source_fds(admin_child.value); config.mutable_launch()->add_inherited_fds(target);
        config.mutable_launch()->add_arguments("--control-supervisor-fd"); config.mutable_launch()->add_arguments(std::to_string(target));
        config.mutable_launch()->add_arguments("--control-supervisor-pid"); config.mutable_launch()->add_arguments(std::to_string(owner));
        launch = std::make_unique<SessionLaunch>(executable, path, config, native,
            std::array<int, 3>{channel.child.value, channel.child.value, STDERR_FILENO}, true);
        admin_child.reset(); channel.child.reset();
        broker = launch->broker_handle();
        root = std::make_unique<BrokerClient>(std::exchange(channel.parent.value, -1), broker.pid(), broker.descriptor());
        administrative = std::make_unique<ControlAdmin>(std::exchange(admin_parent.value, -1), broker.pid(), broker.descriptor());
    }
};
SessionBroker::SessionBroker(const std::filesystem::path& executable, const std::filesystem::path& path,
    v1::AuthoritySupervisorConfig config, int native) : impl_(std::make_unique<Impl>()) { impl_->start(executable, path, std::move(config), native); }
SessionBroker::~SessionBroker() = default;
BrokerClient& SessionBroker::root() { impl_->live(); return *impl_->root; }
ControlAdmin& SessionBroker::administrative() { impl_->live(); return *impl_->administrative; }
const v1::AuthorityLaunchRecord& SessionBroker::launch() const { impl_->live(); return impl_->launch->record(); }
pid_t SessionBroker::supervisor_pid() const { impl_->live(); return impl_->launch->supervisor_pid(); }
pid_t SessionBroker::broker_pid() const { impl_->live(); return impl_->broker.pid(); }
void SessionBroker::check_alive() const { impl_->live(); impl_->root->check_alive(); }
v1::AuthorityLaunchRecord SessionBroker::stop() {
    require(impl_->owner == getpid() && impl_->launch, "Session broker cleanup ownership lost");
    auto result = impl_->launch->stop();
    require(!impl_->cleanup || impl_->cleanup->SerializeAsString() == result.SerializeAsString(),
        "Broker cleanup differs from retained receipt");
    impl_->cleanup = result;
    impl_->administrative.reset(); impl_->root.reset(); return result;
}
identity::v1::AuthorityRetirementState SessionBroker::retire() {
    auto& state = *impl_;
    require(state.owner == getpid() && state.cleanup, "Broker retirement requires retained checked cleanup");
    AuthorityLaunch journal(state.journal_directory);
    auto result = journal.retire_broker_cleanup(*state.cleanup, state.authorization,
        state.launch->checked_failure(*state.cleanup));
    require(!state.retirement || state.retirement->SerializeAsString() == result.SerializeAsString(),
        "Saved broker retirement differs from retained issuer result");
    state.retirement = result;
    return result;
}
const v1::AuthorityLaunchRecord& SessionBroker::cleanup() const {
    require(impl_->owner == getpid() && impl_->cleanup, "Broker dependency recovery requires retained checked cleanup");
    return *impl_->cleanup;
}
}
