"""Pure prearm control validation. Native authentication and FD I/O are unimplemented."""
from dataclasses import dataclass, replace
import time
from google.protobuf.message import DecodeError
import native_input_observer_control_v2_pb2 as control_pb
import native_input_observer_v2_pb2 as pb
import input_state_observer_v2 as artifact_v2

MAX_PAYLOAD = 4096
MAX_FRAME = 4098
MAX_CONTROL = 8196
MAX_DEADLINE_NS = 10_000_000_000
MAX_BINDING = 1024
UINT32_MAX = (1 << 32) - 1
UINT64_MAX = (1 << 64) - 1


class SetupError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise SetupError(code)


def _uint(value, maximum=UINT64_MAX, *, zero=False):
    return type(value) is int and (0 if zero else 1) <= value <= maximum


def _serialize(message):
    return message.SerializeToString(deterministic=True)


def _same(left, right):
    return _serialize(left) == _serialize(right)


def _canonical(message, raw=None):
    encoded = _serialize(message)
    clean = type(message)()
    clean.CopyFrom(message)
    clean.DiscardUnknownFields()
    require(_serialize(clean) == encoded, "PROTOBUF_UNKNOWN_FIELDS")
    if raw is not None:
        require(encoded == raw, "PROTOBUF_NONCANONICAL")
    return encoded


def _copy(message, cls, *, cap=MAX_PAYLOAD, code="MESSAGE_TYPE_INVALID"):
    require(type(message) is cls, code)
    require(message.ByteSize() <= cap, "BINDING_BYTES_LIMIT" if cap == MAX_BINDING else "PAYLOAD_SIZE_INVALID")
    raw = _canonical(message)
    copy = cls()
    copy.CopyFrom(message)
    return copy


def _parse(cls, raw):
    require(type(raw) is bytes and len(raw) <= MAX_PAYLOAD, "PAYLOAD_SIZE_INVALID")
    message = cls()
    try:
        message.ParseFromString(raw)
    except DecodeError as error:
        raise SetupError("PROTOBUF_INVALID") from error
    _canonical(message, raw)
    return message


def _varint(value):
    out = bytearray()
    while value > 127:
        out.append((value & 127) | 128)
        value >>= 7
    out.append(value)
    return bytes(out)


def _read_length(data, pos):
    start, value = pos, 0
    for shift in (0, 7):
        require(pos < len(data), "FRAME_LENGTH_TRUNCATED")
        byte = data[pos]
        pos += 1
        value |= (byte & 127) << shift
        if byte < 128:
            require(data[start:pos] == _varint(value), "FRAME_LENGTH_NONCANONICAL")
            require(0 < value <= MAX_PAYLOAD, "PAYLOAD_SIZE_INVALID")
            return value, pos
    raise SetupError("FRAME_LENGTH_OVERFLOW")


def frame(message):
    require(type(message) in (control_pb.ObserverControlEnvelopeV2, control_pb.ObserverSetupReplyV2),
            "FRAME_MESSAGE_TYPE")
    require(0 < message.ByteSize() <= MAX_PAYLOAD, "PAYLOAD_SIZE_INVALID")
    raw = _canonical(message)
    return _varint(len(raw)) + raw


def _envelope(field, message):
    classes = {"setup_request": control_pb.ObserverSetupRequestV2, "arm": pb.ObserverArmV2}
    require(field in classes and type(message) is classes[field], "MESSAGE_TYPE_INVALID")
    message = _copy(message, classes[field])
    envelope = control_pb.ObserverControlEnvelopeV2()
    getattr(envelope, field).CopyFrom(message)
    return frame(envelope)


def encode_control(request, arm):
    return _envelope("setup_request", request) + _envelope("arm", arm)


