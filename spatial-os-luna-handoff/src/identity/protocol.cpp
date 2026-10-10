#include "identity/protocol.hpp"
#include "world/store.hpp"
#include "build_id.hpp"

namespace spatial::identity {
v1::IssuerReply dispatch_issuer(const v1::IssuerRequest& request, Issuer& issuer, IssuerConnection* connection) {
    v1::IssuerReply reply; reply.set_protocol_version(1); reply.set_request_id(request.request_id());
    try {
        spatial::validate_message(request);
        if (request.protocol_version() != 1) throw spatial::WorldError(spatial::wire::UNSUPPORTED_VERSION, "Unsupported issuer protocol version");
        if (request.has_hello()) {
            auto* result = reply.mutable_hello(); result->set_protocol("spatial-identity-protobuf/1");
            result->set_implementation("production"); result->set_build_id(SPATIAL_BUILD_ID);
            result->add_capabilities("identity.issuer-control/v1");
            result->add_capabilities("identity.authority-fencing/v1");
            result->add_capabilities("identity.enrollment/v1");
            result->add_capabilities("identity.revocation/v1");
            result->add_capabilities("identity.authentication/v1");
            result->add_capabilities("identity.revocation-feed/v1");
            result->add_capabilities("identity.actor-status/v1");
            result->add_capabilities("identity.bootstrap-profile/v1");
            result->add_capabilities("identity.authority-registry/v1");
            result->add_capabilities("identity.authority-participation-proof/v1");
            result->add_capabilities("identity.authority-barrier/v1");
            result->add_capabilities("identity.authority-supervisor-registry/v1");
            result->add_capabilities("identity.authority-launch-authorization/v1");
        } else if (request.has_info()) *reply.mutable_info() = issuer.info();
        else if (request.has_challenge()) *reply.mutable_challenge() = issuer.challenge(request.challenge());
        else if (request.has_execute()) {
            const auto result = issuer.execute(request.execute());
            if (result.has_policy()) *reply.mutable_policy() = result.policy();
            else if (result.has_enrollment()) *reply.mutable_enrollment() = result.enrollment();
            else if (result.has_profile()) *reply.mutable_profile() = result.profile();
            else if (result.has_participant()) *reply.mutable_participant() = result.participant();
            else if (result.has_registry()) *reply.mutable_registry() = result.registry();
            else if (result.has_supervision()) *reply.mutable_supervision() = result.supervision();
            else if (result.has_barrier()) *reply.mutable_authority_barrier() = result.barrier();
            else *reply.mutable_identity_status() = result.identity_status();
        } else if (request.has_enrollment_begin()) *reply.mutable_challenge() = issuer.enrollment_begin(request.enrollment_begin());
        else if (request.has_enrollment_complete()) *reply.mutable_enrollment() = issuer.enrollment_complete(request.enrollment_complete());
        else if (request.has_credential_check()) *reply.mutable_identity_status() = issuer.credential_check(request.credential_check());
        else if (request.has_actor_status()) *reply.mutable_actor_status() = issuer.actor_status(request.actor_status());
        else if (request.has_profile_read()) *reply.mutable_profile() = issuer.profile_read(request.profile_read());
        else if (request.has_authentication_begin()) *reply.mutable_challenge() = issuer.authentication_begin(request.authentication_begin());
        else if (request.has_authentication_complete()) *reply.mutable_authentication() = issuer.authentication_complete(request.authentication_complete());
        else if (request.has_revocations()) *reply.mutable_revocations() = issuer.revocations(request.revocations());
        else if (request.has_authority_fence_begin() || request.has_authority_fence_complete()) {
            if (!connection) throw spatial::WorldError(spatial::wire::FORBIDDEN, "Fencing requires a TLS connection context");
            if (request.has_authority_fence_begin()) *reply.mutable_challenge() = issuer.authority_fence_begin(request.authority_fence_begin(), *connection);
            else *reply.mutable_authority_fence() = issuer.authority_fence_complete(request.authority_fence_complete(), *connection);
        }
        else if (request.has_authority_launch_begin() || request.has_authority_launch_complete()) {
            if (!connection || connection->binding.size() != 32)
                throw spatial::WorldError(spatial::wire::FORBIDDEN, "Launch proof requires a TLS connection context");
            if (request.has_authority_launch_begin()) *reply.mutable_challenge() = issuer.authority_launch_begin(request.authority_launch_begin(), *connection);
            else *reply.mutable_authority_launch_authorization() = issuer.authority_launch_complete(request.authority_launch_complete(), *connection);
        }
        else if (request.has_authority_authentication_begin() || request.has_authority_authentication_complete()) {
            if (!connection || connection->binding.size() != 32)
                throw spatial::WorldError(spatial::wire::FORBIDDEN, "Service identity proof requires a TLS connection context");
            if (request.has_authority_authentication_begin())
                *reply.mutable_challenge() = issuer.authority_authentication_begin(request.authority_authentication_begin(), *connection);
            else *reply.mutable_authority_authentication() = issuer.authority_authentication_complete(request.authority_authentication_complete(), *connection);
        }
        else if (request.has_authority_activate() || request.has_authority_applied() || request.has_authority_release() || request.has_authority_state()) {
            if (!connection) throw spatial::WorldError(spatial::wire::FORBIDDEN, "Service participation requires a TLS connection context");
            if (request.has_authority_activate()) *reply.mutable_authority_activation() = issuer.authority_activate(request.authority_activate(), *connection);
            else if (request.has_authority_applied()) *reply.mutable_authority_activation() = issuer.authority_applied(request.authority_applied(), *connection);
            else if (request.has_authority_release()) *reply.mutable_authority_activation() = issuer.authority_release(request.authority_release(), *connection);
            else *reply.mutable_authority_activation() = issuer.authority_state(*connection);
        }
        else throw spatial::WorldError(spatial::wire::UNKNOWN_METHOD, "No recognized issuer operation");
    } catch (const spatial::WorldError& error) {
        reply.mutable_error()->set_code(error.code); reply.mutable_error()->set_message(error.what());
    } catch (const std::invalid_argument& error) {
        reply.mutable_error()->set_code(spatial::wire::INVALID_ARGUMENT); reply.mutable_error()->set_message(error.what());
    } catch (const std::exception&) {
        reply.mutable_error()->set_code(spatial::wire::INTERNAL_ERROR); reply.mutable_error()->set_message("Issuer operation failed internally");
    }
    return reply;
}

v1::IssuerReply handle_issuer_frame(std::string_view bytes, Issuer& issuer, IssuerConnection* connection) {
    v1::IssuerRequest request;
    if (bytes.size() <= max_frame_bytes && request.ParseFromArray(bytes.data(), static_cast<int>(bytes.size())))
        return dispatch_issuer(request, issuer, connection);
    v1::IssuerReply reply; reply.set_protocol_version(1);
    reply.mutable_error()->set_code(wire::INVALID_ARGUMENT);
    reply.mutable_error()->set_message("Malformed issuer protobuf payload");
    return reply;
}
}
