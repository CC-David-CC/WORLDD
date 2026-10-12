#pragma once
#include <filesystem>
#include <string>
#include <sys/types.h>

namespace spatial {
// Owns the compositor process and its private transport endpoints.
// Authority to borrow its transport remains private to the presentation owner.
class RenderProcess final {
public:
    enum class Mode { OwnedWorld, Presentation };
    RenderProcess(const std::filesystem::path& executable, const std::filesystem::path& state,
                  std::string display, int native_fd = -1, Mode mode = Mode::OwnedWorld);
    ~RenderProcess();
    RenderProcess(const RenderProcess&) = delete;
    RenderProcess& operator=(const RenderProcess&) = delete;
    int socket() const { return socket_; }
    int pidfd() const { return pidfd_; }
    pid_t pid() const { return pid_; }
    const std::string& display() const { return display_; }
    void terminate() const;
private:
    int socket_ = -1, pidfd_ = -1;
    pid_t pid_ = -1, creator_ = -1;
    std::string display_;
};
}
