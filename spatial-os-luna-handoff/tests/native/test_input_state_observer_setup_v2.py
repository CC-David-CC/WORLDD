"""Pure setup controls. Synthetic keycodes and pins are not native execution evidence."""
from dataclasses import replace, FrozenInstanceError
import unittest
from unittest import mock
import native_input_observer_v2_pb2 as pb
import native_input_observer_control_v2_pb2 as cp
import input_state_observer_setup_v2 as v


class Clock:
    """Repeat the final sample. A finite fixture never raises accidental StopIteration."""
    def __init__(self, *values):
        self.values = values or (2000,)
        self.calls = 0

    def __call__(self):
        value = self.values[min(self.calls, len(self.values)-1)]
        self.calls += 1
        return value


def devices(base):
    values = [pb.DeviceIdentity(device_id=base+i, native_object_epoch=base+100+i,
        kind=pb.DeviceIdentity.POINTER if i < 2 else pb.DeviceIdentity.KEYBOARD,
        role=pb.DeviceIdentity.MASTER if i % 2 == 0 else pb.DeviceIdentity.SLAVE) for i in range(4)]
    for device, attached in zip(values, (base+2, base, base, base+2)):
        device.paired_or_attached = attached
    return values


def fixture(*, label_size=None, watermark=1, probe_role=pb.ClientIdentity.SUPERVISOR):
    sid, aid = b"s"*16, b"a"*16
    app = pb.ApplicationIdentity(workspace_id=b"w"*(label_size or 9), application_id=b"a"*(label_size or 3))
    old = pb.ActorPrincipalRef(issuer_id=b"i"*32, actor_id=b"o"*32)
    peer = pb.ActorPrincipalRef(issuer_id=b"i"*32, actor_id=b"p"*32)
    owner = pb.ClientIdentity(client_id=30, native_object_epoch=31, role=pb.ClientIdentity.APPLICATION, application=app)
    probe = pb.ClientIdentity(client_id=20, native_object_epoch=21, role=probe_role)
    seat = pb.ClientIdentity(client_id=10, native_object_epoch=11, role=pb.ClientIdentity.COMPOSITOR)
    # Presentation and target spatial generations deliberately differ.
    target = pb.WindowIdentity(xid=400, native_object_epoch=401, application=app, spatial_generation=75, owner=owner)
    request = cp.ObserverSetupRequestV2(session_id=sid, arm_id=aid, old_native_seat_id=60,
        peer_native_seat_id=61, target_xid=400, probe_marker_xid=500, expected_application=app,
        presentation_generation=50, target_spatial_generation=75, expected_probe_role=probe_role,
        deadline_monotonic_ns=10_000_001_000)
    reply = cp.ObserverSetupReplyV2(session_id=sid, arm_id=aid, native_session_epoch=70,
        old_native_seat_id=60, peer_native_seat_id=61, old_actor_devices=devices(100), peer_actor_devices=devices(110),
        compositor_seat_owner=seat, app_target=target, app_target_owner=owner, accepted_request=request)
    reply.probe_marker.CopyFrom(cp.ProbeMarkerPinV2(xid=500, window_epoch=501, privileged_owner=probe))
    old_binding = pb.ObserverActorSeatBindingV2(actor=old, world_actor_id=1, application=app,
        presentation_generation=50, native_seat_id=60, session_id=sid, arm_id=aid)
    peer_binding = pb.ObserverActorSeatBindingV2(actor=peer, world_actor_id=2, application=app,
        presentation_generation=50, native_seat_id=61, session_id=sid, arm_id=aid)
    trusted = v.TrustedSetup(session_id=sid, arm_id=aid, old_binding=old_binding, peer_binding=peer_binding,
        expected_application=app, expected_target_xid=400, expected_target_spatial_generation=75,
        expected_presentation_generation=50, expected_probe_marker_xid=500, expected_probe_role=probe_role,
        start_monotonic_ns=1000, deadline_monotonic_ns=10_000_001_000)
    arm = pb.ObserverArmV2(session_id=sid, arm_id=aid, arm_monotonic_ns=1500,
        deadline_monotonic_ns=request.deadline_monotonic_ns, prearm_receipt_watermark=watermark,
        old_actor=old, peer_actor=peer, old_world_actor_id=1, peer_world_actor_id=2,
        old_native_seat_id=60, peer_native_seat_id=61, expected_application=app,
        presentation_generation=50, expected_window=target, expected_xi2_event_type=4, expected_detail=1,
        expected_recipient=probe, expected_seat_owner=seat, expected_window_owner=owner, expected_grab_owner=probe,
        expected_held_keys=[29, 30], expected_held_buttons=[1], expected_modifier_keycode=29,
        old_actor_devices=reply.old_actor_devices, peer_actor_devices=reply.peer_actor_devices)
    held = v.HeldInputContext((29, 30), (1,), 29, watermark, 1)
    return request, reply, trusted, arm, held


