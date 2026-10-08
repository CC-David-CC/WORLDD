#pragma once
#include "world/store.hpp"
#include "broker.pb.h"
#include <map>
#include <optional>
#include <set>
namespace spatial {
// World-owned placements; native handles and generations come only from spatialwm.
class Surfaces {
public:
    explicit Surfaces(WorldStore& world) : world_(world) {}
    void refresh(const wire::NativeSnapshot& snapshot);
    wire::SurfaceReply find(const std::string& probe) const;
    wire::SurfaceRecord get(const std::string& id) const;
    std::string control_group(const wire::SurfaceRecord& surface) const;
    std::optional<workspace::v1::ApplicationRef> application(const wire::SurfaceHandle& handle) const;
    wire::SurfaceRecord place(const wire::SurfacePlaceRequest& request);
    wire::SurfaceRecord attach_slot(const wire::SurfaceAttachSlotRequest& request);
    wire::SurfaceValidationReply validate_handle(const wire::SurfaceValidateRequest& request) const;
    void forget_slot(const std::string& id);
    wire::SurfaceListReply children(const std::string& id) const;
    void append(wire::RenderScene& scene, const std::string& room) const;
    const wire::ResourceStats& resources() const { return resources_; }
private:
    struct Surface { wire::NativeSurface native; std::string room, slot; wire::Pose pose; wire::Vec2 size; };
    wire::SurfaceRecord resolve(const std::string& id, std::set<std::string>& ancestry) const;
    WorldStore& world_;
    std::map<std::string, Surface> surfaces_;
    wire::ResourceStats resources_;
};
}
