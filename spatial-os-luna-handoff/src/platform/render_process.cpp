#include "platform/render_process.hpp"
#include <cerrno>
#include <csignal>
#include <fcntl.h>
#include <poll.h>
#include <stdexcept>
#include <utility>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>

namespace spatial {
RenderProcess::RenderProcess(const std::filesystem::path& executable, const std::filesystem::path& state,
                             std::string display, int native_fd)
    : display_(std::move(display)) {
    int pair[2];
    if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, pair) != 0)
        throw std::runtime_error("Cannot create renderer channel");
    const auto parent = getpid();
    const auto native_argument = std::to_string(native_fd);
    pid_ = fork();
    if (pid_ == 0) {
        if (prctl(PR_SET_PDEATHSIG, SIGKILL) != 0 || getppid() != parent) _exit(126);
        if (dup2(pair[1], STDIN_FILENO) < 0 || dup2(pair[1], STDOUT_FILENO) < 0) _exit(126);
        close(pair[0]); close(pair[1]);
        if (native_fd >= 0) {
            if (native_fd <= STDERR_FILENO) _exit(126);
            if (native_fd > 3 && syscall(SYS_close_range, 3u, static_cast<unsigned>(native_fd - 1), 0u) != 0) _exit(126);
            if (syscall(SYS_close_range, static_cast<unsigned>(native_fd) + 1u, ~0u, 0u) != 0) _exit(126);
            const int flags = fcntl(native_fd, F_GETFD);
            if (flags < 0 || fcntl(native_fd, F_SETFD, flags & ~FD_CLOEXEC) < 0) _exit(126);
            execl(executable.c_str(), executable.c_str(), "--state-dir", state.c_str(), "--display", display_.c_str(),
                  "--native-security-fd", native_argument.c_str(), static_cast<char*>(nullptr));
            _exit(127);
        }
        execl(executable.c_str(), executable.c_str(), "--state-dir", state.c_str(), "--display", display_.c_str(),
              static_cast<char*>(nullptr));
        _exit(127);
    }
    close(pair[1]);
    if (pid_ < 0) { close(pair[0]); throw std::runtime_error("Cannot fork compositor"); }
    socket_ = pair[0];
    pidfd_ = static_cast<int>(syscall(SYS_pidfd_open, pid_, 0));
    if (pidfd_ < 0) {
        close(socket_); terminate();
        while (waitpid(pid_, nullptr, 0) < 0 && errno == EINTR) {}
        throw std::runtime_error("Cannot monitor compositor");
    }
}
RenderProcess::~RenderProcess() {
    close(socket_);
    pollfd fd{pidfd_, POLLIN, 0};
    int ready;
    do { ready = poll(&fd, 1, 2000); } while (ready < 0 && errno == EINTR);
    if (ready <= 0) terminate();
    while (waitpid(pid_, nullptr, 0) < 0 && errno == EINTR) {}
    close(pidfd_);
}
void RenderProcess::terminate() const { kill(pid_, SIGKILL); }
}
