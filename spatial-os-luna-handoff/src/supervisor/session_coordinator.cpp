#include "supervisor/session_coordinator.hpp"
#include "supervisor/session_broker.hpp"
#include "supervisor/session_launch.hpp"
#include "supervisor/launch.hpp"
#include "supervisor/control_admin.hpp"
#include "platform/broker.hpp"
#include "platform/presentation_owner.hpp"
#include "identity/participant_stream.hpp"
#include "identity/tls_support.hpp"
#include <map>
#include <iostream>
#include <vector>
#include <fcntl.h>
#include <netinet/in.h>
#include <sys/socket.h>

namespace spatial::supervisor {
namespace {
using File = identity::tls_detail::Fd;
using Clock = std::chrono::steady_clock;
void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
int retain_actor_listener(int borrowed) {
    require(borrowed >= 3, "Actor listener must be above stderr");
    File retained(fcntl(borrowed, F_DUPFD_CLOEXEC, 3));
    require(retained.value >= 3, "Cannot retain actor listener");
    sockaddr_storage address{}; socklen_t size = sizeof(address);
    int accepting = 0, type = 0, protocol = 0; socklen_t option = sizeof(int);
    require(getsockname(retained.value, reinterpret_cast<sockaddr*>(&address), &size) == 0 &&
        (address.ss_family == AF_INET || address.ss_family == AF_INET6) &&
        getsockopt(retained.value, SOL_SOCKET, SO_ACCEPTCONN, &accepting, &option) == 0 && accepting &&
        getsockopt(retained.value, SOL_SOCKET, SO_TYPE, &type, &option) == 0 && type == SOCK_STREAM &&
        getsockopt(retained.value, SOL_SOCKET, SO_PROTOCOL, &protocol, &option) == 0 && protocol == IPPROTO_TCP,
        "Session actor transport requires a listening TCP socket");
    // Private coordinator endpoints are UNIX sockets. No duplicate of one can pass this check.
    return std::exchange(retained.value, -1);
}
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
    v1::AuthorityLaunchAuthorize authorization;
    std::optional<v1::AuthorityDependentCleanup> dependent;
    std::optional<identity::v1::AuthorityRetirementState> retirement;
    std::filesystem::path journal_directory;
    std::vector<std::unique_ptr<File>> actors;
    render::v1::WorldPresentationBinding presentation;
    bool presentation_attached=false,presentation_retired=false,world_identity_checked=false;
    bool registered = false, stopped = false, failed = false;
    std::uint64_t sequence = 0;
};
}
struct SessionCoordinator::Impl {
    const pid_t creator = getpid();
    std::filesystem::path executable;
    SessionBroker broker;
    std::unique_ptr<PresentationOwner> presentation;
    std::uint64_t presentation_generation=0;
    // Reverse destruction closes world scopes before broker root ownership.
    std::map<std::string, std::unique_ptr<World>> worlds;
    std::uint64_t sequence = 0;
    Impl(const std::filesystem::path& executable, const std::filesystem::path& path,
        v1::AuthoritySupervisorConfig config, int native)
        : executable(executable), broker(executable, path, std::move(config), native) {}
    void owned() const { require(getpid() == creator, "Session coordinator ownership lost"); }
    void live() const { owned(); broker.check_alive(); }
    World& find_owned(const workspace::v1::ControlOwner& owner) const {
        owned(); validate_message(owner);
        auto found = worlds.find(owner.launch().service_incarnation());
        require(found != worlds.end() && found->second->registered &&
            found->second->owner.SerializeAsString() == owner.SerializeAsString(), "Control owner is not a retained session world");
        return *found->second;
    }
    World& find(const workspace::v1::ControlOwner& owner) const { live(); return find_owned(owner); }
    workspace::v1::ControlOwnerState administrative(const workspace::v1::ControlOwner& owner, int endpoint = -1,
        bool fence = false, const v1::AuthorityLaunchRecord* cleanup = nullptr) {
        live(); require(sequence != UINT64_MAX, "Session administrative request sequence exhausted");
        workspace::v1::ControlSupervisorRequest request;
        request.set_protocol_version(1); request.set_request_id(++sequence);
        if (cleanup) {
            *request.mutable_recover()->mutable_owner() = owner;
            *request.mutable_recover()->mutable_process_stopped() = cleanup->process_stopped();
            *request.mutable_recover()->mutable_native_stopped() = cleanup->native_stopped();
        }
        else if (endpoint >= 3) *request.mutable_register_() = owner;
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
void SessionCoordinator::start_presentation(const std::filesystem::path& executable,const std::filesystem::path& path,int native){
    auto& state=*impl_;state.live();
    require(!state.presentation && state.worlds.empty(),"Start presentation before any world launches");
    state.presentation=std::make_unique<PresentationOwner>(executable,path,native);
}
render::v1::PresentationState SessionCoordinator::select_world(const workspace::v1::ControlOwner& owner){
    auto& state=*impl_;auto& world=state.find(owner);
    require(state.presentation && world.presentation_attached && world.world_identity_checked && !world.stopped && !world.failed,
        "World presentation is not available");world.supervisor->live();
    return state.presentation->select(world.presentation);
}
render::v1::PresentationState SessionCoordinator::inspect_presentation(const workspace::v1::ControlOwner& owner){
    auto& state=*impl_;auto& world=state.find_owned(owner);
    require(state.presentation && world.presentation_attached && !world.presentation_retired,"No retained world presentation");
    return state.presentation->inspect(world.presentation);
}
void SessionCoordinator::recover_presentation(const workspace::v1::ControlOwner& owner){
    auto& state=*impl_;auto& world=state.find_owned(owner);
    require(state.presentation && world.presentation_attached && world.stopped,"Presentation recovery requires stopped retained world ownership");
    if(world.presentation_retired)return;
    const auto checked=world.supervisor->stop();
    require(checked.SerializeAsString()==world.record.SerializeAsString() && checked.stage()==v1::OWNED_SCOPES_STOPPED &&
        checked.has_process_stopped() && checked.has_native_stopped(),"Presentation recovery requires checked supervisor receipts");
    state.presentation->recover(world.presentation,checked.process_stopped(),checked.native_stopped());
    const auto retired=state.presentation->detach(world.presentation);
    require(retired.stage()==render::v1::RETIRED,"Presentation retirement remains unconfirmed");world.presentation_retired=true;
}
void SessionCoordinator::stop_presentation(){
    auto& state=*impl_;state.owned();require(bool(state.presentation),"No host presentation owner");
    for(const auto& [key,world]:state.worlds){
        (void)key;require(!world->presentation_attached || world->presentation_retired,"World presentation cleanup remains unconfirmed");
    }
    state.presentation->shutdown();state.presentation.reset();
}
workspace::v1::ControlOwner SessionCoordinator::start_world(const std::filesystem::path& path,
    v1::AuthoritySupervisorConfig config, int native, int actor_listener, unsigned local_actor_channels,const std::string& world_id) {
    auto& state = *impl_; state.live(); validate_message(config);
    require(config.has_compositor_native_target_fd() && !config.has_broker_native_target_fd() &&
        config.inherited_source_fds_size() == 0 && config.launch().inherited_fds_size() == 0,
        "Session world requires an unmapped compositor launch configuration");
    require(config.binding().issuer_id() == state.broker.launch().binding().issuer_id(), "World issuer differs from the configured session issuer");
    require(bool(state.presentation)==!world_id.empty(),"Shared presentation requires the explicit world identity");
    require(world_id.size()<=256 && world_id.find('\0')==std::string::npos,"Invalid presentation world identity");
    const auto key = config.binding().service_incarnation();
    require(key.size() == 32 && !state.worlds.contains(key) && state.worlds.size() < 256, "Duplicate or excessive session world launch");
    for (const auto& arg : config.launch().arguments())
        require(arg != "--workspace-fd" && arg != "--workspace-pid" && arg != "--control-supervisor-fd" && arg != "--control-supervisor-pid" && arg != "--presentation-fd" && arg != "--presentation-pidfd" &&
            arg != "--presentation-pid" && arg != "--presentation-owner-pid",
            "Session owns world broker endpoint arguments");
    unsigned identity_configs = 0, tls_configs = 0;
    for (int i = 0; i < config.launch().arguments_size(); ++i) {
        const auto& arg = config.launch().arguments(i);
        require(arg != "--actor-fd" && arg != "--actor-listen-fd", "Session owns world actor endpoint arguments");
        if (arg == "--identity-config" || arg == "--actor-tls-config") {
            require(i + 1 < config.launch().arguments_size() &&
                std::filesystem::path(config.launch().arguments(i + 1)).is_absolute(), "Actor configuration requires an absolute path");
            if (arg == "--identity-config") ++identity_configs; else ++tls_configs;
        }
    }
    require(local_actor_channels <= 32, "Excessive local actor channels");
    require(identity_configs == ((actor_listener != -1 || local_actor_channels) ? 1U : 0U),
        "Actor transport and identity configuration are required together");
    require(tls_configs == (actor_listener != -1 ? 1U : 0U),
        "Actor listener, identity configuration, and TLS configuration are required together");
    File listener;
    if (actor_listener != -1) listener.reset(retain_actor_listener(actor_listener));
    int target = 255;
    while (target == static_cast<int>(config.participant_target_fd()) || target == static_cast<int>(config.compositor_native_target_fd())) --target;
    const auto next_target = [&] {
        do { --target; } while (target == static_cast<int>(config.participant_target_fd()) ||
            target == static_cast<int>(config.compositor_native_target_fd()));
        return target;
    };
    Pair endpoint, channel;
    std::unique_ptr<Pair> presentation_channel;
    if(state.presentation){
        presentation_channel=std::make_unique<Pair>();int enabled=1;
        require(setsockopt(presentation_channel->parent.value,SOL_SOCKET,SO_PASSCRED,&enabled,sizeof(enabled))==0 &&
            setsockopt(presentation_channel->child.value,SOL_SOCKET,SO_PASSCRED,&enabled,sizeof(enabled))==0,
            "Cannot authenticate world presentation channel");
    }
    config.add_inherited_source_fds(endpoint.child.value); config.mutable_launch()->add_inherited_fds(target);
    config.mutable_launch()->add_arguments("--workspace-fd"); config.mutable_launch()->add_arguments(std::to_string(target));
    config.mutable_launch()->add_arguments("--workspace-pid"); config.mutable_launch()->add_arguments(std::to_string(state.broker.broker_pid()));
    if (listener.value >= 3) {
        config.add_inherited_source_fds(listener.value); config.mutable_launch()->add_inherited_fds(next_target());
        config.mutable_launch()->add_arguments("--actor-listen-fd"); config.mutable_launch()->add_arguments(std::to_string(target));
    }
    if(state.presentation){
        config.add_inherited_source_fds(presentation_channel->child.value);config.mutable_launch()->add_inherited_fds(next_target());
        config.mutable_launch()->add_arguments("--presentation-fd");config.mutable_launch()->add_arguments(std::to_string(target));
        config.add_inherited_source_fds(state.presentation->pidfd());config.mutable_launch()->add_inherited_fds(next_target());
        config.mutable_launch()->add_arguments("--presentation-pidfd");config.mutable_launch()->add_arguments(std::to_string(target));
        config.mutable_launch()->add_arguments("--presentation-pid");config.mutable_launch()->add_arguments(std::to_string(state.presentation->pid()));
        config.mutable_launch()->add_arguments("--presentation-owner-pid");config.mutable_launch()->add_arguments(std::to_string(state.creator));
    }
    std::vector<std::unique_ptr<Pair>> actors;
    for (unsigned index = 0; index < local_actor_channels; ++index) {
        auto actor = std::make_unique<Pair>();
        config.add_inherited_source_fds(actor->child.value); config.mutable_launch()->add_inherited_fds(next_target());
        config.mutable_launch()->add_arguments("--actor-fd"); config.mutable_launch()->add_arguments(std::to_string(target));
        actors.push_back(std::move(actor));
    }
    auto [position, inserted] = state.worlds.emplace(key, std::make_unique<World>());
    require(inserted, "Session world insertion failed");
    auto& world = *position->second;
    world.journal_directory = config.journal_directory();
    world.authorization = config.authorization();
    try {
        world.supervisor = std::make_unique<SessionLaunch>(state.executable, path, config, native,
            std::array<int, 3>{channel.child.value, channel.child.value, STDERR_FILENO}, bool(state.presentation));
        endpoint.child.reset(); channel.child.reset(); listener.reset();
        world.actors.reserve(actors.size());
        for (auto& actor : actors) {
            auto client = std::make_unique<File>();
            client->reset(std::exchange(actor->parent.value, -1));
            actor->child.reset();
            world.actors.push_back(std::move(client));
        }
        world.record = world.supervisor->record();
        require(world.record.native().server_id() == state.broker.launch().native().server_id(), "World and broker have different native server identities");
        world.owner = world.supervisor->control_owner();
        if(state.presentation){
            require(state.presentation_generation!=UINT64_MAX,"Presentation generation exhausted");
            auto& binding=world.presentation;binding.set_protocol_version(1);binding.set_world_id(world_id);
            binding.set_service_incarnation(world.owner.launch().service_incarnation());
            binding.set_authority_generation(world.owner.launch().generation());binding.set_presentation_generation(++state.presentation_generation);
            auto process=world.supervisor->world_handle();
            world.presentation_attached=true; // Retain possible attachment even if its reply is lost.
            const auto attached=state.presentation->attach(binding,presentation_channel->parent.value,process,world.owner,world.record.process());
            require(attached.stage()==render::v1::ATTACHED,"Unexpected world attachment state");
            presentation_channel.reset();
        }
        world.management = std::make_unique<identity::ParticipantStream>(std::exchange(channel.parent.value, -1), state.creator);
        state.administrative(world.owner, endpoint.parent.value);
        endpoint.parent.reset();
        world.registered = true;
        if(state.presentation){
            core::v1::Request request;request.set_protocol_version(1);request.mutable_world_info();
            const auto reply=world_call(world.owner,request);
            require(reply.has_world_info() && reply.world_info().world_id()==world_id,"Presented world identity differs from launch request");
            world.world_identity_checked=true;
        }
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
        world.actors.clear();
        throw;
    }
}
int SessionCoordinator::take_actor_channel(const workspace::v1::ControlOwner& owner, unsigned index) {
    auto& world = impl_->find(owner);
    require(!world.failed && !world.stopped, "Session world is not available");
    world.supervisor->live();
    require(index < world.actors.size() && world.actors[index]->value >= 3, "Actor channel is unavailable or already transferred");
    return std::exchange(world.actors[index]->value, -1);
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
    auto& state = *impl_; auto& world = state.find_owned(owner);
    require(!world.stopped, "Session world already stopped");
    // Stop its input path before native/process cleanup. Reservations remain pending separate selective recovery.
    try { state.administrative(owner, -1, true); }
    catch (const std::exception& error) {
        std::cerr << "World broker fence unconfirmed; stopping owned scopes: " << error.what() << '\n';
    }
    world.record = world.supervisor->stop(); world.stopped = true; world.management.reset(); world.actors.clear();
    if(world.presentation_attached)recover_presentation(owner);
    return world.record;
}
BrokerClient& SessionCoordinator::root() { impl_->live(); return impl_->broker.root(); }
workspace::v1::ControlOwnerState SessionCoordinator::recover_world(const workspace::v1::ControlOwner& owner) {
    auto& state = *impl_; auto& world = state.find(owner);
    // Stopped state requires SessionLaunch to validate both owned cleanup receipts.
    require(world.stopped && world.record.stage() == v1::OWNED_SCOPES_STOPPED,
        "Control recovery requires checked world cleanup");
    const auto recovered = state.administrative(owner, -1, false, &world.record);
    // Broker release can precede a persistence error. Exact retry uses its retained receipt.
    AuthorityLaunch journal(world.journal_directory);
    world.dependent = journal.record_control_recovery(world.record, state.broker.launch(), recovered);
    return recovered;
}
identity::v1::AuthorityRetirementState SessionCoordinator::retire_world(const workspace::v1::ControlOwner& owner) {
    auto& world = impl_->find_owned(owner);
    require(world.stopped && world.record.stage() == v1::OWNED_SCOPES_STOPPED && world.dependent,
        "Retirement requires retained durable world cleanup");
    AuthorityLaunch journal(world.journal_directory);
    auto result = journal.retire_control_recovery(*world.dependent, world.authorization,
        world.supervisor->checked_failure(world.record));
    require(!world.retirement || world.retirement->SerializeAsString() == result.SerializeAsString(),
        "Saved retirement differs from retained issuer result");
    world.retirement = result;
    return result;
}
v1::AuthorityDependentCleanup SessionCoordinator::recover_world_after_broker_stop(const workspace::v1::ControlOwner& owner) {
    auto& state = *impl_; auto& world = state.find_owned(owner);
    require(world.stopped && world.record.stage() == v1::OWNED_SCOPES_STOPPED,
        "Dependency recovery requires checked world cleanup");
    const auto& broker = state.broker.cleanup();
    AuthorityLaunch journal(world.journal_directory);
    world.dependent = journal.record_stopped_broker_recovery(world.record, broker);
    return *world.dependent;
}
const v1::AuthorityLaunchRecord& SessionCoordinator::broker_launch() const { impl_->live(); return impl_->broker.launch(); }
v1::AuthorityLaunchRecord SessionCoordinator::stop_broker() { impl_->owned(); return impl_->broker.stop(); }
identity::v1::AuthorityRetirementState SessionCoordinator::retire_broker() { impl_->owned(); return impl_->broker.retire(); }
v1::AuthorityLaunchRecord SessionCoordinator::stop() {
    auto& state = *impl_; state.owned();
    for (const auto& [key, world] : state.worlds) {
        (void)key; require(world->stopped || !world->supervisor, "Stop retained world scopes before the session broker");
    }
    return state.broker.stop();
}
}
