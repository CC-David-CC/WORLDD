#include "supervisor/launch.hpp"
#include "supervisor/child_handle.hpp"
#include "identity/participant_stream.hpp"
#include "identity/tls_support.hpp"
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
#include <sys/mman.h>

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
pb::AuthoritySupervisorConfig configuration(const char* path, int native, int management, int handle) {
    pb::AuthoritySupervisorConfig value;
    require(google::protobuf::TextFormat::ParseFromString(spatial::identity::tls_detail::load_config(path), &value),
        "Malformed supervisor configuration");
    spatial::validate_message(value);
    require(value.protocol_version() == 1 && value.binding().supervised() &&
        value.has_participant() && value.has_authorization() && value.has_launch() &&
        absolute(value.journal_directory()) && absolute(value.delegated_cgroup()) &&
        absolute(value.child_participant_config()) && absolute(value.launch().executable()) &&
        value.participant_target_fd() >= 3 && value.participant_target_fd() <= 255 &&
        !(value.has_broker_native_target_fd() && value.has_compositor_native_target_fd()) &&
        value.inherited_source_fds_size() + ((value.has_broker_native_target_fd() || value.has_compositor_native_target_fd()) ? 2 : 1) <= 16 &&
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
    std::unique_ptr<spatial::identity::ParticipantStream> management;
    std::unique_ptr<spatial::supervisor::ChildHandleServer> handles;
    bool explicit_shutdown = false;
    std::signal(SIGPIPE, SIG_IGN);
    std::signal(SIGTERM, terminate); std::signal(SIGINT, terminate);
    try {
        require(argc == 4 || argc == 5,
            "Expected supervisor configuration, sealed native descriptor, management descriptor, and optional private handle descriptor");
        const int native = descriptor(argv[2]), control = descriptor(argv[3]);
        const int handle = argc == 5 ? descriptor(argv[4]) : -1;
        require(native != control && handle != native && handle != control, "Privileged descriptors must differ");
        auto config = configuration(argv[1], native, control, handle);
        management = std::make_unique<spatial::identity::ParticipantStream>(control, getppid());
        if (handle >= 0) handles = std::make_unique<spatial::supervisor::ChildHandleServer>(handle, getppid());
        File native_config(native);
        File broker_config;
        File compositor_config;
        std::optional<pb::NativeScopeConfig> source;
        if (config.has_broker_native_target_fd() || config.has_compositor_native_target_fd())
            source = handoff_source(native, config.has_compositor_native_target_fd());
        parent_alive(control);
        launch = std::make_unique<spatial::supervisor::AuthorityLaunch>(config.journal_directory());
        const auto prepared = launch->prepare(config.delegated_cgroup(), config.binding(), native);
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
        if (config.has_compositor_native_target_fd()) {
            config.mutable_launch()->add_inherited_fds(config.compositor_native_target_fd());
            inherited.push_back(compositor_config.value);
        }
        const auto started = launch->start(config.launch(), {STDIN_FILENO, STDOUT_FILENO, STDERR_FILENO}, inherited);
        release_child_sources(config);
        view(*management, started);
        broker_config.reset();
        compositor_config.reset();
        for (;;) {
            require(!interrupted, "Supervisor termination requested");
            // Inspect the private parent channel before accepting more child commands.
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
                } catch (const std::exception& error) { reply.mutable_error()->set_message(error.what()); }
                if (!reply.has_error()) {
                    // An explicit shutdown failure must not trigger an implicit fence retry.
                    explicit_shutdown = stop;
                    // Cleanup errors are fatal and cannot resume child routing.
                    if (request.has_fence()) launch->fence();
                    if (request.has_control_owner()) {
                        try { *reply.mutable_control_owner() = launch->control_owner(); }
                        catch (const std::exception& error) { reply.mutable_error()->set_message(error.what()); }
                    }
                    else *reply.mutable_view()->mutable_record() = stop ? launch->stop() : launch->inspect();
                }
                management->send(reply.SerializeAsString());
                if (stop) { drain(*management); return 0; }
            }
            if (handles) handles->pump(*launch);
            launch->pump_channel();
            if (launch->child_completed()) {
                view(*management, launch->stop()); drain(*management); return 0;
            }
            management->flush(); tick();
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
