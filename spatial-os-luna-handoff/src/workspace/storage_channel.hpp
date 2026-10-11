#pragma once
#include <chrono>
#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <sys/types.h>

namespace spatial {
// Private inherited Unix stream. Authenticate every received fragment.
// The channel consumes its descriptor, including on construction failure.
class StorageChannel final {
public:
    using Clock = std::chrono::steady_clock;
    StorageChannel(int descriptor, uid_t peer_uid, std::optional<pid_t> peer_pid, int borrowed_peer_pidfd = -1);
    ~StorageChannel();
    void send(const std::string& payload, Clock::time_point deadline,
              const std::function<void()>& tick = {});
    // An idle server waits until input, EOF, or peer death. Started frames are bounded.
    std::optional<std::string> receive(Clock::time_point deadline, bool idle_server = false,
                                      const std::function<void()>& tick = {});
    pid_t peer_pid() const;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
