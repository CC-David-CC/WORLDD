#include "supervisor/process_scope.hpp"
#include "core/protocol.hpp"
#include <cerrno>
#include <chrono>
#include <climits>
#include <csignal>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <linux/close_range.h>
#include <linux/magic.h>
#include <poll.h>
#include <sstream>
#include <set>
#include <stdexcept>
#include <string>
#include <sys/prctl.h>
#include <sys/random.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

namespace spatial::supervisor {
namespace {
using Clock = std::chrono::steady_clock;
struct Fd {
    int value = -1;
    explicit Fd(int fd = -1) : value(fd) {}
    ~Fd() { if (value >= 0) close(value); }
    Fd(const Fd&) = delete;
    Fd& operator=(const Fd&) = delete;
};
void check(bool okay, const char* message) {
    if (!okay) throw std::runtime_error(std::string(message) + ": " + std::strerror(errno));
}
void valid(bool okay, const char* message) {
    if (!okay) throw std::invalid_argument(message);
}
std::string contents(int directory, const char* name) {
    Fd fd(openat(directory, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW));
    check(fd.value >= 0, "Cannot open cgroup state");
    std::string result; char buffer[4096];
    for (;;) {
        auto size = read(fd.value, buffer, sizeof(buffer));
        if (size < 0 && errno == EINTR) continue;
        check(size >= 0, "Cannot read cgroup state");
        if (!size) return result;
        result.append(buffer, static_cast<std::size_t>(size));
        valid(result.size() <= 65536, "Excessive cgroup state");
    }
}
void control(int directory, const char* name, const std::string& value) {
    Fd fd(openat(directory, name, O_WRONLY | O_CLOEXEC | O_NOFOLLOW));
    check(fd.value >= 0, "Cannot open cgroup control");
    ssize_t size;
    do { size = write(fd.value, value.data(), value.size()); } while (size < 0 && errno == EINTR);
    check(size == static_cast<ssize_t>(value.size()), "Cannot write cgroup control");
}
void tick() { poll(nullptr, 0, 5); }
[[noreturn]] void child_failure(int gate, int error) {
    (void)send(gate, &error, sizeof(error), MSG_NOSIGNAL); _exit(126);
}
}

std::unique_ptr<ProcessScope> ProcessScope::prepare(const std::filesystem::path& root) {
    auto result = std::unique_ptr<ProcessScope>(new ProcessScope);
    result->parent_ = open(root.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    check(result->parent_ >= 0, "Cannot open delegated cgroup");
    struct statfs fs{}; struct stat parent{}, above{};
    check(fstatfs(result->parent_, &fs) == 0 && fstat(result->parent_, &parent) == 0,
          "Cannot inspect delegated cgroup");
    Fd upper(openat(result->parent_, "..", O_RDONLY | O_DIRECTORY | O_CLOEXEC));
    check(upper.value >= 0 && fstat(upper.value, &above) == 0, "Cannot inspect cgroup parent");
    valid(fs.f_type == CGROUP2_SUPER_MAGIC && parent.st_uid == geteuid() &&
          !(parent.st_dev == above.st_dev && parent.st_ino == above.st_ino), "Expected owned delegated cgroup v2 directory");
    valid(contents(result->parent_, "cgroup.type") == "domain\n", "Expected domain cgroup");
    std::string random(32, '\0'); std::size_t received = 0;
    while (received != random.size()) {
        auto count = getrandom(random.data()+received, random.size()-received, 0);
        if (count < 0 && errno == EINTR) continue;
        check(count > 0, "Cannot create launch identity"); received += static_cast<std::size_t>(count);
    }
    std::string name = "authority-"; constexpr char hex[] = "0123456789abcdef";
    for (unsigned char c : random) { name += hex[c >> 4]; name += hex[c & 15]; }
    std::ifstream boot("/proc/sys/kernel/random/boot_id"); std::string boot_id; std::getline(boot, boot_id);
    valid(boot_id.size() == 36, "Cannot read kernel boot identity");
    result->identity_.set_launch_id(random); result->identity_.set_boot_id(boot_id);
    check(mkdirat(result->parent_, name.c_str(), 0700) == 0, "Cannot create authority scope");
    result->identity_.set_scope_name(name);
    result->scope_ = openat(result->parent_, name.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    check(result->scope_ >= 0, "Cannot retain authority scope");
    struct stat owned{}; check(fstat(result->scope_, &owned) == 0, "Cannot identify authority scope");
    result->identity_.set_device(owned.st_dev); result->identity_.set_inode(owned.st_ino);
    return result;
}

std::unique_ptr<ProcessScope> ProcessScope::launch(const std::filesystem::path& root,
    const v1::AuthorityProcessLaunch& request, const std::array<int, 3>& streams, const std::vector<int>& inherited) {
    auto result = prepare(root);
    result->start(request, streams, inherited);
    return result;
}

void ProcessScope::start(const v1::AuthorityProcessLaunch& request,
    const std::array<int, 3>& streams, const std::vector<int>& inherited) {
    valid(!started_ && !removed_, "Authority scope cannot start again");
    validate_message(request);
    valid(inherited.size() <= 16 && static_cast<std::size_t>(request.inherited_fds_size()) == inherited.size(),
          "Invalid inherited descriptor table size");
    std::set<unsigned> targets;
    for (const auto target : request.inherited_fds())
        valid(target >= 3 && target <= 255 && targets.insert(target).second, "Invalid inherited descriptor target");
    const auto& exe = request.executable();
    valid(!exe.empty() && exe.front() == '/' && exe.size() <= 4096 && exe.find('\0') == std::string::npos,
          "Authority executable must be an absolute path");
    valid(request.arguments_size() <= 128, "Too many authority arguments");
    std::size_t size = exe.size();
    std::vector<char*> argv{const_cast<char*>(exe.c_str())};
    for (const auto& arg : request.arguments()) {
        size += arg.size()+1;
        valid(size <= 65536 && arg.find('\0') == std::string::npos, "Invalid authority argument");
        argv.push_back(const_cast<char*>(arg.c_str()));
    }
    argv.push_back(nullptr);
    // Do not inherit provider credentials or supervisor secrets through the environment.
    char path[] = "PATH=/usr/bin:/bin", lang[] = "LANG=C.UTF-8";
    char* environment[]{path, lang, nullptr};
    std::array<Fd, 3> copied;
    for (std::size_t i = 0; i < copied.size(); ++i) {
        copied[i].value = fcntl(streams[i], F_DUPFD_CLOEXEC, 256);
        check(copied[i].value >= 0, "Cannot retain explicit authority stream");
    }
    std::vector<Fd> selected(inherited.size());
    for (std::size_t i = 0; i < inherited.size(); ++i) {
        selected[i].value = fcntl(inherited[i], F_DUPFD_CLOEXEC, 256);
        check(selected[i].value >= 0, "Cannot retain selected authority descriptor");
    }
    int pair[2]; check(socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, pair) == 0, "Cannot create startup gate");
    Fd gate(pair[0]), original_child_gate(pair[1]);
    Fd child_gate(fcntl(original_child_gate.value, F_DUPFD_CLOEXEC, 256));
    check(child_gate.value >= 0, "Cannot retain private startup gate");
    close(original_child_gate.value); original_child_gate.value = -1;
    const pid_t owner = getpid();
    started_ = true;
    child_ = fork(); check(child_ >= 0, "Cannot fork authority");
    if (!child_) {
        close(gate.value);
        if (prctl(PR_SET_PDEATHSIG, SIGKILL) < 0) child_failure(child_gate.value, errno);
        if (getppid() != owner) child_failure(child_gate.value, ECHILD);
        char token; ssize_t count;
        do { count = read(child_gate.value, &token, 1); } while (count < 0 && errno == EINTR);
        if (count != 1 || token != 'G') child_failure(child_gate.value, EPIPE);
        // Select targets only after marking all private descriptors close-on-exec.
        // Retained sources and the gate are above the complete permitted target range.
        if (syscall(SYS_close_range, 3U, UINT_MAX, CLOSE_RANGE_CLOEXEC) < 0) child_failure(child_gate.value, errno);
        for (int i = 0; i < 3; ++i) if (dup2(copied[i].value, i) < 0) child_failure(child_gate.value, errno);
        for (std::size_t i = 0; i < selected.size(); ++i)
            if (dup2(selected[i].value, static_cast<int>(request.inherited_fds(static_cast<int>(i)))) < 0)
                child_failure(child_gate.value, errno);
        execve(exe.c_str(), argv.data(), environment);
        const int error = errno;
        (void)send(child_gate.value, &error, sizeof(error), MSG_NOSIGNAL);
        _exit(127);
    }
    close(child_gate.value); child_gate.value = -1;
    pidfd_ = static_cast<int>(syscall(SYS_pidfd_open, child_, 0));
    check(pidfd_ >= 0, "Cannot pin authority child");
    control(scope_, "cgroup.procs", std::to_string(child_)+"\n");
    valid(populated(), "Authority did not enter its owned scope");
    check(send(gate.value, "G", 1, MSG_NOSIGNAL) == 1, "Cannot release authority startup gate");
    pollfd ready{gate.value, POLLIN | POLLHUP, 0}; int polled;
    do { polled = poll(&ready, 1, 2000); } while (polled < 0 && errno == EINTR);
    check(polled > 0, "Authority exec deadline exceeded");
    int error = 0; ssize_t count;
    do { count = read(gate.value, &error, sizeof(error)); } while (count < 0 && errno == EINTR);
    if (count > 0) { errno = error; check(false, "Authority exec failed"); }
    check(count == 0, "Cannot confirm authority exec");
}

bool ProcessScope::populated() const {
    std::istringstream input(contents(scope_, "cgroup.events"));
    std::string key; unsigned long long value; bool found = false, populated = false;
    while (input >> key >> value) {
        if (key == "populated") {
            valid(!found && value <= 1, "Invalid cgroup population state");
            found = true; populated = value != 0;
        }
    }
    valid(found, "Missing cgroup population state"); return populated;
}
bool ProcessScope::child_exited() const {
    if (reaped_ || child_ < 0) return true;
    valid(pidfd_ >= 0, "Direct child was not pinned");
    pollfd event{pidfd_, POLLIN, 0}; int result;
    do { result = poll(&event, 1, 0); } while (result < 0 && errno == EINTR);
    check(result >= 0 && !(event.revents & (POLLERR | POLLNVAL)), "Cannot inspect child exit");
    return event.revents & POLLIN;
}
v1::ProcessScopeState ProcessScope::state() const {
    v1::ProcessScopeState result; *result.mutable_identity() = identity_;
    result.set_populated(!removed_ && populated()); result.set_started(started_);
    result.set_direct_child_exited(started_ && child_exited()); return result;
}
bool ProcessScope::exited_successfully() const {
    valid(started_ && child_ > 0 && !reaped_, "No retained child exit status");
    siginfo_t info{};
    int result;
    // The direct child remains unreaped and its pidfd remains owned until stop().
    do { result = waitid(P_PID, static_cast<id_t>(child_), &info, WEXITED | WNOHANG | WNOWAIT); }
    while (result < 0 && errno == EINTR);
    check(result == 0, "Cannot inspect retained child exit status");
    return info.si_pid == child_ && info.si_code == CLD_EXITED && info.si_status == 0;
}
v1::ProcessScopeStopped ProcessScope::stop() {
    if (!removed_) {
        if (scope_ >= 0) control(scope_, "cgroup.kill", "1\n");
        // Also cover a startup failure before cgroup migration. The direct child remains unreaped.
        if (child_ > 0 && !reaped_) {
            const int rc = pidfd_ >= 0 ? static_cast<int>(syscall(SYS_pidfd_send_signal, pidfd_, SIGKILL, nullptr, 0)) : kill(child_, SIGKILL);
            check(rc == 0 || errno == ESRCH, "Cannot terminate owned authority child");
        }
        const auto deadline = Clock::now()+std::chrono::seconds(2);
        while (scope_ >= 0 && populated()) {
            valid(Clock::now() < deadline, "Authority scope remains populated"); tick();
        }
        while (child_ > 0 && !reaped_) {
            int status; const auto waited = waitpid(child_, &status, WNOHANG);
            if (waited < 0 && errno == EINTR) continue;
            check(waited >= 0, "Cannot reap authority child");
            if (waited == child_) { reaped_ = true; break; }
            valid(Clock::now() < deadline, "Authority child reap deadline exceeded"); tick();
        }
        if (!identity_.scope_name().empty()) {
            struct stat named{};
            check(fstatat(parent_, identity_.scope_name().c_str(), &named, AT_SYMLINK_NOFOLLOW) == 0,
                  "Cannot find owned scope for removal");
            valid(identity_.inode() && identity_.device() == static_cast<std::uint64_t>(named.st_dev) &&
                  identity_.inode() == static_cast<std::uint64_t>(named.st_ino), "Owned scope identity changed");
            check(unlinkat(parent_, identity_.scope_name().c_str(), AT_REMOVEDIR) == 0, "Cannot remove owned scope");
        }
        removed_ = true;
    }
    v1::ProcessScopeStopped result; *result.mutable_identity() = identity_; return result;
}
ProcessScope::~ProcessScope() {
    try { stop(); }
    catch (const std::exception& error) {
        std::cerr << "authority process cleanup unconfirmed: " << error.what() << '\n'; _exit(2);
    }
    for (int fd : {pidfd_, scope_, parent_}) if (fd >= 0) close(fd);
}
}