def decode_control(data):
    require(type(data) is bytes and len(data) <= MAX_CONTROL, "CONTROL_SIZE_INVALID")
    result, pos = [], 0
    for expected in ("setup_request", "arm"):
        size, pos = _read_length(data, pos)
        end = pos + size
        require(end <= len(data), "PROTOBUF_TRUNCATED")
        envelope = _parse(control_pb.ObserverControlEnvelopeV2, data[pos:end])
        pos = end
        require(envelope.WhichOneof("message") == expected, "CONTROL_MESSAGE_ORDER")
        result.append(getattr(envelope, expected))
    require(pos == len(data), "CONTROL_EXTRA_BYTES")
    return tuple(result)


def decode_reply(data):
    require(type(data) is bytes and len(data) <= MAX_FRAME, "REPLY_SIZE_INVALID")
    size, pos = _read_length(data, 0)
    require(pos + size == len(data), "REPLY_FRAME_SHAPE")
    return _parse(control_pb.ObserverSetupReplyV2, data[pos:])


def _app(application):
    return (0 < len(application.workspace_id) <= 256 and b"\0" not in application.workspace_id
            and 0 < len(application.application_id) <= 256 and b"\0" not in application.application_id)


def _principal(actor):
    return len(actor.issuer_id) == len(actor.actor_id) == 32


def _valid_client(client, role):
    require(_uint(client.client_id, UINT32_MAX) and _uint(client.native_object_epoch)
            and client.role == role, "CLIENT_PIN_INVALID")
    if role == pb.ClientIdentity.APPLICATION:
        require(_app(client.application), "APP_CLIENT_IDENTITY_INVALID")
    else:
        require(not client.HasField("application"), "PRIVILEGED_CLIENT_HAS_APPLICATION")


def _devices(devices):
    pointer, keyboard = pb.DeviceIdentity.POINTER, pb.DeviceIdentity.KEYBOARD
    master, slave = pb.DeviceIdentity.MASTER, pb.DeviceIdentity.SLAVE
    require(len(devices) == 4 and {(d.kind, d.role) for d in devices}
            == {(pointer, master), (pointer, slave), (keyboard, master), (keyboard, slave)},
            "DEVICE_ROLE_SET")
    require(len({d.device_id for d in devices}) == 4
            and all(_uint(d.device_id, UINT32_MAX) and _uint(d.native_object_epoch) for d in devices),
            "DEVICE_PIN_INVALID")
    by_role = {(d.kind, d.role): d for d in devices}
    pm, km = by_role[pointer, master], by_role[keyboard, master]
    require(pm.paired_or_attached == km.device_id and km.paired_or_attached == pm.device_id
            and by_role[pointer, slave].paired_or_attached == pm.device_id
            and by_role[keyboard, slave].paired_or_attached == km.device_id, "DEVICE_ATTACHMENT_INVALID")
    return tuple(sorted((d.device_id, _serialize(d)) for d in devices))


@dataclass(frozen=True)
class TrustedSetup:
    session_id: bytes
    arm_id: bytes
    old_binding: object
    peer_binding: object
    expected_application: object
    expected_target_xid: int
    expected_target_spatial_generation: int
    expected_presentation_generation: int
    expected_probe_marker_xid: int
    expected_probe_role: int
    start_monotonic_ns: int
    deadline_monotonic_ns: int
    expected_native_session_epoch: int | None = None


@dataclass(frozen=True)
class HeldInputContext:
    keys: tuple
    buttons: tuple
    modifier_keycode: int
    prearm_receipt_watermark: int
    peer_button: int


@dataclass(frozen=True)
class ValidatedPins:
    native_session_epoch: int
    old_native_seat_id: int
    peer_native_seat_id: int
    old_devices: tuple
    peer_devices: tuple
    compositor_seat_owner: bytes
    app_target: bytes
    probe_marker: bytes
    privileged_probe_owner: bytes
    deadline_monotonic_ns: int
    session_id: bytes
    arm_id: bytes
    application: bytes
    target_xid: int
    presentation_generation: int
    marker_xid: int
    probe_role: int
    bindings: tuple
    start_monotonic_ns: int
    target_spatial_generation: int
    request_bytes: bytes
    reply_bytes: bytes


