#include "supervisor/control_reply.hpp"
#include "workspace/control_owner.hpp"
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
    require(expected != nullptr, "Missing control request operation");
    validate_control_owner(*expected);
    if (reply.has_error()) {
        require(reply.error().code() != wire::ERROR_CODE_UNSPECIFIED, "Missing control error code");
        return;
    }
    require(reply.has_state(), "Missing control reply outcome");
    const auto& state = reply.state();
    require(state.owner().SerializeAsString() == expected->SerializeAsString(), "Control reply owner mismatch");
    require(!request.has_fence() || state.channel_fenced(), "Control fence was not confirmed");
    // Registration replies are generated before any request can run on that channel.
    require(!request.has_register_() || (!state.channel_fenced() && state.reservations().empty()),
        "Invalid initial control owner state");
    require(state.reservations_size() <= 256, "Control reservation count exceeds limit");
    std::set<std::string> identifiers, applications;
    std::size_t snapshot_bytes = 0;
    for (const auto& reservation : state.reservations()) {
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
}
}
