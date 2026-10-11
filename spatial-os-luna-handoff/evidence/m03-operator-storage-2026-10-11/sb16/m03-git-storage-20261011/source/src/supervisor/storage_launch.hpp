#pragma once
#include "storage.pb.h"
#include <filesystem>
#include <functional>
#include <memory>
#include <stdexcept>
#include <string>

namespace spatial::supervisor {
class StorageLaunchFailure final : public std::runtime_error {
public:
    StorageLaunchFailure(const std::string& reason, std::string unit, bool confirmed)
        : std::runtime_error(reason), unit_(std::move(unit)), confirmed_(confirmed) {}
    const std::string& unit() const { return unit_; }
    bool cleanup_confirmed() const { return confirmed_; }
private:
    std::string unit_;
    bool confirmed_;
};
void validate_storage_launch(const workspace::v1::StorageOwnerLaunchConfig& configuration,
                            const std::filesystem::path& volume);
// Retains a private root service. Neither its channel nor pidfd reaches applications.
class StorageOwnerLaunch final {
public:
    StorageOwnerLaunch(const workspace::v1::StorageOwnerLaunchConfig& configuration,
                       const std::filesystem::path& volume, int owner_channel, int borrowed_broker_pidfd,
                       int diagnostic_stream, const std::function<void()>& before_ready = {});
    ~StorageOwnerLaunch();
    StorageOwnerLaunch(const StorageOwnerLaunch&) = delete;
    StorageOwnerLaunch& operator=(const StorageOwnerLaunch&) = delete;
    void check_alive();
    void stop();
    const std::string& unit() const;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