class _Budget:
    """One non-resetting interval, shared by every framed validation stage."""
    def __init__(self, trusted, clock_ns):
        require(type(trusted) is TrustedSetup, "TRUSTED_CONTEXT_TYPE")
        require(_uint(trusted.start_monotonic_ns) and _uint(trusted.deadline_monotonic_ns)
                and 0 < trusted.deadline_monotonic_ns - trusted.start_monotonic_ns <= MAX_DEADLINE_NS,
                "TRUSTED_CONTEXT_INVALID")
        require(callable(clock_ns), "MONOTONIC_CLOCK_INVALID")
        self.start = trusted.start_monotonic_ns
        self.deadline = trusted.deadline_monotonic_ns
        self.clock = clock_ns
        self.last = self.start

    def sample(self):
        try:
            value = self.clock()
        except (StopIteration, TypeError, ValueError, OverflowError) as error:
            raise SetupError("MONOTONIC_CLOCK_INVALID") from error
        require(_uint(value, zero=True) and value >= self.last, "MONOTONIC_CLOCK_INVALID")
        require(value < self.deadline, "DEADLINE_EXPIRED")
        self.last = value
        return value


def _trusted(trusted):
    """Copy every borrowed protobuf before using or retaining its values."""
    require(type(trusted) is TrustedSetup, "TRUSTED_CONTEXT_TYPE")
    require(type(trusted.session_id) is bytes and len(trusted.session_id) == 16
            and type(trusted.arm_id) is bytes and len(trusted.arm_id) == 16, "SESSION_ARM_IDS_INVALID")
    require(_uint(trusted.expected_target_xid, UINT32_MAX)
            and _uint(trusted.expected_probe_marker_xid, UINT32_MAX)
            and trusted.expected_target_xid != trusted.expected_probe_marker_xid
            and _uint(trusted.expected_target_spatial_generation)
            and _uint(trusted.expected_presentation_generation)
            and type(trusted.expected_probe_role) is int
            and trusted.expected_probe_role in (pb.ClientIdentity.SUPERVISOR, pb.ClientIdentity.COMPOSITOR),
            "TRUSTED_SELECTOR_INVALID")
    require(trusted.expected_native_session_epoch is None or _uint(trusted.expected_native_session_epoch),
            "TRUSTED_NATIVE_EPOCH_INVALID")
    app = _copy(trusted.expected_application, pb.ApplicationIdentity, code="TRUSTED_APP_INVALID")
    require(_app(app), "TRUSTED_APP_INVALID")
    bindings = tuple(_copy(b, pb.ObserverActorSeatBindingV2, cap=MAX_BINDING, code="BINDING_TYPE")
                     for b in (trusted.old_binding, trusted.peer_binding))
    for binding in bindings:
        require(binding.session_id == trusted.session_id and binding.arm_id == trusted.arm_id
                and _principal(binding.actor) and _uint(binding.world_actor_id)
                and _uint(binding.native_seat_id) and _same(binding.application, app)
                and binding.presentation_generation == trusted.expected_presentation_generation,
                "WORLD_BINDING_JOIN")
    old, peer = bindings
    require(old.world_actor_id != peer.world_actor_id and not _same(old.actor, peer.actor),
            "WORLD_ACTOR_DISTINCTION")
    require(old.native_seat_id != peer.native_seat_id, "SEAT_SELECTORS_INVALID")
    return replace(trusted, expected_application=app, old_binding=old, peer_binding=peer)


