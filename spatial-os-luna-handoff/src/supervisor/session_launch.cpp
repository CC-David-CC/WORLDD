#include "supervisor/session_launch.hpp"
#include "supervisor/child_handle_client.hpp"
#include "workspace/control_owner.hpp"
#include "identity/participant_stream.hpp"
#include "identity/tls_support.hpp"
#include <google/protobuf/text_format.h>
#include <climits>
#include <csignal>
#include <iostream>
#include <linux/close_range.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <vector>
#include <optional>

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
void checked_cleanup(const v1::AuthorityLaunchRecord& result, const v1::AuthorityLaunchRecord& expected) {
    validate_message(result);
    require(result.stage() == v1::OWNED_SCOPES_STOPPED && result.binding().SerializeAsString() == expected.binding().SerializeAsString() &&
        result.schema_version() == expected.schema_version() && result.revision() > expected.revision() &&
        result.authorization().SerializeAsString() == expected.authorization().SerializeAsString() &&
        result.process().SerializeAsString() == expected.process().SerializeAsString() &&
        result.native().SerializeAsString() == expected.native().SerializeAsString() &&
        result.has_process_stopped() && result.has_native_stopped() &&
        result.process_stopped().identity().SerializeAsString() == expected.process().SerializeAsString() &&
        result.native_stopped().identity().SerializeAsString() == expected.native().SerializeAsString(), "Supervisor cleanup reply does not match owned scopes");
}
struct SessionLaunch::Impl {
    pid_t owner = getpid(), child = -1;
    ProcessHandle supervisor;
    std::unique_ptr<identity::ParticipantStream> management;
    std::unique_ptr<ChildHandleClient> handles;
    v1::AuthorityLaunchRecord record;
    std::optional<v1::AuthorityLaunchRecord> terminal_record;
    std::string terminal_failure;
    bool stopped = false, reaped = false;
    std::uint64_t sequence = 0;

