#pragma once
#include "core/protocol.hpp"
#include <filesystem>
#include <memory>
#include <stdexcept>
#include <vector>

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
    wire::WorldDeleteReply erase(const wire::WorldDeleteRequest& request, bool live_reference = false);
    wire::WorldSnapshotReply snapshot();
    wire::WorldInfoReply info();
    wire::Entity create_actor(const wire::Entity& entity);
    std::vector<wire::Entity> actors();
    std::vector<wire::Entity> checkpoint_actors(const std::vector<wire::Entity>& entities);
private:
    wire::Entity insert_entity(const wire::Entity& entity);
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
} // namespace spatial
