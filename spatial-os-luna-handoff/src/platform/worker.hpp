#pragma once
#include <filesystem>
#include <memory>

namespace spatial {
class BrokerProcess;
// Owns exactly one subprocess, with inherited process-group membership.
class WorldWorker {
public:
    WorldWorker(const std::filesystem::path& executable, const std::filesystem::path& state,
                const std::filesystem::path& broker_resources = {});
    ~WorldWorker();
    WorldWorker(const WorldWorker&) = delete;
    WorldWorker& operator=(const WorldWorker&) = delete;
    void proxy();
    void finish();
private:
    std::unique_ptr<BrokerProcess> broker_;
    int pid_ = -1;
    int pidfd_ = -1;
    int input_ = -1;
    int output_ = -1;
    bool world_stopped_ = false;
};
} // namespace spatial
