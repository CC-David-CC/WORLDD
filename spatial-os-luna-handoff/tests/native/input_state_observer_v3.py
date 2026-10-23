"""Additive lifecycle oracle. Structural validity never proves native provenance.

Expected arms and actor-seat bindings are independently trusted inputs.
V2 primitives retain their original error codes and validation semantics.
"""
from dataclasses import dataclass
from google.protobuf.message import DecodeError
import native_input_observer_v2_pb2 as v2_pb
import native_input_lifecycle_v3_pb2 as pb
import input_state_observer_v2 as v2

ObservationV3Error = v2.ObservationV2Error
require = v2.require
same = v2._same
MAX_FRAME_BYTES = 8192
MAX_ARTIFACT_BYTES = 32776
MAX_CONTROL_BYTES = 4096
FRAME_COUNT = 4
CAUSES = (pb.TARGET_DESTRUCTION, pb.ACTOR_DISCONNECT, pb.ACTOR_REVOCATION)
PAYLOADS = ("arm", "native_transition", "observer_press", "probe_receipts")


@dataclass(frozen=True)
class Limits:
    max_frame_bytes: int = MAX_FRAME_BYTES
    max_artifact_bytes: int = MAX_ARTIFACT_BYTES
    max_receipts: int = v2.MAX_RECEIPTS


def _limits(limits):
    require(type(limits) is Limits, "LIMITS_INVALID")
    for value, bound in ((limits.max_frame_bytes, MAX_FRAME_BYTES),
                         (limits.max_artifact_bytes, MAX_ARTIFACT_BYTES),
                         (limits.max_receipts, v2.MAX_RECEIPTS)):
        require(type(value) is int and 0 < value <= bound, "LIMITS_INVALID")


def decode_records(artifact, limits=Limits()):
    _limits(limits)
    require(type(artifact) is bytes, "FINITE_BYTES_REQUIRED")
    require(len(artifact) <= limits.max_artifact_bytes, "ARTIFACT_BYTES_LIMIT")
    records, pos = [], 0
    while pos < len(artifact):
        require(len(records) < FRAME_COUNT, "RECORD_COUNT_LIMIT")
        size, pos = v2._varint(artifact, pos, limits.max_frame_bytes)
        end = pos + size
        require(end <= len(artifact), "PROTOBUF_TRUNCATED")
        raw = artifact[pos:end]
        pos = end
        record = pb.NativeInputLifecycleRecordV3()
        try:
            record.ParseFromString(raw)
        except DecodeError as error:
            raise ObservationV3Error("PROTOBUF_INVALID") from error
        v2._canonical(record, raw)
        records.append(record)
    require(len(records) == FRAME_COUNT, "FOUR_RECORDS_REQUIRED")
    return records


def _expected(arm, bindings, session, arm_id):
    require(type(arm) is pb.LifecycleArmV3, "EXPECTED_LIFECYCLE_ARM_TYPE")
    v2._canonical(arm)
    # Envelope overhead must also fit the actual setup transport packet.
    control = pb.LifecycleControlEnvelopeV3(lifecycle_arm=arm)
    require(control.ByteSize() <= MAX_CONTROL_BYTES, "LIFECYCLE_ARM_BYTES_LIMIT")
    old, peer = v2._expected(arm.common, bindings, session, arm_id)
    require(arm.cause in CAUSES, "LIFECYCLE_CAUSE_INVALID")
    require(v2._window(arm.peer_window, arm.common.expected_application), "PEER_WINDOW_IDENTITY_INVALID")
    require(arm.peer_window.owner.client_id not in (
        arm.common.expected_recipient.client_id, arm.common.expected_seat_owner.client_id),
        "PEER_WINDOW_OWNER_ALIAS")
    original = arm.common.expected_window
    owner, old_owner = arm.peer_window.owner, original.owner
    require(owner.native_object_epoch not in (
        arm.common.expected_recipient.native_object_epoch,
        arm.common.expected_seat_owner.native_object_epoch), "PEER_WINDOW_OWNER_EPOCH_ALIAS")
    if owner.client_id == old_owner.client_id:
        require(same(owner, old_owner), "PEER_WINDOW_OWNER_IDENTITY_ALIAS")
    else:
        require(owner.native_object_epoch != old_owner.native_object_epoch,
                "PEER_WINDOW_OWNER_EPOCH_ALIAS")
    if arm.peer_window.xid == original.xid:
        require(same(arm.peer_window, original), "PEER_WINDOW_NUMERICAL_ALIAS")
    else:
        require(arm.peer_window.native_object_epoch != original.native_object_epoch,
                "PEER_WINDOW_EPOCH_ALIAS")
    if arm.cause == pb.TARGET_DESTRUCTION:
        require(arm.peer_window.xid != original.xid, "DESTROY_SURVIVOR_REQUIRED")
    return old, peer


