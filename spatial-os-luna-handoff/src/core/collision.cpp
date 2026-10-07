#include "core/geometry.hpp"
#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace spatial {
namespace {
using Wide3 = std::array<long double, 3>;
void finite(const Vec3& value) {
    for (double component : value) if (!std::isfinite(component)) throw std::invalid_argument("Non-finite collision coordinate");
}
double narrow(long double value) {
    const double result = static_cast<double>(value);
    if (!std::isfinite(result)) throw std::invalid_argument("Collision coordinate overflow");
    return result;
}
double dot(const Vec3& a, const Vec3& b) { return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]; }
}
void validate_bounds(const Bounds3& bounds) {
    finite(bounds.min); finite(bounds.max);
    for (unsigned i = 0; i < 3; ++i) if (bounds.min[i] >= bounds.max[i]) throw std::invalid_argument("Invalid collision bounds");
}
std::optional<SweepHit> sweep_sphere_aabb(const Vec3& origin, const Vec3& displacement, double radius, const Bounds3& bounds) {
    finite(origin); finite(displacement); validate_bounds(bounds);
    if (!std::isfinite(radius) || radius <= 0) throw std::invalid_argument("Invalid sphere radius");
    const long double r = radius;
    auto contact = [&](long double time) {
        SweepHit hit{}; hit.fraction = narrow(time);
        Wide3 point{}, delta{};
        for (unsigned i = 0; i < 3; ++i) {
            point[i] = std::fma(static_cast<long double>(displacement[i]), time, static_cast<long double>(origin[i]));
            delta[i] = point[i] - std::clamp(point[i], static_cast<long double>(bounds.min[i]), static_cast<long double>(bounds.max[i]));
            hit.position[i] = narrow(point[i]);
        }
        const long double distance = std::hypot(delta[0], delta[1], delta[2]);
        if (distance > 0) {
            for (unsigned i = 0; i < 3; ++i) hit.normal[i] = narrow(delta[i] / distance);
            hit.penetration = narrow(std::max(0.0L, r - distance));
        } else {
            // Inside the box: choose its closest face. Ties use -X,+X,-Y,+Y,-Z,+Z.
            long double nearest = std::numeric_limits<long double>::infinity();
            for (unsigned i = 0; i < 3; ++i) for (int sign : {-1, 1}) {
                const long double distance_to_face = sign < 0 ? point[i] - bounds.min[i] : bounds.max[i] - point[i];
                if (distance_to_face < nearest) {
                    nearest = distance_to_face; hit.normal = {}; hit.normal[i] = sign;
                }
            }
            hit.penetration = narrow(r + nearest);
        }
        return hit;
    };
    long double initial_squared = 0;
    for (unsigned i = 0; i < 3; ++i) {
        const long double delta = static_cast<long double>(origin[i]) - std::clamp(origin[i], bounds.min[i], bounds.max[i]);
        initial_squared += delta * delta;
    }
    if (initial_squared <= r*r) return contact(0);
    std::vector<long double> cuts{0, 1};
    for (unsigned i = 0; i < 3; ++i) if (displacement[i] != 0) {
        for (double bound : {bounds.min[i], bounds.max[i]}) {
            const long double time = (static_cast<long double>(bound) - origin[i]) / displacement[i];
            if (time > 0 && time < 1) cuts.push_back(time);
        }
    }
    std::sort(cuts.begin(), cuts.end());
    cuts.erase(std::unique(cuts.begin(), cuts.end()), cuts.end());
    // Distance to the actual box is a quadratic within each clamp interval.
    // Rounded edge and corner contacts follow from the same Euclidean distance.
    for (std::size_t j = 1; j < cuts.size(); ++j) {
        const long double start = cuts[j-1], end = cuts[j], midpoint = (start + end) / 2;
        long double a = 0, b = 0, c = -r*r;
        for (unsigned i = 0; i < 3; ++i) {
            const long double middle = std::fma(static_cast<long double>(displacement[i]), midpoint, static_cast<long double>(origin[i]));
            long double edge;
            if (middle < bounds.min[i]) edge = bounds.min[i];
            else if (middle > bounds.max[i]) edge = bounds.max[i];
            else continue;
            const long double offset = std::fma(static_cast<long double>(displacement[i]), start, static_cast<long double>(origin[i]) - edge);
            const long double velocity = displacement[i];
            a += velocity * velocity; b += offset * velocity; c += offset * offset;
        }
        if (c <= 0) return contact(start);
        if (a == 0 || b >= 0) continue;
        const long double discriminant = std::fma(-a, c, b*b);
        if (discriminant < 0) continue;
        // The alternate quadratic form avoids subtracting almost equal roots.
        const long double delta = c / (-b + std::sqrt(discriminant));
        if (delta >= 0 && delta <= end - start) return contact(start + delta);
    }
    return std::nullopt;
}
std::optional<SweepHit> sweep_sphere_world(const Vec3& origin, const Vec3& displacement, double radius, const std::vector<Collider>& colliders) {
    const double skin = std::max(radius * 1e-9, 1e-12);
    std::optional<SweepHit> nearest;
    for (const auto& collider : colliders) {
        const Pose rotation{{0, 0, 0}, collider.pose.q};
        const auto local_origin = inverse_point(collider.pose, origin);
        const auto local_displacement = inverse_point(rotation, displacement);
        auto hit = sweep_sphere_aabb(local_origin, local_displacement, radius, collider.bounds);
        if (!hit) continue;
        hit->normal = transform_point(rotation, hit->normal);
        // Tangency and separating contact do not prevent motion away from a wall.
        if (hit->fraction == 0 && hit->penetration <= skin && dot(displacement, hit->normal) >= 0) continue;
        hit->position = transform_point(collider.pose, hit->position);
        if (!nearest || hit->fraction < nearest->fraction) nearest = hit;
    }
    return nearest;
}
Vec3 slide_sphere(const Vec3& origin, const Vec3& displacement, double radius, const std::vector<Collider>& colliders) {
    Vec3 position = origin, remaining = displacement;
    const double skin = std::max(radius * 1e-9, 1e-12);
    for (unsigned iteration = 0; iteration < 32; ++iteration) {
        const auto nearest = sweep_sphere_world(position, remaining, radius, colliders);
        if (!nearest) {
            for (unsigned i = 0; i < 3; ++i) position[i] += remaining[i];
            finite(position); return position;
        }
        for (unsigned i = 0; i < 3; ++i) {
            position[i] = nearest->position[i] + nearest->normal[i] * (nearest->penetration + skin);
            remaining[i] *= 1 - nearest->fraction;
        }
        const double inward = std::min(0.0, dot(remaining, nearest->normal));
        for (unsigned i = 0; i < 3; ++i) remaining[i] -= inward * nearest->normal[i];
        finite(position); finite(remaining);
        if (std::hypot(remaining[0], remaining[1], remaining[2]) <= skin) return position;
    }
    // Dense conflicting contacts stop the remaining motion rather than tunnel.
    return position;
}
} // namespace spatial
