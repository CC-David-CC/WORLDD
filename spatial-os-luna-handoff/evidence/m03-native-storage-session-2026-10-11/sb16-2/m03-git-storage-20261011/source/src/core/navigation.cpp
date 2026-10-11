#include "core/navigation.hpp"
#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace spatial {
namespace {
std::optional<double> crossing(const Portal& portal, const Vec3& origin, const Vec3& displacement) {
    const auto local = inverse_point(portal.entry, origin);
    const auto delta = inverse_point(Pose{{0, 0, 0}, portal.entry.q}, displacement);
    // The plane belongs to its front side. A step ending on it crosses on the next forward step.
    if (local[2] < 0 || delta[2] >= 0 || local[2] + delta[2] >= 0) return std::nullopt;
    const double fraction = -local[2] / delta[2];
    const double u = (local[0] + fraction * delta[0]) / portal.size[0] + .5;
    const double v = .5 - (local[1] + fraction * delta[1]) / portal.size[1];
    if (u < 0 || u >= 1 || v < 0 || v >= 1) return std::nullopt;
    return fraction;
}
}
RoomMotion navigate_rooms(const Vec3& origin, const Pose& target, const std::string& room, double radius,
                          const std::map<std::string, RoomGeometry>& rooms, unsigned& crossings_left) {
    RoomMotion result{target, room}; result.pose.p = origin;
    Vec3 remaining{};
    for (unsigned i = 0; i < 3; ++i) remaining[i] = target.p[i] - origin[i];
    const double skin = std::max(radius * 1e-9, 1e-12);
    for (unsigned contact = 0; contact < 64; ++contact) {
        const auto& geometry = rooms.at(result.room);
        const auto collision = sweep_sphere_world(result.pose.p, remaining, radius, geometry.colliders);
        const Portal* portal = nullptr;
        double fraction = 1;
        for (const auto& candidate : geometry.portals) {
            const auto time = crossing(candidate, result.pose.p, remaining);
            if (time && (!portal || *time < fraction)) { portal = &candidate; fraction = *time; }
        }
        if (portal && (!collision || fraction < collision->fraction)) {
            Vec3 entry{};
            for (unsigned i = 0; i < 3; ++i) entry[i] = result.pose.p[i] + fraction * remaining[i];
            const auto landing = transform_point(portal->mapping, entry);
            const auto overlap = sweep_sphere_world(landing, {0, 0, 0}, radius, rooms.at(portal->destination).colliders);
            if (crossings_left == 0 || (overlap && overlap->penetration > skin)) {
                // Preserve the source room when an exit is obstructed or a cycle exhausts its budget.
                const auto normal = transform_point(Pose{{0, 0, 0}, portal->entry.q}, {0, 0, 1});
                for (unsigned i = 0; i < 3; ++i) result.pose.p[i] = entry[i] + normal[i] * skin;
                result.stopped = true; return result;
            }
            --crossings_left; result.crossed = true;
            result.pose.p = landing;
            result.pose.q = compose_pose(portal->mapping, Pose{{0, 0, 0}, result.pose.q}).q;
            for (double& value : remaining) value *= 1 - fraction;
            remaining = transform_point(Pose{{0, 0, 0}, portal->mapping.q}, remaining);
            result.room = portal->destination;
            continue;
        }
        if (!collision) {
            for (unsigned i = 0; i < 3; ++i) result.pose.p[i] += remaining[i];
            validate_pose(result.pose); return result;
        }
        for (unsigned i = 0; i < 3; ++i) {
            result.pose.p[i] = collision->position[i] + collision->normal[i] * (collision->penetration + skin);
            remaining[i] *= 1 - collision->fraction;
        }
        double inward = 0;
        for (unsigned i = 0; i < 3; ++i) inward += remaining[i] * collision->normal[i];
        for (unsigned i = 0; i < 3; ++i) remaining[i] -= std::min(0.0, inward) * collision->normal[i];
        if (std::hypot(remaining[0], remaining[1], remaining[2]) <= skin) return result;
    }
    result.stopped = true;
    return result;
}
}
