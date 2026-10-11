#include "workspace/git_command.hpp"
#include "world/store.hpp"
#include <algorithm>
#include <array>
#include <cerrno>
#include <fcntl.h>
#include <iostream>
#include <poll.h>
#include <set>
#include <sys/stat.h>
#include <unistd.h>
#include <utility>

namespace spatial {
namespace {
struct File {
    int fd = -1;
    explicit File(int value = -1) : fd(value) {}
    ~File() { if (fd >= 0) close(fd); }
    File(const File&) = delete;
    File& operator=(const File&) = delete;
    File(File&& other) noexcept : fd(std::exchange(other.fd, -1)) {}
    File& operator=(File&& other) noexcept {
        if (this != &other) { if (fd >= 0) close(fd); fd = std::exchange(other.fd, -1); }
        return *this;
    }
};
void require(bool value, const char* message) {
    if (!value) throw std::invalid_argument(message);
}
std::filesystem::path pin_directory(const std::filesystem::path& input, File& descriptor) {
    require(input.is_absolute() && input.string().find('\0') == std::string::npos,
            "Git worker paths must be absolute");
    const auto canonical = std::filesystem::canonical(input);
    descriptor.fd = open(canonical.c_str(), O_PATH | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (descriptor.fd < 0) throw std::runtime_error("Cannot pin Git worker directory");
    struct stat info{};
    if (fstat(descriptor.fd, &info) != 0 || !S_ISDIR(info.st_mode))
        throw std::runtime_error("Git worker mount is not a directory");
    return canonical;
}
bool inside(const std::filesystem::path& child, const std::filesystem::path& parent) {
    auto a = child.begin(), b = parent.begin();
    for (; b != parent.end(); ++a, ++b) if (a == child.end() || *a != *b) return false;
    return true;
}
void append_read(int fd, std::string& output, std::size_t limit) {
    std::array<char, 65536> bytes{};
    for (;;) {
        const auto count = read(fd, bytes.data(), bytes.size());
        if (count == 0) return;
        if (count < 0) {
            if (errno == EINTR) continue;
            if (errno == EAGAIN || errno == EWOULDBLOCK) return;
            throw std::runtime_error("Cannot read Git worker output");
        }
        if (output.size() + static_cast<std::size_t>(count) > limit)
            throw WorldError(wire::RESOURCE_EXHAUSTED, "Git worker output exceeds its limit");
        output.append(bytes.data(), static_cast<std::size_t>(count));
    }
}
}

GitResult git_command(supervisor::ProcessScope& scope, const std::filesystem::path& working_directory,
                      const std::vector<std::string>& arguments, const std::vector<GitMount>& requested_mounts,
                      std::chrono::seconds timeout) {
    require(!arguments.empty() && arguments.size() <= 64 && timeout.count() > 0 && timeout <= std::chrono::minutes(5),
            "Invalid Git worker request");
    const char* bwrap = "/usr/local/libexec/spatial-bwrap";
    struct stat trusted{};
    if (lstat(bwrap, &trusted) != 0 || !S_ISREG(trusted.st_mode) || trusted.st_uid != 0 || (trusted.st_mode & 0022))
        throw WorldError(wire::UNAVAILABLE, "Install the reviewed Git worker bootstrap first");

    std::vector<File> mount_fds;
    std::vector<GitMount> mounts;
    mount_fds.reserve(requested_mounts.size()); mounts.reserve(requested_mounts.size());
    require(!requested_mounts.empty() && requested_mounts.size() <= 12,
            "Git worker requires a bounded mount set");
    for (const auto& requested : requested_mounts) {
        File descriptor;
        const auto path = pin_directory(requested.path, descriptor);
        require(path != "/" && path != "/tmp" && path != "/home" && path != "/var" && path != "/opt" &&
                path != "/root" && path != "/mnt", "Git worker mount root is too broad");
        for (const auto* denied : {"/usr", "/bin", "/lib", "/lib64", "/proc", "/dev", "/sys", "/etc", "/run"})
            require(!inside(path, denied), "Git worker cannot mount system directories");
        for (const auto& earlier : mounts)
            require(!inside(path, earlier.path) && !inside(earlier.path, path),
                    "Git worker mounts must not overlap");
        GitMount mount{path, requested.writable, {}};
        for (const auto& hidden : requested.hidden_directories) {
            const auto canonical_hidden = std::filesystem::canonical(hidden);
            require(canonical_hidden.parent_path() == path && canonical_hidden != path,
                    "Hidden Git worker paths must be direct mount children");
            struct stat hidden_info{};
            if (lstat(canonical_hidden.c_str(), &hidden_info) != 0 || !S_ISDIR(hidden_info.st_mode))
                throw std::invalid_argument("Hidden Git worker path must be a real directory");
            mount.hidden_directories.push_back(canonical_hidden);
        }
        mounts.push_back(std::move(mount));
        mount_fds.push_back(std::move(descriptor));
    }
    const auto cwd = std::filesystem::canonical(working_directory);
    require(std::any_of(mounts.begin(), mounts.end(), [&](const auto& mount) { return inside(cwd, mount.path); }),
            "Git worker working directory is outside its mounts");

    std::vector<std::string> command{bwrap, "--unshare-all", "--unshare-user", "--disable-userns",
        "--die-with-parent", "--new-session", "--cap-drop", "ALL", "--hostname", "spatial-git-worker",
        "--clearenv", "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "HOME", "/nonexistent",
        "--setenv", "TMPDIR", "/tmp", "--setenv", "LC_ALL", "C", "--setenv", "GIT_CONFIG_NOSYSTEM", "1",
        "--setenv", "GIT_CONFIG_GLOBAL", "/dev/null", "--setenv", "GIT_TERMINAL_PROMPT", "0",
        "--setenv", "GIT_ATTR_NOSYSTEM", "1", "--setenv", "GIT_OPTIONAL_LOCKS", "0",
        "--ro-bind", "/usr/bin", "/usr/bin", "--ro-bind", "/usr/lib", "/usr/lib",
        "--ro-bind", "/usr/share", "/usr/share", "--symlink", "usr/bin", "/bin",
        "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
        "--ro-bind", "/usr/lib64", "/usr/lib64", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"};
    std::set<std::filesystem::path> parents;
    for (const auto& mount : mounts) {
        auto parent = mount.path.parent_path();
        while (parent != "/" && parent != "/tmp") { parents.insert(parent); parent = parent.parent_path(); }
    }
    for (const auto& parent : parents) { command.push_back("--dir"); command.push_back(parent.string()); }
    std::vector<int> inherited;
    for (std::size_t index = 0; index < mounts.size(); ++index) {
        command.push_back(mounts[index].writable ? "--bind-fd" : "--ro-bind-fd");
        command.push_back(std::to_string(3 + index)); command.push_back(mounts[index].path.string());
        inherited.push_back(mount_fds[index].fd);
    }
    for (const auto& mount : mounts) for (const auto& hidden : mount.hidden_directories) {
        command.push_back("--tmpfs"); command.push_back(hidden.string());
    }
    command.insert(command.end(), {"--chdir", cwd.string(), "--remount-ro", "/", "--", "/usr/bin/git",
        "--no-pager", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.fsync=all",
        "-c", "protocol.allow=never", "-c", "protocol.file.allow=always", "-c", "gc.auto=0",
        "-c", "maintenance.auto=false"});
    std::size_t total = 0;
    for (const auto& argument : command) {
        require(argument.find('\0') == std::string::npos, "NUL in Git worker argument");
        total += argument.size() + 1;
        require(total <= 65536, "Git worker argument limit exceeded");
    }
    for (const auto& argument : arguments) {
        require(argument.find('\0') == std::string::npos, "NUL in Git argument");
        total += argument.size() + 1;
        require(total <= 65536, "Git argument limit exceeded");
        command.push_back(argument);
    }
    supervisor::v1::AuthorityProcessLaunch launch;
    launch.set_executable(bwrap);
    for (std::size_t index = 1; index < command.size(); ++index) launch.add_arguments(command[index]);
    require(launch.arguments_size() <= 128, "Git worker argument count exceeded");
    for (std::size_t index = 0; index < inherited.size(); ++index) launch.add_inherited_fds(3 + index);

    int output_pipe[2], error_pipe[2];
    if (pipe2(output_pipe, O_CLOEXEC) != 0)
        throw std::runtime_error("Cannot create Git worker output pipes");
    File output_read(output_pipe[0]), output_write(output_pipe[1]);
    if (pipe2(error_pipe, O_CLOEXEC) != 0)
        throw std::runtime_error("Cannot create Git worker diagnostic pipe");
    File error_read(error_pipe[0]), error_write(error_pipe[1]);
    // Only the broker reads asynchronously. Git and its filters retain normal pipe backpressure.
    for (const auto fd : {output_read.fd, error_read.fd}) {
        const int flags = fcntl(fd, F_GETFL);
        if (flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0)
            throw std::runtime_error("Cannot configure Git worker output pipe");
    }
    File null(open("/dev/null", O_RDWR | O_CLOEXEC));
    if (null.fd < 0) throw std::runtime_error("Cannot open Git worker null input");
    scope.start(launch, {null.fd, output_write.fd, error_write.fd}, inherited);
    close(output_write.fd); output_write.fd = -1;
    close(error_write.fd); error_write.fd = -1;

    std::string output, diagnostics;
    bool output_eof = false, error_eof = false;
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    int status = -1;
    while (status < 0) {
        if (std::chrono::steady_clock::now() >= deadline) {
            (void)scope.stop();
            throw WorldError(wire::TIMEOUT, "Git worker deadline exceeded; inspect operation state");
        }
        pollfd descriptors[2]{{output_eof ? -1 : output_read.fd, POLLIN | POLLHUP, 0},
                              {error_eof ? -1 : error_read.fd, POLLIN | POLLHUP, 0}};
        const auto ready = poll(descriptors, 2, 10);
        if (ready < 0 && errno != EINTR) { (void)scope.stop(); throw std::runtime_error("Cannot poll Git worker"); }
        if (descriptors[0].revents) {
            append_read(output_read.fd, output, 8 * 1024 * 1024);
            output_eof = descriptors[0].revents & POLLHUP;
        }
        if (descriptors[1].revents) {
            append_read(error_read.fd, diagnostics, 1024 * 1024);
            error_eof = descriptors[1].revents & POLLHUP;
        }
        if (scope.state().direct_child_exited()) {
            status = scope.exit_status();
            (void)scope.stop(); // Kill filters or helpers that outlive the top-level Git process.
        }
    }
    while (!output_eof || !error_eof) {
        pollfd descriptors[2]{{output_eof ? -1 : output_read.fd, POLLIN | POLLHUP, 0},
                              {error_eof ? -1 : error_read.fd, POLLIN | POLLHUP, 0}};
        const auto ready = poll(descriptors, 2, 100);
        if (ready < 0 && errno != EINTR) throw std::runtime_error("Cannot drain Git worker output");
        if (descriptors[0].revents) { append_read(output_read.fd, output, 8 * 1024 * 1024); output_eof = descriptors[0].revents & POLLHUP; }
        if (descriptors[1].revents) { append_read(error_read.fd, diagnostics, 1024 * 1024); error_eof = descriptors[1].revents & POLLHUP; }
    }
    // This is the broker's private diagnostic stream, separate from actor replies.
    if (status != 0) std::cerr << "Git worker exited with status " << status << ": " << diagnostics << '\n';
    return {status, std::move(output)};
}
}
