#pragma once
#include <array>
#include <cstdint>
#include <optional>

namespace spatial {
using Vec2 = std::array<double, 2>;
using Vec3 = std::array<double, 3>;
using Quaternion = std::array<double, 4>; // x, y, z, w
using Pixels = std::array<std::uint32_t, 2>;
struct Pose { Vec3 p; Quaternion q; };
struct RayHit { Vec2 uv; Pixels pixel; double distance; };

void validate_pose(const Pose& pose);
Vec3 transform_point(const Pose& pose, const Vec3& point);
Vec3 inverse_point(const Pose& pose, const Vec3& point);
std::optional<Pixels> uv_pixel(const Vec2& uv, const Pixels& pixels);
std::optional<RayHit> ray_panel(const Vec3& origin, const Vec3& direction,
                                const Pose& pose, const Vec2& size, const Pixels& pixels);
Pose integrate(const Pose& pose, const Vec3& linear, const Vec3& angular, double seconds);
} // namespace spatial
