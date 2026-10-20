#pragma once
#include "core/protocol.hpp"
#include "broker.pb.h"
#include "process_scope.pb.h"
#include <chrono>
#include <cstdint>
#include <exception>
#include <filesystem>
#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <sys/types.h>

namespace spatial {
class NativeCredential;
class ApplicationResources;
struct PersistentApplicationStateMount {int root_fd=-1;bool data=false,config=false;};
class ApplicationProcess {
public:
    ApplicationProcess(const std::filesystem::path& project, const std::filesystem::path& repository,
                       const std::filesystem::path& state, bool writable,
                       const wire::ProcessLaunchRequest& request, std::function<void()> authority = [] {},
                       std::unique_ptr<NativeCredential> native = {},
                       std::unique_ptr<ApplicationResources> resources = {},
                       std::optional<PersistentApplicationStateMount> persistent = {});
    ~ApplicationProcess();
    ApplicationProcess(const ApplicationProcess&) = delete;
    ApplicationProcess& operator=(const ApplicationProcess&) = delete;
    std::optional<int> wait(double seconds);
    void request_stop(std::chrono::steady_clock::time_point deadline);
    void complete_stop(std::chrono::steady_clock::time_point deadline);
    void stop();
    void begin_quiescence();
    bool quiescent() const;
    supervisor::v1::PayloadQuiescenceReceipt quiescence_receipt() const;
    void release_quiescence();
private:
    void pin_namespace();
    void pin_held_payload(unsigned namespace_pid);
    void stop_or_exit() noexcept;
    workspace::v1::AppEvent event(std::chrono::steady_clock::time_point deadline);
    void read_all(char* bytes, std::size_t length, std::chrono::steady_clock::time_point deadline);
    pid_t pid_ = -1, namespace_pid_ = -1, payload_pid_ = -1;
    std::uint64_t payload_start_ = 0;
    int pidfd_ = -1, namespace_pidfd_ = -1, payload_pidfd_ = -1, output_ = -1;
    std::optional<int> result_;
    bool stop_request_started_ = false;
    std::chrono::steady_clock::time_point stop_started_{};
    std::chrono::steady_clock::time_point stop_deadline_{};
    std::exception_ptr stop_request_failure_;
    const char* stop_request_failure_operation_ = nullptr;
    std::function<void()> authority_;
    std::unique_ptr<NativeCredential> native_;
    std::unique_ptr<ApplicationResources> resources_;
};
}
