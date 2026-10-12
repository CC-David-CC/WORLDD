#pragma once
#include <array>
#include <cstdint>
#include <optional>
#include <vector>

namespace spatial {
using Vec2 = std::array<double, 2>;
using Vec3 = std::array<double, 3>;
using Quaternion = std::array<double, 4>; // x, y, z, w
using Pixels = std::array<std::uint32_t, 2>;
struct Pose { Vec3 p; Quaternion q; };
struct RayHit { Vec2 uv; Pixels pixel; double distance; };
struct Bounds3 { Vec3 min, max; };
struct SweepHit { double fraction; Vec3 position, normal; double penetration; };
struct Collider { Pose pose; Bounds3 bounds; };

void validate_pose(const Pose& pose);
Vec3 transform_point(const Pose& pose, const Vec3& point);
Vec3 inverse_point(const Pose& pose, const Vec3& point);
Pose compose_pose(const Pose& parent, const Pose& local);
Pose inverse_pose(const Pose& pose);
std::optional<Pixels> uv_pixel(const Vec2& uv, const Pixels& pixels);
std::optional<RayHit> ray_panel(const Vec3& origin, const Vec3& direction,
                                const Pose& pose, const Vec2& size, const Pixels& pixels);
Pose integrate(const Pose& pose, const Vec3& linear, const Vec3& angular, double seconds);
void validate_bounds(const Bounds3& bounds);
std::optional<SweepHit> sweep_sphere_aabb(const Vec3& origin, const Vec3& displacement, double radius, const Bounds3& bounds);
std::optional<SweepHit> sweep_sphere_world(const Vec3& origin, const Vec3& displacement, double radius, const std::vector<Collider>& colliders);
Vec3 slide_sphere(const Vec3& origin, const Vec3& displacement, double radius, const std::vector<Collider>& colliders);
} // namespace spatial
