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
#include <exception>
#include <linux/audit.h>
#include <linux/btrfs.h>
#include <linux/magic.h>
#include <linux/filter.h>
#include <linux/fs.h>
#include <linux/memfd.h>
#include <linux/openat2.h>
#include <linux/seccomp.h>
#include <poll.h>
#include <sched.h>
#include <sstream>
#include <system_error>
#include <sys/ioctl.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/statfs.h>
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
             SYS_reboot, SYS_swapon, SYS_swapoff, SYS_syslog, SYS_io_uring_setup,
             SYS_quotactl, SYS_quotactl_fd}) {
        code.push_back(BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, static_cast<unsigned>(call), 0, 1));
        code.push_back(BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM));
    }
    // A new Btrfs subvolume can evade its parent's quota graph without host capabilities.
    // Keep ordinary reflink copies. Deny other Btrfs-specific controls, including new commands.
    code.push_back(BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, SYS_ioctl, 0, 6));
    code.push_back(BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(seccomp_data, args[1])));
    code.push_back(BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, FICLONE, 4, 0));
    code.push_back(BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, FICLONERANGE, 3, 0));
    code.push_back(BPF_STMT(BPF_ALU | BPF_AND | BPF_K, 0xff00));
    code.push_back(BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, 0x9400, 0, 1));
    code.push_back(BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM));
    code.push_back(BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(seccomp_data, nr)));
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
void ensure_copy_staging_root(int project_fd) {
    const int project_read = openat(project_fd, ".", O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (project_read < 0) throw std::runtime_error("Cannot verify project root for copy staging");
    struct stat project{}, stage{};
    const int project_stat_result = fstat(project_read, &project);
    const int project_stat_error = project_stat_result < 0 ? errno : 0;
    if (project_stat_result < 0 || !S_ISDIR(project.st_mode)) {
        std::ostringstream detail;
        detail << "Cannot verify project root for copy staging (fstat_errno=" << project_stat_error
               << ",mode=" << std::oct << (project.st_mode & 07777) << std::dec << ")";
        std::string message = detail.str();
        if (message.size() > 256) message.resize(256);
        close(project_read);
        throw std::runtime_error(message);
    }
    bool created = false;
    if (mkdirat(project_fd, ".spatial-copy-staging", 0700) == 0) created = true;
    else if (errno != EEXIST) {
        close(project_read);
        throw std::runtime_error("Cannot prepare private copy staging root");
    }
    open_how how{};
    how.flags = O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK;
    how.resolve = RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_MAGICLINKS | RESOLVE_NO_XDEV;
    const int staging = static_cast<int>(syscall(SYS_openat2, project_fd, ".spatial-copy-staging", &how, sizeof(how)));
    if (staging < 0) {
        close(project_read);
        throw std::runtime_error("Copy staging root has an unsafe type or mount");
    }
    const int stage_stat_result = fstat(staging, &stage);
    const int stage_stat_error = stage_stat_result < 0 ? errno : 0;
    const uid_t effective_uid = geteuid();
    const bool valid = stage_stat_result == 0 && S_ISDIR(stage.st_mode) &&
        stage.st_dev == project.st_dev && stage.st_uid == effective_uid && (stage.st_mode & 0777) == 0700;
    struct statfs filesystem{};
    bool same_subvolume = false;
    int filesystem_error = 0;
    int project_subvolume_error = 0;
    int staging_subvolume_error = 0;
    int project_subvolume_result = -1;
    int staging_subvolume_result = -1;
    std::uint64_t project_tree = 0;
    std::uint64_t staging_tree = 0;
    if (valid) {
        if (fstatfs(project_read, &filesystem) < 0) {
            filesystem_error = errno;
        } else {
            if (static_cast<unsigned long>(filesystem.f_type) == BTRFS_SUPER_MAGIC) {
                btrfs_ioctl_get_subvol_info_args project_info{}, stage_info{};
                project_subvolume_result = ioctl(project_read, BTRFS_IOC_GET_SUBVOL_INFO, &project_info);
                if (project_subvolume_result < 0) project_subvolume_error = errno;
                staging_subvolume_result = ioctl(staging, BTRFS_IOC_GET_SUBVOL_INFO, &stage_info);
                if (staging_subvolume_result < 0) staging_subvolume_error = errno;
                project_tree = project_subvolume_result >= 0 ? project_info.treeid : 0;
                staging_tree = staging_subvolume_result >= 0 ? stage_info.treeid : 0;
                same_subvolume = project_subvolume_result >= 0 && staging_subvolume_result >= 0 &&
                    project_tree != 0 && project_tree == staging_tree;
            } else {
                same_subvolume = stage.st_dev == project.st_dev;
            }
        }
    }
    close(staging);
    if (!valid || !same_subvolume) {
        std::ostringstream detail;
        detail << "Copy staging root identity or subvolume is unsafe"
               << " (valid=" << valid << ",same_subvolume=" << same_subvolume
               << ",euid=" << effective_uid
               << ",project_uid=" << project.st_uid << ",project_gid=" << project.st_gid
               << ",project_mode=" << std::oct << (project.st_mode & 07777) << std::dec
               << ",project_dev=" << static_cast<unsigned long long>(project.st_dev)
               << ",project_ino=" << static_cast<unsigned long long>(project.st_ino)
               << ",stage_uid=" << stage.st_uid << ",stage_gid=" << stage.st_gid
               << ",stage_mode=" << std::oct << (stage.st_mode & 07777) << std::dec
               << ",stage_dev=" << static_cast<unsigned long long>(stage.st_dev)
               << ",stage_ino=" << static_cast<unsigned long long>(stage.st_ino)
               << ",project_stat_errno=" << project_stat_error
               << ",stage_stat_errno=" << stage_stat_error
               << ",fstatfs_errno=" << filesystem_error
               << ",fs_type=" << std::hex << static_cast<unsigned long>(filesystem.f_type) << std::dec
               << ",project_subvolume_result=" << project_subvolume_result
               << ",stage_subvolume_result=" << staging_subvolume_result
               << ",project_subvolume_errno=" << project_subvolume_error
               << ",stage_subvolume_errno=" << staging_subvolume_error
               << ",project_tree=" << project_tree << ",stage_tree=" << staging_tree << ")";
        std::string message = detail.str();
        if (message.size() > 768) message.resize(768);
        close(project_read);
        throw std::runtime_error(message);
    }
    if (created && fsync(project_read) < 0) {
        const int error = errno; close(project_read);
        throw std::system_error(error, std::generic_category(), "Cannot synchronize copy staging root");
    }
    close(project_read);
}
}
ApplicationProcess::ApplicationProcess(const std::filesystem::path& project, const std::filesystem::path& repository,
                                      const std::filesystem::path& state, bool writable, const wire::ProcessLaunchRequest& request,
                                      std::function<void()> authority, std::unique_ptr<NativeCredential> native,
                                      std::unique_ptr<ApplicationResources> resources, std::optional<PersistentApplicationStateMount> persistent)
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
    ensure_copy_staging_root(project_fd.fd);
    const std::string bind = writable ? "--bind-fd" : "--ro-bind-fd";
    std::vector<std::string> strings{bwrap, "--unshare-all", "--unshare-user", "--disable-userns", "--die-with-parent", "--new-session",
        "--cap-drop", "ALL", "--hostname", "spatial-workspace", "--clearenv", "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "HOME", "/state/home", "--setenv", "TMPDIR", "/tmp", "--setenv", "XDG_CACHE_HOME", "/state/cache",
        "--setenv", "XDG_CONFIG_HOME", "/state/config", "--setenv", "XDG_DATA_HOME", "/state/data", "--setenv", "LC_ALL", "C.UTF-8",
        "--ro-bind", "/usr/bin", "/usr/bin", "--ro-bind", "/usr/lib", "/usr/lib", "--ro-bind", "/usr/share", "/usr/share",
        "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
        "--ro-bind", "/usr/lib64", "/usr/lib64", "--proc", "/proc", "--dev", "/dev",
        "--bind-fd", "5", "/state", "--bind", (state / "tmp").string(), "/tmp",
        bind, "3", "/project", "--tmpfs", "/project/.spatial-copy-staging",
        "--remount-ro", "/project/.spatial-copy-staging",
        "--symlink", "/project", project.string(), bind, "4", repository.string(),
        "--ro-bind", executable.string(), "/spatial-app-init", "--chdir", "/project"};
    if (native_) {
        const std::vector<std::string> display{
            "--dir", "/run/spatial", "--perms", "0400", "--ro-bind-data", "8", "/run/spatial/Xauthority",
            "--dir", "/tmp/.X11-unix", "--ro-bind-fd", "9", "/tmp/.X11-unix/X"+native_->display_number(),
            "--setenv", "DISPLAY", ":"+native_->display_number(), "--setenv", "XAUTHORITY", "/run/spatial/Xauthority"};
        strings.insert(strings.end(), display.begin(), display.end());
    }
    File persistent_data,persistent_config;
    unsigned persistent_fd=native_?10:8;
    if(persistent) {
        if(persistent->root_fd<0 || (!persistent->data && !persistent->config))
            throw WorldError(wire::STORAGE_ERROR,"Configured persistent mount is incomplete");
        const auto mount=[&](bool selected,const char* name,File& pinned) {
            if(!selected)return;
            pinned.fd=openat(persistent->root_fd,name,O_PATH|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
            if(pinned.fd<0)throw WorldError(wire::STORAGE_ERROR,"Cannot pin declared persistent subdomain");
            const std::vector<std::string> binding{"--bind-fd",std::to_string(persistent_fd++),std::string("/state/")+name};
            strings.insert(strings.end(),binding.begin(),binding.end());
        };
        mount(persistent->data,"data",persistent_data);mount(persistent->config,"config",persistent_config);
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
    if(persistent_data.fd>=0)selected.push_back(persistent_data.fd);
    if(persistent_config.fd>=0)selected.push_back(persistent_config.fd);
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
        workspace::v1::AppStart start; start.set_protocol_version(1); start.set_require_payload_boundary(true); *start.mutable_argv() = request.argv();
        const auto payload = start.SerializeAsString(); const auto length = static_cast<std::uint32_t>(payload.size());
        std::string framed{static_cast<char>(length >> 24), static_cast<char>(length >> 16), static_cast<char>(length >> 8), static_cast<char>(length)};
        framed += payload; write_all(input_write.fd, framed, authority_);
        const auto held=event(Clock::now()+std::chrono::seconds(5));
        if(!held.has_payload_ready() || held.payload_ready()<=1)
            throw std::runtime_error("Application did not confirm its held payload");
        pin_held_payload(held.payload_ready());
        workspace::v1::AppContinue release;release.set_protocol_version(1);release.set_release_payload(true);
        const auto release_bytes=release.SerializeAsString();const auto n=static_cast<std::uint32_t>(release_bytes.size());
        const std::string release_frame{static_cast<char>(n>>24),static_cast<char>(n>>16),static_cast<char>(n>>8),static_cast<char>(n)};
        write_all(input_write.fd,release_frame+release_bytes,authority_);
        close(input_write.fd);input_write.fd=-1;
        const auto ready = event(Clock::now() + std::chrono::seconds(5));
        if (!ready.has_started() || !ready.started()) throw std::runtime_error("Application did not confirm exec");
        authority_();
    } catch (...) { stop_or_exit(); if (output_ >= 0) close(output_); output_ = -1; throw; }
}
void ApplicationProcess::begin_quiescence(){resources_->scope().begin_payload_freeze();}
bool ApplicationProcess::quiescent() const{return resources_->scope().payload_frozen();}
supervisor::v1::PayloadQuiescenceReceipt ApplicationProcess::quiescence_receipt() const{return resources_->scope().payload_receipt();}
void ApplicationProcess::release_quiescence(){resources_->scope().thaw_payload();}
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
            namespace_pid_ = child; namespace_pidfd_ = selected.fd; selected.fd = -1; return;
        }
        pollfd state{pidfd_, POLLIN, 0};
        const auto result = poll(&state, 1, 5);
        if (result > 0 || (result < 0 && errno != EINTR)) throw std::runtime_error("Application boundary exited before namespace pinning");
    }
    throw std::runtime_error("Application namespace startup deadline expired");
}
void ApplicationProcess::pin_held_payload(unsigned expected) {
    struct Identity { pid_t parent=0; unsigned local=0,levels=0; std::uint64_t start=0; dev_t device=0; ino_t inode=0; };
    const auto identity=[](pid_t pid) {
        Identity value;std::string line;
        std::ifstream status("/proc/"+std::to_string(pid)+"/status");
        while(std::getline(status,line)) {
            if(line.starts_with("PPid:")) {std::istringstream input(line.substr(5));input>>value.parent;}
            if(line.starts_with("NSpid:")) {std::istringstream input(line.substr(6));unsigned number=0;while(input>>number){value.local=number;++value.levels;}}
        }
        std::ifstream stat_file("/proc/"+std::to_string(pid)+"/stat");std::string stat_line;
        std::getline(stat_file,stat_line);const auto close=stat_line.rfind(')');
        if(close==std::string::npos)throw std::runtime_error("Cannot inspect held payload start identity");
        std::istringstream fields(stat_line.substr(close+2));std::string field;
        for(unsigned index=0;index<19;++index)if(!(fields>>field))throw std::runtime_error("Malformed held payload start identity");
        if(!(fields>>value.start) || value.start==0)throw std::runtime_error("Missing held payload start identity");
        File ns{open(("/proc/"+std::to_string(pid)+"/ns/pid").c_str(),O_RDONLY|O_CLOEXEC)};
        struct stat inode{};
        if(ns.fd<0 || fstat(ns.fd,&inode)!=0)throw std::runtime_error("Cannot pin held payload PID namespace");
        value.device=inode.st_dev;value.inode=inode.st_ino;return value;
    };
    if(namespace_pid_<=1 || namespace_pidfd_<0 || expected<=1)
        throw std::runtime_error("Held payload requires a retained namespace leader");
    const auto leader=identity(namespace_pid_);
    if(leader.local!=1 || leader.levels<2)throw std::runtime_error("Retained namespace leader identity changed");
    std::array<File,8> retained;
    std::array<pid_t,8> chain{};
    std::array<Identity,8> identities{};
    pid_t parent=namespace_pid_;std::size_t count=0;bool found=false;
    for(;count<retained.size();++count) {
        std::ifstream children("/proc/"+std::to_string(parent)+"/task/"+std::to_string(parent)+"/children");
        pid_t child=0,extra=0;
        if(!(children>>child) || child<=1 || (children>>extra))
            throw std::runtime_error("Held payload requires a bounded unique descendant chain");
        retained[count].fd=static_cast<int>(syscall(SYS_pidfd_open,child,0));
        if(retained[count].fd<0)throw std::runtime_error("Cannot retain held payload chain identity");
        const auto current=identity(child);
        pollfd alive{retained[count].fd,POLLIN,0};
        if(current.parent!=parent || current.levels!=leader.levels || current.local<=1 ||
           current.device!=leader.device || current.inode!=leader.inode || poll(&alive,1,0)!=0)
            throw std::runtime_error("Held payload descendant or namespace identity differs");
        chain[count]=child;identities[count]=current;
        if(current.local==expected){found=true;++count;break;}
        parent=child;
    }
    if(!found)throw std::runtime_error("Reported held payload is absent from the bounded namespace chain");
    pollfd leader_alive{namespace_pidfd_,POLLIN,0};
    if(poll(&leader_alive,1,0)!=0)throw std::runtime_error("Retained namespace leader exited during payload attachment");
    for(std::size_t index=0;index<count;++index) {
        const auto current=identity(chain[index]);const auto& original=identities[index];
        pollfd alive{retained[index].fd,POLLIN,0};
        if(current.parent!=original.parent || current.local!=original.local || current.levels!=original.levels ||
           current.start!=original.start || current.device!=original.device || current.inode!=original.inode || poll(&alive,1,0)!=0)
            throw std::runtime_error("Held payload chain changed before attachment");
    }
    const auto selected=count-1;
    resources_->scope().attach_payload(chain[selected]);
    payload_pid_=chain[selected];payload_start_=identities[selected].start;
    payload_pidfd_=retained[selected].fd;retained[selected].fd=-1;
    const auto attached=identity(payload_pid_);pollfd alive{payload_pidfd_,POLLIN,0};
    if(attached.start!=payload_start_ || attached.local!=expected || attached.device!=leader.device ||
       attached.inode!=leader.inode || poll(&alive,1,0)!=0)
        throw std::runtime_error("Held payload changed during owned cgroup attachment");
    std::cerr<<"owned payload attachment host_pid="<<payload_pid_<<" start_ticks="<<payload_start_
             <<" namespace_pid="<<expected<<" namespace_inode="<<leader.inode<<" chain_nodes="<<count<<'\n';
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
    const auto started = Clock::now();
    const auto deadline = started + std::chrono::seconds(2);
    std::exception_ptr failure;
    const char* failure_operation = nullptr;
    std::exception_ptr completion_failure;
    const auto attempt = [&failure, &failure_operation](const char* label, auto&& action) {
        try { action(); }
        catch (...) {
            if (!failure) { failure = std::current_exception(); failure_operation = label; }
        }
    };
    // Issue every owned termination request before entering any process wait.
    attempt("owned scope termination request", [this] { resources_->scope().request_stop(); });
    const auto signal_fd = [](int fd) {
        if (syscall(SYS_pidfd_send_signal, fd, SIGKILL, nullptr, 0) < 0 && errno != ESRCH)
            throw std::runtime_error("Cannot signal application boundary");
    };
    if (namespace_pidfd_ >= 0) attempt("namespace leader signal", [this, &signal_fd] { signal_fd(namespace_pidfd_); });
    if (pidfd_ >= 0) attempt("application child signal", [this, &signal_fd] { signal_fd(pidfd_); });
    else attempt("application child signal", [this] {
        if (kill(pid_, SIGKILL) < 0 && errno != ESRCH) throw std::runtime_error("Cannot signal unreaped application boundary");
    });

    int poll_result = 0, poll_revents = 0, poll_error = 0;
    bool namespace_exited = namespace_pidfd_ < 0, namespace_wait_failed = false, namespace_deadline_expired = false;
    if (namespace_pidfd_ >= 0) {
        while (!namespace_exited && !failure) {
            pollfd state{namespace_pidfd_, POLLIN, 0};
            poll_result = poll(&state, 1, 10);
            if (poll_result < 0) poll_error = errno;
            poll_revents = state.revents;
            if (poll_result > 0 &&
                (poll_revents == POLLIN || poll_revents == POLLHUP || poll_revents == (POLLIN | POLLHUP))) {
                namespace_exited = true; break;
            }
            if (poll_result < 0 && poll_error == EINTR) {
                if (Clock::now() < deadline) continue;
                namespace_wait_failed = true;
                namespace_deadline_expired = true;
                break;
            }
            if (poll_result < 0 || poll_result > 0 || Clock::now() >= deadline) {
                namespace_wait_failed = true;
                if (poll_result == 0 && Clock::now() >= deadline) namespace_deadline_expired = true;
                break;
            }
        }
    }
    // Scope emptiness and direct-child reap remain mandatory after namespace-wait errors.
    try { resources_->scope().complete_stop(deadline); }
    catch (...) {
        completion_failure = std::current_exception();
        if (!failure && !namespace_wait_failed) {
            failure = completion_failure;
            failure_operation = "owned scope completion";
        }
    }
    const bool stop_failed = static_cast<bool>(failure) || static_cast<bool>(completion_failure) ||
                             namespace_wait_failed || !namespace_exited;
    if (stop_failed) {
        const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(Clock::now() - started).count();
        try {
            const auto& owned = resources_->scope().identity();
            std::cerr << "application stop unconfirmed pid=" << pid_ << " namespace_pid=" << namespace_pid_
                      << " scope=" << owned.scope_name() << " scope_device=" << owned.device()
                      << " scope_inode=" << owned.inode() << " elapsed_us=" << elapsed
                      << " poll_result=" << poll_result << " poll_revents=" << poll_revents;
            if (poll_result < 0) std::cerr << " poll_errno=" << poll_error;
            try {
                std::cerr << " scope_populated=" << resources_->scope().diagnostic_populated()
                          << " child_reaped=" << resources_->scope().diagnostic_child_reaped();
            } catch (...) { /* Keep the primary diagnostic if cgroup inspection fails. */ }
            if (failure) {
                std::cerr << " first_failure=" << (failure_operation ? failure_operation : "owned stop") << ':';
                try { std::rethrow_exception(failure); }
                catch (const std::exception& error) { std::cerr << error.what(); }
                catch (...) { std::cerr << "unknown"; }
            } else if (namespace_wait_failed) std::cerr << " first_failure=namespace leader poll";
            if (completion_failure && completion_failure != failure) {
                std::cerr << " completion_failure=";
                try { std::rethrow_exception(completion_failure); }
                catch (const std::exception& error) { std::cerr << error.what(); }
                catch (...) { std::cerr << "unknown"; }
            }
            std::cerr << '\n';
        } catch (...) { /* Diagnostic failure must not replace the cleanup result. */ }
        if (failure) std::rethrow_exception(failure);
        if (namespace_wait_failed) {
            if (namespace_deadline_expired) throw std::runtime_error("Application namespace exit deadline exceeded");
            if (poll_result < 0)
                throw std::system_error(poll_error, std::generic_category(), "Cannot poll application namespace leader");
            if (poll_result > 0) throw std::runtime_error("Unexpected application namespace poll events");
            throw std::runtime_error("Application namespace exit was not confirmed");
        }
        if (completion_failure) std::rethrow_exception(completion_failure);
        throw std::runtime_error("Cannot confirm application namespace exit");
    }
    if (namespace_pidfd_ >= 0) close(namespace_pidfd_);
    namespace_pidfd_ = -1; namespace_pid_ = -1;
    if(payload_pidfd_>=0)close(payload_pidfd_);
    payload_pidfd_=-1;payload_pid_=-1;payload_start_=0;
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
