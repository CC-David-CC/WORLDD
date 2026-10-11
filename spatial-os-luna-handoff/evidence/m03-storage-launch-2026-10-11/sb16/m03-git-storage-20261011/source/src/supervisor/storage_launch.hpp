#pragma once
#include "storage.pb.h"
#include <filesystem>
#include <memory>
#include <string>

namespace spatial::supervisor {
void validate_storage_launch(const workspace::v1::StorageOwnerLaunchConfig& configuration,
                            const std::filesystem::path& volume);
// Retains a private root service. Neither its channel nor pidfd reaches applications.
class StorageOwnerLaunch final {
public:
    StorageOwnerLaunch(const workspace::v1::StorageOwnerLaunchConfig& configuration,
                       const std::filesystem::path& volume, int owner_channel, int borrowed_broker_pidfd,
                       int diagnostic_stream);
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
