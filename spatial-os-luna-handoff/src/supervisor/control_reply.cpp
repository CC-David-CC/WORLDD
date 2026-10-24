#include "supervisor/control_reply.hpp"
#include "workspace/control_owner.hpp"
#include "workspace/observation_watch.hpp"
#include <set>

namespace spatial::supervisor {
namespace {
void require(bool valid, const char* message) {
    if (!valid) throw std::invalid_argument(message);
}
}
void validate_control_reply(const workspace::v1::ControlSupervisorRequest& request,
                            const workspace::v1::ControlSupervisorReply& reply) {
    require(request.ByteSizeLong() <= 65536 && reply.ByteSizeLong() <= 65536,
        "Control message exceeds packet limit");
    validate_message(request);
    validate_message(reply);
    require(request.protocol_version() == 1 && reply.protocol_version() == 1,
        "Unsupported control protocol version");
    require(reply.request_id() == request.request_id(), "Control reply request mismatch");
    const workspace::v1::ControlOwner* expected = nullptr;
    if (request.has_register_()) expected = &request.register_();
    else if (request.has_inspect()) expected = &request.inspect();
    else if (request.has_fence()) expected = &request.fence();
    else if (request.has_recover()) expected = &request.recover().owner();
    require(expected != nullptr, "Missing control request operation");
    validate_control_owner(*expected);
    if (request.has_recover()) validate_control_recovery(request.recover());
    if (reply.has_error()) {
        require(reply.error().code() != wire::ERROR_CODE_UNSPECIFIED, "Missing control error code");
        return;
    }
    require(reply.has_state(), "Missing control reply outcome");
    const auto& state = reply.state();
    require(state.owner().SerializeAsString() == expected->SerializeAsString(), "Control reply owner mismatch");
    require(!request.has_fence() || state.channel_fenced(), "Control fence was not confirmed");
    // Registration replies are generated before any request can run on that channel.
    require(!request.has_register_() || (!state.channel_fenced() && state.reservations().empty() && state.observation_watches().empty() && !state.has_recovery()),
        "Invalid initial control owner state");
    if (request.has_recover()) {
        require(state.has_recovery() && state.recovery().cleanup().SerializeAsString() == request.recover().SerializeAsString(),
            "Control recovery reply does not match requested cleanup");
    }
    if (state.has_recovery()) {
        validate_control_recovery(state.recovery().cleanup());
        require(state.channel_fenced() && state.reservations().empty() && state.observation_watches().empty() &&
            state.recovery().cleanup().owner().SerializeAsString() == expected->SerializeAsString(),
            "Invalid recovered control owner state");
    }
    const auto& reservations = state.has_recovery() ? state.recovery().released() : state.reservations();
    require(reservations.size() <= 256, "Control reservation count exceeds limit");
    std::set<std::string> identifiers, applications;
    std::size_t snapshot_bytes = 0;
    for (const auto& reservation : reservations) {
        const auto& principal = reservation.principal();
        const auto& application = reservation.application();
        require(reservation.id().size() == 32 && principal.issuer_id().size() == 32 &&
            principal.actor_id().size() == 32 && !application.workspace().empty() &&
            !application.application().empty() && application.server_id() == expected->native().server_id(),
            "Invalid control reservation identity");
        require(identifiers.insert(reservation.id()).second &&
            applications.insert(application.SerializeAsString()).second, "Duplicate control reservation");
        snapshot_bytes += reservation.ByteSizeLong()+16;
        require(snapshot_bytes <= 60000, "Control reservation snapshot exceeds limit");
    }
    const auto& observations=state.has_recovery()?state.recovery().observation_released():state.observation_watches();
    require(observations.size()<=256,"Observation reservation count exceeds limit");
    std::set<std::pair<std::string,std::uint64_t>> views;
    for(const auto& watch:observations) {
        validate_observation_watch(watch);
        require(watch.principal().issuer_id()==expected->issuer_id() &&
            watch.view().authority_service_incarnation()==expected->launch().service_incarnation() &&
            watch.view().authority_generation()==expected->launch().generation(),"Observation owner context mismatch");
        require(identifiers.insert(watch.id()).second &&
            views.emplace(watch.view().SerializeAsString(),watch.presentation_generation()).second,
            "Duplicate observation reservation identity or view");
        if(state.has_recovery()) {
            const auto& cleanup=state.recovery().cleanup();
            require(cleanup.has_retired_presentation() &&
                cleanup.retired_presentation().world_id()==watch.view().world_id() &&
                cleanup.retired_presentation().presentation_generation()==watch.presentation_generation(),
                "Observation recovery lacks matching presentation retirement");
        }
        snapshot_bytes+=watch.ByteSizeLong()+16;
        require(snapshot_bytes<=60000,"Combined control and observation snapshot exceeds limit");
    }
}
}
