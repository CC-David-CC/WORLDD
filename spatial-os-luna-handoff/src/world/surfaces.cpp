#include "world/surfaces.hpp"
#include "core/scene.hpp"
#include <algorithm>
#include <cmath>
#include <set>
namespace spatial {
void Surfaces::refresh(const wire::NativeSnapshot& snapshot) {
    validate_message(snapshot);
    std::set<std::string> present;
    for (const auto& native : snapshot.surfaces()) {
        const auto& id = native.handle().id();
        if (id.empty() || !native.has_pixels() || !present.insert(id).second)
            throw std::runtime_error("Invalid native surface snapshot");
        auto [it, inserted] = surfaces_.try_emplace(id);
        if (inserted) {
            it->second.pose.mutable_position(); it->second.pose.mutable_orientation()->set_w(1);
            it->second.size.set_x(1); it->second.size.set_y(1);
        }
        it->second.native = native;
        // Native transients follow their parent. They cannot retain a separate slot attachment.
        if (native.parent_xid()) it->second.slot.clear();
    }
    std::erase_if(surfaces_, [&](const auto& item) { return !present.contains(item.first); });
    resources_ = snapshot.resources();
}
wire::SurfaceRecord Surfaces::resolve(const std::string& id, std::set<std::string>& ancestry) const {
    const auto it = surfaces_.find(id);
    if (it == surfaces_.end()) throw WorldError(wire::NOT_FOUND, "Surface is no longer live");
    const auto& surface = it->second;
    const auto& native = surface.native;
    wire::SurfaceRecord result;
    result.set_id(id); result.set_xid(native.xid()); result.set_generation(native.handle().generation());
    result.set_mapped(native.mapped()); *result.mutable_pixels() = native.pixels();
    result.set_room(surface.room); *result.mutable_pose() = surface.pose; *result.mutable_size() = surface.size;
    if (!surface.slot.empty()) {
        const auto slot = world_.get(surface.slot);
        result.set_slot(slot.id()); result.set_room(slot.room());
        *result.mutable_pose() = slot.pose(); *result.mutable_size() = slot.size();
    }
    // Application properties cannot make an unrelated actor's scene unusable.
    // Unresolved relationships have no world placement until the client repairs them.
    if (ancestry.size() >= 32 || !ancestry.insert(id).second) {
        result.clear_room(); result.set_mapped(false); return result;
    }
    if (native.parent_xid()) {
        const auto parent = std::find_if(surfaces_.begin(), surfaces_.end(),
            [&](const auto& item) { return item.second.native.xid() == native.parent_xid(); });
        if (parent == surfaces_.end()) { result.clear_room(); result.set_mapped(false); return result; }
        const auto p = resolve(parent->first, ancestry);
        result.set_parent_id(p.id()); result.set_room(p.room()); result.set_mapped(p.mapped() && native.mapped());
        if (p.room().empty()) { result.set_mapped(false); return result; }
        const double sx = p.size().x() / p.pixels().width(), sy = p.size().y() / p.pixels().height();
        const Vec3 local{(native.root_x() - parent->second.native.root_x() +
                          (double(native.pixels().width()) - p.pixels().width()) / 2) * sx,
                         -(native.root_y() - parent->second.native.root_y() +
                          (double(native.pixels().height()) - p.pixels().height()) / 2) * sy, 0.001};
        const auto point = transform_point(scene_pose(p.pose()), local);
        auto* position = result.mutable_pose()->mutable_position();
        position->set_x(point[0]); position->set_y(point[1]); position->set_z(point[2]);
        *result.mutable_pose()->mutable_orientation() = p.pose().orientation();
        result.mutable_size()->set_x(native.pixels().width() * sx);
        result.mutable_size()->set_y(native.pixels().height() * sy);
    }
    return result;
}
wire::SurfaceRecord Surfaces::get(const std::string& id) const {
    std::set<std::string> ancestry;
    return resolve(id, ancestry);
}
wire::SurfaceReply Surfaces::find(const std::string& probe) const {
    if (probe.empty() || probe.size() > 4096) throw std::invalid_argument("Invalid probe property value");
    wire::SurfaceReply result;
    for (const auto& [id, surface] : surfaces_) if (surface.native.mapped() && surface.native.probe_id() == probe) {
        *result.mutable_surface() = get(id); break;
    }
    return result;
}
wire::SurfaceRecord Surfaces::place(const wire::SurfacePlaceRequest& request) {
    get(request.id());
    if (surfaces_.at(request.id()).native.parent_xid()) throw std::invalid_argument("Place the application parent surface");
    if (!request.has_pose() || !request.has_size() || request.size().x() <= 0 || request.size().y() <= 0)
        throw std::invalid_argument("Missing or invalid surface placement");
    scene_pose(request.pose());
    if (world_.get(request.room()).kind() != wire::ROOM) throw std::invalid_argument("Surface destination is not a room");
    auto& surface = surfaces_.at(request.id());
    if (!surface.slot.empty()) {
        const auto slot = world_.get(surface.slot);
        wire::WorldUpdateRequest edit; edit.set_id(slot.id()); edit.set_expected_revision(slot.revision());
        auto* patch = edit.mutable_patch(); patch->set_room(request.room());
        *patch->mutable_pose() = request.pose(); *patch->mutable_size() = request.size();
        world_.update(edit);
    } else {
        surface.room = request.room(); surface.pose = request.pose(); surface.size = request.size();
    }
    return get(request.id());
}
wire::SurfaceRecord Surfaces::attach_slot(const wire::SurfaceAttachSlotRequest& request) {
    if (request.id().empty() || request.slot().empty()) throw std::invalid_argument("Missing surface or slot identity");
    get(request.id());
    auto& surface = surfaces_.at(request.id());
    if (surface.native.parent_xid()) throw std::invalid_argument("Attach the application parent surface");
    const auto slot = world_.get(request.slot());
    if (slot.kind() != wire::SURFACE_SLOT) throw std::invalid_argument("Attachment target is not a surface slot");
    for (const auto& [id, other] : surfaces_)
        if (id != request.id() && other.slot == slot.id()) throw WorldError(wire::BUSY, "Slot has another live attachment");
    surface.slot = slot.id();
    // A later detach must not revive an earlier independent placement.
    surface.room.clear();
    return get(request.id());
}
void Surfaces::forget_slot(const std::string& id) {
    for (auto& [surface_id, surface] : surfaces_) {
        (void)surface_id;
        if (surface.slot == id) surface.slot.clear();
    }
}
wire::SurfaceValidationReply Surfaces::validate_handle(const wire::SurfaceValidateRequest& request) const {
    if (!request.has_handle()) throw std::invalid_argument("Missing surface handle");
    const auto& handle = request.handle();
    const auto found = surfaces_.find(handle.id());
    if (found == surfaces_.end() || !found->second.native.mapped() ||
        found->second.native.handle().generation() != handle.generation())
        throw WorldError(wire::STALE_TARGET, "Surface handle has no current mapped resource");
    wire::SurfaceValidationReply result; result.set_valid(true); return result;
}
wire::SurfaceListReply Surfaces::children(const std::string& id) const {
    const auto parent = get(id);
    wire::SurfaceListReply result;
    for (const auto& [child, surface] : surfaces_)
        if (surface.native.parent_xid() == parent.xid()) *result.add_surfaces() = get(child);
    return result;
}
void Surfaces::append(wire::RenderScene& scene, const std::string& room) const {
    for (const auto& [id, surface] : surfaces_) {
        (void)surface;
        const auto record = get(id);
        if (!record.mapped() || record.room() != room) continue;
        auto* output = scene.add_surfaces();
        output->mutable_handle()->set_id(id); output->mutable_handle()->set_generation(record.generation());
        *output->mutable_pixels() = record.pixels();
        auto* panel = output->mutable_panel();
        panel->set_id(id); panel->set_kind(wire::PANEL); panel->set_room(room);
        *panel->mutable_pose() = record.pose(); *panel->mutable_size() = record.size();
        panel->mutable_color()->set_red(255); panel->mutable_color()->set_green(255); panel->mutable_color()->set_blue(255);
    }
}
}
