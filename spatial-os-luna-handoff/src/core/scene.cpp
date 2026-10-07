#include "core/scene.hpp"
#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
namespace spatial {
namespace {
void require(bool value, const char* message) {
    if (!value) throw std::invalid_argument(message);
}
}
Pose scene_pose(const wire::Pose& value) {
    require(value.has_position() && value.has_orientation(), "Incomplete scene pose");
    const auto& p = value.position(); const auto& q = value.orientation();
    Pose result{{p.x(), p.y(), p.z()}, {q.x(), q.y(), q.z(), q.w()}};
    validate_pose(result); return result;
}
void validate_render_config(const wire::RenderConfig& value) {
    require(value.has_vertical_fov() && std::isfinite(value.vertical_fov()) &&
            value.vertical_fov() > 0.01 && value.vertical_fov() < 3.13, "Invalid vertical FOV");
    require(value.has_near_clip() && value.has_far_clip() && std::isfinite(value.near_clip()) &&
            std::isfinite(value.far_clip()) && value.near_clip() > 0 &&
            value.far_clip() > value.near_clip() && value.far_clip() <= 1e6, "Invalid clipping planes");
    require(value.has_background() && value.background().red() <= 255 && value.background().green() <= 255 &&
            value.background().blue() <= 255, "Invalid background color");
}
void validate_render_extent(std::uint32_t width, std::uint32_t height) {
    require(width > 0 && height > 0 && width <= 4096 && height <= 4096 &&
            std::uint64_t(width) * height <= 8 * 1024 * 1024, "Invalid or excessive frame dimensions");
}
wire::RenderScene panel_scene(const wire::WorldSnapshotReply& world, const wire::Entity& actor,
                              const wire::RenderConfig& config) {
    wire::RenderScene result;
    *result.mutable_camera() = actor.pose(); *result.mutable_config() = config;
    result.set_world_revision(world.world_revision());
    // Snapshot order is stable by ID. Equal-depth ties use the same first panel in both paths.
    for (const auto& entity : world.entities())
        if (entity.kind() == wire::PANEL && entity.room() == actor.room()) *result.add_panels() = entity;
    return result;
}
wire::AimReply pick_scene(const wire::RenderScene& scene, const wire::Vec3& input) {
    const auto camera = scene_pose(scene.camera());
    Vec3 local{input.x(), input.y(), input.z()};
    const double scale = std::max({std::abs(local[0]), std::abs(local[1]), std::abs(local[2])});
    require(std::isfinite(scale) && scale > 0, "Invalid aim direction");
    for (auto& v : local) v /= scale;
    const double norm = std::hypot(local[0], local[1], local[2]);
    for (auto& v : local) v /= norm;
    const Pose rotation{{0, 0, 0}, camera.q};
    const auto direction = transform_point(rotation, local);
    wire::AimReply result;
    double nearest = std::numeric_limits<double>::infinity();
    for (const auto& panel : scene.panels()) {
        const auto hit = ray_panel(camera.p, direction, scene_pose(panel.pose()),
                                   {panel.size().x(), panel.size().y()}, {1, 1});
        if (!hit || hit->distance >= nearest) continue;
        const double depth = -local[2] * hit->distance;
        if (depth < scene.config().near_clip() || depth > scene.config().far_clip()) continue;
        nearest = hit->distance;
        auto* out = result.mutable_hit(); out->set_id(panel.id()); out->set_generation(1);
        out->set_distance(hit->distance);
        out->mutable_uv()->set_x(hit->uv[0]); out->mutable_uv()->set_y(hit->uv[1]);
        // Solid panels have UV but no application pixel extent or input sink.
    }
    return result;
}
std::vector<ScenePanel> scene_panels(const wire::RenderScene& scene) {
    validate_render_config(scene.config());
    const auto camera = scene_pose(scene.camera());
    const Pose camera_rotation{{0, 0, 0}, camera.q};
    std::vector<ScenePanel> result;
    constexpr std::array<Vec2, 4> corners{{{-0.5,-0.5}, {0.5,-0.5}, {0.5,0.5}, {-0.5,0.5}}};
    for (const auto& panel : scene.panels()) {
        require(panel.kind() == wire::PANEL && panel.has_pose() && panel.has_size() &&
                panel.size().x() > 0 && panel.size().y() > 0 && panel.has_color() &&
                panel.color().red() <= 255 && panel.color().green() <= 255 && panel.color().blue() <= 255,
                "Invalid scene panel");
        const auto pose = scene_pose(panel.pose());
        const Pose panel_rotation{{0, 0, 0}, pose.q};
        ScenePanel out;
        out.origin = inverse_point(pose, camera.p);
        out.ray_x = inverse_point(panel_rotation, transform_point(camera_rotation, {1, 0, 0}));
        out.ray_y = inverse_point(panel_rotation, transform_point(camera_rotation, {0, 1, 0}));
        out.ray_z = inverse_point(panel_rotation, transform_point(camera_rotation, {0, 0, 1}));
        out.size = {panel.size().x(), panel.size().y()};
        out.color = {panel.color().red() / 255.0f, panel.color().green() / 255.0f, panel.color().blue() / 255.0f};
        for (std::size_t i = 0; i < corners.size(); ++i) {
            out.corners[i] = inverse_point(camera, transform_point(pose,
                {corners[i][0] * out.size[0], corners[i][1] * out.size[1], 0}));
            for (const double value : out.corners[i]) require(std::isfinite(value), "Scene coordinate overflow");
        }
        result.push_back(out);
    }
    return result;
}
std::optional<std::array<float, 4>> panel_bounds(const ScenePanel& panel, const wire::RenderConfig& config,
                                                unsigned width, unsigned height) {
    std::vector<Vec3> polygon(panel.corners.begin(), panel.corners.end());
    // Clip before perspective division, including panels that cross the camera plane.
    for (const double plane : {config.near_clip(), config.far_clip()}) {
        std::vector<Vec3> next;
        const bool near = plane == config.near_clip();
        auto distance = [=](const Vec3& p) { return near ? -p[2] - plane : plane + p[2]; };
        for (std::size_t i = 0; i < polygon.size(); ++i) {
            const auto& a = polygon[i]; const auto& b = polygon[(i + 1) % polygon.size()];
            const double da = distance(a), db = distance(b);
            if (da >= 0) next.push_back(a);
            if ((da < 0) != (db < 0)) {
                const double t = da / (da - db);
                next.push_back({a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]), a[2] + t * (b[2] - a[2])});
            }
        }
        polygon = std::move(next);
    }
    if (polygon.empty()) return std::nullopt;
    const double f = 1 / std::tan(config.vertical_fov() / 2);
    double left = 1, right = -1, bottom = 1, top = -1;
    for (const auto& p : polygon) {
        const double x = f * height / width * p[0] / -p[2], y = f * p[1] / -p[2];
        left = std::min(left, x); right = std::max(right, x);
        bottom = std::min(bottom, y); top = std::max(top, y);
    }
    left = std::max(-1.0, left - 2.0 / width); right = std::min(1.0, right + 2.0 / width);
    bottom = std::max(-1.0, bottom - 2.0 / height); top = std::min(1.0, top + 2.0 / height);
    if (left >= right || bottom >= top) return std::nullopt;
    return std::array<float, 4>{static_cast<float>(left), static_cast<float>(bottom),
                                static_cast<float>(right), static_cast<float>(top)};
}
}
