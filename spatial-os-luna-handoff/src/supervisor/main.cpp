#include "supervisor/launch.hpp"
#include "identity/participant_stream.hpp"
#include "identity/tls_support.hpp"
#include <google/protobuf/text_format.h>
#include <charconv>
#include <chrono>
#include <climits>
#include <csignal>
#include <iostream>
#include <poll.h>
#include <set>

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
pb::AuthoritySupervisorConfig configuration(const char* path, int native, int management) {
    pb::AuthoritySupervisorConfig value;
    require(google::protobuf::TextFormat::ParseFromString(spatial::identity::tls_detail::load_config(path), &value),
        "Malformed supervisor configuration");
    spatial::validate_message(value);
    require(value.protocol_version() == 1 && value.binding().supervised() &&
        value.has_participant() && value.has_authorization() && value.has_launch() &&
        absolute(value.journal_directory()) && absolute(value.delegated_cgroup()) &&
        absolute(value.child_participant_config()) && absolute(value.launch().executable()) &&
        value.participant_target_fd() >= 3 && value.participant_target_fd() <= 255 &&
        value.inherited_source_fds_size() < 16 &&
        value.inherited_source_fds_size() == value.launch().inherited_fds_size(),
        "Invalid supervisor configuration");
    std::set<std::uint32_t> targets{value.participant_target_fd()}, sources;
    struct stat native_info{}, management_info{};
    require(fstat(native, &native_info) == 0 && fstat(management, &management_info) == 0,
        "Cannot identify privileged descriptors");
    for (int i = 0; i < value.inherited_source_fds_size(); ++i) {
        const auto source = value.inherited_source_fds(i), target = value.launch().inherited_fds(i);
        struct stat source_info{};
        require(source >= 3 && source <= INT_MAX && static_cast<int>(source) != native &&
            static_cast<int>(source) != management && sources.insert(source).second &&
            fcntl(static_cast<int>(source), F_GETFD) >= 0 &&
            target >= 3 && target <= 255 && targets.insert(target).second,
            "Invalid or privileged child descriptor mapping");
        require(fstat(static_cast<int>(source), &source_info) == 0 &&
            !(source_info.st_dev == native_info.st_dev && source_info.st_ino == native_info.st_ino) &&
            !(source_info.st_dev == management_info.st_dev && source_info.st_ino == management_info.st_ino),
            "Child descriptor aliases privileged supervisor input");
    }
    return value;
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
}

int main(int argc, char** argv) {
    std::unique_ptr<spatial::supervisor::AuthorityLaunch> launch;
    std::unique_ptr<spatial::identity::ParticipantStream> management;
    std::signal(SIGPIPE, SIG_IGN);
    std::signal(SIGTERM, terminate); std::signal(SIGINT, terminate);
    try {
        require(argc == 4, "Expected supervisor configuration, sealed native descriptor, and management descriptor");
        const int native = descriptor(argv[2]), control = descriptor(argv[3]);
        require(native != control, "Native and management descriptors must differ");
        auto config = configuration(argv[1], native, control);
        management = std::make_unique<spatial::identity::ParticipantStream>(control, getppid());
        File native_config(native);
        parent_alive(control);
        launch = std::make_unique<spatial::supervisor::AuthorityLaunch>(config.journal_directory());
        launch->prepare(config.delegated_cgroup(), config.binding(), native);
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
        view(*management, launch->start(config.launch(), {STDIN_FILENO, STDOUT_FILENO, STDERR_FILENO}, inherited));
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
                    require(request.has_inspect() || request.has_stop(), "Supervisor supports only inspect and stop");
                    stop = request.has_stop();
                } catch (const std::exception& error) { reply.mutable_error()->set_message(error.what()); }
                if (!reply.has_error()) {
                    // Cleanup errors are fatal and cannot resume child routing.
                    *reply.mutable_view()->mutable_record() = stop ? launch->stop() : launch->inspect();
                }
                management->send(reply.SerializeAsString());
                if (stop) { drain(*management); return 0; }
            }
            launch->pump_channel();
            if (launch->child_completed()) {
                view(*management, launch->stop()); drain(*management); return 0;
            }
            management->flush(); tick();
        }
    } catch (const std::exception& error) {
        std::cerr << "authority supervisor: " << error.what() << '\n';
        if (launch && launch->inspect().revision()) {
            try { launch->stop(); }
            catch (const std::exception& cleanup) { std::cerr << "authority supervisor cleanup unresolved: " << cleanup.what() << '\n'; }
        }
        if (management) {
            try {
                pb::AuthorityLaunchReply reply; reply.set_protocol_version(1);
                reply.mutable_error()->set_message(error.what()); management->send(reply.SerializeAsString()); drain(*management);
            } catch (...) { /* The durable journal retains the cleanup result when delivery fails. */ }
        }
        return 2;
    }
}
