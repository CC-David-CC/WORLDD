#include "core/geometry.hpp"
#include <cmath>
#include <stdexcept>

namespace spatial {
namespace {
template<std::size_t N> void finite(const std::array<double, N>& values) {
    for (const double value : values) {
        if (!std::isfinite(value)) throw std::invalid_argument("Non-finite geometry value");
    }
}
Vec3 add(const Vec3& a, const Vec3& b) { return {a[0]+b[0], a[1]+b[1], a[2]+b[2]}; }
Vec3 scale(const Vec3& a, double s) { return {a[0]*s, a[1]*s, a[2]*s}; }
Vec3 cross(const Vec3& a, const Vec3& b) {
    return {a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]};
}
double norm(const Vec3& a) { return std::hypot(a[0], a[1], a[2]); }
Quaternion unit(const Quaternion& q) {
    const double length = std::hypot(std::hypot(q[0], q[1]), std::hypot(q[2], q[3]));
    return {q[0]/length, q[1]/length, q[2]/length, q[3]/length};
}
Quaternion multiply(const Quaternion& a, const Quaternion& b) {
    return {a[3]*b[0]+a[0]*b[3]+a[1]*b[2]-a[2]*b[1],
            a[3]*b[1]-a[0]*b[2]+a[1]*b[3]+a[2]*b[0],
            a[3]*b[2]+a[0]*b[1]-a[1]*b[0]+a[2]*b[3],
            a[3]*b[3]-a[0]*b[0]-a[1]*b[1]-a[2]*b[2]};
}
Vec3 rotate(const Quaternion& q, const Vec3& v) {
    const Vec3 axis{q[0], q[1], q[2]};
    const auto twice_cross = scale(cross(axis, v), 2);
    return add(v, add(scale(twice_cross, q[3]), cross(axis, twice_cross)));
}
void valid_pixels(const Pixels& pixels) {
    if (pixels[0] == 0 || pixels[1] == 0) throw std::invalid_argument("Nonpositive pixel extent");
}
}

void validate_pose(const Pose& pose) {
    finite(pose.p); finite(pose.q);
    const double length = std::hypot(std::hypot(pose.q[0], pose.q[1]), std::hypot(pose.q[2], pose.q[3]));
    if (std::abs(length - 1) > 1e-5) throw std::invalid_argument("Quaternion must be unit length");
}
Vec3 transform_point(const Pose& pose, const Vec3& point) {
    validate_pose(pose); finite(point);
    const auto result = add(pose.p, rotate(unit(pose.q), point));
    finite(result);
    return result;
}
Vec3 inverse_point(const Pose& pose, const Vec3& point) {
    validate_pose(pose); finite(point);
    const auto q = unit(pose.q);
    const auto result = rotate({-q[0], -q[1], -q[2], q[3]}, add(point, scale(pose.p, -1)));
    finite(result);
    return result;
}
Pose compose_pose(const Pose& parent, const Pose& local) {
    validate_pose(parent); validate_pose(local);
    return {transform_point(parent, local.p), unit(multiply(unit(parent.q), unit(local.q)))};
}
Pose inverse_pose(const Pose& pose) {
    validate_pose(pose);
    const auto q = unit(pose.q);
    const Quaternion inverse{-q[0], -q[1], -q[2], q[3]};
    return {inverse_point(pose, {0, 0, 0}), inverse};
}
std::optional<Pixels> uv_pixel(const Vec2& uv, const Pixels& pixels) {
    finite(uv); valid_pixels(pixels);
    if (uv[0] < 0 || uv[0] >= 1 || uv[1] < 0 || uv[1] >= 1) return std::nullopt;
    return Pixels{static_cast<std::uint32_t>(std::floor(uv[0]*pixels[0])),
                  static_cast<std::uint32_t>(std::floor(uv[1]*pixels[1]))};
}
std::optional<RayHit> ray_panel(const Vec3& origin, const Vec3& direction,
                                const Pose& pose, const Vec2& size, const Pixels& pixels) {
    validate_pose(pose); finite(origin); finite(direction); finite(size); valid_pixels(pixels);
    if (size[0] <= 0 || size[1] <= 0) throw std::invalid_argument("Nonpositive panel extent");
    const double length = norm(direction);
    if (length == 0 || !std::isfinite(length)) throw std::invalid_argument("Invalid ray direction");
    const auto q = unit(pose.q);
    const auto local_origin = inverse_point(pose, origin);
    const Vec3 normalized{direction[0]/length, direction[1]/length, direction[2]/length};
    const auto local_direction = rotate({-q[0], -q[1], -q[2], q[3]}, normalized);
    if (local_direction[2] >= 0 || local_origin[2] < 0) return std::nullopt;
    const double distance = -local_origin[2]/local_direction[2];
    if (!std::isfinite(distance)) return std::nullopt;
    const auto contact = add(local_origin, scale(local_direction, distance));
    const Vec2 uv{contact[0]/size[0]+0.5, 0.5-contact[1]/size[1]};
    const auto pixel = uv_pixel(uv, pixels);
    if (!pixel) return std::nullopt;
    return RayHit{uv, *pixel, distance};
}
Pose integrate(const Pose& pose, const Vec3& linear, const Vec3& angular, double seconds) {
    validate_pose(pose); finite(linear); finite(angular);
    if (!std::isfinite(seconds) || seconds < 0) throw std::invalid_argument("Invalid motion interval");
    if (seconds == 0) return pose;
    const auto phi = scale(angular, seconds);
    const auto translation = scale(linear, seconds);
    finite(phi); finite(translation);
    const double theta = norm(phi);
    if (!std::isfinite(theta)) throw std::invalid_argument("Angular interval overflow");
    Vec3 delta;
    Quaternion rotation;
    if (theta < 1e-4) {
        // Left Jacobian of SO(3), evaluated with its small-angle series.
        const double t2 = theta*theta;
        const double b = 0.5-t2/24+t2*t2/720;
        const double c = 1.0/6-t2/120+t2*t2/5040;
        delta = add(translation, add(scale(cross(phi, translation), b),
                                    scale(cross(phi, cross(phi, translation)), c)));
        const double s = 0.5-t2/48+t2*t2/3840;
        rotation = {phi[0]*s, phi[1]*s, phi[2]*s, std::cos(theta/2)};
    } else {
        const Vec3 axis{phi[0]/theta, phi[1]/theta, phi[2]/theta};
        const double half_sin = std::sin(theta/2);
        delta = add(translation, add(scale(cross(axis, translation), 2*half_sin*half_sin/theta),
                                    scale(cross(axis, cross(axis, translation)), 1-std::sin(theta)/theta)));
        rotation = {axis[0]*half_sin, axis[1]*half_sin, axis[2]*half_sin, std::cos(theta/2)};
    }
    Pose result{add(pose.p, rotate(unit(pose.q), delta)), unit(multiply(unit(pose.q), rotation))};
    validate_pose(result);
    return result;
}
} // namespace spatial
