"""Validate the authenticated issuer trace for one denied supervisor launch."""
import core_pb2 as core
import issuer_pb2 as identity
import key_pb2 as keys

ACTIVE_GENERATION_DENIAL = "New launch requires prior generation cleanup"
EXPECTED_OPERATIONS = ("hello", "info", "authority_launch_begin", "authority_launch_complete")
MAX_DIAGNOSTIC_MESSAGES = 8


def describe_issuer_trace(observations):
    """Return bounded operation and correlation metadata, never message payloads."""
    try:
        pairs = list(observations)
    except Exception:
        return "observed_issuer_trace=<unavailable>"
    details = []
    for pair in pairs[:MAX_DIAGNOSTIC_MESSAGES]:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            details.append({"malformed_pair": True})
            continue
        request, reply = pair
        try:
            operation = request.WhichOneof("operation")
        except Exception:
            operation = None
        try:
            reply_variant = reply.WhichOneof("outcome")
        except Exception:
            reply_variant = None
        details.append({
            "operation": operation,
            "request_id": getattr(request, "request_id", None),
            "reply_id": getattr(reply, "request_id", None),
            "reply_variant": reply_variant,
        })
    omitted = max(0, len(pairs) - len(details))
    return "observed_issuer_trace=" + repr({"count": len(pairs), "messages": details,
                                             "omitted": omitted})


def _require(condition, message):
    if not condition:
        raise AssertionError(message)


def require_active_generation_denial_trace(observations, participant_id, service_incarnation,
                                           launch_id, supervision_revision, supervisor_public_key,
                                           issuer_id):
    pairs = list(observations)
    operations = tuple(request.WhichOneof("operation") for request, _ in pairs)
    _require(operations == EXPECTED_OPERATIONS,
             "Issuer relay must observe exactly hello, info, launch begin, and launch complete")

    for index, (request, reply) in enumerate(pairs):
        label = EXPECTED_OPERATIONS[index]
        _require(request.protocol_version == 1 and reply.protocol_version == 1,
                 "Issuer " + label + " protocol version is invalid")
        _require(request.request_id == reply.request_id,
                 "Issuer " + label + " reply does not correlate to its request")

    _, hello_reply = pairs[0]
    _require(hello_reply.WhichOneof("outcome") == "hello",
             "Issuer hello did not return a successful hello reply")

    _, info_reply = pairs[1]
    _require(info_reply.WhichOneof("outcome") == "info",
             "Issuer info did not return a successful info reply")
    _require(info_reply.info.issuer_id == issuer_id,
             "Issuer info reply does not match the pinned issuer identity")

    begin_request, begin_reply = pairs[2]
    complete_request, complete_reply = pairs[3]
    _require(begin_reply.WhichOneof("outcome") == "challenge" and
             begin_reply.challenge.HasField("signing_request"),
             "Issuer launch begin did not return its signed challenge")
    envelope = begin_reply.challenge
    _require(envelope.signing_request.purpose == keys.AUTHORITY_LAUNCH_AUTHORIZATION,
             "Issuer challenge has the wrong signing purpose")
    challenge = identity.AuthorityLaunchChallenge.FromString(envelope.signing_request.payload)
    begin = begin_request.authority_launch_begin
    _require(challenge.request == begin,
             "Issuer challenge does not bind the observed candidate request")
    _require(envelope.nonce == challenge.nonce,
             "Issuer challenge envelope nonce does not match its signed payload")
    _require(challenge.protocol_version == 1 and challenge.issuer_id == issuer_id,
             "Issuer challenge belongs to a different issuer or protocol")
    _require(challenge.issuer_incarnation == begin.service.issuer_incarnation,
             "Issuer challenge belongs to a different issuer incarnation")
    _require(len(challenge.nonce) == 32 and len(challenge.connection_binding) == 32,
             "Issuer challenge has invalid nonce or connection binding")
    _require(50 <= challenge.lifetime_ms <= 60000,
             "Issuer challenge lifetime is outside the production bounds")
    _require(challenge.audience == "identityd/authority-launch/v1",
             "Issuer challenge has the wrong audience")
    _require(begin.service.registration.participant.id == participant_id,
             "Issuer begin request belongs to a different participant")
    _require(begin.service.service_incarnation == service_incarnation,
             "Issuer begin request belongs to a different candidate incarnation")
    _require(begin.launch_id == launch_id,
             "Issuer begin request belongs to a different candidate launch")
    _require(begin.supervision_revision == supervision_revision,
             "Issuer begin request belongs to a different supervision revision")
    _require(challenge.supervisor_public_key == supervisor_public_key,
             "Issuer challenge belongs to a different supervisor key")
    _require(complete_request.authority_launch_complete.nonce == challenge.nonce,
             "Issuer launch completion does not answer the observed challenge nonce")
    _require(complete_request.authority_launch_complete.HasField("proof"),
             "Issuer launch completion lacks its proof")
    proof = complete_request.authority_launch_complete.proof
    _require(proof.purpose == keys.AUTHORITY_LAUNCH_AUTHORIZATION,
             "Issuer launch completion proof has the wrong purpose")
    _require(proof.public_key == supervisor_public_key and len(proof.signature) == 64,
             "Issuer launch completion proof does not match the candidate supervisor key")
    _require(complete_reply.WhichOneof("outcome") == "error",
             "Issuer did not return a denial for the candidate launch")
    _require(complete_reply.error.code == core.BUSY,
             "Issuer denial has the wrong error code")
    _require(complete_reply.error.message == ACTIVE_GENERATION_DENIAL,
             "Issuer denial has the wrong message")