    void live() const {
        require(owner == getpid() && !stopped && supervisor.live(), "Session supervisor ownership lost");
    }
    v1::AuthorityLaunchReply receive(std::uint64_t request, std::chrono::seconds timeout, bool recovering = false) {
        bool output_failed = false;
        const auto deadline = Clock::now() + timeout;
        for (;;) {
            require(owner == getpid() && management && Clock::now() < deadline, "Session supervisor reply timed out");
            // A final reply may arrive immediately before supervisor exit.
            if (auto bytes = management->receive()) {
                v1::AuthorityLaunchReply reply;
                require(reply.ParseFromString(*bytes), "Malformed session supervisor reply"); validate_message(reply);
                require(reply.protocol_version() == 1, "Unsupported session supervisor reply version");
                const bool final_error = reply.request_id() == 0 && reply.has_error() && reply.error().has_cleanup_record();
                const bool final_view = reply.request_id() == 0 && reply.has_view() && !reply.view().has_ready() &&
                    reply.view().record().stage() == v1::OWNED_SCOPES_STOPPED;
                const bool terminal = record.stage() == v1::START_ATTEMPTED && (final_error || final_view);
                if (terminal) {
                    const auto& cleanup = final_error ? reply.error().cleanup_record() : reply.view().record();
                    checked_cleanup(cleanup, record);
                    terminal_record = cleanup;
                    if (final_error) terminal_failure = reply.error().message();
                    if (final_error && terminal_failure.empty()) terminal_failure = "Supervisor failed after checked cleanup";
                }
                require(reply.request_id() == request || terminal, "Session supervisor reply correlation failed");
                if (reply.has_error()) throw std::runtime_error("Session supervisor failed: " + reply.error().message());
                return reply;
            }
            // Exit does not discard a final buffered receipt. EOF or this bounded deadline still fails closed.
            if (supervisor.live() && !output_failed) {
                try { management->flush(); }
                catch (...) { if (!recovering) throw; output_failed = true; }
            }
            poll(nullptr, 0, 2);
        }
    }
    v1::AuthorityLaunchReply exchange(v1::AuthorityLaunchRequest request) {
        live(); require(sequence != UINT64_MAX, "Session supervisor request sequence exhausted");
        request.set_protocol_version(1); request.set_request_id(++sequence);
        management->send(request.SerializeAsString());
        return receive(sequence, std::chrono::seconds(10));
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
        if (owner != getpid()) return;
        handles.reset(); management.reset();
        if (child <= 0 || reaped || wait_child(8000)) return;
        if (supervisor.descriptor() >= 0) syscall(SYS_pidfd_send_signal, supervisor.descriptor(), SIGTERM, nullptr, 0);
        else kill(child, SIGTERM); // This direct child has not been reaped or given to other waiters.
        if (wait_child(3000)) return;
        std::cerr << "Session supervisor cleanup unresolved; preserve its launch journal\n";
        if (supervisor.descriptor() >= 0) syscall(SYS_pidfd_send_signal, supervisor.descriptor(), SIGKILL, nullptr, 0);
        else kill(child, SIGKILL);
        while (waitpid(child, nullptr, 0) < 0 && errno == EINTR) {}
    }
    void start(const std::filesystem::path& executable, const std::filesystem::path& path,
               const v1::AuthoritySupervisorConfig& config, int native, const std::array<int, 3>& streams, bool export_handle) {
        validate_message(config);
        require(executable.is_absolute() && executable.string().find('\0') == std::string::npos && native >= 3 &&
            config.protocol_version() == 1 && config.binding().supervised() &&
            config.has_broker_native_target_fd() != config.has_compositor_native_target_fd() &&
            export_handle == config.has_broker_native_target_fd(), "Invalid retained session launch configuration");
        for (int fd : streams) require(fd >= 0 && fcntl(fd, F_GETFD) >= 0, "Missing session child stream");
        Pair mgmt(SOCK_STREAM);
        std::unique_ptr<Pair> transfer;
        if (export_handle) transfer = std::make_unique<Pair>(SOCK_SEQPACKET);
        File sealed(fcntl(native, F_DUPFD_CLOEXEC, 3));
        require(sealed.value >= 3, "Cannot retain sealed native launch input");
        write_config(path, config);
        std::vector<std::string> args{executable.string(), path.string(), std::to_string(sealed.value), std::to_string(mgmt.child.value)};
        if (transfer) args.push_back(std::to_string(transfer->child.value));
        std::vector<char*> argv;
        for (auto& arg : args) argv.push_back(arg.data());
        argv.push_back(nullptr);
        char env_path[] = "PATH=/usr/bin:/bin", env_lang[] = "LANG=C.UTF-8";
        char* environment[]{env_path, env_lang, nullptr};
        std::vector<int> inherited{sealed.value, mgmt.child.value};
        if (transfer) inherited.push_back(transfer->child.value);
        for (const auto fd : config.inherited_source_fds()) inherited.push_back(static_cast<int>(fd));
        child = fork(); require(child >= 0, "Cannot fork retained session supervisor");
        if (child == 0) {
            if (getppid() != owner || syscall(SYS_close_range, 3U, UINT_MAX, CLOSE_RANGE_CLOEXEC) < 0) _exit(126);
            for (unsigned i = 0; i < streams.size(); ++i)
                if (dup2(streams[i], static_cast<int>(i)) < 0) _exit(126);
            for (int fd : inherited) if (fcntl(fd, F_SETFD, 0) < 0) _exit(126);
            execve(args[0].c_str(), argv.data(), environment); _exit(127);
        }
        supervisor = ProcessHandle::take(static_cast<int>(syscall(SYS_pidfd_open, child, 0)), child);
        mgmt.child.reset(); sealed.reset();
        management = std::make_unique<identity::ParticipantStream>(std::exchange(mgmt.parent.value, -1), owner);
        if (transfer) {
            transfer->child.reset();
            handles = std::make_unique<ChildHandleClient>(std::exchange(transfer->parent.value, -1), child, supervisor.descriptor());
        }
        const auto initial = receive(0, std::chrono::seconds(15));
        require(initial.has_view() && initial.view().has_record() && !initial.view().has_ready(), "Unexpected session startup reply");
        record = initial.view().record();
        const auto role = export_handle ? security::v1::BROKER : security::v1::COMPOSITOR;
        require(record.schema_version() == 2 && record.revision() > 0 && record.stage() == v1::START_ATTEMPTED &&
            record.binding().SerializeAsString() == config.binding().SerializeAsString() && record.has_authorization() &&
            record.native().role() == role && record.process().launch_id().size() == 32 &&
            record.native().launch_id() == record.process().launch_id(), "Supervisor started an unexpected session launch");
    }
};
SessionLaunch::SessionLaunch(const std::filesystem::path& executable, const std::filesystem::path& path,
    const v1::AuthoritySupervisorConfig& config, int native, const std::array<int, 3>& streams, bool export_handle)
    : impl_(std::make_unique<Impl>()) { impl_->start(executable, path, config, native, streams, export_handle); }
