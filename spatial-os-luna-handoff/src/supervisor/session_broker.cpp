#include "supervisor/session_broker.hpp"
#include "supervisor/child_handle_client.hpp"
#include "supervisor/control_admin.hpp"
#include "platform/broker.hpp"
#include "identity/participant_stream.hpp"
#include "identity/tls_support.hpp"
#include <google/protobuf/text_format.h>
#include <array>
#include <climits>
#include <csignal>
#include <iostream>
#include <linux/close_range.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/wait.h>

namespace spatial::supervisor {
namespace {
using File = identity::tls_detail::Fd;
using Clock = std::chrono::steady_clock;
void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
struct Pair {
    File parent, child;
    explicit Pair(int type) {
        int pair[2]; require(socketpair(AF_UNIX, type | SOCK_CLOEXEC, 0, pair) == 0, "Cannot create session channel");
        parent.reset(pair[0]); child.reset(pair[1]);
    }
};
void write_config(const std::filesystem::path& path, const v1::AuthoritySupervisorConfig& config) {
    require(path.is_absolute() && !path.filename().empty() && path.filename() != "." && path.filename() != ".." &&
        path.string().find('\0') == std::string::npos, "Session launch configuration requires an absolute filename");
    File parent(open(path.parent_path().c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
    struct stat info{};
    require(parent.value >= 3 && fstat(parent.value, &info) == 0 && info.st_uid == geteuid() && !(info.st_mode & 0077),
        "Session launch configuration requires an owned private directory");
    File file(openat(parent.value, path.filename().c_str(), O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600));
    require(file.value >= 3, "Session launch configuration already exists or cannot be created");
    std::string bytes;
    require(google::protobuf::TextFormat::PrintToString(config, &bytes) && bytes.size() <= 65536, "Invalid session launch configuration size");
    std::size_t offset = 0;
    while (offset < bytes.size()) {
        const auto count = write(file.value, bytes.data() + offset, bytes.size() - offset);
        if (count < 0 && errno == EINTR) continue;
        require(count > 0, "Cannot write session launch configuration"); offset += static_cast<std::size_t>(count);
    }
    require(fsync(file.value) == 0 && fsync(parent.value) == 0, "Session launch configuration durability uncertain");
}
}
struct SessionBroker::Impl {
    pid_t owner = getpid(), child = -1;
    ProcessHandle supervisor, broker;
    std::unique_ptr<identity::ParticipantStream> management;
    std::unique_ptr<ChildHandleClient> handles;
    std::unique_ptr<BrokerClient> root;
    std::unique_ptr<ControlAdmin> administrative;
    v1::AuthorityLaunchRecord record;
    bool stopped = false, reaped = false;
    std::uint64_t sequence = 0;

    void live() const {
        require(owner == getpid() && !stopped && supervisor.live(), "Session broker supervisor ownership lost");
    }
    v1::AuthorityLaunchReply receive(std::uint64_t request, std::chrono::seconds timeout) {
        const auto deadline = Clock::now() + timeout;
        for (;;) {
            require(owner == getpid() && management && Clock::now() < deadline, "Session supervisor reply timed out");
            // Read a final reply before inspecting exit. A stopped supervisor may already have exited.
            if (auto bytes = management->receive()) {
                v1::AuthorityLaunchReply reply;
                require(reply.ParseFromString(*bytes), "Malformed session supervisor reply"); validate_message(reply);
                require(reply.protocol_version() == 1 && reply.request_id() == request, "Session supervisor reply correlation failed");
                if (reply.has_error()) throw std::runtime_error("Session supervisor failed: " + reply.error().message());
                require(reply.has_view() && reply.view().has_record() && !reply.view().has_ready(), "Unexpected session supervisor reply");
                return reply;
            }
            management->flush();
            require(supervisor.live(), "Session supervisor exited before its reply");
            poll(nullptr, 0, 2);
        }
    }
    bool wait_child(int milliseconds, int* status = nullptr) {
        if (child <= 0 || reaped) return true;
        if (supervisor.descriptor() >= 0) {
            pollfd event{supervisor.descriptor(), POLLIN, 0}; int ready;
            do { ready = poll(&event, 1, milliseconds); } while (ready < 0 && errno == EINTR);
            if (ready <= 0) return false;
        }
        int result = 0; pid_t waited;
        do { waited = waitpid(child, &result, WNOHANG); } while (waited < 0 && errno == EINTR);
        if (waited != child) return false;
        reaped = true; if (status) *status = result; return true;
    }
    ~Impl() {
        if (owner != getpid()) return; // Inherited copies may close descriptors but cannot signal the parent's supervisor.
        administrative.reset(); root.reset(); handles.reset(); management.reset();
        if (child <= 0 || reaped) return;
        if (wait_child(8000)) return;
        if (supervisor.descriptor() >= 0) syscall(SYS_pidfd_send_signal, supervisor.descriptor(), SIGTERM, nullptr, 0);
        else kill(child, SIGTERM); // The direct child has not been reaped or exposed for other waiters.
        if (wait_child(3000)) return;
        std::cerr << "Session broker supervisor cleanup unresolved; preserve its launch journal\n";
        if (supervisor.descriptor() >= 0) syscall(SYS_pidfd_send_signal, supervisor.descriptor(), SIGKILL, nullptr, 0);
        else kill(child, SIGKILL);
        while (waitpid(child, nullptr, 0) < 0 && errno == EINTR) {}
    }
    void start(const std::filesystem::path& executable, const std::filesystem::path& path,
               v1::AuthoritySupervisorConfig config, int native) {
        validate_message(config);
        require(executable.is_absolute() && executable.string().find('\0') == std::string::npos && native >= 3 &&
            config.protocol_version() == 1 && config.binding().supervised() && config.has_broker_native_target_fd() &&
            !config.has_compositor_native_target_fd() && config.inherited_source_fds_size() == 0 &&
            config.launch().inherited_fds_size() == 0, "Session broker requires an unmapped supervised broker configuration");
        for (const auto& arg : config.launch().arguments())
            require(arg != "--control-supervisor-fd" && arg != "--control-supervisor-pid", "Session owns broker administrative arguments");
        int target = 255;
        while (target == static_cast<int>(config.participant_target_fd()) || target == static_cast<int>(config.broker_native_target_fd())) --target;
        Pair mgmt(SOCK_STREAM), transfer(SOCK_SEQPACKET), admin(SOCK_SEQPACKET), channel(SOCK_STREAM);
        File sealed(fcntl(native, F_DUPFD_CLOEXEC, 3));
        require(sealed.value >= 3, "Cannot retain sealed native launch input");
        config.add_inherited_source_fds(admin.child.value); config.mutable_launch()->add_inherited_fds(target);
        config.mutable_launch()->add_arguments("--control-supervisor-fd"); config.mutable_launch()->add_arguments(std::to_string(target));
        config.mutable_launch()->add_arguments("--control-supervisor-pid"); config.mutable_launch()->add_arguments(std::to_string(owner));
        write_config(path, config);
        const std::array<std::string, 5> args{executable.string(), path.string(), std::to_string(sealed.value),
            std::to_string(mgmt.child.value), std::to_string(transfer.child.value)};
        std::array<char*, 6> argv{};
        for (std::size_t i = 0; i < args.size(); ++i) argv[i] = const_cast<char*>(args[i].c_str());
        char env_path[] = "PATH=/usr/bin:/bin", env_lang[] = "LANG=C.UTF-8";
        char* environment[]{env_path, env_lang, nullptr};
        const std::array<int, 4> inherited{sealed.value, mgmt.child.value, transfer.child.value, admin.child.value};
        child = fork(); require(child >= 0, "Cannot fork retained broker supervisor");
        if (child == 0) {
            if (getppid() != owner || syscall(SYS_close_range, 3U, UINT_MAX, CLOSE_RANGE_CLOEXEC) < 0 ||
                dup2(channel.child.value, STDIN_FILENO) < 0 || dup2(channel.child.value, STDOUT_FILENO) < 0) _exit(126);
            for (int fd : inherited) if (fcntl(fd, F_SETFD, 0) < 0) _exit(126);
            execve(args[0].c_str(), argv.data(), environment); _exit(127);
        }
        supervisor = ProcessHandle::take(static_cast<int>(syscall(SYS_pidfd_open, child, 0)), child);
        mgmt.child.reset(); transfer.child.reset(); admin.child.reset(); channel.child.reset(); sealed.reset();
        management = std::make_unique<identity::ParticipantStream>(std::exchange(mgmt.parent.value, -1), owner);
        handles = std::make_unique<ChildHandleClient>(std::exchange(transfer.parent.value, -1), child, supervisor.descriptor());
        auto initial = receive(0, std::chrono::seconds(15)); record = initial.view().record();
        require(record.schema_version() == 2 && record.revision() > 0 && record.stage() == v1::START_ATTEMPTED &&
            record.binding().SerializeAsString() == config.binding().SerializeAsString() && record.has_authorization() &&
            record.native().role() == security::v1::BROKER && record.process().launch_id().size() == 32 &&
            record.native().launch_id() == record.process().launch_id(), "Supervisor started an unexpected broker launch");
        broker = handles->broker(record);
        root = std::make_unique<BrokerClient>(std::exchange(channel.parent.value, -1), broker.pid(), broker.descriptor());
        administrative = std::make_unique<ControlAdmin>(std::exchange(admin.parent.value, -1), broker.pid(), broker.descriptor());
    }
};
SessionBroker::SessionBroker(const std::filesystem::path& executable, const std::filesystem::path& path,
    v1::AuthoritySupervisorConfig config, int native) : impl_(std::make_unique<Impl>()) {
    impl_->start(executable, path, std::move(config), native);
}
SessionBroker::~SessionBroker() = default;
BrokerClient& SessionBroker::root() { impl_->live(); return *impl_->root; }
ControlAdmin& SessionBroker::administrative() { impl_->live(); return *impl_->administrative; }
const v1::AuthorityLaunchRecord& SessionBroker::launch() const { impl_->live(); return impl_->record; }
pid_t SessionBroker::supervisor_pid() const { impl_->live(); return impl_->supervisor.pid(); }
pid_t SessionBroker::broker_pid() const { impl_->live(); return impl_->broker.pid(); }
v1::AuthorityLaunchRecord SessionBroker::stop() {
    auto& state = *impl_; state.live();
    v1::AuthorityLaunchRequest request; request.set_protocol_version(1); request.set_request_id(++state.sequence); request.mutable_stop();
    state.management->send(request.SerializeAsString());
    const auto result = state.receive(request.request_id(), std::chrono::seconds(10)).view().record();
    require(result.stage() == v1::OWNED_SCOPES_STOPPED && result.binding().SerializeAsString() == state.record.binding().SerializeAsString() &&
        result.schema_version() == state.record.schema_version() && result.revision() > state.record.revision() &&
        result.authorization().SerializeAsString() == state.record.authorization().SerializeAsString() &&
        result.process().SerializeAsString() == state.record.process().SerializeAsString() &&
        result.native().SerializeAsString() == state.record.native().SerializeAsString() &&
        result.has_process_stopped() && result.has_native_stopped() &&
        result.process_stopped().identity().SerializeAsString() == state.record.process().SerializeAsString() &&
        result.native_stopped().identity().SerializeAsString() == state.record.native().SerializeAsString(), "Supervisor cleanup reply does not match owned broker scopes");
    int status = 0;
    require(state.wait_child(5000, &status) && WIFEXITED(status) && WEXITSTATUS(status) == 0,
        "Supervisor did not exit successfully after checked broker cleanup");
    state.stopped = true; state.record = result;
    state.administrative.reset(); state.root.reset(); state.handles.reset(); state.management.reset();
    return result;
}
}