def _snapshot(snapshot, arm, expected, phase, controller):
    require(snapshot.phase == phase, "TRANSITION_PHASE_ORDER")
    require(snapshot.complete and snapshot.failure_reason == 0, "SNAPSHOT_INCOMPLETE")
    require(same(snapshot.application, arm.expected_application)
            and snapshot.controller_native_seat_id == controller, "APPLICATION_CONTROLLER_PHASE_JOIN")
    return v2._snapshot_devices(snapshot, expected, arm)


def _pending_clear(snapshot):
    require(not snapshot.global_pending and not snapshot.replay_pending
            and not snapshot.replay_in_progress, "GLOBAL_PENDING_REPLAY_REMAINS")


def _retirement(retirement, arm, cancelled_ns):
    common = arm.common
    require(retirement.complete, "RETIREMENT_INCOMPLETE")
    require(retirement.cause == arm.cause and same(retirement.old_window, common.expected_window)
            and retirement.old_native_seat_id == common.old_native_seat_id, "RETIREMENT_IDENTITY_JOIN")
    require(cancelled_ns <= retirement.monotonic_ns <= common.deadline_monotonic_ns,
            "RETIREMENT_TIME_ORDER")
    if arm.cause == pb.TARGET_DESTRUCTION:
        require(retirement.resource_free_monotonic_ns > 0
                and retirement.destroy_hook_return_monotonic_ns == retirement.monotonic_ns,
                "DESTRUCTION_BOUNDARY_MISSING")
        require(retirement.destroy_hook_observed and retirement.destroy_hook_result,
                "DESTRUCTION_HOOK_FAILED")
        require(cancelled_ns <= retirement.spatial_generation_invalidated_monotonic_ns
                <= retirement.destroy_hook_return_monotonic_ns
                and retirement.invalidated_spatial_generation > max(
                    common.expected_window.spatial_generation, arm.peer_window.spatial_generation),
                "DESTRUCTION_GENERATION_BOUNDARY")
        require(retirement.old_window_resource_absent, "DESTROYED_WINDOW_STILL_PRESENT")
        require(not retirement.removals and not retirement.old_native_seat_absent
                and not retirement.all_old_device_ids_absent, "DESTRUCTION_DEVICE_RETIREMENT_FORBIDDEN")
    else:
        require(retirement.resource_free_monotonic_ns == 0
                and retirement.destroy_hook_return_monotonic_ns == 0
                and not retirement.old_window_resource_absent
                and not retirement.spatial_generation_invalidated_monotonic_ns
                and not retirement.invalidated_spatial_generation
                and not retirement.destroy_hook_observed and not retirement.destroy_hook_result,
                "UNEXPECTED_WINDOW_RETIREMENT")
        require(retirement.old_native_seat_absent and retirement.all_old_device_ids_absent,
                "OLD_DEVICES_NOT_RETIRED")
        expected = {d.device_id: d for d in common.old_actor_devices}
        require(len(retirement.removals) == 4
                and {r.identity.device_id for r in retirement.removals} == set(expected),
                "DEVICE_REMOVAL_SET_JOIN")
        previous = cancelled_ns
        for removal in retirement.removals:
            require(same(removal.identity, expected[removal.identity.device_id]), "DEVICE_REMOVAL_PIN_JOIN")
            require(removal.removal_observed and removal.remove_result == 0, "DEVICE_REMOVAL_FAILED")
            require(previous <= removal.monotonic_ns <= retirement.monotonic_ns, "DEVICE_REMOVAL_TIME_ORDER")
            previous = removal.monotonic_ns