def setup(values, clock=None):
    request, reply, trusted, _, _ = values
    return v.validate_setup(request, reply, trusted, clock_ns=clock or Clock())


def arm_check(values, pins=None, *, clock=None, held=None, bindings=None):
    _, _, trusted, arm, original_held = values
    pins = pins or setup(values)
    return v.validate_arm(arm, pins, trusted, held or original_held,
        bindings if bindings is not None else (trusted.old_binding, trusted.peer_binding), clock_ns=clock or Clock())


def full(values, clock=None):
    request, reply, trusted, arm, held = values
    return v.validate_frames(v.encode_control(request, arm), v.frame(reply), trusted, arm, held, clock_ns=clock or Clock())


class Contract(unittest.TestCase):
    def rejects(self, code, call):
        with self.assertRaises(v.SetupError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)

    def test_bootstrap_discovers_native_epochs_and_marker_without_app_label(self):
        for role in (pb.ClientIdentity.SUPERVISOR, pb.ClientIdentity.COMPOSITOR):
            values = fixture(probe_role=role)
            pins = setup(values)
            self.assertEqual(pins.native_session_epoch, 70)
            self.assertEqual(len(pins.old_devices), 4)
            self.assertEqual(pins.probe_role, role)
            self.assertNotIn("application", values[1].probe_marker.DESCRIPTOR.fields_by_name)
            self.assertFalse(values[1].probe_marker.privileged_owner.HasField("application"))
            self.assertTrue(arm_check(values, pins))
            self.assertEqual(full(values), pins)

    def test_window_bytes_use_window_identity_in_standalone_arm(self):
        values = fixture()
        pins = setup(values)
        window = v._parse(pb.WindowIdentity, pins.app_target)
        self.assertEqual(window.owner, values[1].app_target_owner)
        self.assertTrue(arm_check(values, pins))

    def test_native_epoch_reuse_pin_is_optional_but_enforced(self):
        values = list(fixture())
        values[1].native_session_epoch = 999
        self.assertEqual(setup(values).native_session_epoch, 999)
        values[2] = replace(values[2], expected_native_session_epoch=70)
        self.rejects("STALE_NATIVE_SESSION", lambda: setup(values))
        values[1].native_session_epoch = 70
        self.assertTrue(arm_check(values))
        for bad in (True, 0, -1, v.UINT64_MAX+1):
            values[2] = replace(values[2], expected_native_session_epoch=bad)
            self.rejects("TRUSTED_NATIVE_EPOCH_INVALID", lambda: setup(values))

    def test_independent_binding_mismatch_is_rejected(self):
        # A binding's actor ID is authoritative until a separate arm contradicts it.
        values = list(fixture())
        values[2].old_binding.world_actor_id = 99
        pins = setup(values)
        self.rejects("ARM_ACTOR_JOIN", lambda: arm_check(values, pins))
        values[3].old_world_actor_id = 99
        self.assertTrue(arm_check(values, pins))
        values = list(fixture())
        values[2].old_binding.native_seat_id = 99
        self.rejects("WORLD_BINDING_JOIN", lambda: setup(values))

    def test_bad_device_overlap_preserves_valid_attachment_graph(self):
        values = fixture()
        for source, target in zip(values[1].old_actor_devices, values[1].peer_actor_devices):
            target.CopyFrom(source)
        self.rejects("CROSS_SEAT_DEVICE_OVERLAP", lambda: setup(values))

    def test_deadline_cap_is_after_start(self):
        values = list(fixture())
        self.assertTrue(setup(values))  # Exactly ten seconds after start is valid.
        values[2] = replace(values[2], deadline_monotonic_ns=values[2].start_monotonic_ns+v.MAX_DEADLINE_NS+1)
        self.rejects("TRUSTED_CONTEXT_INVALID", lambda: setup(values))

    def test_expired_before_parse_and_during_validation(self):
        values = fixture()
        deadline = values[2].deadline_monotonic_ns
        self.rejects("DEADLINE_EXPIRED", lambda: setup(values, Clock(deadline)))
        self.rejects("DEADLINE_EXPIRED", lambda: setup(values, Clock(1000, deadline)))
        self.rejects("DEADLINE_EXPIRED", lambda: full(values, Clock(1200, deadline)))
        self.rejects("DEADLINE_EXPIRED", lambda: full(values, Clock(1200, 1300, deadline)))
        self.rejects("DEADLINE_EXPIRED", lambda: full(values, Clock(1200, 1300, 1600, deadline)))
        self.rejects("DEADLINE_EXPIRED", lambda: arm_check(values, clock=Clock(2000, deadline)))
        self.rejects("DEADLINE_EXPIRED", lambda: v.validate_frames(b"malformed", b"bad", values[2],
            values[3], values[4], clock_ns=Clock(deadline)))

    def test_monotonic_clock_domains_and_backward_samples(self):
        values = fixture()
        for bad in (True, -1, 1.0, v.UINT64_MAX+1, 999):
            self.rejects("MONOTONIC_CLOCK_INVALID", lambda: setup(values, Clock(bad)))
        self.rejects("MONOTONIC_CLOCK_INVALID", lambda: setup(values, Clock(1300, 1200)))
        self.rejects("MONOTONIC_CLOCK_INVALID", lambda: full(values, Clock(1200, 1300, 1250)))
        self.rejects("MONOTONIC_CLOCK_INVALID", lambda: arm_check(values, clock=Clock(2000, 1999)))
        self.rejects("MONOTONIC_CLOCK_INVALID", lambda: v.validate_setup(*values[:3], clock_ns=42))
        empty = iter(())
        self.rejects("MONOTONIC_CLOCK_INVALID", lambda: v.validate_setup(*values[:3], clock_ns=lambda: next(empty)))
        clock = Clock(2000)
        self.assertTrue(full(values, clock))
        self.assertGreaterEqual(clock.calls, 4)

    def test_arm_required_and_selectors_pins_bindings_deadline(self):
        cases = [("session_id", b"z"*16, "ARM_SESSION_JOIN"), ("arm_id", b"z"*16, "ARM_SESSION_JOIN"),
            ("deadline_monotonic_ns", 2001, "ARM_DEADLINE_JOIN"), ("old_native_seat_id", 99, "ARM_SEAT_JOIN"),
            ("peer_native_seat_id", 99, "ARM_SEAT_JOIN"), ("old_world_actor_id", 99, "ARM_ACTOR_JOIN"),
            ("presentation_generation", 75, "ARM_TARGET_JOIN"), ("arm_monotonic_ns", 999, "ARM_TIME_INVALID"),
            ("arm_monotonic_ns", 2001, "ARM_TIME_INVALID")]
        for field, value, reason in cases:
            values = fixture()
            pins = setup(values)
            setattr(values[3], field, value)
            self.rejects(reason, lambda: arm_check(values, pins))
        values = fixture()
        pins = setup(values)
        values[3].Clear()
        self.rejects("ARM_SESSION_JOIN", lambda: arm_check(values, pins))
        values = fixture()
        pins = setup(values)
        altered = pb.ObserverActorSeatBindingV2()
        altered.CopyFrom(values[2].peer_binding)
        altered.native_seat_id += 1
        self.rejects("ARM_BINDING_JOIN", lambda: arm_check(values, pins,
            bindings=(values[2].old_binding, altered)))

    def test_arm_all_native_pin_and_identity_joins(self):
        mutations = [ ("ARM_TARGET_JOIN", lambda arm: setattr(arm.expected_window, "native_object_epoch", 999)),
            ("ARM_TARGET_JOIN", lambda arm: setattr(arm.expected_window, "spatial_generation", 50)),
            ("ARM_OWNER_JOIN", lambda arm: setattr(arm.expected_seat_owner, "native_object_epoch", 999)),
            ("ARM_OWNER_JOIN", lambda arm: setattr(arm.expected_window_owner, "client_id", 999)),
            ("ARM_PROBE_JOIN", lambda arm: setattr(arm.expected_recipient, "native_object_epoch", 999)),
            ("ARM_DEVICE_PINS_JOIN", lambda arm: setattr(arm.old_actor_devices[0], "native_object_epoch", 999)),
            ("ARM_DEVICE_PINS_JOIN", lambda arm: setattr(arm.peer_actor_devices[0], "device_id", 999)),
            ("ARM_ACTOR_JOIN", lambda arm: setattr(arm.peer_actor, "actor_id", b"z"*32)),
            ("ARM_GRAB_JOIN", lambda arm: arm.expected_grab_owner.CopyFrom(arm.expected_window_owner)) ]
        for reason, mutate in mutations:
            values = fixture()
            pins = setup(values)
            mutate(values[3])
            self.rejects(reason, lambda: arm_check(values, pins))

    def test_zero_and_positive_watermarks_are_independently_joined(self):
        for watermark in (0, 1, v.UINT64_MAX):
            values = fixture(watermark=watermark)
            self.assertTrue(arm_check(values))
            self.assertTrue(full(values))
        values = fixture(watermark=0)
        pins = setup(values)
        values[3].prearm_receipt_watermark = 1
        self.rejects("ARM_WATERMARK_JOIN", lambda: arm_check(values, pins))
        for invalid in (True, -1, v.UINT64_MAX+1):
            held = replace(values[4], prearm_receipt_watermark=invalid)
            self.rejects("HELD_INPUT_CONTEXT_INVALID", lambda: arm_check(fixture(), held=held))

    def test_independent_held_and_peer_event_context(self):
        values = fixture()
        for held in (replace(values[4], keys=(29, 31)), replace(values[4], buttons=(2,)),
                     replace(values[4], modifier_keycode=30)):
            self.rejects("ARM_HELD_INPUT_JOIN", lambda: arm_check(values, held=held))
        self.rejects("ARM_EVENT_JOIN", lambda: arm_check(values, held=replace(values[4], peer_button=2)))
        for held in (replace(values[4], keys=(True, 30)), replace(values[4], keys=(30, 29)),
                     replace(values[4], buttons=()), replace(values[4], peer_button=True)):
            self.rejects("HELD_INPUT_CONTEXT_INVALID", lambda: arm_check(values, held=held))
        values[3].expected_xi2_event_type = 2
        self.rejects("ARM_EVENT_JOIN", lambda: arm_check(values))

    def test_role_mapping_and_app_label_boundaries(self):
        for role in (pb.ClientIdentity.BROKER, 0, 99):
            values = fixture()
            values[1].probe_marker.privileged_owner.role = role
            self.rejects("MARKER_PIN_JOIN", lambda: setup(values))
        values = fixture(label_size=256)
        self.assertTrue(setup(values))
        self.assertTrue(arm_check(values))
        self.assertTrue(full(values))
        self.assertEqual(values[1].app_target.owner.application, values[0].expected_application)
        self.rejects("TRUSTED_APP_INVALID", lambda: setup(fixture(label_size=257)))

    def test_trusted_selector_numeric_domains(self):
        for name in ("expected_target_xid", "expected_target_spatial_generation", "expected_presentation_generation",
                     "expected_probe_marker_xid", "expected_probe_role"):
            values = list(fixture())
            values[2] = replace(values[2], **{name: True})
            self.rejects("TRUSTED_SELECTOR_INVALID", lambda: setup(values))
        for name in ("start_monotonic_ns", "deadline_monotonic_ns"):
            values = list(fixture())
            values[2] = replace(values[2], **{name: True})
            self.rejects("TRUSTED_CONTEXT_INVALID", lambda: setup(values))
        values = list(fixture())
        values[2] = replace(values[2], expected_target_xid=v.UINT32_MAX+1)
        self.rejects("TRUSTED_SELECTOR_INVALID", lambda: setup(values))

    def test_session_and_arm_equal_bytes_are_distinct_domains(self):
        values = list(fixture())
        sid = values[2].session_id
        values[2] = replace(values[2], arm_id=sid)
        for message in (values[0], values[1], values[1].accepted_request, values[2].old_binding,
                        values[2].peer_binding, values[3]):
            message.arm_id = sid
        self.assertTrue(full(values))

    def test_request_reply_and_selector_echo_joins(self):
        for field, value, reason in (("session_id", b"z"*16, "SESSION_ARM_JOIN"),
            ("old_native_seat_id", 99, "WORLD_BINDING_JOIN"), ("peer_native_seat_id", 60, "SEAT_SELECTORS_INVALID"),
            ("target_xid", 999, "TRUSTED_SELECTOR_JOIN"), ("target_spatial_generation", 50, "TRUSTED_SELECTOR_JOIN"),
            ("presentation_generation", 75, "TRUSTED_SELECTOR_JOIN"), ("expected_probe_role", 3, "TRUSTED_SELECTOR_JOIN"),
            ("probe_marker_xid", 999, "TRUSTED_SELECTOR_JOIN"), ("deadline_monotonic_ns", 9999, "DEADLINE_JOIN")):
            values = fixture()
            setattr(values[0], field, value)
            self.rejects(reason, lambda: setup(values))
        values = fixture()
        values[1].accepted_request.deadline_monotonic_ns -= 1
        self.rejects("SETUP_REQUEST_ECHO_JOIN", lambda: setup(values))
        for field in ("native_session_epoch", "old_native_seat_id", "peer_native_seat_id"):
            values = fixture()
            setattr(values[1], field, 0)
            self.rejects("REPLY_SESSION_OR_SEAT_JOIN", lambda: setup(values))

    def test_reply_owners_windows_and_marker(self):
        cases = [("APP_TARGET_PIN_INVALID", lambda r: setattr(r.app_target, "spatial_generation", 50)),
            ("APP_TARGET_PIN_INVALID", lambda r: setattr(r.app_target.owner, "native_object_epoch", 999)),
            ("APP_TARGET_PIN_INVALID", lambda r: setattr(r.app_target, "native_object_epoch", 0)),
            ("MARKER_PIN_JOIN", lambda r: setattr(r.probe_marker, "window_epoch", 0)),
            ("MARKER_PIN_JOIN", lambda r: setattr(r.probe_marker, "xid", 999)),
            ("CLIENT_PIN_INVALID", lambda r: setattr(r.compositor_seat_owner, "role", pb.ClientIdentity.SUPERVISOR)),
            ("CLIENT_PIN_INVALID", lambda r: setattr(r.app_target_owner, "role", pb.ClientIdentity.COMPOSITOR)),
            ("NATIVE_OWNER_SEPARATION", lambda r: setattr(r.probe_marker.privileged_owner, "client_id", 10)),
            ("NATIVE_OWNER_SEPARATION", lambda r: setattr(r.probe_marker.privileged_owner, "native_object_epoch", 11))]
        for reason, mutate in cases:
            values = fixture()
            mutate(values[1])
            self.rejects(reason, lambda: setup(values))
        for field in ("compositor_seat_owner", "probe_marker"):
            values = fixture()
            client = values[1].compositor_seat_owner if field == "compositor_seat_owner" else values[1].probe_marker.privileged_owner
            client.application.SetInParent()
            self.rejects("PRIVILEGED_CLIENT_HAS_APPLICATION", lambda: setup(values))

    def test_devices_require_all_roles_complete_attachment_and_positive_epochs(self):
        cases = [("DEVICE_ROLE_SET", lambda r: setattr(r.old_actor_devices[0], "role", 0)),
            ("DEVICE_PIN_INVALID", lambda r: setattr(r.old_actor_devices[0], "native_object_epoch", 0)),
            ("DEVICE_PIN_INVALID", lambda r: setattr(r.old_actor_devices[0], "device_id", 0)),
            ("DEVICE_PIN_INVALID", lambda r: setattr(r.old_actor_devices[0], "device_id", r.old_actor_devices[1].device_id)),
            ("DEVICE_ATTACHMENT_INVALID", lambda r: setattr(r.old_actor_devices[1], "paired_or_attached", 999))]
        for reason, mutate in cases:
            values = fixture()
            mutate(values[1])
            self.rejects(reason, lambda: setup(values))

    def test_typed_unknown_field_rejected_and_returned_pins_are_immutable(self):
        for index in (0, 1):
            values = fixture()
            values[index].MergeFromString(b"\x98\x06\x01")
            self.rejects("PROTOBUF_UNKNOWN_FIELDS", lambda: setup(values))
        for name in ("expected_application", "old_binding", "peer_binding"):
            values = fixture()
            getattr(values[2], name).MergeFromString(b"\x98\x06\x01")
            self.rejects("PROTOBUF_UNKNOWN_FIELDS", lambda: setup(values))
        values = fixture()
        pins = setup(values)
        before = pins.app_target
        values[1].app_target.xid = 999
        values[1].compositor_seat_owner.client_id = 999
        self.assertEqual(pins.app_target, before)
        self.assertTrue(arm_check(values, pins))
        with self.assertRaises(FrozenInstanceError):
            pins.target_xid = 999
        values[2].old_binding.world_actor_id = 99
        self.rejects("ARM_BINDING_JOIN", lambda: arm_check(values, pins))

    def test_standalone_arm_snapshots_inputs_before_final_clock_callback(self):
        values = fixture()
        pins = setup(values)
        calls = 0
        def clock():
            nonlocal calls
            calls += 1
            if calls == 2:
                values[3].expected_window.xid = 999
                values[2].old_binding.world_actor_id = 99
                values[2].expected_application.workspace_id = b"changed"
            return 2000
        validated_arm = arm_check(values, pins, clock=clock)
        self.assertIsInstance(validated_arm, bytes)
        self.assertEqual(v._parse(pb.ObserverArmV2, validated_arm).expected_window.xid, 400)
        self.assertEqual(values[3].expected_window.xid, 999)
        self.assertEqual(v._parse(pb.WindowIdentity, pins.app_target).xid, 400)
        self.assertEqual(v._parse(pb.ObserverActorSeatBindingV2, pins.bindings[0]).world_actor_id, 1)

    def test_arm_typed_unknowns_and_stale_native_context(self):
        values = fixture()
        pins = setup(values)
        values[3].MergeFromString(b"\x98\x06\x01")
        self.rejects("PROTOBUF_UNKNOWN_FIELDS", lambda: arm_check(values, pins))
        values = list(fixture())
        pins = setup(values)
        values[2] = replace(values[2], expected_native_session_epoch=71)
        self.rejects("STALE_NATIVE_SESSION", lambda: arm_check(values, pins))
        values = fixture()
        expected = pb.ObserverArmV2()
        expected.CopyFrom(values[3])
        expected.MergeFromString(b"\x98\x06\x01")
        self.rejects("PROTOBUF_UNKNOWN_FIELDS", lambda: v.validate_frames(
            v.encode_control(values[0], values[3]), v.frame(values[1]), values[2], expected, values[4], clock_ns=Clock()))

    def test_frozen_context_start_session_and_pin_consistency(self):
        values = list(fixture())
        pins = setup(values)
        values[2] = replace(values[2], start_monotonic_ns=1001)
        self.rejects("ARM_TRUSTED_CONTEXT_JOIN", lambda: arm_check(values, pins))
        values = fixture()
        pins = setup(values)
        self.rejects("VALIDATED_PINS_JOIN", lambda: arm_check(values, replace(pins, native_session_epoch=71)))
        self.rejects("VALIDATED_PINS_INVALID", lambda: arm_check(values, replace(pins, native_session_epoch=True)))
        self.rejects("VALIDATED_PINS_INVALID", lambda: arm_check(values, replace(pins, app_target="wrong domain")))
        self.rejects("VALIDATED_PINS_INVALID", lambda: arm_check(values, replace(pins, app_target=b"x"*4097)))
        self.rejects("VALIDATED_PINS_INVALID", lambda: arm_check(values,
            replace(pins, bindings=(b"x"*1025, pins.bindings[1]))))

    def test_control_kind_order_unknown_and_limits(self):
        values = fixture()
        request, reply, trusted, arm, held = values
        wire = v.encode_control(request, arm)
        decoded = v.decode_control(wire)
        self.assertIsInstance(decoded[0], cp.ObserverSetupRequestV2)
        self.assertIsInstance(decoded[1], pb.ObserverArmV2)
        self.assertTrue(full(values))
        self.rejects("CONTROL_MESSAGE_ORDER", lambda: v.decode_control(v._envelope("arm", arm)+v._envelope("setup_request", request)))
        self.rejects("CONTROL_EXTRA_BYTES", lambda: v.decode_control(wire+b"\0"))
        self.rejects("CONTROL_SIZE_INVALID", lambda: v.decode_control(b"x"*(v.MAX_CONTROL+1)))
        self.rejects("REPLY_SIZE_INVALID", lambda: v.decode_reply(b"x"*(v.MAX_FRAME+1)))
        wrong = pb.ObserverArmV2()
        wrong.CopyFrom(arm)
        wrong.expected_detail = 2
        self.rejects("ARM_FRAME_JOIN", lambda: v.validate_frames(v.encode_control(request, wrong), v.frame(reply), trusted,
            arm, held, clock_ns=Clock()))
        empty = pb.ObserverArmV2()
        self.rejects("ARM_SESSION_JOIN", lambda: v.validate_frames(v.encode_control(request, empty), v.frame(reply), trusted,
            empty, held, clock_ns=Clock()))

    def test_canonical_raw_fields_and_frame_failures(self):
        request, reply, _, arm, _ = fixture()
        env = cp.ObserverControlEnvelopeV2(setup_request=request)
        raw = v._serialize(env)
        self.rejects("PROTOBUF_UNKNOWN_FIELDS", lambda: v._parse(cp.ObserverControlEnvelopeV2, raw+b"\x98\x06\x01"))
        # Same oneof repeated and duplicate scalar fields must not become canonical silently.
        self.rejects("PROTOBUF_NONCANONICAL", lambda: v._parse(cp.ObserverControlEnvelopeV2, raw+raw))
        reply_raw = v._serialize(reply)
        self.rejects("PROTOBUF_NONCANONICAL", lambda: v._parse(cp.ObserverSetupReplyV2, reply_raw+b"\x18\x46"))
        for data, reason in ((b"\x80", "FRAME_LENGTH_TRUNCATED"), (b"\x80\x80", "FRAME_LENGTH_OVERFLOW"),
                             (b"\x81\x00x", "FRAME_LENGTH_NONCANONICAL"),
                             (v._varint(4097), "PAYLOAD_SIZE_INVALID"),
                             (v._varint(100)+b"x", "PROTOBUF_TRUNCATED"),
                             (b"\x01\xff", "PROTOBUF_INVALID")):
            self.rejects(reason, lambda: v.decode_control(data))
        self.rejects("REPLY_FRAME_SHAPE", lambda: v.decode_reply(v.frame(reply)+b"x"))
        self.rejects("REPLY_FRAME_SHAPE", lambda: v.decode_reply(v.frame(reply)[:-1]))

    def test_typed_size_guards_precede_protobuf_serialization(self):
        reply = cp.ObserverSetupReplyV2(session_id=b"x"*4097)
        binding = pb.ObserverActorSeatBindingV2(session_id=b"x"*1025)
        request = cp.ObserverSetupRequestV2(session_id=b"x"*4097)
        with mock.patch.object(v, "_serialize", side_effect=AssertionError("unbounded serialization")):
            self.rejects("PAYLOAD_SIZE_INVALID", lambda: v.frame(reply))
            self.rejects("PAYLOAD_SIZE_INVALID", lambda: v._copy(reply, cp.ObserverSetupReplyV2))
            self.rejects("PAYLOAD_SIZE_INVALID", lambda: v._envelope("setup_request", request))
            self.rejects("BINDING_BYTES_LIMIT", lambda: v._copy(binding, pb.ObserverActorSeatBindingV2, cap=1024))

    def test_exact_frame_caps(self):
        def sized(message, path, size):
            # Framing-only boundary. The semantic validators separately reject fake session IDs.
            amount = size-8
            for _ in range(10):
                setattr(path(message), "session_id", b"x"*amount)
                actual = len(v._serialize(message))
                if actual == size:
                    return message
                amount += size-actual
            self.fail("could not construct canonical boundary message")
        setup_env = sized(cp.ObserverControlEnvelopeV2(setup_request=cp.ObserverSetupRequestV2()), lambda m: m.setup_request, 4096)
        arm_env = sized(cp.ObserverControlEnvelopeV2(arm=pb.ObserverArmV2()), lambda m: m.arm, 4096)
        reply = sized(cp.ObserverSetupReplyV2(), lambda m: m, 4096)
        wire = v.frame(setup_env)+v.frame(arm_env)
        self.assertEqual(len(wire), 8196)
        self.assertEqual(len(v.decode_control(wire)), 2)
        self.assertEqual(len(v.frame(reply)), 4098)
        self.assertEqual(v.decode_reply(v.frame(reply)), reply)
        setup_env.setup_request.session_id += b"x"
        self.rejects("PAYLOAD_SIZE_INVALID", lambda: v.frame(setup_env))

    def test_wrong_app_message_and_empty_app(self):
        values = fixture()
        values[0].expected_application.Clear()
        self.rejects("TRUSTED_SELECTOR_JOIN", lambda: setup(values))
        self.rejects("TRUSTED_APP_INVALID", lambda: setup((values[0], values[1],
            replace(values[2], expected_application=pb.ApplicationIdentity()), values[3], values[4])))
        self.rejects("MESSAGE_TYPE_INVALID", lambda: v.validate_setup(pb.ClientIdentity(), values[1], values[2], clock_ns=Clock()))

    def test_binding_byte_cap_is_independent_of_semantic_identity_validation(self):
        binding = pb.ObserverActorSeatBindingV2()
        binding.actor.actor_id = b"x"*1010
        for _ in range(10):
            size = len(v._serialize(binding))
            if size == 1024:
                break
            binding.actor.actor_id += b"x"*(1024-size) if size < 1024 else b""
            if size > 1024:
                binding.actor.actor_id = binding.actor.actor_id[:len(binding.actor.actor_id)-(size-1024)]
        self.assertEqual(len(v._serialize(binding)), 1024)
        self.assertEqual(v._copy(binding, pb.ObserverActorSeatBindingV2, cap=1024), binding)
        binding.actor.actor_id += b"x"
        self.rejects("BINDING_BYTES_LIMIT", lambda: v._copy(binding, pb.ObserverActorSeatBindingV2, cap=1024))


if __name__ == "__main__":
    unittest.main()
