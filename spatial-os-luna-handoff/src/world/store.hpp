#pragma once
#include "core/protocol.hpp"
#include <filesystem>
#include <cstdint>
#include <functional>
#include <memory>
#include <stdexcept>
#include <vector>
#include <string>

namespace spatial {
class WorldError : public std::runtime_error {
public:
    WorldError(wire::ErrorCode code, const char* message) : std::runtime_error(message), code(code) {}
    wire::ErrorCode code;
};

// One Store belongs to one worldd. All mutations commit before returning.
struct WorkspacePresentation {
    // id is the exact 16-byte protocol identity, including embedded NUL bytes.
    std::string id;
    std::string workspace_id;
    std::string room_id;
    wire::Pose pose;
    std::uint64_t generation = 0;
};

class WorldStore {
public:
    explicit WorldStore(const std::filesystem::path& state_dir, bool migrate_v1 = false, bool fixture_faults = false, bool migrate_v2 = false);
    ~WorldStore();
    WorldStore(const WorldStore&) = delete;
    WorldStore& operator=(const WorldStore&) = delete;
    bool fixture_faults() const;
    wire::FaultArmReply arm_fault(const wire::FaultArmRequest& request, const std::function<void()>& arm_git = {});
    wire::Entity create(const wire::Entity& entity);
    wire::Entity get(const std::string& id);
    wire::Entity update(const wire::WorldUpdateRequest& request);
    wire::WorldDeleteReply erase(const wire::WorldDeleteRequest& request, bool live_reference = false);
    wire::WorldSnapshotReply snapshot();
    wire::WorldInfoReply info();
    wire::Entity create_actor(const wire::Entity& entity);
    std::vector<wire::Entity> actors();
    std::vector<wire::Entity> checkpoint_actors(const std::vector<wire::Entity>& entities);
    void workspace_available(const std::string& id, bool available);
    bool workspace_available(const std::string& id);
    std::string selected_workspace(const std::string& actor);
    void select_workspace(const std::string& actor, const std::string& workspace);
    void clear_workspace(const std::string& actor);
    void revoke_workspace_selection(const std::string& workspace, const std::string& actor);
    WorkspacePresentation create_presentation(const WorkspacePresentation&, std::uint64_t expected_world_revision, const std::string& operation_id, const std::string& request_digest, const std::string& principal);
    WorkspacePresentation move_presentation(const std::string& id, const std::string& room, const wire::Pose& pose, std::uint64_t expected_generation, const std::string& operation_id, const std::string& request_digest, const std::string& principal);
    void remove_presentation(const std::string& id, std::uint64_t expected_generation, const std::string& operation_id, const std::string& request_digest, const std::string& principal);
    std::vector<WorkspacePresentation> presentations();
    WorkspacePresentation presentation(const std::string& id);
private:
    wire::Entity insert_entity(const wire::Entity& entity);
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
} // namespace spatial