def _retired_at_peer(check, arm, timestamp):
    require(check.complete and check.monotonic_ns == timestamp, "RETIREMENT_CHECK_INCOMPLETE")
    require(check.old_native_seat_id == arm.old_native_seat_id
            and check.old_native_seat_absent and check.all_old_device_ids_absent,
            "RETIREMENT_CHECK_ABSENCE")
    expected = {d.device_id: d for d in arm.old_actor_devices}
    require(len(check.retired_devices) == 4
            and {d.device_id for d in check.retired_devices} == set(expected), "RETIREMENT_CHECK_DEVICE_SET")
    require(all(same(d, expected[d.device_id]) for d in check.retired_devices), "RETIREMENT_CHECK_DEVICE_PIN")


def validate_records(records, *, expected_session_id, expected_arm_id,
                     expected_native_session_epoch, expected_arm, expected_bindings,
                     limits=Limits()):
    """Validate finite assertions against trusted inputs; the caller owns provenance."""
    _limits(limits)
    require(type(expected_session_id) is bytes and len(expected_session_id) == 16
            and type(expected_arm_id) is bytes and len(expected_arm_id) == 16
            and type(expected_native_session_epoch) is int and expected_native_session_epoch > 0,
            "EXPECTED_SESSION_INVALID")
    old, peer = _expected(expected_arm, expected_bindings, expected_session_id, expected_arm_id)
    arm, common = expected_arm, expected_arm.common
    require(type(records) is list and len(records) == FRAME_COUNT, "FOUR_RECORDS_REQUIRED")
    total = 0
    for seq, (record, payload) in enumerate(zip(records, PAYLOADS), 1):
        require(type(record) is pb.NativeInputLifecycleRecordV3, "RECORD_TYPE")
        raw = v2._canonical(record)
        require(0 < len(raw) <= limits.max_frame_bytes, "FRAME_BYTES_LIMIT")
        total += len(v2._prefix(len(raw))) + len(raw)
        require(record.protocol_version == 3, "PROTOCOL_VERSION")
        require(record.session_id == expected_session_id and record.arm_id == expected_arm_id
                and record.native_session_epoch == expected_native_session_epoch, "RECORD_SESSION_JOIN")
        require(record.stream_sequence == seq, "RECORD_SEQUENCE")
        require(record.WhichOneof("payload") == payload, "RECORD_PAYLOAD_ORDER")
    require(total <= limits.max_artifact_bytes, "ARTIFACT_BYTES_LIMIT")
    require(same(records[0].arm.accepted_command, arm), "ACCEPTED_ARM_JOIN")
    transition = records[1].native_transition
    cancellation = list(transition.cancellation)
    require(tuple(s.phase for s in cancellation) == v2.PHASES[:3], "TRANSITION_PHASE_ORDER")
    expected_old = {d.device_id: d for d in common.old_actor_devices}
    expected_peer = {d.device_id: d for d in common.peer_actor_devices}
    native = []
    previous = common.arm_monotonic_ns
    for index, snapshot in enumerate(cancellation):
        require(previous <= snapshot.monotonic_ns <= common.deadline_monotonic_ns, "TRANSITION_TIME_ORDER")
        previous = snapshot.monotonic_ns
        native.append(_snapshot(snapshot, common, expected_old, v2.PHASES[index],
                                common.old_native_seat_id if index < 2 else 0))
        v2._seat(snapshot.old_seat, common)
        require(not snapshot.HasField("peer_seat"), "UNEXPECTED_PEER_CONTEXT")
        if index < 2:
            v2._context_bound(snapshot.old_seat, common, common.old_native_seat_id)
        else:
            v2._context_clear(snapshot.old_seat)
        if index:
            _pending_clear(snapshot)
            v2._clear_native(native[-1].values())
    pre, released, cancelled = cancellation
    keys, buttons = set(common.expected_held_keys), set(common.expected_held_buttons)
    require(v2._bits(pre.old_seat.tracked_keys) == keys
            and {b for b in range(32) if pre.old_seat.tracked_buttons & (1 << b)} == buttons,
            "PRE_CANCEL_TRACKED_HELD_JOIN")
    for kind, held in ((v2_pb.DeviceIdentity.KEYBOARD, keys), (v2_pb.DeviceIdentity.POINTER, buttons)):
        actual = set()
        for device in native[0].values():
            if device.identity.kind == kind:
                require((v2._bits(device.down_bitmap) | v2._bits(device.postdown_bitmap)) <= held,
                        "PRE_CANCEL_NATIVE_HELD_JOIN")
                actual |= v2._bits(device.down_bitmap)
        require(actual == held, "PRE_CANCEL_NATIVE_HELD_JOIN")
    require(any(d.active_grab for d in native[0].values()
                if d.identity.kind == v2_pb.DeviceIdentity.POINTER), "PRE_CANCEL_CAPTURE_MISSING")
    require(native[0][old[v2_pb.DeviceIdentity.KEYBOARD, v2_pb.DeviceIdentity.MASTER].device_id].focus_kind == 4,
            "PRE_CANCEL_FOCUS_MISSING")
    require(not any(released.old_seat.tracked_keys) and not released.old_seat.tracked_buttons,
            "POST_RELEASE_TRACKED_REMAINS")
    _retirement(transition.retirement, arm, cancelled.monotonic_ns)
    if arm.cause == pb.TARGET_DESTRUCTION:
        require(transition.retirement.resource_free_monotonic_ns == pre.monotonic_ns,
                "RESOURCE_FREE_PRE_CANCEL_JOIN")
    bound = transition.peer_bind
    require(transition.retirement.monotonic_ns <= bound.monotonic_ns <= common.deadline_monotonic_ns,
            "PEER_BIND_BEFORE_RETIREMENT")
    peer_arm = v2_pb.ObserverArmV2()
    peer_arm.CopyFrom(common)
    peer_arm.expected_window.CopyFrom(arm.peer_window)
    expected_bound = expected_peer if arm.cause != pb.TARGET_DESTRUCTION else {**expected_old, **expected_peer}
    bound_native = _snapshot(bound, peer_arm, expected_bound, v2_pb.POST_PEER_BIND, common.peer_native_seat_id)
    _pending_clear(bound)
    v2._seat(bound.peer_seat, peer_arm, peer=True)
    require(same(bound.peer_seat.application, common.expected_application)
            and bound.peer_seat.application_controller_native_seat_id == common.peer_native_seat_id,
            "PEER_CONTROLLER_CONTEXT_JOIN")
    require(not bound.peer_seat.HasField("target") and not bound.peer_seat.HasField("focus")
            and not any(bound.peer_seat.tracked_keys) and not bound.peer_seat.tracked_buttons,
            "PEER_PRE_EVENT_CONTEXT")
    if arm.cause == pb.TARGET_DESTRUCTION:
        v2._seat(bound.old_seat, common)
        v2._context_clear(bound.old_seat)
        v2._clear_native(bound_native[d] for d in expected_old)
    else:
        require(not bound.HasField("old_seat"), "RETIRED_SEAT_SNAPSHOT_FORBIDDEN")
    v2._clear_native(bound_native[d] for d in expected_peer)
    press, state = records[2].observer_press.press, records[2].observer_press.state
    require(state.complete and state.monotonic_ns == press.callback_monotonic_ns,
            "FIRST_PEER_STATE_INCOMPLETE")
    require(bound.monotonic_ns <= state.monotonic_ns <= common.deadline_monotonic_ns,
            "OBSERVER_TIME_ORDER")
    if arm.cause == pb.TARGET_DESTRUCTION:
        require(state.WhichOneof("old_state") == "live_old" and state.old_window_resource_absent,
                "DESTROY_FIRST_PEER_LIVE_STATE_REQUIRED")
        current = state.live_old
        require(current.monotonic_ns == state.monotonic_ns, "FIRST_PEER_STATE_NOT_FRESH")
        current_native = _snapshot(current, common, expected_old, v2_pb.POST_PEER_BIND, common.peer_native_seat_id)
        v2._seat(current.old_seat, common)
        v2._context_clear(current.old_seat)
        require(not current.HasField("peer_seat"), "UNEXPECTED_PEER_CONTEXT")
        _pending_clear(current)
        v2._clear_native(current_native.values())
    else:
        require(state.WhichOneof("old_state") == "retired_old" and not state.old_window_resource_absent,
                "RETIRED_FIRST_PEER_STATE_REQUIRED")
        _retired_at_peer(state.retired_old, common, state.monotonic_ns)
    require(not press.overflow and not press.disconnected and not press.synthetic, "OBSERVER_INVALID")
    require(press.observer_ordinal == 1, "OBSERVER_ORDINAL")
    require(same(press.recipient, common.expected_recipient), "OBSERVER_RECIPIENT_JOIN")
    require(same(press.window, arm.peer_window), "OBSERVER_WINDOW_JOIN")
    require(same(press.device, peer[v2_pb.DeviceIdentity.POINTER, v2_pb.DeviceIdentity.MASTER])
            and same(press.source, peer[v2_pb.DeviceIdentity.POINTER, v2_pb.DeviceIdentity.SLAVE]),
            "OBSERVER_DEVICE_JOIN")
    require((press.xi2_event_type, press.detail) == (common.expected_xi2_event_type, common.expected_detail),
            "OBSERVER_EVENT_JOIN")
    _receipts(records[3].probe_receipts, arm, old, press, pre.monotonic_ns, limits)
    return press


