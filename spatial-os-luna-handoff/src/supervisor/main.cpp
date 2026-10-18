#include "supervisor/launch.hpp"
#include "supervisor/child_handle.hpp"
#include "supervisor/storage_launch.hpp"
#include "identity/participant_stream.hpp"
#include "identity/tls_support.hpp"
#include "workspace/resources.hpp"
#include "workspace/broker_copy_cleanup.hpp"
#include "supervisor/broker_cleanup_bootstrap.hpp"
#include "broker.pb.h"
#include "compositor_native.pb.h"
#include <google/protobuf/text_format.h>
#include <charconv>
#include <chrono>
#include <climits>
#include <csignal>
#include <iostream>
#include <optional>
#include <poll.h>
#include <set>
#include <sys/random.h>
#include <sys/stat.h>
#include <sys/mman.h>
#include <sys/socket.h>

namespace {
namespace pb = spatial::supervisor::v1;
using Clock = std::chrono::steady_clock;
using File = spatial::identity::tls_detail::Fd;
volatile std::sig_atomic_t interrupted = 0;
void terminate(int signal) { interrupted = signal; }
void require(bool okay, const char* message) {
    if (!okay) throw std::runtime_error(message);
}
void tick() { poll(nullptr, 0, 5); }
void parent_alive(int fd) {
    pollfd event{fd, static_cast<short>(POLLIN | POLLRDHUP), 0};
    int result;
    do { result = poll(&event, 1, 0); } while (result < 0 && errno == EINTR);
    require(result >= 0 && !(event.revents & (POLLERR | POLLHUP | POLLRDHUP | POLLNVAL)),
        "Supervisor management parent disconnected");
}
int descriptor(const char* text) {
    int fd = -1;
    const std::string value(text);
    const auto result = std::from_chars(value.data(), value.data()+value.size(), fd);
    require(result.ec == std::errc{} && result.ptr == value.data()+value.size() && fd >= 3 &&
        fcntl(fd, F_GETFD) >= 0, "Expected an open inherited descriptor");
    return fd;
}
bool absolute(const std::string& text) {
    return !text.empty() && text.find('\0') == std::string::npos && std::filesystem::path(text).is_absolute();
}
bool cleanup_bootstrap_required(const pb::AuthoritySupervisorConfig& config) {
    return config.has_broker_resources() && config.has_broker_native_target_fd();
}
std::filesystem::path configured_broker_state(const pb::AuthoritySupervisorConfig& config) {
    std::filesystem::path state; unsigned found=0;
    for (int i=0;i<config.launch().arguments_size();++i) {
        const auto& argument=config.launch().arguments(i);
        require(argument!="--cleanup-bootstrap-fd","Supervisor configuration cannot choose the private bootstrap endpoint");
        if(argument=="--state-dir") {
            require(i+1<config.launch().arguments_size() && found==0,"Broker requires one configured state directory");
            state=config.launch().arguments(++i); ++found;
        }
    }
    require(found==1 && absolute(state.string()) && state==state.lexically_normal(),
        "Broker cleanup requires one absolute literal state directory");
    if(config.has_broker_storage_owner())
        require(state==config.broker_storage_owner().volume_root(),"Broker state differs from its storage-owner root");
    return state;
}
pb::AuthoritySupervisorConfig configuration(const char* path, int native, int management, int handle) {
    pb::AuthoritySupervisorConfig value;
    require(google::protobuf::TextFormat::ParseFromString(spatial::identity::tls_detail::load_config(path), &value),
        "Malformed supervisor configuration");
    spatial::validate_message(value);
    require(!value.require_broker_confinement() ||
        (value.has_broker_storage_owner() && value.has_broker_resources() && value.broker_resources().protocol_version() == 2),
        "Production broker supervisor requires aggregate resources and storage ownership");
    for (const auto& arg : value.launch().arguments())
        require(arg != "--cleanup-bootstrap-fd" && arg != "--production" && (!value.require_broker_confinement() || arg != "--fixture-faults"),
                "Supervisor owns the production broker profile");
    require(value.protocol_version() == 1 && value.binding().supervised() &&
        value.has_participant() && value.has_authorization() && value.has_launch() &&
        absolute(value.journal_directory()) && absolute(value.delegated_cgroup()) &&
        absolute(value.child_participant_config()) && absolute(value.launch().executable()) &&
        value.participant_target_fd() >= 3 && value.participant_target_fd() <= 255 &&
        !(value.has_broker_native_target_fd() && value.has_compositor_native_target_fd()) &&
        value.has_broker_resources() == value.has_broker_resource_target_fd() &&
        (!value.has_broker_resources() || value.has_broker_native_target_fd()) &&
        value.has_broker_storage_owner() == value.has_broker_storage_target_fd() &&
        (!value.has_broker_storage_owner() || value.has_broker_resources()) &&
        value.inherited_source_fds_size() + ((value.has_broker_native_target_fd() || value.has_compositor_native_target_fd()) ? 2 : 1) +
            (value.has_broker_resources() ? 1 : 0) + (value.has_broker_storage_owner() ? 1 : 0) +
            (cleanup_bootstrap_required(value) ? 1 : 0) <= 16 &&
        value.inherited_source_fds_size() == value.launch().inherited_fds_size(),
        "Invalid supervisor configuration");
    std::set<std::uint32_t> targets{value.participant_target_fd()}, sources;
    if (value.has_broker_native_target_fd()) {
        const auto target = value.broker_native_target_fd();
        require(target >= 3 && target <= 255 && targets.insert(target).second,
            "Invalid broker native descriptor target");
    }
    if (value.has_compositor_native_target_fd()) {
        const auto target = value.compositor_native_target_fd();
        require(target >= 3 && target <= 255 && targets.insert(target).second,
            "Invalid compositor native descriptor target");
    }
    if (value.has_broker_resources()) {
        spatial::validate_resource_config(value.broker_resources());
        require(value.broker_resources().delegated_cgroup().empty(), "Broker resource template must not select a cgroup");
        const auto target = value.broker_resource_target_fd();
        require(target >= 3 && target <= 255 && targets.insert(target).second, "Invalid broker resource descriptor target");
        for (const auto& arg : value.launch().arguments())
            require(arg != "--resource-config" && arg != "--resource-config-fd", "Supervisor owns broker resource arguments");
    }
    if (value.has_broker_storage_owner()) {
        const auto target = value.broker_storage_target_fd();
        require(target >= 3 && target <= 255 && targets.insert(target).second, "Invalid broker storage descriptor target");
        unsigned states = 0;
        for (int i = 0; i < value.launch().arguments_size(); ++i) {
            const auto& arg = value.launch().arguments(i);
            require(arg != "--storage-fd", "Supervisor owns broker storage arguments");
            if (arg == "--state-dir") {
                require(i+1 < value.launch().arguments_size() &&
                    value.launch().arguments(i+1) == value.broker_storage_owner().volume_root(),
                    "Broker state must match its storage-owner volume");
                ++states;
            }
        }
        require(states == 1, "Broker storage requires exactly one state directory");
    }
    struct stat native_info{}, management_info{}, handle_info{};
    require(fstat(native, &native_info) == 0 && fstat(management, &management_info) == 0,
        "Cannot identify privileged descriptors");
    require(handle < 0 || fstat(handle, &handle_info) == 0, "Cannot identify private handle descriptor");
    for (int i = 0; i < value.inherited_source_fds_size(); ++i) {
        const auto source = value.inherited_source_fds(i), target = value.launch().inherited_fds(i);
        struct stat source_info{};
        require(source >= 3 && source <= INT_MAX && static_cast<int>(source) != native &&
            static_cast<int>(source) != management && static_cast<int>(source) != handle && sources.insert(source).second &&
            fcntl(static_cast<int>(source), F_GETFD) >= 0 &&
            target >= 3 && target <= 255 && targets.insert(target).second,
            "Invalid or privileged child descriptor mapping");
        require(fstat(static_cast<int>(source), &source_info) == 0 &&
            !(source_info.st_dev == native_info.st_dev && source_info.st_ino == native_info.st_ino) &&
            !(source_info.st_dev == management_info.st_dev && source_info.st_ino == management_info.st_ino) &&
            (handle < 0 || !(source_info.st_dev == handle_info.st_dev && source_info.st_ino == handle_info.st_ino)),
            "Child descriptor aliases privileged supervisor input");
    }
    if (value.has_broker_storage_owner())
        spatial::supervisor::validate_storage_launch(value.broker_storage_owner(), value.broker_storage_owner().volume_root());
    return value;
}
pb::NativeScopeConfig handoff_source(int fd, bool compositor) {
    constexpr int seals = F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL;
    struct stat info{};
    const int actual = fcntl(fd, F_GET_SEALS);
    require(actual >= 0 && (actual & seals) == seals && fstat(fd, &info) == 0 &&
        info.st_size > 0 && info.st_size <= 65536, "Expected sealed broker handoff source");
    std::string bytes(static_cast<std::size_t>(info.st_size), '\0');
    require(pread(fd, bytes.data(), bytes.size(), 0) == static_cast<ssize_t>(bytes.size()),
        "Cannot read broker handoff source");
    pb::NativeScopeConfig value;
    require(value.ParseFromString(bytes), "Malformed broker handoff source");
    spatial::validate_message(value);
    require(value.role() == (compositor ? spatial::security::v1::COMPOSITOR : spatial::security::v1::BROKER),
        compositor ? "Compositor handoff requires a compositor native scope" : "Broker handoff requires a broker native scope");
    // AuthorityLaunch::prepare validates all source pins against the live native server.
    return value;
}
void seal_broker_config(File& file, const pb::NativeScopeConfig& source, const pb::NativeScopeReady& ready) {
    require(ready.identity().role() == spatial::security::v1::BROKER &&
        ready.identity().server_id() == source.server_id() &&
        ready.credential().role() == spatial::security::v1::BROKER &&
        ready.credential().id() == ready.identity().credential_id() &&
        ready.credential().cookie().size() == 32 && ready.credential().cookie() != source.supervisor_cookie(),
        "Broker handoff requires the owned issued native credential");
    spatial::workspace::v1::NativeLaunchConfig config;
    config.set_protocol_version(1); config.set_display_number(source.display_number());
    config.set_broker_cookie(ready.credential().cookie()); config.set_server_id(source.server_id());
    config.set_server_pid(source.server_pid()); config.set_server_uid(source.server_uid());
    const auto bytes = config.SerializeAsString();
    file.reset(memfd_create("supervised-broker-native", MFD_CLOEXEC | MFD_ALLOW_SEALING));
    require(file.value >= 0, "Cannot allocate sealed broker configuration");
    std::size_t written = 0;
    while (written < bytes.size()) {
        const auto count = write(file.value, bytes.data()+written, bytes.size()-written);
        if (count < 0 && errno == EINTR) continue;
        require(count > 0, "Cannot write sealed broker configuration");
        written += static_cast<std::size_t>(count);
    }
    require(fcntl(file.value, F_ADD_SEALS, F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL) == 0,
        "Cannot seal broker configuration");
}
void seal_compositor_config(File& file, const pb::NativeScopeConfig& source, const pb::NativeScopeReady& ready) {
    require(ready.identity().role() == spatial::security::v1::COMPOSITOR &&
        ready.identity().server_id() == source.server_id() &&
        ready.credential().role() == spatial::security::v1::COMPOSITOR &&
        ready.credential().id() == ready.identity().credential_id() &&
        ready.credential().cookie().size() == 32 && ready.credential().cookie() != source.supervisor_cookie(),
        "Compositor handoff requires the owned issued native credential");
    spatial::render::v1::NativeCompositorConfig config;
    config.set_protocol_version(1); config.set_display_number(source.display_number());
    config.set_compositor_cookie(ready.credential().cookie()); config.set_server_id(source.server_id());
    config.set_server_pid(source.server_pid()); config.set_server_uid(source.server_uid());
    const auto bytes = config.SerializeAsString();
    file.reset(memfd_create("supervised-compositor-native", MFD_CLOEXEC | MFD_ALLOW_SEALING));
    require(file.value >= 0, "Cannot allocate sealed compositor configuration");
    std::size_t written = 0;
    while (written < bytes.size()) {
        const auto count = write(file.value, bytes.data()+written, bytes.size()-written);
        if (count < 0 && errno == EINTR) continue;
        require(count > 0, "Cannot write sealed compositor configuration");
        written += static_cast<std::size_t>(count);
    }
    require(fcntl(file.value, F_ADD_SEALS, F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL) == 0,
        "Cannot seal compositor configuration");
}
void write_child_config(const std::filesystem::path& path,
                        const spatial::identity::v1::AuthorityParticipantRuntimeConfig& config) {
    require(!path.filename().empty() && path.filename() != "." && path.filename() != "..",
        "Child configuration requires a filename");
    File directory(open(path.parent_path().c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW));
    struct stat info{};
    require(directory.value >= 0 && fstat(directory.value, &info) == 0 &&
        info.st_uid == geteuid() && !(info.st_mode & 0077), "Child configuration requires an owned private directory");
    File file(openat(directory.value, path.filename().c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600));
    require(file.value >= 0, "Child configuration already exists or cannot be created");
    std::string bytes;
    require(google::protobuf::TextFormat::PrintToString(config, &bytes), "Cannot encode child configuration");
    std::size_t written = 0;
    while (written < bytes.size()) {
        const auto count = write(file.value, bytes.data()+written, bytes.size()-written);
        if (count < 0 && errno == EINTR) continue;
        require(count > 0, "Cannot persist child configuration");
        written += static_cast<std::size_t>(count);
    }
    require(fsync(file.value) == 0 && fsync(directory.value) == 0, "Child configuration durability uncertain");
}
void view(spatial::identity::ParticipantStream& channel, const pb::AuthorityLaunchRecord& record,
          std::uint64_t request = 0) {
    pb::AuthorityLaunchReply reply;
    reply.set_protocol_version(1); reply.set_request_id(request);
    *reply.mutable_view()->mutable_record() = record;
    channel.send(reply.SerializeAsString());
}
void drain(spatial::identity::ParticipantStream& channel) {
    while (channel.pending_output()) { channel.flush(); if (channel.pending_output()) tick(); }
}
void release_child_sources(const pb::AuthoritySupervisorConfig& config) {
    // Keep standard descriptor slots occupied while releasing the original child pipes.
    File null_file(open("/dev/null", O_RDWR | O_CLOEXEC | O_NOFOLLOW));
    struct stat info{};
    require(null_file.value >= 3 && fstat(null_file.value, &info) == 0 && S_ISCHR(info.st_mode),
        "Cannot reserve supervisor standard descriptor slots");
    for (const int target : {STDIN_FILENO, STDOUT_FILENO}) {
        int result;
        do { result = dup2(null_file.value, target); } while (result < 0 && errno == EINTR);
        require(result == target, "Cannot release supervisor child stream copy");
    }
    for (const auto source : config.inherited_source_fds()) close(static_cast<int>(source));
}
}

