#include "world/actors.hpp"
#include "core/scene.hpp"
#include <algorithm>
#include <cmath>

namespace spatial {
void Actors::cancel_object(Actor& actor) {
    if (actor.object) object_leases_.erase(actor.object->original.id());
    actor.object.reset(); actor.object_error = wire::NO_TARGET;
}
void Actors::refresh_objects() {
    for (auto& [id, actor] : actors_) {
        (void)id;
        if (!actor.object) continue;
        bool valid = false;
        try {
            const auto current = store_.get(actor.object->original.id());
            const auto& original = actor.object->original;
            valid = current.revision() == original.revision() && current.world_revision() == original.world_revision() &&
                    current.room() == actor.entity.room() && actor.grants.contains("world.write");
        } catch (const WorldError& error) {
            if (error.code != wire::NOT_FOUND) throw;
        }
        if (!valid) { cancel_object(actor); actor.object_error = wire::STALE_TARGET; }
    }
}
void Actors::object_action(Actor& actor, const wire::ActorActionRequest& request) {
    refresh_objects();
    if (request.has_object_grab()) {
        const auto& id = request.object_grab().id();
        if (id.empty() || id.size() > 256 || id.find('\0') != std::string::npos)
            throw std::invalid_argument("Invalid object ID");
        const auto object = store_.get(id);
        if (object.kind() != wire::OBJECT) throw std::invalid_argument("Target is not a movable object");
        if (object.room() != actor.entity.room()) throw WorldError(wire::NOT_VISIBLE, "Object is in another room");
        if (actor.object) throw WorldError(wire::BUSY, "Actor already holds an object");
        auto placement = scene_pose(object.pose());
        const auto holder = object_leases_.find(id);
        if (holder != object_leases_.end()) placement = scene_pose(actors_.at(holder->second).object->preview);
        const auto camera = scene_pose(actor.entity.pose());
        if (std::hypot(placement.p[0]-camera.p[0], placement.p[1]-camera.p[1], placement.p[2]-camera.p[2]) > config_.interaction_reach())
            throw WorldError(wire::OUT_OF_REACH, "Object center exceeds interaction reach");
        const auto observation = observe(actor, wire::ActorObserveRequest{});
        // Compositor waits can advance motion. Never acquire a target using stale actor context.
        if (actor.entity.room() != observation.room() || actor.entity.pose().SerializeAsString() != observation.pose().SerializeAsString())
            throw WorldError(wire::STALE_TARGET, "Actor moved during visibility check");
        const auto visible = std::find_if(observation.visible().begin(), observation.visible().end(),
                                         [&](const auto& item) { return item.id() == id && item.kind() == wire::OBJECT; });
        if (visible == observation.visible().end())
            throw WorldError(wire::NOT_VISIBLE, "Object has no visible pixels");
        if (object_leases_.contains(id)) throw WorldError(wire::BUSY, "Object has another controller");
        // Another actor can cross a portal during the renderer wait and cancel its draft.
        // The visible pose must still describe the unleased object that this action acquires.
        if (visible->generation() != object.revision() || visible->pose().SerializeAsString() != object.pose().SerializeAsString())
            throw WorldError(wire::STALE_TARGET, "Object moved during visibility check");
        placement = scene_pose(object.pose());
        if (std::hypot(placement.p[0]-camera.p[0], placement.p[1]-camera.p[1], placement.p[2]-camera.p[2]) > config_.interaction_reach())
            throw WorldError(wire::OUT_OF_REACH, "Current object center exceeds interaction reach");
        actor.object = ObjectHold{object, object.pose()}; actor.object_error = wire::NO_TARGET;
        object_leases_.emplace(id, actor.entity.id());
        return;
    }
    if (request.has_object_move()) {
        if (!request.object_move().has_pose()) throw std::invalid_argument("Missing object preview pose");
        (void)scene_pose(request.object_move().pose());
    } else {
        if (!request.object_release().has_commit()) throw std::invalid_argument("Missing object commit choice");
        if (!request.object_release().commit()) { cancel_object(actor); return; }
    }
    if (!actor.object) throw WorldError(actor.object_error, "No valid object lease; acquire the current object before editing");
    if (request.has_object_move()) {
        actor.object->preview = request.object_move().pose();
        return;
    }
    wire::WorldUpdateRequest edit;
    edit.set_id(actor.object->original.id()); edit.set_expected_revision(actor.object->original.revision());
    *edit.mutable_patch()->mutable_pose() = actor.object->preview;
    // A failed durable write retains the draft and lease for an explicit retry or cancellation.
    store_.update(edit);
    cancel_object(actor);
}
}