SessionLaunch::~SessionLaunch() = default;
void SessionLaunch::live() const { impl_->live(); }
const v1::AuthorityLaunchRecord& SessionLaunch::record() const { impl_->live(); return impl_->record; }
pid_t SessionLaunch::supervisor_pid() const { impl_->live(); return impl_->supervisor.pid(); }
int SessionLaunch::supervisor_pidfd() const { impl_->live(); return impl_->supervisor.descriptor(); }
ProcessHandle SessionLaunch::broker_handle() {
    impl_->live(); require(bool(impl_->handles), "This session launch cannot export a broker handle");
    return impl_->handles->broker(impl_->record);
}
workspace::v1::ControlOwner SessionLaunch::control_owner() {
    auto& state = *impl_; state.live();
    require(state.record.native().role() == security::v1::COMPOSITOR, "Control owner requires a world launch");
    v1::AuthorityLaunchRequest request; request.mutable_control_owner();
    const auto reply = state.exchange(request);
    require(reply.has_control_owner(), "Supervisor did not return its retained control owner");
    const auto& received = reply.control_owner(); validate_control_owner(received);
    const auto& record = state.record; const auto& auth = record.authorization();
    const auto& expected = auth.request(); const auto& binding = record.binding();
    const auto& launch = received.launch();
    require(expected.previous_generation() < INT64_MAX && received.issuer_id() == binding.issuer_id() &&
        received.native().SerializeAsString() == record.native().SerializeAsString() &&
        launch.participant_id() == binding.participant_id() && launch.registered_revision() == binding.registered_revision() &&
        launch.generation() == expected.previous_generation() + 1 && launch.service_incarnation() == binding.service_incarnation() &&
        launch.launch_id() == record.process().launch_id() && launch.supervision_revision() == expected.supervision_revision() &&
        launch.authorization_id() == auth.authorization_id(), "Supervisor control owner does not match its retained launch");
    return received;
}
v1::AuthorityLaunchRecord SessionLaunch::stop() {
    auto& state = *impl_;
    require(state.owner == getpid() && !state.stopped && state.management, "Session cleanup ownership lost");
    v1::AuthorityLaunchRecord result;
    if (!state.terminal_record) {
        std::uint64_t request_id = 0;
        if (state.supervisor.live()) {
            require(state.sequence != UINT64_MAX, "Session supervisor request sequence exhausted");
            v1::AuthorityLaunchRequest request; request.set_protocol_version(1); request.set_request_id(++state.sequence); request.mutable_stop();
            request_id = state.sequence; state.management->send(request.SerializeAsString());
        }
        try {
            const auto reply = state.receive(request_id, std::chrono::seconds(10), true);
            require(reply.has_view() && !reply.view().has_ready(), "Unexpected session cleanup reply");
            result = reply.view().record();
        } catch (...) { if (!state.terminal_record) throw; }
    }
    if (state.terminal_record) result = *state.terminal_record;
    checked_cleanup(result, state.record);
    int status = 0;
    const int expected_exit = state.terminal_failure.empty() ? 0 : 2;
    require(state.wait_child(5000, &status) && WIFEXITED(status) && WEXITSTATUS(status) == expected_exit,
        "Supervisor exit does not match its checked cleanup outcome");
    if (!state.terminal_failure.empty()) std::cerr << "Session launch failed after checked cleanup: " << state.terminal_failure << '\n';
    state.stopped = true; state.record = result; state.handles.reset(); state.management.reset();
    return result;
}
}
