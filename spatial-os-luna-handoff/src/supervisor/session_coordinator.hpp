#pragma once
#include "launch.pb.h"
#include "presentation.pb.h"
#include <filesystem>
#include <memory>
#include <string>
#include <vector>
#include <optional>
#include <cstdint>

namespace spatial { class BrokerClient; }
namespace spatial::supervisor {
// Owns broker management above individual world launches. No actor receives this API.
// The calling process must ignore SIGPIPE, as required by its issuer transport.
class SessionCoordinator final {
public:
    SessionCoordinator(const std::filesystem::path& supervisor, const std::filesystem::path& broker_config_path,
        v1::AuthoritySupervisorConfig broker_config, int borrowed_broker_native);
    ~SessionCoordinator();
    SessionCoordinator(const SessionCoordinator&) = delete;
    SessionCoordinator& operator=(const SessionCoordinator&) = delete;
    workspace::v1::ControlOwner start_world(const std::filesystem::path& private_config,
        v1::AuthoritySupervisorConfig world_config, int borrowed_native, int borrowed_actor_listener = -1,
        unsigned local_actor_channels = 0, const std::string& world_id = {});
    void start_presentation(const std::filesystem::path& compositor,const std::filesystem::path& state,int borrowed_native);
    render::v1::PresentationState select_world(const workspace::v1::ControlOwner&);
    render::v1::PresentationState inspect_presentation(const workspace::v1::ControlOwner&);
    render::v1::GeometryPresentationTarget attach_geometry(const identity::v1::ActorGeometryView&);
    render::v1::GeometryPresentationState select_geometry(const render::v1::GeometryPresentationTarget&);
    render::v1::GeometryPresentationState publish_geometry(const render::v1::GeometryPresentationTarget&,
        const identity::v1::ActorGeometryView&);
    render::v1::GeometryPresentationState retire_geometry(const render::v1::GeometryPresentationTarget&);
    core::v1::ActorLocalViewState present_local_actor(const workspace::v1::ControlOwner&,
        const core::v1::ActorLocalViewIdentity&, std::uint32_t width, std::uint32_t height);
    struct LocalPresentationTicket {
        std::uint64_t serial = 0, presentation_generation = 0;
        workspace::v1::ControlOwner owner;
        core::v1::ActorLocalViewIdentity identity;
    };
    LocalPresentationTicket begin_present_local_actor(const workspace::v1::ControlOwner&,
        const core::v1::ActorLocalViewIdentity&, std::uint32_t width, std::uint32_t height);
    enum class LocalPresentationStage { bind, select, present };
    struct LocalPresentationProgress {
        LocalPresentationStage stage;
        bool submitted = false, cancelled = false;
    };
    // Trusted inspection performs no I/O and cannot advance the operation.
    LocalPresentationProgress local_presentation_progress(const LocalPresentationTicket&) const;
    std::optional<core::v1::ActorLocalViewState> poll_present_local_actor(const LocalPresentationTicket&,
        unsigned maximum_steps = 3);
    // Drain only the existing request. Never advance a cancelled frame to another effect.
    void cancel_present_local_actor(const LocalPresentationTicket&);
    void retire_local_actor(const workspace::v1::ControlOwner&, const core::v1::ActorLocalViewIdentity&);
    // Queue exact cleanup without waiting for producer or compositor replies.
    enum class LocalRetirementKind { context, presentation };
    struct LocalRetirementProgress {
        bool pending=false, attested=false, native_retired=false, releases_binding=false;
        unsigned stage=0;
        std::uint64_t serial=0;
    };
    void defer_retire_local_actor(const workspace::v1::ControlOwner&,const core::v1::ActorLocalViewIdentity&,
        LocalRetirementKind = LocalRetirementKind::context);
    LocalRetirementProgress local_retirement_progress(const workspace::v1::ControlOwner&,
        const core::v1::ActorLocalViewIdentity&) const;
    bool local_retirement_pending(const workspace::v1::ControlOwner&,const core::v1::ActorLocalViewIdentity&) const;
    bool pump_deferred_cleanup(unsigned maximum_steps = 3);
    bool deferred_cleanup_pending() const;
    void defer_present_controls(std::uint64_t,const control::v1::ControlMenuView&);
    bool stop_capture_progress(std::uint64_t);
    void clear_presentation();
    core::v1::PixelExtent native_viewport() const;
    void present_controls(std::uint64_t capture_generation,const control::v1::ControlMenuView&);
    void start_capture(std::uint64_t,const control::v1::NativeCaptureConfig&,int borrowed_endpoint);
    void stop_capture(std::uint64_t);
    void stop_presentation();
    void recover_presentation(const workspace::v1::ControlOwner&);
    // Transfers one actor-only endpoint to the caller, which must close it. No caller FD is admitted.
    int take_actor_channel(const workspace::v1::ControlOwner&, unsigned index);
    const v1::AuthorityLaunchRecord& world_launch(const workspace::v1::ControlOwner&) const;
    workspace::v1::ControlOwnerState inspect_world(const workspace::v1::ControlOwner&);
    core::v1::Reply world_call(const workspace::v1::ControlOwner&, const core::v1::Request&);
    v1::AuthorityLaunchRecord stop_world(const workspace::v1::ControlOwner&);
    workspace::v1::ControlOwnerState recover_world(const workspace::v1::ControlOwner&);
    identity::v1::AuthorityRetirementState retire_world(const workspace::v1::ControlOwner&);
    v1::AuthorityDependentCleanup recover_world_after_broker_stop(const workspace::v1::ControlOwner&);
    BrokerClient& root();
    const v1::AuthorityLaunchRecord& broker_launch() const;
    // Broker scope cleanup remains separate from world cleanup and issuer retirement.
    v1::AuthorityLaunchRecord stop_broker();
    identity::v1::AuthorityRetirementState retire_broker();
    v1::AuthorityLaunchRecord stop();
    // Trusted shutdown of all retained entries, including failed starts without a returned owner.
    // Empty means every world has checked stop receipts and no retained presentation.
    std::vector<std::string> stop_retained_worlds();
private:
    void drain_deferred_native();
    void drain_deferred_administration();
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
