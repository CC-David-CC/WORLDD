#pragma once
#include "launch.pb.h"
#include <filesystem>
#include <memory>
#include <sys/types.h>

namespace spatial { class BrokerClient; }
namespace spatial::supervisor {
class ControlAdmin;
// Private session ownership. Worlds never own these management channels.
class SessionBroker final {
public:
    SessionBroker(const std::filesystem::path& supervisor_executable,
        const std::filesystem::path& private_config_path,
        v1::AuthoritySupervisorConfig config, int borrowed_native_config);
    ~SessionBroker();
    SessionBroker(const SessionBroker&) = delete;
    SessionBroker& operator=(const SessionBroker&) = delete;
    BrokerClient& root();
    ControlAdmin& administrative();
    const v1::AuthorityLaunchRecord& launch() const;
    pid_t supervisor_pid() const;
    pid_t broker_pid() const;
    void check_alive() const;
    v1::AuthorityLaunchRecord stop();
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
