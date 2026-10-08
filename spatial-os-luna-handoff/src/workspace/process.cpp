#include "core/protocol.hpp"
#include "workspace/process.hpp"
#include "world/store.hpp"
#include <array>
#include <algorithm>
#include <cerrno>
#include <csignal>
#include <cstddef>
#include <fcntl.h>
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/memfd.h>
#include <linux/seccomp.h>
#include <poll.h>
#include <sched.h>
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
                                      std::function<void()> authority) : authority_(std::move(authority)) {
    authority_();
    const auto executable = std::filesystem::read_symlink("/proc/self/exe").parent_path() / "spatial-app-init";
    const char* bwrap = "/usr/local/libexec/spatial-bwrap";
    struct stat trusted{};
    if (lstat(bwrap, &trusted) != 0 || !S_ISREG(trusted.st_mode) || trusted.st_uid != 0 || (trusted.st_mode & 0022))
        throw WorldError(wire::UNAVAILABLE, "Install the reviewed execution bootstrap first");
    File project_fd{directory(project)}, repo_fd{directory(repository)}, state_fd{directory(state)}, policy{filter()};
    const std::string bind = writable ? "--bind-fd" : "--ro-bind-fd";
    std::vector<std::string> strings{bwrap, "--unshare-all", "--unshare-user", "--disable-userns", "--die-with-parent", "--new-session",
        "--cap-drop", "ALL", "--hostname", "spatial-workspace", "--clearenv", "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "HOME", "/state/home", "--setenv", "TMPDIR", "/tmp", "--setenv", "XDG_CACHE_HOME", "/state/cache",
        "--setenv", "XDG_CONFIG_HOME", "/state/config", "--setenv", "XDG_DATA_HOME", "/state/data", "--setenv", "LC_ALL", "C.UTF-8",
        "--ro-bind", "/usr/bin", "/usr/bin", "--ro-bind", "/usr/lib", "/usr/lib", "--ro-bind", "/usr/share", "/usr/share",
        "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
        "--ro-bind", "/usr/lib64", "/usr/lib64", "--proc", "/proc", "--dev", "/dev",
        "--bind-fd", std::to_string(state_fd.fd), "/state", "--bind", (state / "tmp").string(), "/tmp",
        bind, std::to_string(project_fd.fd), "/project", "--symlink", "/project", project.string(),
        bind, std::to_string(repo_fd.fd), repository.string(),
        "--ro-bind", executable.string(), "/spatial-app-init", "--chdir", "/project",
        "--seccomp", std::to_string(policy.fd), "--remount-ro", "/", "--", "/spatial-app-init"};
    std::vector<char*> argv;
    for (auto& value : strings) argv.push_back(value.data());
    argv.push_back(nullptr);
    File input_read, input_write, output_read, output_write;
    int descriptors[2];
    if (pipe2(descriptors, O_CLOEXEC) != 0) throw std::runtime_error("Cannot create app configuration pipe");
    input_read.fd = descriptors[0]; input_write.fd = descriptors[1];
    if (pipe2(descriptors, O_CLOEXEC) != 0) throw std::runtime_error("Cannot create app event pipe");
    output_read.fd = descriptors[0]; output_write.fd = descriptors[1];
    const auto parent = getpid();
    pid_ = fork();
    if (pid_ == 0) {
        if (prctl(PR_SET_PDEATHSIG, SIGKILL) != 0 || getppid() != parent) _exit(126);
        const int diagnostic = open((state / "setup.log").c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
        if (diagnostic < 0 || dup2(input_read.fd, 0) < 0 || dup2(output_write.fd, 1) < 0 || dup2(diagnostic, 2) < 0) _exit(126);
        // Only explicit mount and policy descriptors survive into Bubblewrap.
        if (syscall(SYS_close_range, 3u, ~0u, 4u /* CLOSE_RANGE_CLOEXEC */) != 0) _exit(126);
        for (const int fd : {project_fd.fd, repo_fd.fd, state_fd.fd, policy.fd}) if (fcntl(fd, F_SETFD, 0) < 0) _exit(126);
        char path[] = "PATH=/usr/bin:/bin"; char* env[]{path, nullptr};
        execve(bwrap, argv.data(), env); _exit(127);
    }
    if (pid_ < 0) throw std::runtime_error("Cannot fork application boundary");
    close(input_read.fd); input_read.fd = -1; close(output_write.fd); output_write.fd = -1;
    output_ = output_read.fd; output_read.fd = -1;
    try {
        pidfd_ = static_cast<int>(syscall(SYS_pidfd_open, pid_, 0));
        if (pidfd_ < 0 || fcntl(output_, F_SETFL, O_NONBLOCK) < 0 || fcntl(input_write.fd, F_SETFL, O_NONBLOCK) < 0)
            throw std::runtime_error("Cannot monitor application boundary");
        workspace::v1::AppStart start; start.set_protocol_version(1); *start.mutable_argv() = request.argv();
        const auto payload = start.SerializeAsString(); const auto length = static_cast<std::uint32_t>(payload.size());
        std::string framed{static_cast<char>(length >> 24), static_cast<char>(length >> 16), static_cast<char>(length >> 8), static_cast<char>(length)};
        framed += payload; write_all(input_write.fd, framed, authority_); close(input_write.fd); input_write.fd = -1;
        const auto ready = event(Clock::now() + std::chrono::seconds(5));
        if (!ready.has_started() || !ready.started()) throw std::runtime_error("Application did not confirm exec");
        authority_();
    } catch (...) { stop(); if (output_ >= 0) close(output_); output_ = -1; throw; }
}
ApplicationProcess::~ApplicationProcess() { stop(); if (output_ >= 0) close(output_); }
void ApplicationProcess::stop() {
    if (pid_ <= 0) return;
    if (pidfd_ >= 0) syscall(SYS_pidfd_send_signal, pidfd_, SIGKILL, nullptr, 0);
    else kill(pid_, SIGKILL); // Still our unreaped child.
    while (waitpid(pid_, nullptr, 0) < 0 && errno == EINTR) {}
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
