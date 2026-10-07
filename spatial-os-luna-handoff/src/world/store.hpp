#pragma once
#include "core/protocol.hpp"
#include <filesystem>
#include <memory>
#include <stdexcept>

namespace spatial {
class WorldError : public std::runtime_error {
public:
    WorldError(wire::ErrorCode code, const char* message) : std::runtime_error(message), code(code) {}
    wire::ErrorCode code;
};

// One Store belongs to one worldd. All mutations commit before returning.
class WorldStore {
public:
    explicit WorldStore(const std::filesystem::path& state_dir);
    ~WorldStore();
    WorldStore(const WorldStore&) = delete;
    WorldStore& operator=(const WorldStore&) = delete;
    wire::Entity create(const wire::Entity& entity);
    wire::Entity get(const std::string& id);
    wire::Entity update(const wire::WorldUpdateRequest& request);
    wire::WorldDeleteReply erase(const wire::WorldDeleteRequest& request);
    wire::WorldSnapshotReply snapshot();
    wire::WorldInfoReply info();
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
} // namespace spatial
