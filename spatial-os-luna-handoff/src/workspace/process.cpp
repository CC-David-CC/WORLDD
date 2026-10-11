#include "core/protocol.hpp"
#include "workspace/process.hpp"
#include "workspace/native_launch.hpp"
#include "workspace/resources.hpp"
#include "world/store.hpp"
#include <array>
#include <algorithm>
#include <cerrno>
#include <csignal>
#include <cstddef>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/memfd.h>
#include <linux/seccomp.h>
#include <poll.h>
#include <sched.h>
#include <sstream>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

namespace spatial {
namespace {
using Clock = std::chrono::steady_clock;
struct File { int fd = -1; ~File() { if (fd >= 0) close(fd); } };
void write_all(int fd, const std::string& bytes, const std::function<void()>& authority = [] {}) {
    std::size_t used = 0;
    const auto deadline = Clock::now() + std::chrono::seconds(5);
    while (used < bytes.size()) {
        authority();
        const auto n = write(fd, bytes.data() + used, bytes.size() - used);
        if (n < 0 && errno == EINTR) continue;
        if (n < 0 && errno == EAGAIN && Clock::now() < deadline) {
            pollfd target{fd, POLLOUT, 0}; poll(&target, 1, 10); continue;
        }
        if (n <= 0) throw std::runtime_error("Cannot send application configuration");
        used += static_cast<std::size_t>(n);
    }
}
int filter() {
#if !defined(__x86_64__)
#error Define and independently qualify the native syscall policy for this architecture.
#endif
    std::vector<sock_filter> code{
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(seccomp_data, arch)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AUDIT_ARCH_X86_64, 1, 0),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(seccomp_data, nr)),
        BPF_JUMP(BPF_JMP | BPF_JSET | BPF_K, 0x40000000, 0, 1),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
    };
    for (const int call : {SYS_mount, SYS_umount2, SYS_pivot_root, SYS_chroot, SYS_setns, SYS_unshare,
             SYS_fsopen, SYS_fsconfig, SYS_fsmount, SYS_move_mount, SYS_open_tree, SYS_mount_setattr,
             SYS_bpf, SYS_keyctl, SYS_add_key, SYS_request_key, SYS_perf_event_open, SYS_userfaultfd,
             SYS_ptrace, SYS_process_vm_readv, SYS_process_vm_writev, SYS_mknod, SYS_mknodat,
             SYS_init_module, SYS_finit_module, SYS_delete_module, SYS_kexec_load, SYS_kexec_file_load,
             SYS_reboot, SYS_swapon, SYS_swapoff, SYS_syslog, SYS_io_uring_setup}) {
        code.push_back(BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, static_cast<unsigned>(call), 0, 1));
        code.push_back(BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM));
    }
    // glibc can fall back to clone. Its ordinary fork/thread flags remain available.
    code.push_back(BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, SYS_clone3, 0, 1));
    code.push_back(BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | ENOSYS));
    code.push_back(BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, SYS_clone, 0, 3));
    code.push_back(BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(seccomp_data, args[0])));
    code.push_back(BPF_JUMP(BPF_JMP | BPF_JSET | BPF_K,
        CLONE_NEWUSER | CLONE_NEWNS | CLONE_NEWPID | CLONE_NEWNET | CLONE_NEWIPC | CLONE_NEWUTS | CLONE_NEWCGROUP, 0, 1));
    code.push_back(BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM));
    code.push_back(BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW));
    File fd; fd.fd = static_cast<int>(syscall(SYS_memfd_create, "spatial-syscalls", MFD_CLOEXEC | MFD_ALLOW_SEALING));
    if (fd.fd < 0) throw std::runtime_error("Cannot allocate syscall policy");
    write_all(fd.fd, std::string(reinterpret_cast<const char*>(code.data()), code.size() * sizeof(sock_filter)));
    if (lseek(fd.fd, 0, SEEK_SET) < 0 || fcntl(fd.fd, F_ADD_SEALS, F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL) < 0)
        throw std::runtime_error("Cannot seal syscall policy");
    const int result = fd.fd; fd.fd = -1; return result;
}
int directory(const std::filesystem::path& path) {
    const int fd = open(path.c_str(), O_PATH | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) throw std::runtime_error("Cannot pin application mount");
    return fd;
}
}
ApplicationProcess::ApplicationProcess(const std::filesystem::path& project, const std::filesystem::path& repository,
                                      const std::filesystem::path& state, bool writable, const wire::ProcessLaunchRequest& request,
                                      std::function<void()> authority, std::unique_ptr<NativeCredential> native,
                                      std::unique_ptr<ApplicationResources> resources)
    : authority_(std::move(authority)), native_(std::move(native)), resources_(std::move(resources)) {
    if (!resources_) throw WorldError(wire::UNAVAILABLE, "Application resource scope is required");
    authority_();
    const auto executable = std::filesystem::read_symlink("/proc/self/exe").parent_path() / "spatial-app-init";
    const char* bwrap = "/usr/local/libexec/spatial-bwrap";
    struct stat trusted{};
    if (lstat(bwrap, &trusted) != 0 || !S_ISREG(trusted.st_mode) || trusted.st_uid != 0 || (trusted.st_mode & 0022))
        throw WorldError(wire::UNAVAILABLE, "Install the reviewed execution bootstrap first");
    // Hold the sandbox before exec until its namespace leader has a pinned pidfd.
    int launch_pipe[2];
    if (pipe2(launch_pipe, O_CLOEXEC) != 0) throw std::runtime_error("Cannot hold application startup");
    File launch_read{launch_pipe[0]}, launch_write{launch_pipe[1]};
    File project_fd{directory(project)}, repo_fd{directory(repository)}, state_fd{directory(state)}, policy{filter()};
    const std::string bind = writable ? "--bind-fd" : "--ro-bind-fd";
    std::vector<std::string> strings{bwrap, "--unshare-all", "--unshare-user", "--disable-userns", "--die-with-parent", "--new-session",
        "--cap-drop", "ALL", "--hostname", "spatial-workspace", "--clearenv", "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "HOME", "/state/home", "--setenv", "TMPDIR", "/tmp", "--setenv", "XDG_CACHE_HOME", "/state/cache",
        "--setenv", "XDG_CONFIG_HOME", "/state/config", "--setenv", "XDG_DATA_HOME", "/state/data", "--setenv", "LC_ALL", "C.UTF-8",
        "--ro-bind", "/usr/bin", "/usr/bin", "--ro-bind", "/usr/lib", "/usr/lib", "--ro-bind", "/usr/share", "/usr/share",
        "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
        "--ro-bind", "/usr/lib64", "/usr/lib64", "--proc", "/proc", "--dev", "/dev",
        "--bind-fd", "5", "/state", "--bind", (state / "tmp").string(), "/tmp",
        bind, "3", "/project", "--symlink", "/project", project.string(),
        bind, "4", repository.string(),
        "--ro-bind", executable.string(), "/spatial-app-init", "--chdir", "/project"};
    if (native_) {
        const std::vector<std::string> display{
            "--dir", "/run/spatial", "--perms", "0400", "--ro-bind-data", "8", "/run/spatial/Xauthority",
            "--dir", "/tmp/.X11-unix", "--ro-bind-fd", "9", "/tmp/.X11-unix/X"+native_->display_number(),
            "--setenv", "DISPLAY", ":"+native_->display_number(), "--setenv", "XAUTHORITY", "/run/spatial/Xauthority"};
        strings.insert(strings.end(), display.begin(), display.end());
    }
    const std::vector<std::string> tail{"--block-fd", "7", "--seccomp", "6", "--remount-ro", "/", "--", "/spatial-app-init"};
    strings.insert(strings.end(), tail.begin(), tail.end());
    File input_read, input_write, output_read, output_write;
    int descriptors[2];
    if (pipe2(descriptors, O_CLOEXEC) != 0) throw std::runtime_error("Cannot create app configuration pipe");
    input_read.fd = descriptors[0]; input_write.fd = descriptors[1];
    if (pipe2(descriptors, O_CLOEXEC) != 0) throw std::runtime_error("Cannot create app event pipe");
    output_read.fd = descriptors[0]; output_write.fd = descriptors[1];
    File diagnostic{open((state / "setup.log").c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600)};
    if (diagnostic.fd < 0) throw std::runtime_error("Cannot open application setup log");
    supervisor::v1::AuthorityProcessLaunch launch;
    launch.set_executable(bwrap);
    for (std::size_t i = 1; i < strings.size(); ++i) launch.add_arguments(strings[i]);
    std::vector<int> selected{project_fd.fd, repo_fd.fd, state_fd.fd, policy.fd, launch_read.fd};
    if (native_) { selected.push_back(native_->authority_fd()); selected.push_back(native_->socket_fd()); }
    for (unsigned i = 0; i < selected.size(); ++i) launch.add_inherited_fds(3+i);
    resources_->scope().start(launch, {input_read.fd, output_write.fd, diagnostic.fd}, selected);
    pid_ = resources_->scope().direct_child_pid();
    close(launch_read.fd); launch_read.fd = -1;
    close(input_read.fd); input_read.fd = -1; close(output_write.fd); output_write.fd = -1;
    output_ = output_read.fd; output_read.fd = -1;
    try {
        pidfd_ = resources_->scope().duplicate_child_pidfd();
        if (pidfd_ < 0 || fcntl(output_, F_SETFL, O_NONBLOCK) < 0 || fcntl(input_write.fd, F_SETFL, O_NONBLOCK) < 0)
            throw std::runtime_error("Cannot monitor application boundary");
        pin_namespace();
        write_all(launch_write.fd, "1", authority_);
        close(launch_write.fd); launch_write.fd = -1;
        workspace::v1::AppStart start; start.set_protocol_version(1); *start.mutable_argv() = request.argv();
        const auto payload = start.SerializeAsString(); const auto length = static_cast<std::uint32_t>(payload.size());
        std::string framed{static_cast<char>(length >> 24), static_cast<char>(length >> 16), static_cast<char>(length >> 8), static_cast<char>(length)};
        framed += payload; write_all(input_write.fd, framed, authority_); close(input_write.fd); input_write.fd = -1;
        const auto ready = event(Clock::now() + std::chrono::seconds(5));
        if (!ready.has_started() || !ready.started()) throw std::runtime_error("Application did not confirm exec");
        authority_();
    } catch (...) { stop_or_exit(); if (output_ >= 0) close(output_); output_ = -1; throw; }
}
ApplicationProcess::~ApplicationProcess() { stop_or_exit(); if (output_ >= 0) close(output_); }
void ApplicationProcess::pin_namespace() {
    const auto deadline = Clock::now()+std::chrono::seconds(5);
    while (Clock::now() < deadline) {
        authority_();
        std::ifstream children("/proc/"+std::to_string(pid_)+"/task/"+std::to_string(pid_)+"/children");
        pid_t child = 0, extra = 0;
        if (children >> child) {
            if (child <= 0 || (children >> extra)) throw std::runtime_error("Unexpected application namespace children");
            File selected{static_cast<int>(syscall(SYS_pidfd_open, child, 0))};
            if (selected.fd < 0) throw std::runtime_error("Cannot pin application namespace leader");
            std::ifstream status("/proc/"+std::to_string(child)+"/status");
            std::string line; bool parent_matches = false, namespace_matches = false;
            while (std::getline(status, line)) {
                if (line.starts_with("PPid:")) {
                    std::istringstream value(line.substr(5)); pid_t parent = 0;
                    parent_matches = static_cast<bool>(value >> parent) && parent == pid_;
                } else if (line.starts_with("NSpid:")) {
                    std::istringstream values(line.substr(6)); pid_t current = 0, last = 0; unsigned count = 0;
                    while (values >> current) { last = current; ++count; }
                    namespace_matches = count >= 2 && last == 1;
                }
            }
            pollfd state{selected.fd, POLLIN, 0};
            if (!parent_matches || !namespace_matches || poll(&state, 1, 0) != 0)
                throw std::runtime_error("Application namespace identity is unavailable");
            namespace_pidfd_ = selected.fd; selected.fd = -1; return;
        }
        pollfd state{pidfd_, POLLIN, 0};
        const auto result = poll(&state, 1, 5);
        if (result > 0 || (result < 0 && errno != EINTR)) throw std::runtime_error("Application boundary exited before namespace pinning");
    }
    throw std::runtime_error("Application namespace startup deadline expired");
}
void ApplicationProcess::stop_or_exit() noexcept {
    try { stop(); }
    catch (const std::exception& error) {
        std::cerr << "workspaced: application cleanup unconfirmed: " << error.what() << '\n';
        // A failed destructor must never let the broker acknowledge cancelled execution.
        _exit(2);
    }
}
void ApplicationProcess::stop() {
    if (pid_ <= 0) return;
    const auto signal_fd = [](int fd) {
        if (syscall(SYS_pidfd_send_signal, fd, SIGKILL, nullptr, 0) < 0 && errno != ESRCH)
            throw std::runtime_error("Cannot signal application boundary");
    };
    if (namespace_pidfd_ >= 0) signal_fd(namespace_pidfd_);
    if (pidfd_ >= 0) signal_fd(pidfd_);
    else if (kill(pid_, SIGKILL) < 0 && errno != ESRCH) // Still our unreaped child.
        throw std::runtime_error("Cannot signal unreaped application boundary");
    const auto deadline = Clock::now()+std::chrono::seconds(2);
    if (namespace_pidfd_ >= 0) {
        for (;;) {
            pollfd state{namespace_pidfd_, POLLIN, 0};
            const auto result = poll(&state, 1, 10);
            if (result > 0 && (state.revents & POLLIN)) break;
            if (result > 0 || (result < 0 && errno != EINTR) || Clock::now() >= deadline)
                throw std::runtime_error("Cannot confirm application namespace exit");
        }
    }
    resources_->scope().stop();
    if (namespace_pidfd_ >= 0) close(namespace_pidfd_);
    namespace_pidfd_ = -1;
    if (pidfd_ >= 0) close(pidfd_);
    pidfd_ = -1; pid_ = -1;
}
void ApplicationProcess::read_all(char* bytes, std::size_t length, Clock::time_point deadline) {
    std::size_t used = 0;
    while (used < length) {
        authority_();
        const auto n = read(output_, bytes + used, length - used);
        if (n > 0) { used += static_cast<std::size_t>(n); continue; }
        if (n < 0 && errno == EINTR) continue;
        if (n == 0 || (n < 0 && errno != EAGAIN)) throw std::runtime_error("Application supervisor channel closed");
        if (Clock::now() >= deadline) throw WorldError(wire::TIMEOUT, "Application event deadline");
        pollfd fd{output_, POLLIN, 0};
        if (poll(&fd, 1, 10) < 0 && errno != EINTR) throw std::runtime_error("Cannot poll application supervisor");
    }
}
workspace::v1::AppEvent ApplicationProcess::event(Clock::time_point deadline) {
    std::array<unsigned char, 4> header{}; read_all(reinterpret_cast<char*>(header.data()), 4, deadline);
    const auto size = (std::uint32_t(header[0]) << 24) | (std::uint32_t(header[1]) << 16) | (std::uint32_t(header[2]) << 8) | header[3];
    if (size > 4096) throw std::runtime_error("Excessive application event");
    std::string bytes(size, '\0'); read_all(bytes.data(), size, deadline);
    workspace::v1::AppEvent reply;
    if (!reply.ParseFromString(bytes) || reply.protocol_version() != 1) throw std::runtime_error("Invalid application event");
    validate_message(reply);
    if (reply.has_error()) throw std::runtime_error(reply.error());
    return reply;
}
std::optional<int> ApplicationProcess::wait(double seconds) {
    if (result_) return result_;
    const auto deadline = Clock::now()+std::chrono::duration<double>(seconds);
    pollfd fd{output_, POLLIN, 0};
    for (;;) {
        authority_();
        const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(deadline-Clock::now()).count();
        const auto ready = poll(&fd, 1, static_cast<int>(std::clamp<std::int64_t>(remaining, 0, 10)));
        if (ready > 0) break;
        if (ready < 0 && errno != EINTR) throw std::runtime_error("Cannot poll application completion");
        if (Clock::now() >= deadline) return {};
    }
    const auto reply = event(Clock::now() + std::chrono::seconds(1));
    if (!reply.has_exit_code()) throw std::runtime_error("Missing actual application exit");
    result_ = reply.exit_code(); stop(); return result_;
}
}
