#pragma once
#include "broker_cleanup_bootstrap.pb.h"
#include <chrono>
#include <filesystem>
#include <string>
#include <sys/types.h>
#include <utility>

namespace spatial::supervisor {
class BrokerCleanupBootstrap final {
public:
    using Deadline = std::chrono::steady_clock::time_point;
    static Deadline deadline_after(std::chrono::milliseconds timeout);
    struct Received final {
        v1::BrokerCleanupBootstrapFrame frame;
        int descriptor = -1;
        pid_t pid = -1;
        uid_t uid = static_cast<uid_t>(-1);
        Received() = default;
        ~Received();
        Received(Received&&) noexcept;
        Received& operator=(Received&&) noexcept;
        Received(const Received&) = delete;
        Received& operator=(const Received&) = delete;
    };
    static void require_credentials(int fd);
    static void validate_state_root(int directory_fd, const std::filesystem::path& configured_root);
    static void send_request(int fd, const std::string& correlation, int directory_fd,
                             Deadline deadline);
    static void send_request(int fd, const std::string& correlation, int directory_fd,
                             std::chrono::milliseconds timeout = std::chrono::seconds(10));
    static Received receive_request(int fd, pid_t expected_pid, uid_t expected_uid, Deadline deadline);
    static Received receive_request(int fd, pid_t expected_pid, uid_t expected_uid,
                                   std::chrono::milliseconds timeout);
    static void send_ack(int fd, const std::string& correlation, Deadline deadline);
    static void send_ack(int fd, const std::string& correlation,
                         std::chrono::milliseconds timeout = std::chrono::seconds(10));
    static v1::BrokerCleanupBootstrapFrame receive_ack(int fd, pid_t expected_pid, uid_t expected_uid,
                                                        Deadline deadline, const std::string& correlation);
    static v1::BrokerCleanupBootstrapFrame receive_ack(int fd, pid_t expected_pid, uid_t expected_uid,
                                                        std::chrono::milliseconds timeout,
                                                        const std::string& correlation);
};
}