def _setup(request, reply, trusted):
    require(request.session_id == reply.session_id == trusted.session_id
            and request.arm_id == reply.arm_id == trusted.arm_id, "SESSION_ARM_JOIN")
    require(_uint(request.old_native_seat_id) and _uint(request.peer_native_seat_id)
            and request.old_native_seat_id != request.peer_native_seat_id, "SEAT_SELECTORS_INVALID")
    require(request.old_native_seat_id == trusted.old_binding.native_seat_id
            and request.peer_native_seat_id == trusted.peer_binding.native_seat_id, "WORLD_BINDING_JOIN")
    require((request.target_xid, request.target_spatial_generation, request.presentation_generation,
             request.probe_marker_xid, request.expected_probe_role)
            == (trusted.expected_target_xid, trusted.expected_target_spatial_generation,
                trusted.expected_presentation_generation, trusted.expected_probe_marker_xid, trusted.expected_probe_role)
            and _same(request.expected_application, trusted.expected_application), "TRUSTED_SELECTOR_JOIN")
    require(request.deadline_monotonic_ns == trusted.deadline_monotonic_ns, "DEADLINE_JOIN")
    require(_same(reply.accepted_request, request), "SETUP_REQUEST_ECHO_JOIN")
    require(reply.old_native_seat_id == request.old_native_seat_id
            and reply.peer_native_seat_id == request.peer_native_seat_id
            and _uint(reply.native_session_epoch), "REPLY_SESSION_OR_SEAT_JOIN")
    if trusted.expected_native_session_epoch is not None:
        require(reply.native_session_epoch == trusted.expected_native_session_epoch, "STALE_NATIVE_SESSION")
    old_devices = _devices(reply.old_actor_devices)
    peer_devices = _devices(reply.peer_actor_devices)
    require(not ({i for i, _ in old_devices} & {i for i, _ in peer_devices}), "CROSS_SEAT_DEVICE_OVERLAP")
    seat, owner = reply.compositor_seat_owner, reply.app_target_owner
    _valid_client(seat, pb.ClientIdentity.COMPOSITOR)
    _valid_client(owner, pb.ClientIdentity.APPLICATION)
    window = reply.app_target
    require(window.xid == trusted.expected_target_xid and _uint(window.native_object_epoch)
            and window.spatial_generation == trusted.expected_target_spatial_generation
            and _same(window.application, trusted.expected_application) and _same(window.owner, owner)
            and _same(owner.application, trusted.expected_application), "APP_TARGET_PIN_INVALID")
    marker = reply.probe_marker
    require(marker.xid == trusted.expected_probe_marker_xid and _uint(marker.window_epoch)
            and marker.privileged_owner.role == trusted.expected_probe_role, "MARKER_PIN_JOIN")
    probe = marker.privileged_owner
    _valid_client(probe, trusted.expected_probe_role)
    require(len({c.client_id for c in (seat, owner, probe)}) == 3
            and len({c.native_object_epoch for c in (seat, owner, probe)}) == 3, "NATIVE_OWNER_SEPARATION")
    return ValidatedPins(reply.native_session_epoch, request.old_native_seat_id, request.peer_native_seat_id,
        old_devices, peer_devices, _serialize(seat), _serialize(window), _serialize(marker), _serialize(probe),
        trusted.deadline_monotonic_ns, trusted.session_id, trusted.arm_id, _serialize(trusted.expected_application),
        trusted.expected_target_xid, trusted.expected_presentation_generation, trusted.expected_probe_marker_xid,
        trusted.expected_probe_role, (_serialize(trusted.old_binding), _serialize(trusted.peer_binding)),
        trusted.start_monotonic_ns, trusted.expected_target_spatial_generation, _serialize(request), _serialize(reply))


def validate_setup(request, reply, trusted, *, clock_ns=time.monotonic_ns):
    budget = _Budget(trusted, clock_ns)
    budget.sample()
    trusted = _trusted(trusted)
    request = _copy(request, control_pb.ObserverSetupRequestV2)
    reply = _copy(reply, control_pb.ObserverSetupReplyV2)
    pins = _setup(request, reply, trusted)
    budget.sample()
    return pins


