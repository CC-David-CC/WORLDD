#pragma once
#include "core/protocol.hpp"
#include "broker.pb.h"
#include <chrono>
#include <filesystem>
#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <sys/types.h>

namespace spatial {
class NativeCredential;
class ApplicationResources;
class ApplicationProcess {
public:
    ApplicationProcess(const std::filesystem::path& project, const std::filesystem::path& repository,
                       const std::filesystem::path& state, bool writable,
                       const wire::ProcessLaunchRequest& request, std::function<void()> authority = [] {},
                       std::unique_ptr<NativeCredential> native = {},
                       std::unique_ptr<ApplicationResources> resources = {});
    ~ApplicationProcess();
    ApplicationProcess(const ApplicationProcess&) = delete;
    ApplicationProcess& operator=(const ApplicationProcess&) = delete;
    std::optional<int> wait(double seconds);
    void stop();
private:
    void pin_namespace();
    void stop_or_exit() noexcept;
    workspace::v1::AppEvent event(std::chrono::steady_clock::time_point deadline);
    void read_all(char* bytes, std::size_t length, std::chrono::steady_clock::time_point deadline);
    pid_t pid_ = -1;
    int pidfd_ = -1, namespace_pidfd_ = -1, output_ = -1;
    std::optional<int> result_;
    std::function<void()> authority_;
    std::unique_ptr<NativeCredential> native_;
    std::unique_ptr<ApplicationResources> resources_;
};
}
