#pragma once
#include "world/store.hpp"
#include "core/navigation.hpp"
#include "grants.pb.h"
#include "broker.pb.h"
#include "geometry_view.pb.h"
#include "compositor_native.pb.h"
#include "native_seat_guardian.pb.h"
#include <array>
#include <map>
#include <set>
#include <functional>
#include <exception>
#include <stdexcept>

namespace spatial {
namespace broker_wire = workspace::v1;
class Surfaces;
class RenderWorker;
class BrokerClient;
class NativeInput;
namespace identity { class NativeSeatGuardianClient; }
wire::RuntimeConfig load_runtime_config(const std::filesystem::path& path);

// One actor engine serves fixture and monotonic clocks through the same tick.
class Actors {
public:
    Actors(WorldStore& store, const wire::RuntimeConfig& config, bool manual_clock, Surfaces* surfaces = nullptr,
           RenderWorker* renderer = nullptr, BrokerClient* broker = nullptr, NativeInput* revocation_input = nullptr,
           identity::NativeSeatGuardianClient* native_guardian = nullptr);
    using Authority = std::function<bool(const std::string&, const std::vector<std::string>&)>;
    wire::ActorOpenReply open(const wire::ActorOpenRequest& request, bool require_observation_grant = false, Authority authority = {},
                              std::optional<spatial::authority::v1::ActorRef> principal = {}, bool local_application_output = true,
                              std::string credential_id = {}, std::string service_incarnation = {},
                              std::uint64_t authority_generation = 0,std::uint64_t issuer_security_revision = 0);
    struct NativeSeatAuthority {
        authority::v1::ActorRef principal;
        std::string credential_id;
        std::string actor_entity_id;
        std::uint64_t actor_instance_id = 0;
        std::string service_incarnation;
        std::uint64_t authority_generation = 0;
        std::string server_id;
        std::string compositor_credential_id;
        std::uint64_t seat_id = 0;
        std::uint64_t issuer_security_revision = 0;
    };
    // Trusted runtime only. A pending close retains its exact seat authority.
    std::optional<NativeSeatAuthority> native_seat_authority(const std::string& actor_id) const;
    std::optional<identity::guardian::v1::NativeSeatBinding> native_seat_guardian_binding(const std::string& actor_id) const;
    // Atomic key taps may use the reentrant issuer pump only for an already
    // focused, leased, guardian-registered native seat and an idle input path.
    bool guarded_key_tap_ready(const std::string& token) const;
    void consume_sequence(const std::string& token, std::uint64_t sequence);
    void refresh_authority();
    // Trusted session wrapper. Uses the same stop implementation and settles leases.
    void cancel_control(const std::string& token);
    wire::ActorStateReply inspect(const std::string& token);
    wire::ActorCloseReply close(const std::string& token);
    std::string depart(const std::string& token);
    wire::ActorActionReply act(const wire::ActorActionRequest& request);
    // Movement-lane refresh. The session transport checks its own monotonic sequence.
    void refresh_motion(const std::string& token, const wire::ActorInputRequest& input);
    // Same Stop backend, with an exact owner while native release is pending.
    std::uint64_t begin_stop(const wire::ActorActionRequest&);
    std::optional<wire::ActorActionReply> poll_stop(std::uint64_t);
    void abandon_stop(std::uint64_t);
    bool progress_stops();
    wire::RenderScene scene(const std::string& token, bool geometry_only = false, std::vector<broker_wire::AuthorizationFence>* fences = nullptr);
    identity::v1::ActorGeometryView geometry_view(const std::string& token, const std::string& world_id,
        const std::string& actor_id, const std::string& view_id, std::uint64_t authority_generation,
        std::uint64_t sequence);
    bool application_observation_valid(const std::string& token, std::vector<broker_wire::AuthorizationFence>* fences = nullptr);
    // Retained host management only. This changes local-output eligibility, never actor rights.
    void bind_local_view(const std::string& token, const core::v1::ActorLocalViewIdentity& identity);
    void release_local_view(const std::string& token, const core::v1::ActorLocalViewIdentity& identity);
    core::v1::ActorLocalViewState present_local_view(const std::string& token,
        const core::v1::ActorLocalViewIdentity&, std::uint32_t width, std::uint32_t height,
        const std::function<bool()>& session_current = {});
    wire::WorldCheckpointReply checkpoint();
    wire::WorldCheckpointReply checkpoint_closed(const std::string& actor_id);
    wire::ClockReply advance(const wire::ClockAdvanceRequest& request);
    void tick(double monotonic_seconds);
    void flush_input();
    bool cancellation_settled() const;
    void require_native_seat_guardian(bool required) {
        if(required&&!native_guardian_)throw std::invalid_argument("Required native seat guardian is unavailable");
        native_guardian_required_ = required;
    }
    struct InputWitnessBinding {
        authority::v1::ActorRef principal;
        NativeSeatAuthority seat_authority;
        std::string actor_entity_id;
        std::uint64_t actor_instance_id = 0;
        render::v1::NativeActorSeatIdentity native;
        workspace::v1::AuthorizationFence fence;
        std::string private_session_token; // Never serialized into evidence.
    };
    // Private launch-owner diagnostics. This API never authorizes actor input.
    InputWitnessBinding input_witness_binding(const std::string& actor, const workspace::v1::ApplicationRef& application);
    bool input_witness_current(const InputWitnessBinding&) const;
    const wire::Entity* find(const std::string& id) const;
    bool room_in_use(const std::string& id) const;
    bool manual_clock() const { return manual_clock_; }
private:
    struct ObservedApplication {
        wire::SurfaceHandle handle;
        workspace::v1::ApplicationRef application;
    };
    struct ObjectHold {
        wire::Entity original;
        wire::Pose preview;
    };
    struct Actor {
        wire::Entity entity;
        std::string token;
        std::set<std::string> grants;
        Authority authority;
        std::optional<spatial::authority::v1::ActorRef> principal;
        std::string credential_id;
        std::string service_incarnation;
        std::uint64_t authority_generation = 0;
        std::uint64_t issuer_security_revision = 0;
        std::optional<NativeSeatAuthority> seat_authority;
        std::optional<identity::guardian::v1::SeatRegistrationReceipt> guardian_registration;
        bool require_observation_grant = false;
        bool local_application_output = true;
        bool base_local_application_output = true;
        std::optional<core::v1::ActorLocalViewIdentity> local_view;
        std::array<double, 6> axes{};
        wire::TranslationFrame translation_frame = wire::BODY_TRANSLATION;
        void clear_motion() noexcept { axes.fill(0); translation_frame = wire::BODY_TRANSLATION; }
        std::uint64_t sequence = 0, witness_instance_id = 0;
        std::uint64_t last_stop_sequence = 0;
        double expires = 0;
        wire::AimReply aim;
        std::optional<workspace::v1::ApplicationRef> aim_application;
        // Error precedence only. This never restores native focus or control authority.
        std::optional<workspace::v1::ApplicationRef> keyboard_application;
        wire::Vec3 direction;
        wire::NativeActorInput native;
        wire::AimHit capture;
        std::string lease;
        std::optional<workspace::v1::ApplicationControlLease> application_control;
        bool application_input = false;
        bool capture_motion_pending = false;
        bool interaction_reset_pending = false;
        bool close_pending = false;
        bool departure_pending = false;
        std::optional<std::uint64_t> pending_stop;
        std::vector<ObservedApplication> observed_applications;
        std::map<std::string, ObservedApplication> application_discoveries;
        std::optional<ObjectHold> object;
        wire::ErrorCode object_error = wire::NO_TARGET;
    };
    struct RenderedPresentation {
        std::string id, room, workspace;
        wire::Pose pose;
        std::uint64_t generation;
        bool allowed;
    };
    struct Projection {
        wire::RenderScene scene;
        std::string room, pose, token;
        std::uint64_t instance = 0;
        std::vector<broker_wire::AuthorizationFence> fences;
        std::vector<RenderedPresentation> presentations;
        std::vector<ObservedApplication> applications;
    };
    struct Foreground {
        wire::ActorLocalViewIdentity identity;
        Projection projection;
        std::optional<Projection> displayed;
        std::optional<broker_wire::ObservationWatch> watch;
        bool clear_pending = false, cleared = false;
    };
    std::map<std::string, Foreground> foregrounds_;
    bool presenting_local_ = false, flushing_foregrounds_ = false;
    wire::RenderScene scene_projection(const std::string&, bool geometry_only,
        std::vector<broker_wire::AuthorizationFence>*, bool sampled_pose);
    Projection project_observation(Actor&, bool sampled_pose);
    void validate_projection(Actor&, Projection&, bool sampled_pose);
    bool foreground_current(const Foreground&) const;
    void queue_local_clear(const wire::ActorLocalViewIdentity&);
    void drain_foregrounds();
    Actor& authenticated(const std::string& token);
    void apply_motion(Actor&, const wire::ActorInputRequest&);
    bool granted(const Actor&, const std::string& right, const std::vector<std::string>& components = {}) const;
    std::optional<broker_wire::ScopedAuthorityCheckReply> workspace_check(
        const Actor&, const std::string& workspace, authority::v1::Right right,
        const std::vector<std::string>& components = {},
        std::vector<broker_wire::AuthorizationFence>* fences = nullptr);
    bool workspace_right(const Actor&, const std::string& workspace, authority::v1::Right right,
                         const std::vector<std::string>& components = {});
    bool workspace_file_access(const Actor&, const std::string& workspace,
                               std::vector<broker_wire::AuthorizationFence>* fences = nullptr);
    void cabinet_action(Actor&, const workspace::v1::ActorCabinetRequest&, workspace::v1::ActorCabinetReply*);
    wire::ActorApplicationSurfacesReply discover_application(Actor&, const wire::ActorApplicationSurfacesRequest&);
    wire::ActorApplicationPlaceReply place_application(Actor&, const wire::ActorApplicationPlaceRequest&);
    bool application_slot_authorized(const Actor&, const wire::SurfaceRecord&, bool use) const;
    bool held_application_slot_authorized(const Actor&) const;
    void validate_presentation_placement(const std::string& room, const wire::Pose& pose, const std::string& exclude_id = {});
    std::set<std::string> observable_applications(const Actor&, const std::vector<ObservedApplication>&,
                                                  std::vector<broker_wire::AuthorizationFence>* fences);
    void validate_authority_fence(const Actor&, const std::vector<broker_wire::AuthorizationFence>&);
    bool observed_application_current(const Actor&, const ObservedApplication&) const;
    bool observed_applications_current(const Actor&) const;
    std::string selected_workspace(const Actor& actor);
    void step(double target);
    void refresh_native();
    void release_input(Actor& actor, bool close = false, bool keep_reservation = false, bool force_native = false);
    void finish_release_input(Actor&, bool close, bool keep_reservation = false);
    void prepare_stop(Actor&);
    void stop_actor(Actor& actor, bool force_native = false);
    struct PendingStop {
        std::uint64_t serial=0,instance=0,sequence=0;
        std::string actor,token,control_release_id;
        std::optional<std::uint64_t> native;
        bool submitted_close=false,needs_native=false,done=false,abandoned=false;
        std::exception_ptr failure;
    };
    std::map<std::uint64_t,PendingStop> pending_stops_;
    std::uint64_t next_stop_=0;
    bool progressing_stops_=false;
    void poll_control();
    void drain_control_releases();
    void preflight_input_authority(Actor& actor, const workspace::v1::ApplicationRef& application);
    void authorize_input(Actor& actor, const wire::SurfaceHandle& target);
    bool retained_native_binding_current(const Actor& actor) const;
    void finish_input(Actor& actor);
    void send_input(Actor& actor, const wire::RendererInputCommand& command);
    wire::AimHit input_target(Actor& actor, bool captured = true);
    std::string control_group(const wire::SurfaceRecord& surface) const;
    void route_input(Actor& actor, const wire::ActorActionRequest& request);
    void move_capture(Actor& actor, const wire::Vec3& direction);
    wire::ActorObservation observe(Actor& actor, const wire::ActorObserveRequest& request);
    void refresh_collisions();
    void object_action(Actor& actor, const wire::ActorActionRequest& request);
    void cancel_object(Actor& actor);
    void refresh_objects();
    std::uint64_t collision_revision_ = 0;
    std::map<std::string, RoomGeometry> collision_rooms_;
    WorldStore& store_;
    Surfaces* surfaces_;
    RenderWorker* renderer_;
    BrokerClient* broker_;
    NativeInput* revocation_input_;
    identity::NativeSeatGuardianClient* native_guardian_;
    bool native_guardian_required_ = false;
    std::map<std::string, std::string> leases_;
    std::vector<workspace::v1::ApplicationControlLease> control_releases_;
    bool flushing_input_ = false;
    std::map<std::string, std::string> object_leases_;
    wire::RuntimeConfig config_;
    bool manual_clock_;
    double now_ = 0;
    std::uint64_t next_witness_instance_id_ = 0;
    std::map<std::string, Actor> actors_;
    std::map<std::string, std::string> sessions_;
};
} // namespace spatial
