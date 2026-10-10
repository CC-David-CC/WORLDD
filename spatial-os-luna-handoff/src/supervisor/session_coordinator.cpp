#include "supervisor/session_coordinator.hpp"
#include "supervisor/session_broker.hpp"
#include "supervisor/session_launch.hpp"
#include "supervisor/control_admin.hpp"
#include "platform/broker.hpp"
#include "identity/participant_stream.hpp"
#include "identity/tls_support.hpp"
#include <map>
#include <iostream>
#include <sys/socket.h>

namespace spatial::supervisor {
namespace {
using File = identity::tls_detail::Fd;
using Clock = std::chrono::steady_clock;
void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
struct Pair {
    File parent, child;
    Pair() {
        int pair[2]; require(socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, pair) == 0, "Cannot create world session channel");
        parent.reset(pair[0]); child.reset(pair[1]);
    }
};
struct World {
    std::unique_ptr<SessionLaunch> supervisor;
    std::unique_ptr<identity::ParticipantStream> management;
    workspace::v1::ControlOwner owner;
    v1::AuthorityLaunchRecord record;
    bool registered = false, stopped = false, failed = false;
    std::uint64_t sequence = 0;
};
}
struct SessionCoordinator::Impl {
    const pid_t creator = getpid();
    std::filesystem::path executable;
    SessionBroker broker;
    // Reverse destruction closes world scopes before broker root ownership.
    std::map<std::string, std::unique_ptr<World>> worlds;
    std::uint64_t sequence = 0;
    Impl(const std::filesystem::path& executable, const std::filesystem::path& path,
        v1::AuthoritySupervisorConfig config, int native)
        : executable(executable), broker(executable, path, std::move(config), native) {}
    void live() const { require(getpid() == creator, "Session coordinator ownership lost"); broker.check_alive(); }
    World& find(const workspace::v1::ControlOwner& owner) const {
        live(); validate_message(owner);
        auto found = worlds.find(owner.launch().service_incarnation());
        require(found != worlds.end() && found->second->registered &&
            found->second->owner.SerializeAsString() == owner.SerializeAsString(), "Control owner is not a retained session world");
        return *found->second;
    }
    workspace::v1::ControlOwnerState administrative(const workspace::v1::ControlOwner& owner, int endpoint = -1, bool fence = false) {
        live(); require(sequence != UINT64_MAX, "Session administrative request sequence exhausted");
        workspace::v1::ControlSupervisorRequest request;
        request.set_protocol_version(1); request.set_request_id(++sequence);
        if (endpoint >= 3) *request.mutable_register_() = owner;
        else if (fence) *request.mutable_fence() = owner;
        else *request.mutable_inspect() = owner;
        const auto reply = broker.administrative().exchange(request, endpoint);
        require(reply.has_state(), reply.has_error() ? reply.error().message().c_str() : "Missing administrative state");
        return reply.state(); // ControlAdmin already validated the complete reply against this exact request.
    }
};
SessionCoordinator::SessionCoordinator(const std::filesystem::path& executable, const std::filesystem::path& path,
    v1::AuthoritySupervisorConfig config, int native)
    : impl_(std::make_unique<Impl>(executable, path, std::move(config), native)) {}
