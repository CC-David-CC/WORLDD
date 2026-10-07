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
    if (value.has_barrier_color()) require(value.barrier_color().red() <= 255 && value.barrier_color().green() <= 255 &&
                                           value.barrier_color().blue() <= 255, "Invalid barrier color");
    if (value.has_portal_color()) require(value.portal_color().red() <= 255 && value.portal_color().green() <= 255 &&
                                          value.portal_color().blue() <= 255, "Invalid portal color");
    if (value.has_object_size()) require(value.object_size() > 0 && value.object_size() <= 1000, "Invalid object size");
    if (value.has_object_color()) require(value.object_color().red() <= 255 && value.object_color().green() <= 255 &&
                                          value.object_color().blue() <= 255, "Invalid object color");
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
    for (const auto& entity : world.entities()) if (entity.room() == actor.room()) {
        if (entity.kind() == wire::PANEL) *result.add_panels() = entity;
        else if (entity.kind() == wire::BARRIER) *result.add_barriers() = entity;
        else if (entity.kind() == wire::PORTAL) *result.add_portals() = entity;
        else if (entity.kind() == wire::OBJECT) *result.add_objects() = entity;
    }
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
    for (const auto& panel : scene_panels(scene)) {
        const bool application = !panel.surface.id().empty();
        const auto& metadata = panel.representation;
        const Pixels pixels = application ? Pixels{metadata.pixels().width(), metadata.pixels().height()} : Pixels{1, 1};
        const auto hit = ray_panel(camera.p, direction, panel.pose, panel.size, pixels);
        if (!hit || hit->distance >= nearest) continue;
        const double depth = -local[2] * hit->distance;
        if (depth < scene.config().near_clip() || depth > scene.config().far_clip()) continue;
        nearest = hit->distance;
        auto* out = result.mutable_hit(); out->set_id(metadata.id()); out->set_generation(metadata.generation());
        out->set_distance(hit->distance); out->mutable_uv()->set_x(hit->uv[0]); out->mutable_uv()->set_y(hit->uv[1]);
        out->clear_pixel();
        if (application) { out->mutable_pixel()->set_x(hit->pixel[0]); out->mutable_pixel()->set_y(hit->pixel[1]); }
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
        out.pose = pose;
        out.representation.set_id(panel.id()); out.representation.set_kind(panel.kind());
        out.representation.set_generation(1);
        *out.representation.mutable_pose() = panel.pose(); *out.representation.mutable_size() = panel.size();
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
    for (const auto& surface : scene.surfaces()) {
        wire::RenderScene single; *single.mutable_config() = scene.config(); *single.mutable_camera() = scene.camera();
        *single.add_panels() = surface.panel();
        auto converted = scene_panels(single);
        converted.front().surface = surface.handle();
        converted.front().representation.clear_kind();
        converted.front().representation.set_id(surface.handle().id());
        converted.front().representation.set_generation(surface.handle().generation());
        *converted.front().representation.mutable_pixels() = surface.pixels();
        result.push_back(std::move(converted.front()));
    }
    for (const auto& barrier : scene.barriers()) {
        require(barrier.kind() == wire::BARRIER && barrier.has_bounds() && barrier.bounds().has_min() &&
                barrier.bounds().has_max(), "Invalid scene barrier");
        const auto& low = barrier.bounds().min(); const auto& high = barrier.bounds().max();
        const Bounds3 bounds{{low.x(), low.y(), low.z()}, {high.x(), high.y(), high.z()}};
        validate_bounds(bounds);
        const auto placement = scene_pose(barrier.pose());
        Vec3 center{}, extent{};
        for (unsigned i = 0; i < 3; ++i) {
            center[i] = bounds.min[i] / 2 + bounds.max[i] / 2; extent[i] = bounds.max[i] - bounds.min[i];
        }
        const double half = std::sqrt(.5);
        for (unsigned axis = 0; axis < 3; ++axis) for (int sign : {-1, 1}) {
            auto point = center; point[axis] = sign < 0 ? bounds.min[axis] : bounds.max[axis];
            Quaternion q{0, 0, 0, 1};
            if (axis == 0) q = {0, sign * half, 0, half};
            else if (axis == 1) q = {-sign * half, 0, 0, half};
            else if (sign < 0) q = {0, 1, 0, 0};
            const auto face = compose_pose(placement, {point, q});
            wire::RenderScene single; *single.mutable_camera() = scene.camera(); *single.mutable_config() = scene.config();
            auto* panel = single.add_panels(); panel->set_id(barrier.id()); panel->set_kind(wire::PANEL);
            auto* p = panel->mutable_pose()->mutable_position(); p->set_x(face.p[0]); p->set_y(face.p[1]); p->set_z(face.p[2]);
            auto* rotation = panel->mutable_pose()->mutable_orientation();
            rotation->set_x(face.q[0]); rotation->set_y(face.q[1]); rotation->set_z(face.q[2]); rotation->set_w(face.q[3]);
            panel->mutable_size()->set_x(extent[axis == 0 ? 2 : 0]);
            panel->mutable_size()->set_y(extent[axis == 1 ? 2 : 1]);
            if (scene.config().has_barrier_color()) *panel->mutable_color() = scene.config().barrier_color();
            else { panel->mutable_color()->set_red(96); panel->mutable_color()->set_green(104); panel->mutable_color()->set_blue(120); }
            auto converted = scene_panels(single);
            auto& metadata = converted.front().representation;
            metadata.set_kind(wire::BARRIER); metadata.clear_size();
            *metadata.mutable_pose() = barrier.pose(); *metadata.mutable_bounds() = barrier.bounds();
            result.push_back(std::move(converted.front()));
        }
    }
    for (const auto& portal : scene.portals()) {
        require(portal.kind() == wire::PORTAL && portal.has_size() && portal.size().x() > 0 &&
                portal.size().y() > 0, "Invalid scene portal");
        const auto placement = scene_pose(portal.pose());
        const double width = portal.size().x(), height = portal.size().y();
        const double border = std::min(.1, .04 * std::min(width, height));
        // Four non-solid marker strips surround the open aperture. Both sides are visible.
        for (unsigned axis = 0; axis < 2; ++axis) for (int sign : {-1, 1}) for (bool back : {false, true}) {
            Vec3 point{}; point[axis] = sign * ((axis == 0 ? width : height) + border) / 2;
            const auto face = compose_pose(placement, {point, back ? Quaternion{0, 1, 0, 0} : Quaternion{0, 0, 0, 1}});
            wire::RenderScene single; *single.mutable_camera() = scene.camera(); *single.mutable_config() = scene.config();
            auto* panel = single.add_panels(); panel->set_id(portal.id()); panel->set_kind(wire::PANEL);
            auto* p = panel->mutable_pose()->mutable_position(); p->set_x(face.p[0]); p->set_y(face.p[1]); p->set_z(face.p[2]);
            auto* q = panel->mutable_pose()->mutable_orientation();
            q->set_x(face.q[0]); q->set_y(face.q[1]); q->set_z(face.q[2]); q->set_w(face.q[3]);
            panel->mutable_size()->set_x(axis == 0 ? border : width);
            panel->mutable_size()->set_y(axis == 0 ? height + 2 * border : border);
            if (scene.config().has_portal_color()) *panel->mutable_color() = scene.config().portal_color();
            else { panel->mutable_color()->set_red(80); panel->mutable_color()->set_green(180); panel->mutable_color()->set_blue(220); }
            auto converted = scene_panels(single);
            auto& metadata = converted.front().representation; metadata.set_kind(wire::PORTAL);
            *metadata.mutable_pose() = portal.pose(); *metadata.mutable_size() = portal.size();
            result.push_back(std::move(converted.front()));
        }
    }
    for (const auto& object : scene.objects()) {
        require(object.kind() == wire::OBJECT && object.has_pose(), "Invalid scene object");
        wire::RenderScene single; *single.mutable_camera() = scene.camera(); *single.mutable_config() = scene.config();
        auto* box = single.add_barriers(); *box = object; box->set_kind(wire::BARRIER);
        const double half = (scene.config().has_object_size() ? scene.config().object_size() : .2) / 2;
        auto* low = box->mutable_bounds()->mutable_min(); low->set_x(-half); low->set_y(-half); low->set_z(-half);
        auto* high = box->mutable_bounds()->mutable_max(); high->set_x(half); high->set_y(half); high->set_z(half);
        auto* color = single.mutable_config()->mutable_barrier_color();
        if (scene.config().has_object_color()) *color = scene.config().object_color();
        else { color->set_red(224); color->set_green(174); color->set_blue(72); }
        auto converted = scene_panels(single);
        for (auto& face : converted) {
            face.representation.set_kind(wire::OBJECT); face.representation.set_generation(object.revision());
            result.push_back(std::move(face));
        }
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