int main(int argc, char** argv) {
    std::unique_ptr<spatial::supervisor::AuthorityLaunch> launch;
    std::unique_ptr<spatial::supervisor::StorageOwnerLaunch> storage;
    std::unique_ptr<spatial::identity::ParticipantStream> management;
    std::unique_ptr<spatial::supervisor::ChildHandleServer> handles;
    std::shared_ptr<spatial::BrokerCopyCleanupParticipant> cleanup_participant;
    bool explicit_shutdown = false;
    bool storage_start_cleanup_confirmed = true;
    std::signal(SIGPIPE, SIG_IGN);
    std::signal(SIGTERM, terminate); std::signal(SIGINT, terminate);
    try {
        require(argc == 4 || argc == 5,
            "Expected supervisor configuration, sealed native descriptor, management descriptor, and optional private handle descriptor");
        const int native = descriptor(argv[2]), control = descriptor(argv[3]);
        const int handle = argc == 5 ? descriptor(argv[4]) : -1;
        require(native != control && handle != native && handle != control, "Privileged descriptors must differ");
        auto config = configuration(argv[1], native, control, handle);
        // Validate caller arguments before adding the supervisor-owned bootstrap endpoint.
        const auto broker_state = cleanup_bootstrap_required(config)
            ? configured_broker_state(config) : std::filesystem::path{};
        management = std::make_unique<spatial::identity::ParticipantStream>(control, getppid());
        if (handle >= 0) handles = std::make_unique<spatial::supervisor::ChildHandleServer>(handle, getppid());
        File native_config(native);
        File broker_config;
        File broker_resources;
        File storage_owner, storage_child;
        File cleanup_bootstrap_supervisor, cleanup_bootstrap_child;
        if (cleanup_bootstrap_required(config)) {
            int pair[2]; require(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, pair) == 0,
                "Cannot create private broker cleanup bootstrap channel");
            cleanup_bootstrap_supervisor.reset(pair[0]); cleanup_bootstrap_child.reset(pair[1]);
            spatial::supervisor::BrokerCleanupBootstrap::require_credentials(pair[0]);
            spatial::supervisor::BrokerCleanupBootstrap::require_credentials(pair[1]);
        }
        if (config.has_broker_storage_owner()) {
            int pair[2]; require(socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, pair) == 0,
                "Cannot create private broker storage channel");
            storage_owner.reset(pair[0]); storage_child.reset(pair[1]);
            const int enabled = 1;
            for (const int fd : pair)
                require(setsockopt(fd, SOL_SOCKET, SO_PASSCRED, &enabled, sizeof(enabled)) == 0,
                    "Cannot require broker storage credentials");
        }
        File compositor_config;
        std::optional<pb::NativeScopeConfig> source;
        if (config.has_broker_native_target_fd() || config.has_compositor_native_target_fd())
            source = handoff_source(native, config.has_compositor_native_target_fd());
        parent_alive(control);
        launch = std::make_unique<spatial::supervisor::AuthorityLaunch>(config.journal_directory());
        const auto prepared = launch->prepare(config.delegated_cgroup(), config.binding(), native);
        if (config.has_broker_resources())
            broker_resources.reset(spatial::seal_resource_config(config.broker_resources(),
                std::filesystem::path(config.delegated_cgroup()) / prepared.record().process().scope_name()));
        if (cleanup_bootstrap_required(config))
            cleanup_participant = spatial::BrokerCopyCleanupParticipant::deferred(*launch);
        if (config.has_broker_native_target_fd()) seal_broker_config(broker_config, *source, prepared.ready());
        if (config.has_compositor_native_target_fd()) seal_compositor_config(compositor_config, *source, prepared.ready());
        native_config.reset();
        launch->attach(config.participant());
        bool authenticated = false, baseline = false;
        const auto deadline = Clock::now()+std::chrono::seconds(10);
        while (!baseline) {
            require(!interrupted && Clock::now() < deadline, "Supervisor participant startup interrupted or timed out");
            parent_alive(control);
            if (auto update = launch->poll_participant()) {
                if (update->has_authenticated() && !authenticated) authenticated = true;
                else if (authenticated && update->has_revocations() && update->revocations().initial()) baseline = true;
                else throw std::runtime_error("Supervisor participant startup failed");
            } else tick();
        }
        require(!interrupted, "Supervisor startup interrupted");
        parent_alive(control);
        launch->authorize(config.authorization());
        auto child_config = launch->prepare_channel(config.participant_target_fd());
        write_child_config(config.child_participant_config(), child_config);
        require(!interrupted, "Supervisor startup interrupted");
        parent_alive(control);
        std::vector<int> inherited(config.inherited_source_fds().begin(), config.inherited_source_fds().end());
        if (config.has_broker_native_target_fd()) {
            config.mutable_launch()->add_inherited_fds(config.broker_native_target_fd());
            inherited.push_back(broker_config.value);
        }
        if (config.has_broker_resources()) {
            config.mutable_launch()->add_inherited_fds(config.broker_resource_target_fd());
            inherited.push_back(broker_resources.value);
            config.mutable_launch()->add_arguments("--resource-config-fd");
            config.mutable_launch()->add_arguments(std::to_string(config.broker_resource_target_fd()));
        }
        if (config.has_compositor_native_target_fd()) {
            config.mutable_launch()->add_inherited_fds(config.compositor_native_target_fd());
            inherited.push_back(compositor_config.value);
        }
        if (config.has_broker_storage_owner()) {
            config.mutable_launch()->add_inherited_fds(config.broker_storage_target_fd());
            inherited.push_back(storage_child.value);
            config.mutable_launch()->add_arguments("--storage-fd");
            config.mutable_launch()->add_arguments(std::to_string(config.broker_storage_target_fd()));
        }
        if (cleanup_bootstrap_required(config)) {
            std::set<std::uint32_t> used(config.launch().inherited_fds().begin(), config.launch().inherited_fds().end());
            std::uint32_t target = 0;
            for (int candidate = 255; candidate >= 240; --candidate)
                if (!used.count(static_cast<std::uint32_t>(candidate))) { target = static_cast<std::uint32_t>(candidate); break; }
            require(target >= 240 && inherited.size() < 16, "No private broker cleanup descriptor slot remains");
            config.mutable_launch()->add_inherited_fds(target);
            inherited.push_back(cleanup_bootstrap_child.value);
            config.mutable_launch()->add_arguments("--cleanup-bootstrap-fd");
            config.mutable_launch()->add_arguments(std::to_string(target));
            if (config.require_broker_confinement()) config.mutable_launch()->add_arguments("--production");
        }
        const auto started = launch->start(config.launch(), {STDIN_FILENO, STDOUT_FILENO, STDERR_FILENO},
            inherited, config.has_broker_native_target_fd() && config.has_broker_storage_owner());
        release_child_sources(config);
        storage_child.reset(); cleanup_bootstrap_child.reset();
        if (config.has_broker_storage_owner()) {
            File broker_handle(launch->duplicate_broker_pidfd(started));
            storage_start_cleanup_confirmed = false;
            try {
                storage = std::make_unique<spatial::supervisor::StorageOwnerLaunch>(config.broker_storage_owner(),
                    config.broker_storage_owner().volume_root(), storage_owner.value, broker_handle.value, STDERR_FILENO,
                    [&] { require(!interrupted, "Storage startup interrupted"); parent_alive(control); launch->pump_channel(); });
                storage_start_cleanup_confirmed = true;
            } catch (const spatial::supervisor::StorageLaunchFailure& failure) {
                storage_start_cleanup_confirmed = failure.cleanup_confirmed(); throw;
            }
            storage_owner.reset();
        }
        if (cleanup_participant) {
            const auto bootstrap_deadline = spatial::supervisor::BrokerCleanupBootstrap::deadline_after(std::chrono::seconds(10));
            auto received = spatial::supervisor::BrokerCleanupBootstrap::receive_request(
                cleanup_bootstrap_supervisor.value, launch->direct_child_pid(), getuid(), bootstrap_deadline);
            require(Clock::now()<bootstrap_deadline,"Broker cleanup bootstrap deadline expired before root validation");
            spatial::supervisor::BrokerCleanupBootstrap::validate_state_root(received.descriptor, broker_state);
            require(Clock::now()<bootstrap_deadline,"Broker cleanup bootstrap deadline expired before journal pin");
            auto pinned = spatial::FileOperationJournal::pin_existing(received.descriptor);
            require(Clock::now()<bootstrap_deadline,"Broker cleanup bootstrap deadline expired while pinning journal");
            cleanup_participant->bind(std::move(pinned));
            require(Clock::now()<bootstrap_deadline,"Broker cleanup bootstrap deadline expired while binding journal");
            spatial::supervisor::BrokerCleanupBootstrap::send_ack(cleanup_bootstrap_supervisor.value,
                received.frame.correlation_id(), bootstrap_deadline);
            cleanup_bootstrap_supervisor.reset();
        }
        view(*management, started);
        broker_config.reset();
        broker_resources.reset();
        compositor_config.reset();
        constexpr unsigned kMaximumCleanupAttempts = 3;
        constexpr auto kCleanupWindow = std::chrono::seconds(90);
        constexpr auto kMinimumAttemptBudget = std::chrono::seconds(40);
        unsigned cleanup_attempts = 0;
        Clock::time_point cleanup_started{};
        bool cleanup_pending = false;
        bool unexpected_failure = false;
        bool child_completed = false;
        bool cleanup_budget_exhausted = false;
        std::string pending_failure;
        auto cleanup_once = [&]() -> std::optional<pb::AuthorityLaunchRecord> {
            const auto now = Clock::now();
            if (cleanup_attempts >= kMaximumCleanupAttempts) { cleanup_budget_exhausted = true; return std::nullopt; }
            if (cleanup_attempts != 0 && now - cleanup_started + kMinimumAttemptBudget > kCleanupWindow) {
                cleanup_budget_exhausted = true; return std::nullopt;
            }
            if (cleanup_attempts == 0) cleanup_started = now;
            ++cleanup_attempts;
            auto receipt = launch->stop();
            if (storage) storage->stop();
            return receipt;
        };
        auto finish_unexpected = [&](const pb::AuthorityLaunchRecord& receipt, const std::string& message) {
            pb::AuthorityLaunchReply reply; reply.set_protocol_version(1);
            reply.mutable_error()->set_message(message);
            *reply.mutable_error()->mutable_cleanup_record() = receipt;
            management->send(reply.SerializeAsString()); drain(*management);
            return 2;
        };
        auto exit_pending_on_signal = [&]() {
            if (!cleanup_pending || !interrupted) return false;
            std::cerr << "Supervisor termination requested while cleanup remains pending\n";
            std::cerr.flush();
            return true;
        };
        for (;;) {
            try {
                if (exit_pending_on_signal()) return 2;
                require(!interrupted, "Supervisor termination requested");
                parent_alive(control);
                if (cleanup_pending) {
                    short events = POLLIN | POLLHUP | POLLERR;
                    if (management->pending_output()) events |= POLLOUT;
                    pollfd event{management->descriptor(), events, 0};
                    int ready;
                    do { ready = poll(&event, 1, -1); } while (ready < 0 && errno == EINTR && !interrupted);
                    if (exit_pending_on_signal()) return 2; // Preserve pending evidence; no checked receipt.
                    if (ready < 0) throw std::runtime_error("Pending cleanup management poll failed");
                    if (event.revents & POLLOUT) management->flush();
                    parent_alive(control);
                }
                if (auto bytes = management->receive()) {
                    pb::AuthorityLaunchRequest request;
                    pb::AuthorityLaunchReply reply; reply.set_protocol_version(1);
                    bool stop = false;
                    try {
                        require(request.ParseFromString(*bytes), "Malformed supervisor request");
                        reply.set_request_id(request.request_id()); spatial::validate_message(request);
                        require(request.protocol_version() == 1, "Unsupported supervisor request version");
                        require(request.has_inspect() || request.has_stop() || request.has_fence() || request.has_control_owner(),
                            "Supervisor supports only inspect, stop, fence, and control owner");
                        stop = request.has_stop() || request.has_fence();
                        if (cleanup_pending) require(request.has_stop(), "Cleanup is pending; only an explicit stop retry is accepted");
                        if (cleanup_budget_exhausted) throw std::runtime_error("Cleanup retry budget exhausted; preserve pending launch evidence");
                    } catch (const std::exception& error) { reply.mutable_error()->set_message(error.what()); }
                    if (!reply.has_error()) {
                        explicit_shutdown = stop;
                        if (stop) {
                            cleanup_pending = true;
                            if (!unexpected_failure && !child_completed) {
                                try { launch->fence(); }
                                catch (const std::exception& error) {
                                    unexpected_failure = true; pending_failure = error.what();
                                }
                            }
                            try {
                                auto receipt = cleanup_once();
                                require(bool(receipt), "Cleanup retry budget exhausted; preserve pending evidence");
                                if (unexpected_failure) return finish_unexpected(*receipt, pending_failure);
                                *reply.mutable_view()->mutable_record() = *receipt;
                                management->send(reply.SerializeAsString()); drain(*management); return 0;
                            } catch (const std::exception& error) {
                                reply.Clear(); reply.set_protocol_version(1); reply.set_request_id(request.request_id());
                                reply.mutable_error()->set_message(error.what());
                                management->send(reply.SerializeAsString());
                                management->flush();
                                if (cleanup_attempts >= kMaximumCleanupAttempts ||
                                    Clock::now() - cleanup_started + kMinimumAttemptBudget > kCleanupWindow) {
                                    cleanup_budget_exhausted = true;
                                    std::cerr << "Cleanup retry budget exhausted; preserving pending launch evidence\n";
                                }
                                continue;
                            }
                        } else if (request.has_control_owner()) {
                            try { *reply.mutable_control_owner() = launch->control_owner(); }
                            catch (const std::exception& error) { reply.mutable_error()->set_message(error.what()); }
                        } else *reply.mutable_view()->mutable_record() = launch->inspect();
                    }
                    management->send(reply.SerializeAsString());
                    if (!stop) management->flush();
                }
                if (cleanup_pending) continue;
                if (handles) handles->pump(*launch);
                launch->pump_channel();
                if (launch->child_completed()) {
                    child_completed = true;
                    cleanup_pending = true;
                    try {
                        auto receipt = cleanup_once();
                        require(bool(receipt), "Cleanup retry budget exhausted; preserve pending evidence");
                        view(*management, *receipt); drain(*management); return 0;
                    } catch (const std::exception& error) {
                        pending_failure = error.what();
                        std::cerr << "Child completed; cleanup pending: " << pending_failure << '\n';
                    }
                }
                if (storage) storage->check_alive();
                management->flush(); tick();
            } catch (const std::exception& error) {
                if (exit_pending_on_signal()) return 2;
                if (management && management->descriptor() >= 0) {
                    pollfd event{management->descriptor(), POLLHUP | POLLERR | POLLRDHUP, 0};
                    const int ready = poll(&event, 1, 0);
                    if (ready > 0 && (event.revents & (POLLHUP | POLLERR | POLLRDHUP))) {
                        if (!cleanup_pending) {
                            cleanup_pending = true;
                            try { launch->fence_after_failure(); }
                            catch (const std::exception& fencing) {
                                std::cerr << "Management disconnect fencing unresolved: " << fencing.what() << '\n';
                            }
                        }
                        std::cerr << "Management parent disconnected while cleanup may be pending; returning without a checked receipt\n";
                        std::cerr.flush();
                        return 2; // Stack unwinding retains the existing bounded best-effort destructors.
                    }
                }
                if (!cleanup_pending) {
                    unexpected_failure = true;
                    cleanup_pending = true;
                    pending_failure = error.what();
                    std::cerr << "authority supervisor: " << pending_failure << '\n';
                    try { launch->fence_after_failure(); }
                    catch (const std::exception& fencing) {
                        std::cerr << "authority supervisor crash fencing unresolved: " << fencing.what() << '\n';
                    }
                    try {
                        auto receipt = cleanup_once();
                        if (receipt && storage_start_cleanup_confirmed) return finish_unexpected(*receipt, pending_failure);
                    } catch (const std::exception& cleanup) {
                        std::cerr << "authority supervisor cleanup pending: " << cleanup.what() << '\n';
                    }
                    if (cleanup_attempts >= kMaximumCleanupAttempts ||
                        Clock::now() - cleanup_started + kMinimumAttemptBudget > kCleanupWindow) {
                        cleanup_budget_exhausted = true;
                        std::cerr << "Cleanup retry budget exhausted; preserving pending launch evidence\n";
                    }
                } else {
                    std::cerr << "authority supervisor cleanup retry rejected: " << error.what() << '\n';
                    std::cerr.flush();
                    if (cleanup_attempts >= kMaximumCleanupAttempts ||
                        Clock::now() - cleanup_started + kMinimumAttemptBudget > kCleanupWindow)
                        cleanup_budget_exhausted = true;
                }
            }
        }
    } catch (const std::exception& error) {
        std::cerr << "authority supervisor: " << error.what() << '\n';
        std::optional<pb::AuthorityLaunchRecord> cleanup_record;
        if (launch && launch->inspect().revision()) {
            if (!explicit_shutdown) {
                try { launch->fence_after_failure(); }
                catch (const std::exception& fencing) {
                    std::cerr << "authority supervisor crash fencing unresolved: " << fencing.what() << '\n';
                }
            }
            try { cleanup_record = launch->stop(); }
            catch (const std::exception& cleanup) { std::cerr << "authority supervisor cleanup unresolved: " << cleanup.what() << '\n'; }
        }
        if (storage) {
            try { storage->stop(); }
            catch (const std::exception& cleanup) {
                cleanup_record.reset();
                std::cerr << "authority storage cleanup unresolved: " << storage->unit() << ": " << cleanup.what() << '\n';
            }
        }
        if (!storage_start_cleanup_confirmed) {
            cleanup_record.reset();
            std::cerr << "authority storage startup cleanup remains unconfirmed\n";
        }
        if (management) {
            try {
                pb::AuthorityLaunchReply reply; reply.set_protocol_version(1);
                reply.mutable_error()->set_message(error.what());
                if (cleanup_record) *reply.mutable_error()->mutable_cleanup_record() = *cleanup_record;
                management->send(reply.SerializeAsString()); drain(*management);
            } catch (...) { /* The durable journal retains the cleanup result when delivery fails. */ }
        }
        return 2;
    }
}
