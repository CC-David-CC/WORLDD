#pragma once
#include "core/protocol.hpp"
#include <chrono>
#include <filesystem>
#include <functional>
#include <sys/types.h>

namespace spatial {
// Lifecycle belongs to the session supervisor, not a world authority.
class BrokerProcess {
public:
    BrokerProcess(const std::filesystem::path& executable, const std::filesystem::path& state);
    ~BrokerProcess();
    BrokerProcess(const BrokerProcess&) = delete;
    BrokerProcess& operator=(const BrokerProcess&) = delete;
    int channel() const { return channel_; }
    pid_t pid() const { return pid_; }
    void release_channel();
private:
    int channel_ = -1, pidfd_ = -1;
    pid_t pid_ = -1;
};
class BrokerClient {
public:
    BrokerClient(int inherited_channel, pid_t pid);
    ~BrokerClient();
    BrokerClient(const BrokerClient&) = delete;
    BrokerClient& operator=(const BrokerClient&) = delete;
    wire::Reply call(wire::Request request);
    void set_tick(std::function<void()> tick) { tick_ = std::move(tick); }
    void check_alive() const;
    pid_t pid() const { return pid_; }
private:
    void wait(short events);
    void read_all(char* data, std::size_t size);
    int channel_ = -1, pidfd_ = -1;
    pid_t pid_;
    std::uint64_t sequence_ = 0;
    std::function<void()> tick_ = [] {};
    std::chrono::steady_clock::time_point deadline_;
};
}
