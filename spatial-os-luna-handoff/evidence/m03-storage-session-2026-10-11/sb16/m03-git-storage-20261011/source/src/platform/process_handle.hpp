#pragma once
#include <cerrno>
#include <charconv>
#include <fcntl.h>
#include <fstream>
#include <poll.h>
#include <stdexcept>
#include <string>
#include <sys/syscall.h>
#include <unistd.h>
#include <utility>

namespace spatial {
// Retains a kernel process identity. Never reconstruct this from a stored PID.
class ProcessHandle final {
    int fd_ = -1;
    pid_t pid_ = -1;
    ProcessHandle(int fd, pid_t pid) : fd_(fd), pid_(pid) {}
public:
    ProcessHandle() = default;
    ~ProcessHandle() { if (fd_ >= 0) close(fd_); }
    ProcessHandle(const ProcessHandle&) = delete;
    ProcessHandle& operator=(const ProcessHandle&) = delete;
    ProcessHandle(ProcessHandle&& other) noexcept
        : fd_(std::exchange(other.fd_, -1)), pid_(std::exchange(other.pid_, -1)) {}
    ProcessHandle& operator=(ProcessHandle&& other) noexcept {
        if (this != &other) {
            if (fd_ >= 0) close(fd_);
            fd_ = std::exchange(other.fd_, -1); pid_ = std::exchange(other.pid_, -1);
        }
        return *this;
    }
    int descriptor() const { return fd_; }
    pid_t pid() const { return pid_; }
    int release() { pid_ = -1; return std::exchange(fd_, -1); }
    bool live() const {
        if (fd_ < 3) return false;
        pollfd event{fd_, POLLIN, 0}; int result;
        do { result = poll(&event, 1, 0); } while (result < 0 && errno == EINTR);
        return result == 0;
    }
    // Takes ownership, including on validation failure.
    static ProcessHandle take(int fd, pid_t expected = -1) {
        ProcessHandle handle(fd, -1);
        if (fd < 3 || !(fcntl(fd, F_GETFD) & FD_CLOEXEC) ||
            syscall(SYS_pidfd_send_signal, fd, 0, nullptr, 0) != 0 || !handle.live())
            throw std::runtime_error("Expected a live close-on-exec process handle");
        std::ifstream info("/proc/self/fdinfo/" + std::to_string(fd));
        std::string line;
        while (std::getline(info, line)) {
            if (line.rfind("Pid:\t", 0) != 0) continue;
            const auto value = line.substr(5);
            const auto parsed = std::from_chars(value.data(), value.data() + value.size(), handle.pid_);
            if (parsed.ec != std::errc{} || parsed.ptr != value.data() + value.size()) handle.pid_ = -1;
        }
        if (handle.pid_ <= 1 || (expected > 0 && handle.pid_ != expected) || !handle.live())
            throw std::runtime_error("Process handle identity mismatch or process exit");
        return handle;
    }
    static ProcessHandle duplicate(int borrowed, pid_t expected) {
        if (borrowed < 3 || expected <= 1) throw std::runtime_error("Missing retained process identity");
        return take(fcntl(borrowed, F_DUPFD_CLOEXEC, 3), expected);
    }
};
}
