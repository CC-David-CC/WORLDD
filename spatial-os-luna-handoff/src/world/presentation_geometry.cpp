#include "world/presentation_geometry.hpp"
#include "world/store.hpp"
#include "core/presentation_identity.hpp"
#include <set>

namespace spatial {
void append_presentation_geometry(wire::RenderScene& scene, const std::string& room,
                                  const std::vector<PresentationGeometry>& authorized_records) {
    std::set<std::string> occupied;
    const auto add=[&](const auto& values) { for(const auto& item:values) occupied.insert(item.id()); };
    add(scene.panels()); add(scene.barriers()); add(scene.portals()); add(scene.objects()); add(scene.actors());
    for(const auto& surface:scene.surfaces()) occupied.insert(surface.handle().id());
    for(const auto& record:authorized_records) {
        if(record.room_id!=room) continue;
        if(record.representation_id.size()!=16 || record.generation==0 ||
           !record.pose.has_position() || !record.pose.has_orientation())
            throw WorldError(wire::STORAGE_ERROR,"Invalid committed workspace presentation geometry");
        const auto id=presentation_geometry_id(record.representation_id);
        if(!occupied.insert(id).second)
            throw WorldError(wire::ALREADY_EXISTS,"Workspace presentation geometry identity collides");
        auto* entity=scene.add_objects();
        entity->set_id(id); entity->set_kind(wire::OBJECT); entity->set_room(room);
        *entity->mutable_pose()=record.pose; entity->set_revision(record.generation);
        auto* view=scene.add_presentation_views();
        view->set_representation_id(record.representation_id);
        view->set_room_id(record.room_id);
        *view->mutable_transform()=record.pose;
        view->set_generation(record.generation);
        view->set_locked(record.locked);
    }
}
} // namespace spatial
