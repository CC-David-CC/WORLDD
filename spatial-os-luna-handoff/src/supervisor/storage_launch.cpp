#include "supervisor/storage_launch.hpp"
#include "supervisor/process_scope.hpp"
#include "platform/process_handle.hpp"
#include "identity/tls_support.hpp"
#include "core/protocol.hpp"
#include <array>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <fcntl.h>
#include <iostream>
#include <fstream>
#include <poll.h>
#include <sys/random.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

namespace spatial::supervisor {
namespace {
using File = identity::tls_detail::Fd;
using Clock = std::chrono::steady_clock;
constexpr const char* owner_binary = "/usr/local/libexec/spatial-storage-owner";
void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
bool safe_path(const std::string& value) {
    return !value.empty() && value.size() <= 4096 && std::filesystem::path(value).is_absolute() &&
        value.find('\0') == std::string::npos && value.find_first_of(" \t\r\n\v\f\\\"$%") == std::string::npos;
}
void protected_file(const std::filesystem::path& path, bool executable) {
    require(path.is_absolute() && std::filesystem::canonical(path) == path, "Storage launch input must be canonical");
    for (auto parent = path.parent_path();; parent = parent.parent_path()) {
        struct stat info{};
        require(lstat(parent.c_str(), &info) == 0 && S_ISDIR(info.st_mode) && info.st_uid == 0 && !(info.st_mode & 0022),
                "Storage launch input has a mutable parent");
        if (parent == "/") break;
    }
    File file(open(path.c_str(), O_PATH | O_NOFOLLOW | O_CLOEXEC));
    struct stat info{};
    require(file.value >= 3 && fstat(file.value, &info) == 0 && S_ISREG(info.st_mode) && info.st_uid == 0 &&
        info.st_nlink == 1 && !(info.st_mode & 0022) && (executable ? bool(info.st_mode & 0111) : !(info.st_mode & 0077)),
        "Storage launch input must be a protected operator file");
}
pid_t spawn(const std::vector<std::string>& command, const std::array<int, 3>& streams) {
    std::vector<char*> arguments;
    for (const auto& argument : command) arguments.push_back(const_cast<char*>(argument.c_str()));
    arguments.push_back(nullptr);
    const auto parent = getpid();
    const auto child = fork(); require(child >= 0, "Cannot fork storage service controller");
    if (child == 0) {
        if (getppid() != parent || syscall(SYS_close_range, 3U, ~0U, CLOSE_RANGE_CLOEXEC) < 0) _exit(126);
        for (unsigned index = 0; index < streams.size(); ++index)
            if (dup2(streams[index], static_cast<int>(index)) < 0) _exit(126);
        char path[] = "PATH=/usr/bin:/bin", locale[] = "LANG=C.UTF-8";
        char* environment[]{path, locale, nullptr};
        execve(command[0].c_str(), arguments.data(), environment); _exit(127);
    }
    return child;
}
bool wait_child(pid_t child, std::chrono::milliseconds duration, int& status) {
    const auto deadline = Clock::now()+duration;
    for (;;) {
        const auto result = waitpid(child, &status, WNOHANG);
        if (result == child) return true;
        require(result == 0 || (result < 0 && errno == EINTR), "Storage controller ownership lost");
        if (Clock::now() >= deadline) return false;
        poll(nullptr, 0, 10);
    }
}
std::string control(const std::vector<std::string>& command, int diagnostic_stream) {
    int pipe[2]; require(pipe2(pipe, O_CLOEXEC | O_NONBLOCK) == 0, "Cannot capture storage service state");
    File input(open("/dev/null", O_RDONLY | O_CLOEXEC)), read(pipe[0]), write(pipe[1]);
    require(input.value >= 3, "Cannot protect storage controller input");
    const auto child = spawn(command, {input.value, write.value, diagnostic_stream});
    write.reset(); int status = 0;
    if (!wait_child(child, std::chrono::seconds(8), status)) {
        // This unreaped direct child still owns its numeric process identity.
        kill(child, SIGKILL);
        while (waitpid(child, &status, WNOHANG) < 0 && errno == EINTR) {}
        throw std::runtime_error("Storage service control deadline exceeded");
    }
    std::string output; std::array<char, 4096> bytes{};
    for (;;) {
        const auto count = ::read(read.value, bytes.data(), bytes.size());
        if (!count || (count < 0 && errno == EAGAIN)) break;
        if (count < 0 && errno == EINTR) continue;
        require(count > 0 && output.size()+count <= 4096, "Storage controller output rejected");
        output.append(bytes.data(), count);
    }
    require(WIFEXITED(status) && WEXITSTATUS(status) == 0, "Storage service control failed");
    return output;
}
std::string unit_name() {
    std::array<unsigned char, 16> bytes{}; std::size_t offset = 0;
    while (offset < bytes.size()) {
        const auto count = getrandom(bytes.data()+offset, bytes.size()-offset, 0);
        if (count < 0 && errno == EINTR) continue;
        require(count > 0, "Cannot assign private storage service identity"); offset += count;
    }
    std::string name = "spatial-storage-owner-";
    for (auto byte : bytes) { name += "0123456789abcdef"[byte >> 4]; name += "0123456789abcdef"[byte & 15]; }
    return name+".service";
}
}
void validate_storage_launch(const workspace::v1::StorageOwnerLaunchConfig& config,
                            const std::filesystem::path& volume) {
    validate_message(config);
    require(config.protocol_version() == 1 && config.has_budget() && safe_path(config.owner_configuration()) &&
        safe_path(config.volume_root()) && volume == config.volume_root() && std::filesystem::canonical(volume) == volume,
        "Storage launch requires one explicit canonical volume and protected policy");
    validate_budget(config.budget());
    require(config.budget().cpu_microseconds()%10 == 0, "Storage service CPU budget requires systemd's 0.01 percent precision");
    protected_file(config.owner_configuration(), false); protected_file(owner_binary, true);
}
struct StorageOwnerLaunch::Impl {
    std::string name;
    std::filesystem::path cgroup;
    pid_t child = -1;
    int diagnostics;
    bool reaped = false, stopped = false;
    int status = 0;
    explicit Impl(int stream) : diagnostics(stream) {}
    void stop() {
        if (stopped) return;
        if (!reaped) reaped = wait_child(child, std::chrono::milliseconds(100), status);
        if (!reaped) {
            control({"/usr/bin/sudo", "-n", "/usr/bin/systemctl", "stop", name}, diagnostics);
            require(wait_child(child, std::chrono::seconds(8), status), "Storage service controller did not stop");
            reaped = true;
        }
        const auto state = control({"/usr/bin/systemctl", "show", name, "-p", "LoadState", "-p", "ActiveState"}, diagnostics);
        require(state.find("ActiveState=inactive\n") != std::string::npos || state.find("ActiveState=failed\n") != std::string::npos,
                "Storage owner cleanup remains unconfirmed");
        if (std::filesystem::exists(cgroup)) {
            std::ifstream events(cgroup / "cgroup.events");
            const std::string contents((std::istreambuf_iterator<char>(events)), std::istreambuf_iterator<char>());
            require(events && contents.find("populated 0\n") != std::string::npos,
                    "Storage owner process subtree remains populated");
        }
        stopped = true;
    }
    ~Impl() {
        if (child <= 0) return;
        try { stop(); }
        catch (const std::exception& error) { std::cerr << "Storage owner cleanup uncertain: " << name << ": " << error.what() << '\n'; }
    }
};
StorageOwnerLaunch::StorageOwnerLaunch(const workspace::v1::StorageOwnerLaunchConfig& config,
    const std::filesystem::path& volume, int channel, int broker_pidfd, int diagnostics,
    const std::function<void()>& before_ready)
    : impl_(std::make_unique<Impl>(diagnostics)) {
    try {
    validate_storage_launch(config, volume);
    require(channel >= 3 && broker_pidfd >= 3 && diagnostics >= 2 && diagnostics != channel && diagnostics != broker_pidfd,
            "Storage launch requires private live handles");
    auto broker = ProcessHandle::take(fcntl(broker_pidfd, F_DUPFD_CLOEXEC, 3));
    int type = 0, domain = 0; socklen_t size = sizeof(type);
    require(getsockopt(channel, SOL_SOCKET, SO_TYPE, &type, &size) == 0 && type == SOCK_STREAM,
            "Storage launch requires its inherited stream");
    size = sizeof(domain);
    require(getsockopt(channel, SOL_SOCKET, SO_DOMAIN, &domain, &size) == 0 && domain == AF_UNIX,
            "Storage launch requires a private Unix channel");
    int credentials = 0; size = sizeof(credentials);
    require(getsockopt(channel, SOL_SOCKET, SO_PASSCRED, &credentials, &size) == 0 && credentials == 1,
            "Storage launch requires sender credentials before broker startup");
    impl_->name = unit_name();
    std::cerr << "Storage owner unit: " << impl_->name << '\n';
    const auto& budget = config.budget();
    std::vector<std::string> command{"/usr/bin/sudo", "-n", "/usr/bin/systemd-run", "--quiet", "--wait", "--pipe", "--collect",
        "--expand-environment=no", "--service-type=exec", "--unit="+impl_->name};
    auto property = [&](std::string value) { command.emplace_back("-p"); command.push_back(std::move(value)); };
    property("MemoryMax="+std::to_string(budget.memory_bytes()));
    property("MemoryHigh="+std::to_string(budget.memory_bytes()/4*3));
    property("MemorySwapMax="+std::to_string(budget.swap_bytes()));
    property("TasksMax="+std::to_string(budget.processes()));
    const auto cpu = budget.cpu_microseconds();
    auto quota = std::to_string(cpu/1000);
    if (cpu%1000) {
        auto fraction = std::to_string(cpu%1000/10); fraction.insert(0, 2-fraction.size(), '0');
        quota += "."+fraction;
    }
    property("CPUQuota="+quota+"%");
    property("CPUQuotaPeriodSec=100ms");
    property("TimeoutStopSec=3s"); property("PrivateNetwork=yes"); property("RestrictAddressFamilies=AF_UNIX");
    property("NoNewPrivileges=yes"); property("ProtectSystem=strict"); property("ProtectHome=read-only");
    property("ReadWritePaths="+volume.string()); property("PrivateTmp=yes");
    property("CapabilityBoundingSet=CAP_SYS_ADMIN CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER CAP_KILL");
    for (const auto& io : budget.io()) {
        const auto device = "/dev/block/"+std::to_string(io.major())+":"+std::to_string(io.minor());
        property("IOReadBandwidthMax="+device+" "+std::to_string(io.read_bytes_per_second()));
        property("IOWriteBandwidthMax="+device+" "+std::to_string(io.write_bytes_per_second()));
    }
    command.insert(command.end(), {"--", owner_binary, "--config", config.owner_configuration(),
                                  "--channel-stdin", "--broker-pidfd-stdout"});
    impl_->child = spawn(command, {channel, broker.descriptor(), diagnostics});
    const auto deadline = Clock::now()+std::chrono::seconds(5);
    for (;;) {
        check_alive();
        const auto state = control({"/usr/bin/systemctl", "show", impl_->name, "-p", "ActiveState"}, diagnostics);
        if (state == "ActiveState=active\n") {
            const auto group = control({"/usr/bin/systemctl", "show", impl_->name, "-p", "ControlGroup", "--value"}, diagnostics);
            require(group == "/system.slice/"+impl_->name+"\n", "Storage owner cgroup identity rejected");
            impl_->cgroup = std::filesystem::path("/sys/fs/cgroup/system.slice") / impl_->name;
            if (before_ready) before_ready();
            break;
        }
        require(Clock::now() < deadline, "Storage owner service startup deadline exceeded");
        poll(nullptr, 0, 10);
    }
    } catch (const std::exception& error) {
        std::string reason = error.what();
        bool confirmed = impl_->child <= 0;
        if (!confirmed) {
            try { impl_->stop(); confirmed = true; }
            catch (const std::exception& cleanup) { reason += "; storage cleanup unconfirmed: " + std::string(cleanup.what()); }
        }
        throw StorageLaunchFailure(reason, impl_->name, confirmed);
    }
}
StorageOwnerLaunch::~StorageOwnerLaunch() = default;
void StorageOwnerLaunch::check_alive() {
    require(!impl_->stopped && !impl_->reaped, "Storage owner launcher is unavailable");
    impl_->reaped = wait_child(impl_->child, std::chrono::milliseconds(0), impl_->status);
    require(!impl_->reaped, "Storage owner launcher exited");
}
void StorageOwnerLaunch::stop() { impl_->stop(); }
const std::string& StorageOwnerLaunch::unit() const { return impl_->name; }
}
