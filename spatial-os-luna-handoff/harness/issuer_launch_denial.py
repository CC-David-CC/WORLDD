"""Validate an observed issuer denial for one supervised launch candidate."""
import core_pb2 as core
import issuer_pb2 as identity
import key_pb2 as keys

ACTIVE_GENERATION_DENIAL = "New launch requires prior generation cleanup"

def require_active_generation_denial_trace(observations, participant_id, service_incarnation,
                                     launch_id, supervision_revision, supervisor_public_key, issuer_id):
    if len(observations) != 2:
        raise AssertionError("Expected exactly one observed issuer begin/reply and complete/reply")
    begin_request, begin_reply = observations[0]
    complete_request, complete_reply = observations[1]
    if begin_request.WhichOneof("operation") != "authority_launch_begin":
        raise AssertionError("Observed issuer denial trace did not begin at authority_launch_begin")
    if complete_request.WhichOneof("operation") != "authority_launch_complete":
        raise AssertionError("Observed issuer denial trace did not complete at authority_launch_complete")
    if begin_request.request_id != begin_reply.request_id:
        raise AssertionError("Issuer begin reply does not correlate to its request")
    if complete_request.request_id != complete_reply.request_id:
        raise AssertionError("Issuer denial reply does not correlate to its request")
    if not begin_reply.HasField("challenge") or not begin_reply.challenge.HasField("signing_request"):
        raise AssertionError("Issuer launch begin did not return its signed challenge")
    envelope = begin_reply.challenge
    if envelope.signing_request.purpose != keys.AUTHORITY_LAUNCH_AUTHORIZATION:
        raise AssertionError("Issuer challenge has the wrong signing purpose")
    challenge = identity.AuthorityLaunchChallenge.FromString(envelope.signing_request.payload)
    begin = begin_request.authority_launch_begin
    if challenge.request != begin:
        raise AssertionError("Issuer challenge does not bind the observed candidate request")
    if envelope.nonce != challenge.nonce:
        raise AssertionError("Issuer challenge envelope nonce does not match its signed payload")
    if challenge.protocol_version != 1 or challenge.issuer_id != issuer_id:
        raise AssertionError("Issuer challenge belongs to a different issuer or protocol")
    if challenge.issuer_incarnation != begin.service.issuer_incarnation:
        raise AssertionError("Issuer challenge belongs to a different issuer incarnation")
    if len(challenge.nonce) != 32 or len(challenge.connection_binding) != 32:
        raise AssertionError("Issuer challenge has invalid nonce or connection binding")
    if not 50 <= challenge.lifetime_ms <= 60000:
        raise AssertionError("Issuer challenge lifetime is outside the production bounds")
    if challenge.audience != "identityd/authority-launch/v1":
        raise AssertionError("Issuer challenge has the wrong audience")
    if begin.service.registration.participant.id != participant_id:
        raise AssertionError("Issuer begin request belongs to a different participant")
    if begin.service.service_incarnation != service_incarnation:
        raise AssertionError("Issuer begin request belongs to a different candidate incarnation")
    if begin.launch_id != launch_id:
        raise AssertionError("Issuer begin request belongs to a different candidate launch")
    if begin.supervision_revision != supervision_revision:
        raise AssertionError("Issuer begin request belongs to a different supervision revision")
    if challenge.supervisor_public_key != supervisor_public_key:
        raise AssertionError("Issuer challenge belongs to a different supervisor key")
    if complete_request.authority_launch_complete.nonce != challenge.nonce:
        raise AssertionError("Issuer launch completion does not answer the observed challenge nonce")
    if not complete_request.authority_launch_complete.HasField("proof"):
        raise AssertionError("Issuer launch completion lacks its proof")
    proof = complete_request.authority_launch_complete.proof
    if proof.purpose != keys.AUTHORITY_LAUNCH_AUTHORIZATION:
        raise AssertionError("Issuer launch completion proof has the wrong purpose")
    if proof.public_key != supervisor_public_key or len(proof.signature) != 64:
        raise AssertionError("Issuer launch completion proof does not match the candidate supervisor key")
    if not complete_reply.HasField("error"):
        raise AssertionError("Issuer did not return a denial for the candidate launch")
    if complete_reply.error.code != core.BUSY:
        raise AssertionError("Issuer denial has the wrong error code")
    if complete_reply.error.message != ACTIVE_GENERATION_DENIAL:
        raise AssertionError("Issuer denial has the wrong message")
