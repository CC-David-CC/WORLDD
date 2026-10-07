#include "workspace/git_command.hpp"
#include "world/store.hpp"
#include <array>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <fcntl.h>
#include <poll.h>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <unistd.h>

namespace spatial {
namespace {
// This handler runs only in the dedicated Git job supervisor's own process group.
void terminate_git_job(int) { kill(0, SIGKILL); _exit(126); }
}
GitResult git_command(const std::filesystem::path& directory, const std::vector<std::string>& arguments) {
    std::vector<std::string> strings{"/usr/bin/git", "--no-pager", "-c", "core.hooksPath=/dev/null",
        "-c", "core.fsmonitor=false", "-c", "core.fsync=all", "-c", "protocol.allow=never",
        "-c", "protocol.file.allow=always", "-c", "gc.auto=0", "-c", "maintenance.auto=false", "-C", directory.string()};
    strings.insert(strings.end(), arguments.begin(), arguments.end());
    std::vector<char*> argv;
    for (auto& value : strings) {
        if (value.find('\0') != std::string::npos) throw std::invalid_argument("NUL in Git argument");
        argv.push_back(value.data());
    }
    argv.push_back(nullptr);
    std::vector<std::string> environment{"PATH=/usr/bin:/bin", "LC_ALL=C", "HOME=/nonexistent",
        "GIT_CONFIG_NOSYSTEM=1", "GIT_CONFIG_GLOBAL=/dev/null", "GIT_TERMINAL_PROMPT=0",
        "GIT_ATTR_NOSYSTEM=1", "GIT_OPTIONAL_LOCKS=0"};
    std::vector<char*> env;
    for (auto& value : environment) env.push_back(value.data());
    env.push_back(nullptr);
    int descriptors[2];
    if (pipe2(descriptors, O_CLOEXEC) != 0) throw std::runtime_error("Cannot create Git output pipe");
    const auto parent = getpid();
    const auto child = fork();
    if (child == 0) {
        if (setpgid(0, 0) != 0) _exit(126);
        signal(SIGTERM, terminate_git_job);
        if (prctl(PR_SET_PDEATHSIG, SIGTERM) != 0 || getppid() != parent) terminate_git_job(0);
        const auto supervisor = getpid();
        const auto git = fork();
        if (git == 0) {
            if (prctl(PR_SET_PDEATHSIG, SIGKILL) != 0 || getppid() != supervisor) _exit(126);
            const int null = open("/dev/null", O_RDWR | O_CLOEXEC);
            if (null < 0 || dup2(null, STDIN_FILENO) < 0 || dup2(descriptors[1], STDOUT_FILENO) < 0 ||
                dup2(null, STDERR_FILENO) < 0) _exit(126);
            close(null); close(descriptors[0]); close(descriptors[1]);
            execve(argv[0], argv.data(), env.data()); _exit(127);
        }
        close(descriptors[0]); close(descriptors[1]); close(STDIN_FILENO); close(STDOUT_FILENO);
        if (git < 0) _exit(126);
        int status = 0;
        while (waitpid(git, &status, 0) < 0) { if (errno != EINTR) _exit(126); }
        if (WIFEXITED(status)) _exit(WEXITSTATUS(status));
        signal(WTERMSIG(status), SIG_DFL); raise(WTERMSIG(status)); _exit(126);
    }
    close(descriptors[1]);
    if (child < 0) { close(descriptors[0]); throw std::runtime_error("Cannot fork Git"); }
    // Establish the owned group from both sides of the fork before cancellation.
    if (setpgid(child, child) != 0 && errno != ESRCH) {
        kill(child, SIGKILL); while (waitpid(child, nullptr, 0) < 0 && errno == EINTR) {}
        close(descriptors[0]); throw std::runtime_error("Cannot isolate administrative Git job");
    }
    bool reaped = false;
    try {
        const int flags = fcntl(descriptors[0], F_GETFL);
        if (flags < 0 || fcntl(descriptors[0], F_SETFL, flags | O_NONBLOCK) < 0)
            throw std::runtime_error("Cannot configure Git output");
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(8);
        std::string output;
        bool eof = false, exited = false;
        int status = 0;
        while (!eof || !exited) {
            if (std::chrono::steady_clock::now() >= deadline) throw WorldError(wire::TIMEOUT, "Git command deadline exceeded; inspect operation state");
            pollfd fd{eof ? -1 : descriptors[0], POLLIN, 0};
            const auto ready = poll(&fd, 1, 10);
            if (ready < 0 && errno != EINTR) throw std::runtime_error("Cannot poll Git output");
            if (ready > 0 && fd.revents) {
                std::array<char, 65536> bytes{};
                const auto count = read(descriptors[0], bytes.data(), bytes.size());
                if (count == 0) eof = true;
                else if (count > 0) {
                    if (output.size() + static_cast<std::size_t>(count) > 8 * 1024 * 1024)
                        throw std::runtime_error("Git output exceeds limit");
                    output.append(bytes.data(), static_cast<std::size_t>(count));
                } else if (errno != EINTR && errno != EAGAIN) throw std::runtime_error("Cannot read Git output");
            }
            if (!exited) {
                siginfo_t info{};
                const auto result = waitid(P_PID, child, &info, WEXITED | WNOHANG | WNOWAIT);
                if (result == 0 && info.si_pid == child) exited = true;
                else if (result < 0 && errno != EINTR) throw std::runtime_error("Cannot wait for Git");
            }
        }
        // Keep the leader waitable until every output holder exits. Its PID cannot be reused during cancellation.
        kill(-child, SIGKILL); // Stop remaining members that closed their output early.
        while (waitpid(child, &status, 0) < 0) { if (errno != EINTR) throw std::runtime_error("Cannot reap Git supervisor"); }
        reaped = true;
        close(descriptors[0]);
        return {WIFEXITED(status) ? WEXITSTATUS(status) : -WTERMSIG(status), std::move(output)};
    } catch (...) {
        if (!reaped) { kill(-child, SIGKILL); while (waitpid(child, nullptr, 0) < 0 && errno == EINTR) {} }
        close(descriptors[0]); throw;
    }
}
}
