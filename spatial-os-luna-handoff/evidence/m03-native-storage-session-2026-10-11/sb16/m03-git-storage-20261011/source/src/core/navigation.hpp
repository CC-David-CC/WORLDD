#pragma once
#include "core/geometry.hpp"
#include <map>
#include <string>

namespace spatial {
struct Portal {
    std::string id, destination;
    Pose entry, mapping;
    Vec2 size;
};
struct RoomGeometry {
    std::vector<Collider> colliders;
    std::vector<Portal> portals;
};
struct RoomMotion {
    Pose pose;
    std::string room;
    bool crossed = false, stopped = false;
};
// One piece of body-twist motion. Collisions and portal crossings share its remaining displacement.
RoomMotion navigate_rooms(const Vec3& origin, const Pose& target, const std::string& room, double radius,
                          const std::map<std::string, RoomGeometry>& rooms, unsigned& crossings_left);
}