def _held(context):
    require(type(context) is HeldInputContext and type(context.keys) is tuple
            and type(context.buttons) is tuple and type(context.modifier_keycode) is int
            and all(type(value) is int for value in context.keys + context.buttons)
            and _uint(context.prearm_receipt_watermark, zero=True)
            and _uint(context.peer_button, 31), "HELD_INPUT_CONTEXT_INVALID")
    require(context.keys == tuple(sorted(set(context.keys))) and len(context.keys) >= 2
            and all(8 <= key < 256 for key in context.keys) and context.modifier_keycode in context.keys
            and context.buttons == tuple(sorted(set(context.buttons))) and len(context.buttons) >= 1
            and all(0 < button < 32 for button in context.buttons)
            and len(context.keys) + len(context.buttons) + 1 <= 32, "HELD_INPUT_CONTEXT_INVALID")
    return context


def _arm(arm, pins, trusted, held, bindings, now):
    require(type(pins) is ValidatedPins, "ARM_CONTEXT_TYPE")
    integer_fields = ("native_session_epoch", "old_native_seat_id", "peer_native_seat_id",
                      "deadline_monotonic_ns", "target_xid", "presentation_generation", "marker_xid",
                      "probe_role", "start_monotonic_ns", "target_spatial_generation")
    byte_fields = ("compositor_seat_owner", "app_target", "probe_marker", "privileged_probe_owner",
                   "session_id", "arm_id", "application", "request_bytes", "reply_bytes")
    require(all(_uint(getattr(pins, name)) for name in integer_fields)
            and all(type(getattr(pins, name)) is bytes and len(getattr(pins, name)) <= MAX_PAYLOAD
                    for name in byte_fields)
            and type(pins.bindings) is tuple and len(pins.bindings) == 2
            and all(type(value) is bytes and len(value) <= MAX_BINDING for value in pins.bindings),
            "VALIDATED_PINS_INVALID")
    for devices in (pins.old_devices, pins.peer_devices):
        require(type(devices) is tuple and len(devices) == 4
                and all(type(pair) is tuple and len(pair) == 2 and _uint(pair[0], UINT32_MAX)
                        and type(pair[1]) is bytes and len(pair[1]) <= MAX_PAYLOAD for pair in devices),
                "VALIDATED_PINS_INVALID")
    require(type(bindings) in (tuple, list) and len(bindings) == 2, "TWO_BINDINGS_REQUIRED")
    bindings = tuple(_copy(b, pb.ObserverActorSeatBindingV2, cap=MAX_BINDING, code="BINDING_TYPE") for b in bindings)
    require(tuple(_serialize(b) for b in bindings) == pins.bindings
            and (_serialize(trusted.old_binding), _serialize(trusted.peer_binding)) == pins.bindings,
            "ARM_BINDING_JOIN")
    require((trusted.session_id, trusted.arm_id, trusted.start_monotonic_ns, trusted.deadline_monotonic_ns,
             trusted.expected_target_xid, trusted.expected_target_spatial_generation,
             trusted.expected_presentation_generation, trusted.expected_probe_marker_xid, trusted.expected_probe_role,
             _serialize(trusted.expected_application))
            == (pins.session_id, pins.arm_id, pins.start_monotonic_ns, pins.deadline_monotonic_ns, pins.target_xid,
                pins.target_spatial_generation, pins.presentation_generation, pins.marker_xid, pins.probe_role, pins.application),
            "ARM_TRUSTED_CONTEXT_JOIN")
    require(type(pins.request_bytes) is bytes and type(pins.reply_bytes) is bytes, "VALIDATED_PINS_INVALID")
    frozen = _setup(_parse(control_pb.ObserverSetupRequestV2, pins.request_bytes),
                    _parse(control_pb.ObserverSetupReplyV2, pins.reply_bytes), trusted)
    require(frozen == pins, "VALIDATED_PINS_JOIN")
    require(arm.session_id == pins.session_id and arm.arm_id == pins.arm_id, "ARM_SESSION_JOIN")
    require(arm.deadline_monotonic_ns == pins.deadline_monotonic_ns, "ARM_DEADLINE_JOIN")
    require(trusted.start_monotonic_ns <= arm.arm_monotonic_ns <= now, "ARM_TIME_INVALID")
    require(arm.old_native_seat_id == pins.old_native_seat_id and arm.peer_native_seat_id == pins.peer_native_seat_id,
            "ARM_SEAT_JOIN")
    require(_same(arm.old_actor, bindings[0].actor) and _same(arm.peer_actor, bindings[1].actor)
            and arm.old_world_actor_id == bindings[0].world_actor_id
            and arm.peer_world_actor_id == bindings[1].world_actor_id, "ARM_ACTOR_JOIN")
    require(_serialize(arm.expected_application) == pins.application
            and arm.presentation_generation == pins.presentation_generation
            and _serialize(arm.expected_window) == pins.app_target, "ARM_TARGET_JOIN")
    # app_target is a WindowIdentity, never a ClientIdentity.
    window = _parse(pb.WindowIdentity, pins.app_target)
    require(_serialize(arm.expected_seat_owner) == pins.compositor_seat_owner
            and _same(arm.expected_window_owner, window.owner), "ARM_OWNER_JOIN")
    require(_serialize(arm.expected_recipient) == pins.privileged_probe_owner, "ARM_PROBE_JOIN")
    # This setup contract is the chosen strict XI2 fixture. General artifact alternatives stay unchanged.
    require(_same(arm.expected_grab_owner, arm.expected_recipient), "ARM_GRAB_JOIN")
    require(tuple(arm.expected_held_keys) == held.keys and tuple(arm.expected_held_buttons) == held.buttons
            and arm.expected_modifier_keycode == held.modifier_keycode, "ARM_HELD_INPUT_JOIN")
    require(arm.prearm_receipt_watermark == held.prearm_receipt_watermark, "ARM_WATERMARK_JOIN")
    require(arm.expected_xi2_event_type == 4 and arm.expected_detail == held.peer_button, "ARM_EVENT_JOIN")
    for devices, expected in ((arm.old_actor_devices, pins.old_devices), (arm.peer_actor_devices, pins.peer_devices)):
        require(tuple(sorted((d.device_id, _serialize(d)) for d in devices)) == expected, "ARM_DEVICE_PINS_JOIN")
    try:
        artifact_v2._expected(arm, bindings, pins.session_id, pins.arm_id)
    except artifact_v2.ObservationV2Error as error:
        raise SetupError(error.code) from error


def validate_arm(arm, pins, trusted, held_input, bindings=None, *, clock_ns=time.monotonic_ns):
    budget = _Budget(trusted, clock_ns)
    before = budget.sample()
    trusted = _trusted(trusted)
    arm = _copy(arm, pb.ObserverArmV2)
    _arm(arm, pins, trusted, _held(held_input), bindings, before)
    budget.sample()
    # Send this immutable validated snapshot, rather than a borrowed mutable arm.
    return _serialize(arm)


def validate_frames(control_bytes, reply_bytes, trusted, expected_arm, held_input, *, clock_ns=time.monotonic_ns):
    budget = _Budget(trusted, clock_ns)
    budget.sample()
    trusted = _trusted(trusted)
    expected_arm = _copy(expected_arm, pb.ObserverArmV2, code="EXPECTED_ARM_TYPE")
    held = _held(held_input)
    request, arm = decode_control(control_bytes)
    reply = decode_reply(reply_bytes)
    budget.sample()
    pins = _setup(request, reply, trusted)
    before_arm = budget.sample()
    require(_same(arm, expected_arm), "ARM_FRAME_JOIN")
    _arm(arm, pins, trusted, held, (trusted.old_binding, trusted.peer_binding), before_arm)
    budget.sample()
    return pins