def _receipts(batch, arm, old, press, earliest, limits):
    common = arm.common
    require(not batch.overflow and not batch.disconnected, "PROBE_INVALID")
    require(same(batch.recipient, common.expected_recipient) and same(batch.window, arm.peer_window),
            "PROBE_IDENTITY_JOIN")
    require(0 < len(batch.receipts) <= limits.max_receipts, "RECEIPT_COUNT_INVALID")
    keys, buttons = set(common.expected_held_keys), set(common.expected_held_buttons)
    wanted = [(5, b) for b in sorted(buttons)] + [(3, k) for k in sorted(keys)]
    releases, peer_event, previous = [], None, earliest
    for index, receipt in enumerate(batch.receipts):
        require(receipt.receipt_ordinal == common.prearm_receipt_watermark + index + 1, "RECEIPT_ORDINAL")
        require(previous <= receipt.monotonic_ns <= common.deadline_monotonic_ns, "RECEIPT_TIME_ORDER")
        previous = receipt.monotonic_ns
        require(not receipt.synthetic, "RECEIPT_SYNTHETIC")
        if receipt.event_type in (3, 5):
            require(peer_event is None, "OLD_RELEASE_AFTER_PEER")
            require(same(receipt.window, common.expected_window), "RELEASE_WINDOW_JOIN")
            kind = v2_pb.DeviceIdentity.KEYBOARD if receipt.event_type == 3 else v2_pb.DeviceIdentity.POINTER
            require(same(receipt.device, old[kind, v2_pb.DeviceIdentity.MASTER])
                    and same(receipt.source, old[kind, v2_pb.DeviceIdentity.SLAVE]), "RELEASE_DEVICE_JOIN")
            require(receipt.detail in (keys if kind == v2_pb.DeviceIdentity.KEYBOARD else buttons),
                    "RELEASE_DETAIL_JOIN")
            require((receipt.event_type, receipt.detail) not in releases, "OLD_RELEASE_DUPLICATE")
            releases.append((receipt.event_type, receipt.detail))
        else:
            require(peer_event is None, "PEER_EVENT_DUPLICATE")
            require(receipt.monotonic_ns >= press.callback_monotonic_ns, "PEER_RECEIPT_BEFORE_CALLBACK")
            require(v2._event_tuple(receipt) == (press.xi2_event_type, v2._bytes(press.device),
                    v2._bytes(press.source), v2._bytes(press.window), press.detail, press.event_time_ms),
                    "PEER_EVENT_TUPLE_JOIN")
            require(releases == wanted, "OLD_RELEASES_MISSING_OR_ORDER")
            peer_event = receipt
    require(peer_event is not None, "PEER_EVENT_MISSING")
    require(releases == wanted, "OLD_RELEASES_MISSING_OR_ORDER")


def decode_and_validate(artifact, **kwargs):
    return validate_records(decode_records(artifact, kwargs.get("limits", Limits())), **kwargs)