SessionCoordinator::~SessionCoordinator() = default;
workspace::v1::ControlOwner SessionCoordinator::start_world(const std::filesystem::path& path,
    v1::AuthoritySupervisorConfig config, int native) {
    auto& state = *impl_; state.live(); validate_message(config);
    require(config.has_compositor_native_target_fd() && !config.has_broker_native_target_fd() &&
        config.inherited_source_fds_size() == 0 && config.launch().inherited_fds_size() == 0,
        "Session world requires an unmapped compositor launch configuration");
    require(config.binding().issuer_id() == state.broker.launch().binding().issuer_id(), "World issuer differs from the configured session issuer");
    const auto key = config.binding().service_incarnation();
    require(key.size() == 32 && !state.worlds.contains(key) && state.worlds.size() < 256, "Duplicate or excessive session world launch");
    for (const auto& arg : config.launch().arguments())
        require(arg != "--workspace-fd" && arg != "--workspace-pid" && arg != "--control-supervisor-fd" && arg != "--control-supervisor-pid",
            "Session owns world broker endpoint arguments");
    int target = 255;
    while (target == static_cast<int>(config.participant_target_fd()) || target == static_cast<int>(config.compositor_native_target_fd())) --target;
    Pair endpoint, channel;
    config.add_inherited_source_fds(endpoint.child.value); config.mutable_launch()->add_inherited_fds(target);
    config.mutable_launch()->add_arguments("--workspace-fd"); config.mutable_launch()->add_arguments(std::to_string(target));
    config.mutable_launch()->add_arguments("--workspace-pid"); config.mutable_launch()->add_arguments(std::to_string(state.broker.broker_pid()));
    auto [position, inserted] = state.worlds.emplace(key, std::make_unique<World>());
    require(inserted, "Session world insertion failed");
    auto& world = *position->second;
    try {
        world.supervisor = std::make_unique<SessionLaunch>(state.executable, path, config, native,
            std::array<int, 3>{channel.child.value, channel.child.value, STDERR_FILENO}, false);
        endpoint.child.reset(); channel.child.reset();
        world.record = world.supervisor->record();
        require(world.record.native().server_id() == state.broker.launch().native().server_id(), "World and broker have different native server identities");
        world.owner = world.supervisor->control_owner();
        world.management = std::make_unique<identity::ParticipantStream>(std::exchange(channel.parent.value, -1), state.creator);
        state.administrative(world.owner, endpoint.parent.value);
        endpoint.parent.reset();
        world.registered = true;
        return world.owner;
    } catch (...) {
        world.failed = true;
        // Preserve the exact launch entry. An uncertain registration must not be replaced with a new identity.
        if (world.owner.has_launch()) {
            try { state.administrative(world.owner, -1, true); }
            catch (const std::exception& error) { std::cerr << "World registration fence unresolved: " << error.what() << '\n'; }
        }
        if (world.supervisor) {
            try { world.record = world.supervisor->stop(); world.stopped = true; }
            catch (const std::exception& error) { std::cerr << "World registration cleanup unresolved: " << error.what() << '\n'; }
        }
        world.management.reset();
        throw;
    }
}
const v1::AuthorityLaunchRecord& SessionCoordinator::world_launch(const workspace::v1::ControlOwner& owner) const {
    return impl_->find(owner).record;
}
workspace::v1::ControlOwnerState SessionCoordinator::inspect_world(const workspace::v1::ControlOwner& owner) {
    impl_->find(owner); return impl_->administrative(owner);
}
core::v1::Reply SessionCoordinator::world_call(const workspace::v1::ControlOwner& owner, const core::v1::Request& supplied) {
    auto& state = *impl_; auto& world = state.find(owner);
    require(!world.failed && !world.stopped && world.management, "Session world is not available");
    validate_message(supplied); require(supplied.protocol_version() == 1, "Unsupported world request version");
    require(world.sequence != UINT64_MAX, "World request sequence exhausted");
    auto request = supplied; request.set_request_id(++world.sequence);
    const auto deadline = Clock::now() + std::chrono::seconds(10);
    try {
        world.supervisor->live(); world.management->send(request.SerializeAsString());
        for (;;) {
            state.live(); require(Clock::now() < deadline, "Session world response timed out");
            if (auto bytes = world.management->receive()) {
                core::v1::Reply reply; require(reply.ParseFromString(*bytes), "Malformed world reply"); validate_message(reply);
                require(reply.protocol_version() == 1 && reply.request_id() == request.request_id(), "World reply correlation failed");
                reply.set_request_id(supplied.request_id()); return reply;
            }
            world.management->flush(); world.supervisor->live(); poll(nullptr, 0, 2);
        }
    } catch (...) {
        world.failed = true;
        try { state.administrative(world.owner, -1, true); }
        catch (const std::exception& error) { std::cerr << "Failed world fence unresolved: " << error.what() << '\n'; }
        throw;
    }
}
v1::AuthorityLaunchRecord SessionCoordinator::stop_world(const workspace::v1::ControlOwner& owner) {
    auto& state = *impl_; auto& world = state.find(owner);
    require(!world.stopped, "Session world already stopped");
    // Stop its input path before native/process cleanup. Reservations remain pending separate selective recovery.
    state.administrative(owner, -1, true);
    world.record = world.supervisor->stop(); world.stopped = true; world.management.reset();
    return world.record;
}
BrokerClient& SessionCoordinator::root() { impl_->live(); return impl_->broker.root(); }
const v1::AuthorityLaunchRecord& SessionCoordinator::broker_launch() const { impl_->live(); return impl_->broker.launch(); }
v1::AuthorityLaunchRecord SessionCoordinator::stop() {
    auto& state = *impl_; state.live();
    for (const auto& [key, world] : state.worlds) {
        (void)key; require(world->stopped || !world->supervisor, "Stop retained world scopes before the session broker");
    }
    return state.broker.stop();
}
}
