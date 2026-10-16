#pragma once
#include "core/protocol.hpp"
#include "core/presentation_identity.hpp"
#include "core/scene.hpp"
#include "geometry_view.pb.h"
#include <set>
#include <stdexcept>

namespace spatial {
inline void validate_geometry_view(const identity::v1::ActorGeometryView& view) {
    const auto check = [](bool good, const char* message) {
        if (!good) throw std::invalid_argument(message);
    };
    validate_message(view);
    check(view.world_id().size() > 0 && view.world_id().size() <= 256 &&
          view.actor_id().size() > 0 && view.actor_id().size() <= 256 &&
          view.room().size() > 0 && view.room().size() <= 256 &&
          view.view_id().size() == 32 && view.authority_service_incarnation().size() == 32 &&
          view.authority_generation() > 0 && view.sequence() > 0 &&
          view.has_camera() && view.has_config(), "Incomplete geometry view context");
    scene_pose(view.camera());
    validate_render_config(view.config());
    const std::size_t count = view.panels_size() + view.barriers_size() + view.portals_size() +
                              view.objects_size() + view.actors_size();
    check(count <= 4096, "Geometry view entity count exceeds limit");
    std::set<std::string> ids;
    const auto category = [&](const auto& collection, core::v1::EntityKind kind) {
        for (const auto& item : collection) {
            if (kind != core::v1::OBJECT)
                check(!is_reserved_presentation_geometry_id(item.id()),
                      "Reserved presentation identity used outside object geometry");
            check(!item.id().empty() && item.id().size() <= 256 && item.id().find('\0') == std::string::npos &&
                  ids.insert(item.id()).second && item.kind() == kind && item.has_pose(),
                  "Invalid geometry identity or category");
            scene_pose(item.pose());
            const bool size = kind == core::v1::PANEL || kind == core::v1::PORTAL;
            check(item.has_size() == size && item.has_bounds() == (kind == core::v1::BARRIER) &&
                  item.has_color() == (kind == core::v1::PANEL), "Invalid geometry field combination");
            if (size) check(item.size().x() > 0 && item.size().y() > 0, "Invalid geometry size");
            if (kind == core::v1::PANEL) check(item.color().red() <= 255 && item.color().green() <= 255 &&
                  item.color().blue() <= 255, "Invalid geometry color");
            if (kind == core::v1::BARRIER) {
                check(item.bounds().has_min() && item.bounds().has_max(), "Incomplete geometry bounds");
                const auto& low = item.bounds().min(); const auto& high = item.bounds().max();
                check(low.x() < high.x() && low.y() < high.y() && low.z() < high.z(), "Invalid geometry bounds");
            }
        }
    };
    category(view.panels(), core::v1::PANEL);
    category(view.barriers(), core::v1::BARRIER);
    category(view.portals(), core::v1::PORTAL);
    category(view.objects(), core::v1::OBJECT);
    category(view.actors(), core::v1::ACTOR);
    core::v1::RenderScene projected;
    for (const auto& item : view.objects()) {
        auto* object = projected.add_objects();
        object->set_id(item.id()); object->set_kind(item.kind());
        *object->mutable_pose() = item.pose();
    }
    for (const auto& metadata : view.presentation_views())
        *projected.add_presentation_views() = metadata;
    validate_presentation_views(projected, view.room());
    check(view.ByteSizeLong() < max_frame_bytes, "Geometry view exceeds frame limit");
}
}
