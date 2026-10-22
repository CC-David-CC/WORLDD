"""Separate v2 structural contract. No decoder result proves native provenance.

Expected arm and bindings are trusted supervisor inputs, never artifact-derived.
The caller authenticates worldd's inherited binding pipe and pins native objects.
"""
from dataclasses import dataclass
from google.protobuf.message import DecodeError
import native_input_observer_v2_pb2 as pb

PROTOCOL_VERSION = 2
FRAME_COUNT = 4
MAX_FRAME_BYTES = 4096
MAX_RECEIPTS = 32
MAX_ARTIFACT_BYTES = 16392
MAX_ARM_INTERVAL_NS = 10_000_000_000
BITMAP_BITS = 256  # pinned XLibre DOWN_LENGTH * 8
BITMAP_BYTES = 32
PHASES = (pb.PRE_CANCEL, pb.POST_RELEASES_BEFORE_CONTEXT_CLEAR,
          pb.POST_CANCEL, pb.POST_PEER_BIND)
PAYLOADS = ("arm", "native_transition", "observer_press", "probe_receipts")


class ObservationV2Error(ValueError):
    """Stable code identifies the rejected invariant."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Limits:
    max_frame_bytes: int = MAX_FRAME_BYTES
    max_receipts: int = MAX_RECEIPTS
    max_artifact_bytes: int = MAX_ARTIFACT_BYTES


def require(condition, code):
    if not condition:
        raise ObservationV2Error(code)


def _limits(limits):
    require(type(limits) is Limits, "LIMITS_INVALID")
    for value, cap in zip((limits.max_frame_bytes, limits.max_receipts,
                           limits.max_artifact_bytes),
                          (MAX_FRAME_BYTES, MAX_RECEIPTS, MAX_ARTIFACT_BYTES)):
        require(type(value) is int and 0 < value <= cap, "LIMITS_INVALID")


def _prefix(value):
    out = bytearray()
    while value > 127:
        out.append((value & 127) | 128)
        value >>= 7
    out.append(value)
    return bytes(out)


def _varint(data, pos, bound):
    start = pos
    value = 0
    for shift in (0, 7):
        require(pos < len(data), "FRAME_LENGTH_TRUNCATED")
        byte = data[pos]
        pos += 1
        value |= (byte & 127) << shift
        if byte < 128:
            require(data[start:pos] == _prefix(value), "FRAME_LENGTH_NONCANONICAL")
            require(0 < value <= bound, "FRAME_BYTES_LIMIT")
            return value, pos
    raise ObservationV2Error("FRAME_LENGTH_OVERFLOW")


def _bytes(message):
    return message.SerializeToString(deterministic=True)


def _canonical(message, raw=None):
    serialized = _bytes(message)
    clean = type(message)()
    clean.CopyFrom(message)
    clean.DiscardUnknownFields()
    require(_bytes(clean) == serialized, "PROTOBUF_UNKNOWN_FIELDS")
    if raw is not None:
        require(serialized == raw, "PROTOBUF_NONCANONICAL")
    return serialized


def decode_records(artifact, limits=Limits()):
    """Decode finite canonical bytes. Streaming deadlines belong to the collector."""
    _limits(limits)
    require(type(artifact) is bytes, "FINITE_BYTES_REQUIRED")
    require(len(artifact) <= limits.max_artifact_bytes, "ARTIFACT_BYTES_LIMIT")
    records = []
    pos = 0
    while pos < len(artifact):
        require(len(records) < FRAME_COUNT, "RECORD_COUNT_LIMIT")
        size, pos = _varint(artifact, pos, limits.max_frame_bytes)
        end = pos + size
        require(end <= len(artifact), "PROTOBUF_TRUNCATED")
        raw = artifact[pos:end]
        pos = end
        record = pb.NativeInputObservationRecordV2()
        try:
            record.ParseFromString(raw)
        except DecodeError as exc:
            raise ObservationV2Error("PROTOBUF_INVALID") from exc
        _canonical(record, raw)
        records.append(record)
    require(len(records) == FRAME_COUNT, "FOUR_RECORDS_REQUIRED")
    return records


def _same(left, right):
    return _bytes(left) == _bytes(right)


def _app(app):
    return (0 < len(app.workspace_id) <= 256 and b"\0" not in app.workspace_id
            and 0 < len(app.application_id) <= 256 and b"\0" not in app.application_id)


def _principal(actor):
    return len(actor.issuer_id) == 32 and len(actor.actor_id) == 32


def _client(client):
    return client.client_id > 0 and client.native_object_epoch > 0


def _client_role(client):
    require(client.role in (pb.ClientIdentity.SUPERVISOR, pb.ClientIdentity.BROKER,
                            pb.ClientIdentity.COMPOSITOR, pb.ClientIdentity.APPLICATION),
            "CLIENT_ROLE_INVALID")
    if client.role == pb.ClientIdentity.APPLICATION:
        require(_app(client.application), "CLIENT_APPLICATION_INVALID")
    else:
        require(not client.HasField("application"), "NON_APPLICATION_CLIENT_LABEL_PRESENT")


def _window(window, application):
    return (window.xid > 0 and window.native_object_epoch > 0
            and window.spatial_generation > 0 and _same(window.application, application)
            and _client(window.owner) and window.owner.role == pb.ClientIdentity.APPLICATION
            and _app(window.owner.application) and _same(window.owner.application, application))


def _devices(devices, code):
    require(len(devices) == 4, code)
    by_role = {(d.kind, d.role): d for d in devices}
    require(set(by_role) == {(pb.DeviceIdentity.POINTER, pb.DeviceIdentity.MASTER), (pb.DeviceIdentity.POINTER, pb.DeviceIdentity.SLAVE),
                             (pb.DeviceIdentity.KEYBOARD, pb.DeviceIdentity.MASTER), (pb.DeviceIdentity.KEYBOARD, pb.DeviceIdentity.SLAVE)}, code)
    require(all(d.device_id > 0 and d.native_object_epoch > 0 for d in devices), code)
    for kind in (pb.DeviceIdentity.POINTER, pb.DeviceIdentity.KEYBOARD):
        master = by_role[kind, pb.DeviceIdentity.MASTER]
        slave = by_role[kind, pb.DeviceIdentity.SLAVE]
        other = by_role[pb.DeviceIdentity.KEYBOARD if kind == pb.DeviceIdentity.POINTER else pb.DeviceIdentity.POINTER, pb.DeviceIdentity.MASTER]
        require(master.paired_or_attached == other.device_id
                and slave.paired_or_attached == master.device_id, "DEVICE_ATTACHMENT_INVALID")
    return by_role


def _bits(bitmap):
    return {bit for bit in range(BITMAP_BITS) if bitmap[bit // 8] & (1 << (bit % 8))}


def _expected(expected_arm, bindings, session, arm_id):
    require(type(expected_arm) is pb.ObserverArmV2, "EXPECTED_ARM_TYPE")
    _canonical(expected_arm)
    require(len(_bytes(expected_arm)) <= MAX_FRAME_BYTES, "ARM_BYTES_LIMIT")
    a = expected_arm
    require(a.session_id == session and a.arm_id == arm_id, "ARM_SESSION_JOIN")
    require(a.arm_monotonic_ns > 0 and 0 < a.deadline_monotonic_ns - a.arm_monotonic_ns
            <= MAX_ARM_INTERVAL_NS, "ARM_INTERVAL_INVALID")
    require(_principal(a.old_actor) and _principal(a.peer_actor)
            and not _same(a.old_actor, a.peer_actor), "ACTOR_IDENTITY_INVALID")
    require(a.old_world_actor_id > 0 and a.peer_world_actor_id > 0
            and a.old_world_actor_id != a.peer_world_actor_id, "WORLD_ACTOR_INVALID")
    require(a.old_native_seat_id > 0 and a.peer_native_seat_id > 0
            and a.old_native_seat_id != a.peer_native_seat_id, "NATIVE_SEAT_INVALID")
    require(_app(a.expected_application) and a.presentation_generation > 0
            and _window(a.expected_window, a.expected_application), "TARGET_IDENTITY_INVALID")
    clients = (a.expected_recipient, a.expected_seat_owner, a.expected_window_owner)
    require(all(_client(client) for client in clients), "CLIENT_IDENTITY_INVALID")
    for client in clients:
        _client_role(client)
    require(a.expected_recipient.role in (pb.ClientIdentity.SUPERVISOR, pb.ClientIdentity.COMPOSITOR),
            "PROBE_ROLE_INVALID")
    require(a.expected_seat_owner.role == pb.ClientIdentity.COMPOSITOR, "SEAT_OWNER_ROLE_INVALID")
    require(a.expected_window_owner.role == pb.ClientIdentity.APPLICATION, "WINDOW_OWNER_ROLE_INVALID")
    require(_same(a.expected_window_owner.application, a.expected_application), "WINDOW_OWNER_APPLICATION_JOIN")
    require(len({client.client_id for client in clients}) == 3, "NATIVE_CLIENT_INDEX_ALIAS")
    require(len({client.native_object_epoch for client in clients}) == 3, "NATIVE_CLIENT_EPOCH_ALIAS")
    require(_same(a.expected_window.owner, a.expected_window_owner), "TARGET_WINDOW_OWNER_JOIN")
    require(a.HasField("expected_grab_owner"), "EXPECTED_GRAB_OWNER_REQUIRED")
    require(_client(a.expected_grab_owner), "GRAB_OWNER_IDENTITY_INVALID")
    _client_role(a.expected_grab_owner)
    # Only explicit trusted pins are eligible. Never infer from producer grab/window data.
    require(_same(a.expected_grab_owner, a.expected_recipient)
            or _same(a.expected_grab_owner, a.expected_window_owner), "GRAB_OWNER_CONTEXT_JOIN")
    require(a.expected_xi2_event_type == 4 and 0 < a.expected_detail < 32, "EXPECTED_EVENT_INVALID")
    keys, buttons = list(a.expected_held_keys), list(a.expected_held_buttons)
    require(keys == sorted(set(keys)) and len(keys) >= 2 and all(8 <= k < 256 for k in keys)
            and a.expected_modifier_keycode in keys, "EXPECTED_HELD_KEYS_INVALID")
    require(buttons == sorted(set(buttons)) and len(buttons) >= 1
            and all(0 < b < 32 for b in buttons), "EXPECTED_HELD_BUTTONS_INVALID")
    require(len(keys) + len(buttons) + 1 <= MAX_RECEIPTS, "EXPECTED_RECEIPTS_LIMIT")
    require(type(bindings) in (list, tuple) and len(bindings) == 2, "TWO_BINDINGS_REQUIRED")
    require(all(type(b) is pb.ObserverActorSeatBindingV2 for b in bindings), "BINDING_TYPE")
    require(len({_bytes(b.actor) for b in bindings}) == 2, "BINDING_ACTOR_DUPLICATE")
    for actor, world, seat in ((a.old_actor, a.old_world_actor_id, a.old_native_seat_id),
                                (a.peer_actor, a.peer_world_actor_id, a.peer_native_seat_id)):
        matches = [b for b in bindings if _same(b.actor, actor)]
        require(len(matches) == 1, "BINDING_ACTOR_JOIN")
        b = matches[0]
        _canonical(b)
        require(len(_bytes(b)) <= 1024, "BINDING_BYTES_LIMIT")
        require(b.session_id == session and b.arm_id == arm_id, "BINDING_SESSION_JOIN")
        require((b.world_actor_id, b.native_seat_id, b.presentation_generation)
                == (world, seat, a.presentation_generation)
                and _same(b.application, a.expected_application), "BINDING_CONTEXT_JOIN")
    old = _devices(a.old_actor_devices, "OLD_DEVICE_SET_INVALID")
    peer = _devices(a.peer_actor_devices, "PEER_DEVICE_SET_INVALID")
    all_devices = list(a.old_actor_devices) + list(a.peer_actor_devices)
    require(len({d.device_id for d in all_devices}) == 8, "DEVICE_ID_OVERLAP")
    # Epochs are object pins; equal epoch values across distinct object domains add no identity.
    return old, peer


def _seat(context, a, peer=False):
    require(context.complete, "SEAT_CONTEXT_INCOMPLETE")
    actor = a.peer_actor if peer else a.old_actor
    world = a.peer_world_actor_id if peer else a.old_world_actor_id
    seat = a.peer_native_seat_id if peer else a.old_native_seat_id
    require(_same(context.actor, actor) and context.world_actor_id == world
            and context.native_seat_id == seat and _same(context.owner, a.expected_seat_owner)
            and context.presentation_generation == a.presentation_generation, "SEAT_CONTEXT_JOIN")
    require(len(context.tracked_keys) == BITMAP_BYTES, "TRACKED_KEY_BITMAP_LENGTH")


def _context_bound(context, a, seat):
    require(_same(context.application, a.expected_application)
            and context.application_controller_native_seat_id == seat, "CONTROLLER_CONTEXT_JOIN")
    require(_same(context.target, a.expected_window) and _same(context.focus, a.expected_window),
            "TARGET_FOCUS_CONTEXT_JOIN")


def _context_clear(context):
    require(not context.HasField("target") and not context.HasField("focus")
            and not context.HasField("application")
            and context.application_controller_native_seat_id == 0, "CANCELLED_CONTEXT_REMAINS")
    require(not any(context.tracked_keys) and context.tracked_buttons == 0, "TRACKED_INPUT_REMAINS")


def _snapshot_devices(snapshot, expected, a):
    require(len(snapshot.devices) == len(expected), "SNAPSHOT_DEVICE_COUNT")
    ids = [d.identity.device_id for d in snapshot.devices]
    require(len(set(ids)) == len(ids), "SNAPSHOT_DEVICE_DUPLICATE")
    require(set(ids) == set(expected), "SNAPSHOT_DEVICE_SET_JOIN")
    result = {}
    for d in snapshot.devices:
        require(_same(d.identity, expected[d.identity.device_id]), "SNAPSHOT_DEVICE_PIN_JOIN")
        require(d.bitmap_valid and d.bitmap_bits == BITMAP_BITS, "NATIVE_BITMAP_INVALID")
        require(len(d.down_bitmap) == BITMAP_BYTES and len(d.postdown_bitmap) == BITMAP_BYTES,
                "NATIVE_BITMAP_LENGTH")
        require(d.grab_complete and d.context_complete and d.focus_complete, "DEVICE_CONTEXT_INCOMPLETE")
        if d.identity.kind == pb.DeviceIdentity.POINTER:
            require(0 < d.num_buttons <= 255, "NATIVE_BUTTON_COUNT_INVALID")
            require(all(0 < b <= d.num_buttons for b in _bits(d.down_bitmap) | _bits(d.postdown_bitmap)),
                    "NATIVE_BUTTON_RANGE")
            require(d.buttons_down == len(_bits(d.down_bitmap)), "NATIVE_BUTTON_POPCOUNT")
            require(not d.has_focus_class and d.focus_kind == 0 and d.focus_revert == 0
                    and not d.HasField("native_focus"), "POINTER_FOCUS_INVALID")
        else:
            require(d.num_buttons == d.buttons_down == d.button_state == 0, "KEYBOARD_BUTTON_STATE")
            require((d.has_focus_class and d.focus_kind in (1, 2, 3, 4))
                    or (not d.has_focus_class and d.focus_kind == 0), "NATIVE_FOCUS_KIND")
            require(all(k >= 8 for k in _bits(d.down_bitmap) | _bits(d.postdown_bitmap)),
                    "NATIVE_KEY_RANGE")
            if d.focus_kind == 4:
                require(_same(d.native_focus, a.expected_window), "NATIVE_FOCUS_JOIN")
            else:
                require(not d.HasField("native_focus") and d.focus_revert in (0, 1, 2),
                        "NATIVE_FOCUS_NONE_INVALID")
        if d.active_grab:
            require(d.HasField("grab") and d.grab.resource > 0 and _client(d.grab.owner)
                    and _same(d.grab.owner, a.expected_grab_owner)
                    and _same(d.grab.window, a.expected_window)
                    and _same(d.grab.device, d.identity), "GRAB_IDENTITY_JOIN")
            require(not d.grab.HasField("confine_to")
                    or _window(d.grab.confine_to, a.expected_application), "GRAB_CONFINE_IDENTITY")
        else:
            require(not d.HasField("grab"), "INACTIVE_GRAB_IDENTITY")
        result[d.identity.device_id] = d
    return result


def _clear_native(devices):
    for d in devices:
        require(not any(d.down_bitmap) and not any(d.postdown_bitmap)
                and not d.buttons_down and not d.button_state, "OLD_NATIVE_HELD_REMAINS")
        require(not d.active_grab, "OLD_NATIVE_GRAB_REMAINS")
        require(not d.native_frozen and not d.frozen_by_other_grab, "OLD_NATIVE_FREEZE_REMAINS")
        require(d.native_sync_state == 0, "OLD_NATIVE_SYNC_REMAINS")
        require(not d.pending_events and not d.replay_device, "OLD_NATIVE_PENDING_REPLAY_REMAINS")
        if d.identity.kind == pb.DeviceIdentity.KEYBOARD:
            require((d.focus_kind == 1 if d.has_focus_class else d.focus_kind == 0)
                    and d.focus_revert == 0 and not d.HasField("native_focus"),
                    "OLD_NATIVE_FOCUS_REMAINS")


def _event_tuple(event):
    return (event.event_type, _bytes(event.device), _bytes(event.source), _bytes(event.window),
            event.detail, event.event_time_ms)


def validate_records(records, *, expected_session_id, expected_arm_id,
                     expected_native_session_epoch, expected_arm, expected_bindings,
                     limits=Limits()):
    """Validate producer assertions against caller-supplied trusted context.

    The caller must not compose expected_bindings or expected_arm from this artifact.
    Validation cannot authenticate an inherited FD or establish live native truth.
    """
    _limits(limits)
    require(type(expected_session_id) is bytes and len(expected_session_id) == 16
            and type(expected_arm_id) is bytes and len(expected_arm_id) == 16
            and type(expected_native_session_epoch) is int and expected_native_session_epoch > 0,
            "EXPECTED_SESSION_INVALID")
    old, peer = _expected(expected_arm, expected_bindings, expected_session_id, expected_arm_id)
    a = expected_arm
    require(type(records) is list and len(records) == FRAME_COUNT, "FOUR_RECORDS_REQUIRED")
    total = 0
    for seq, (record, payload) in enumerate(zip(records, PAYLOADS), 1):
        require(type(record) is pb.NativeInputObservationRecordV2, "RECORD_TYPE")
        raw = _canonical(record)
        require(0 < len(raw) <= limits.max_frame_bytes, "FRAME_BYTES_LIMIT")
        total += len(_prefix(len(raw))) + len(raw)
        require(record.protocol_version == PROTOCOL_VERSION, "PROTOCOL_VERSION")
        require(record.session_id == expected_session_id and record.arm_id == expected_arm_id
                and record.native_session_epoch == expected_native_session_epoch, "RECORD_SESSION_JOIN")
        require(record.stream_sequence == seq, "RECORD_SEQUENCE")
        require(record.WhichOneof("payload") == payload, "RECORD_PAYLOAD_ORDER")
    require(total <= limits.max_artifact_bytes, "ARTIFACT_BYTES_LIMIT")
    require(_same(records[0].arm.accepted_command, a), "ACCEPTED_ARM_JOIN")
    snaps = list(records[1].native_transition.snapshots)
    require(tuple(s.phase for s in snaps) == PHASES, "TRANSITION_PHASE_ORDER")
    previous = a.arm_monotonic_ns
    expected_old = {d.device_id: d for d in a.old_actor_devices}
    expected_all = {d.device_id: d for d in list(a.old_actor_devices) + list(a.peer_actor_devices)}
    native = []
    for index, s in enumerate(snaps):
        require(s.complete and s.failure_reason == 0, "SNAPSHOT_INCOMPLETE")
        require(previous <= s.monotonic_ns <= a.deadline_monotonic_ns, "TRANSITION_TIME_ORDER")
        previous = s.monotonic_ns
        controller = a.old_native_seat_id if index < 2 else (0 if index == 2 else a.peer_native_seat_id)
        require(_same(s.application, a.expected_application)
                and s.controller_native_seat_id == controller, "APPLICATION_CONTROLLER_PHASE_JOIN")
        _seat(s.old_seat, a)
        if index == 3:
            _seat(s.peer_seat, a, peer=True)
            require(_same(s.peer_seat.application, a.expected_application)
                    and s.peer_seat.application_controller_native_seat_id == a.peer_native_seat_id,
                    "PEER_CONTROLLER_CONTEXT_JOIN")
            # Hook is before BUTTON assigns peer target/focus or sends its event.
            require(not s.peer_seat.HasField("target") and not s.peer_seat.HasField("focus"),
                    "PEER_PRE_EVENT_TARGET_FOCUS")
            require(not any(s.peer_seat.tracked_keys) and not s.peer_seat.tracked_buttons,
                    "PEER_PRE_EVENT_HELD_STATE")
        else:
            require(not s.HasField("peer_seat"), "UNEXPECTED_PEER_CONTEXT")
        native.append(_snapshot_devices(s, expected_all if index == 3 else expected_old, a))
        if index:
            require(not s.global_pending and not s.replay_pending and not s.replay_in_progress,
                    "GLOBAL_PENDING_REPLAY_REMAINS")
            _clear_native(native[-1][device] for device in expected_old)
        if index < 2:
            _context_bound(s.old_seat, a, a.old_native_seat_id)
        else:
            _context_clear(s.old_seat)
    pre, released, cancelled, bound = snaps
    held_keys, held_buttons = set(a.expected_held_keys), set(a.expected_held_buttons)
    require(_bits(pre.old_seat.tracked_keys) == held_keys
            and {b for b in range(32) if pre.old_seat.tracked_buttons & (1 << b)} == held_buttons,
            "PRE_CANCEL_TRACKED_HELD_JOIN")
    for kind, held in ((pb.DeviceIdentity.KEYBOARD, held_keys), (pb.DeviceIdentity.POINTER, held_buttons)):
        actual = set()
        for d in native[0].values():
            if d.identity.kind == kind:
                require((_bits(d.down_bitmap) | _bits(d.postdown_bitmap)) <= held,
                        "PRE_CANCEL_NATIVE_HELD_JOIN")
                actual |= _bits(d.down_bitmap)
        require(actual == held, "PRE_CANCEL_NATIVE_HELD_JOIN")
    require(any(d.active_grab for d in native[0].values() if d.identity.kind == pb.DeviceIdentity.POINTER),
            "PRE_CANCEL_CAPTURE_MISSING")
    require(native[0][old[pb.DeviceIdentity.KEYBOARD, pb.DeviceIdentity.MASTER].device_id].focus_kind == 4,
            "PRE_CANCEL_FOCUS_MISSING")
    require(not any(released.old_seat.tracked_keys) and not released.old_seat.tracked_buttons,
            "POST_RELEASE_TRACKED_REMAINS")
    observed = records[2].observer_press
    require(not observed.overflow, "OBSERVER_OVERFLOW")
    require(not observed.disconnected, "OBSERVER_DISCONNECTED")
    require(not observed.synthetic, "OBSERVER_SYNTHETIC")
    require(observed.observer_ordinal == 1, "OBSERVER_ORDINAL")
    require(previous <= observed.callback_monotonic_ns <= a.deadline_monotonic_ns, "OBSERVER_TIME_ORDER")
    require(_same(observed.recipient, a.expected_recipient), "OBSERVER_RECIPIENT_JOIN")
    require(_same(observed.window, a.expected_window), "OBSERVER_WINDOW_JOIN")
    require(_same(observed.device, peer[pb.DeviceIdentity.POINTER, pb.DeviceIdentity.MASTER])
            and _same(observed.source, peer[pb.DeviceIdentity.POINTER, pb.DeviceIdentity.SLAVE]), "OBSERVER_DEVICE_JOIN")
    require((observed.xi2_event_type, observed.detail)
            == (a.expected_xi2_event_type, a.expected_detail), "OBSERVER_EVENT_JOIN")
    batch = records[3].probe_receipts
    require(not batch.overflow, "PROBE_OVERFLOW")
    require(not batch.disconnected, "PROBE_DISCONNECTED")
    require(_same(batch.recipient, a.expected_recipient)
            and _same(batch.window, a.expected_window), "PROBE_IDENTITY_JOIN")
    receipts = list(batch.receipts)
    require(0 < len(receipts) <= limits.max_receipts, "RECEIPT_COUNT_INVALID")
    wanted = [(5, button) for button in sorted(held_buttons)] + [(3, key) for key in sorted(held_keys)]
    releases = []
    peer_event = None
    last_time = pre.monotonic_ns
    for index, receipt in enumerate(receipts):
        require(receipt.receipt_ordinal == a.prearm_receipt_watermark + index + 1, "RECEIPT_ORDINAL")
        require(last_time <= receipt.monotonic_ns <= a.deadline_monotonic_ns, "RECEIPT_TIME_ORDER")
        last_time = receipt.monotonic_ns
        require(not receipt.synthetic, "RECEIPT_SYNTHETIC")
        require(_same(receipt.window, a.expected_window), "RECEIPT_WINDOW_JOIN")
        if receipt.event_type in (3, 5):
            require(peer_event is None, "OLD_RELEASE_AFTER_PEER")
            kind = pb.DeviceIdentity.KEYBOARD if receipt.event_type == 3 else pb.DeviceIdentity.POINTER
            require(_same(receipt.device, old[kind, pb.DeviceIdentity.MASTER])
                    and _same(receipt.source, old[kind, pb.DeviceIdentity.SLAVE]), "RELEASE_DEVICE_JOIN")
            require(receipt.detail in (held_keys if kind == pb.DeviceIdentity.KEYBOARD else held_buttons), "RELEASE_DETAIL_JOIN")
            require((receipt.event_type, receipt.detail) not in releases, "OLD_RELEASE_DUPLICATE")
            releases.append((receipt.event_type, receipt.detail))
        else:
            require(peer_event is None, "PEER_EVENT_DUPLICATE")
            require(receipt.monotonic_ns >= observed.callback_monotonic_ns, "PEER_RECEIPT_BEFORE_CALLBACK")
            expected = (observed.xi2_event_type, _bytes(observed.device), _bytes(observed.source),
                        _bytes(observed.window), observed.detail, observed.event_time_ms)
            require(_event_tuple(receipt) == expected, "PEER_EVENT_TUPLE_JOIN")
            require(releases == wanted, "OLD_RELEASES_MISSING_OR_ORDER")
            peer_event = receipt
    require(peer_event is not None, "PEER_EVENT_MISSING")
    require(releases == wanted, "OLD_RELEASES_MISSING_OR_ORDER")
    return observed


def decode_and_validate(artifact, **kwargs):
    return validate_records(decode_records(artifact, kwargs.get("limits", Limits())), **kwargs)
