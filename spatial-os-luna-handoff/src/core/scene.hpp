#pragma once
#include "core/geometry.hpp"
#include "core/protocol.hpp"
#include <vector>
namespace spatial {
Pose scene_pose(const wire::Pose& value);
void validate_render_config(const wire::RenderConfig& config);
void validate_render_extent(std::uint32_t width, std::uint32_t height);
void validate_observation_extent(std::uint32_t width, std::uint32_t height, bool include_pixels);
wire::RenderScene panel_scene(const wire::WorldSnapshotReply& world, const wire::Entity& actor,
                              const wire::RenderConfig& config);
wire::AimReply pick_scene(const wire::RenderScene& scene, const wire::Vec3& direction);
struct ScenePanel {
    Pose pose;
    std::array<Vec3, 4> corners;
    Vec3 origin, ray_x, ray_y, ray_z;
    Vec2 size;
    std::array<float, 3> color;
    wire::SurfaceHandle surface;
    wire::ObservedRepresentation representation;
};
std::vector<ScenePanel> scene_panels(const wire::RenderScene& scene);
// Conservative screen bounds, padded by one pixel; shader applies exact panel coverage.
std::optional<std::array<float, 4>> panel_bounds(const ScenePanel& panel, const wire::RenderConfig& config,
                                               unsigned width, unsigned height);
}
