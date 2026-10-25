#pragma once
#include "presentation.pb.h"
#include "platform/process_handle.hpp"
#include <filesystem>
#include <memory>
#include <optional>
#include <cstdint>
namespace spatial {
// Private host-session API. No actor or world receives the control channel.
class PresentationOwner final {
public:
    PresentationOwner(const std::filesystem::path& executable,const std::filesystem::path& state,int borrowed_native);
    ~PresentationOwner();
    PresentationOwner(const PresentationOwner&)=delete;
    PresentationOwner& operator=(const PresentationOwner&)=delete;
    pid_t pid() const;
    int pidfd() const;
    render::v1::PresentationState attach(const render::v1::WorldPresentationBinding&,int borrowed_channel,const ProcessHandle& world,const workspace::v1::ControlOwner&,const supervisor::v1::ProcessScopeIdentity&);
    render::v1::PresentationState select(const render::v1::WorldPresentationBinding&);
    render::v1::PresentationState inspect(const render::v1::WorldPresentationBinding&);
    render::v1::PresentationState detach(const render::v1::WorldPresentationBinding&);
    render::v1::PresentationState recover(const render::v1::WorldPresentationBinding&,const supervisor::v1::ProcessScopeStopped&,const supervisor::v1::NativeScopeStopped&);
    render::v1::GeometryPresentationState attach_geometry(const render::v1::GeometryPresentationTarget&);
    render::v1::GeometryPresentationState select_geometry(const render::v1::GeometryPresentationTarget&);
    render::v1::GeometryPresentationState publish_geometry(const render::v1::GeometryPresentationTarget&,
        const identity::v1::ActorGeometryView&);
    render::v1::GeometryPresentationState retire_geometry(const render::v1::GeometryPresentationTarget&);
    render::v1::LocalActorPresentationState select_local_actor(const render::v1::LocalActorPresentationTarget&);
    // One exact control-channel owner. Poll never waits for a reply.
    std::uint64_t begin_select_local_actor(const render::v1::LocalActorPresentationTarget&);
    std::optional<render::v1::LocalActorPresentationState> poll_select_local_actor(std::uint64_t);
    render::v1::LocalActorPresentationState retire_local_actor(const render::v1::LocalActorPresentationTarget&);
    std::uint64_t begin_retire_local_actor(const render::v1::LocalActorPresentationTarget&);
    std::optional<render::v1::LocalActorPresentationState> poll_retire_local_actor(std::uint64_t);
    bool pending() const noexcept;
    void clear_selection();
    core::v1::PixelExtent native_viewport() const;
    void present_controls(std::uint64_t capture_generation,const control::v1::ControlMenuView&);
    std::uint64_t begin_present_controls(std::uint64_t,const control::v1::ControlMenuView&);
    bool poll_present_controls(std::uint64_t);
    void start_capture(std::uint64_t,const control::v1::NativeCaptureConfig&,int borrowed_endpoint);
    void stop_capture(std::uint64_t);
    std::uint64_t begin_stop_capture(std::uint64_t);
    bool poll_stop_capture(std::uint64_t);
    void shutdown();
private:
    struct Impl;std::unique_ptr<Impl> impl_;
};
}
