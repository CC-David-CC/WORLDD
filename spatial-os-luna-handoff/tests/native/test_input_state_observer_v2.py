"""Unlocked v2 structural controls. UNRUN; fixtures are not native evidence."""
import unittest
import json
import subprocess
import sys
import re
from pathlib import Path
from types import SimpleNamespace
import native_input_observer_v2_pb2 as pb
import input_state_observer_v2 as v

SID = b"s" * 16
AID = b"a" * 16
NATIVE = 9
ZERO = bytes(32)


def bitmap(values):
    out = bytearray(32)
    for value in values:
        out[value // 8] |= 1 << (value % 8)
    return bytes(out)


def frame(seq, name, payload):
    record = pb.NativeInputObservationRecordV2(protocol_version=2, session_id=SID,
        arm_id=AID, stream_sequence=seq, native_session_epoch=NATIVE)
    getattr(record, name).CopyFrom(payload)
    return record


def devices(base):
    result = [pb.DeviceIdentity(device_id=base+i, native_object_epoch=base+100+i,
        kind=pb.DeviceIdentity.POINTER if i < 2 else pb.DeviceIdentity.KEYBOARD,
        role=pb.DeviceIdentity.MASTER if i % 2 == 0 else pb.DeviceIdentity.SLAVE) for i in range(4)]
    for index, attachment in enumerate((base+2, base, base, base+2)):
        result[index].paired_or_attached = attachment
    return result


def fixture(probe_role=pb.ClientIdentity.SUPERVISOR):
    old = pb.ActorPrincipalRef(issuer_id=b"i"*32, actor_id=b"o"*32)
    peer = pb.ActorPrincipalRef(issuer_id=b"i"*32, actor_id=b"p"*32)
    app = pb.ApplicationIdentity(workspace_id=b"w", application_id=b"app")
    window_owner = pb.ClientIdentity(client_id=9, native_object_epoch=10,
        application=app, role=pb.ClientIdentity.APPLICATION)
    window = pb.WindowIdentity(xid=500, native_object_epoch=501,
                               application=app, spatial_generation=502, owner=window_owner)
    recipient = pb.ClientIdentity(client_id=5, native_object_epoch=6, role=probe_role)
    owner = pb.ClientIdentity(client_id=7, native_object_epoch=8, role=pb.ClientIdentity.COMPOSITOR)
    old_devices, peer_devices = devices(100), devices(110)
    arm = pb.ObserverArmV2(session_id=SID, arm_id=AID, arm_monotonic_ns=100,
        deadline_monotonic_ns=1000, prearm_receipt_watermark=10,
        old_actor=old, peer_actor=peer, old_world_actor_id=1, peer_world_actor_id=2,
        old_native_seat_id=11, peer_native_seat_id=12, expected_application=app,
        presentation_generation=40, expected_window=window, expected_xi2_event_type=4,
        expected_detail=1, old_actor_devices=old_devices, peer_actor_devices=peer_devices,
        expected_recipient=recipient, expected_seat_owner=owner, expected_window_owner=window_owner, expected_grab_owner=recipient,
        expected_held_keys=[50, 62], expected_held_buttons=[1], expected_modifier_keycode=62)
    bindings = [pb.ObserverActorSeatBindingV2(actor=actor, world_actor_id=world,
        native_seat_id=seat, application=app, presentation_generation=40,
        session_id=SID, arm_id=AID) for actor, world, seat in ((old, 1, 11), (peer, 2, 12))]
    snapshots = []
    for index, phase in enumerate(v.PHASES):
        context = pb.SeatContext(actor=old, world_actor_id=1, native_seat_id=11,
            owner=owner, presentation_generation=40, tracked_keys=ZERO, complete=True)
        if index < 2:
            context.application.CopyFrom(app)
            context.application_controller_native_seat_id = 11
            context.target.CopyFrom(window)
            context.focus.CopyFrom(window)
        if index == 0:
            context.tracked_keys = bitmap([50, 62])
            context.tracked_buttons = 2
        snapshot = pb.TransitionSnapshot(phase=phase, monotonic_ns=110+index*10,
            old_seat=context, complete=True, application=app,
            controller_native_seat_id=11 if index < 2 else (0 if index == 2 else 12))
        if index == 3:
            snapshot.peer_seat.CopyFrom(pb.SeatContext(actor=peer, world_actor_id=2,
                native_seat_id=12, owner=owner, presentation_generation=40,
                application=app, application_controller_native_seat_id=12,
                tracked_keys=ZERO, complete=True))
        for identity in old_devices + (peer_devices if index == 3 else []):
            pointer = identity.kind == pb.DeviceIdentity.POINTER
            held = [1] if pointer else [50, 62]
            data = bitmap(held) if index == 0 else ZERO
            d = snapshot.devices.add(identity=identity, bitmap_valid=True, bitmap_bits=256,
                down_bitmap=data, postdown_bitmap=data, num_buttons=8 if pointer else 0,
                buttons_down=1 if pointer and index == 0 else 0,
                button_state=256 if pointer and index == 0 else 0,
                grab_complete=True, context_complete=True, focus_complete=True,
                has_focus_class=not pointer, focus_kind=4 if not pointer and index == 0 else (1 if not pointer else 0))
            if index == 0 and not pointer:
                d.native_focus.CopyFrom(window)
            if index == 0 and pointer:
                d.active_grab = True
                d.grab.CopyFrom(pb.GrabIdentity(resource=600, owner=recipient,
                                                window=window, device=identity))
        snapshots.append(snapshot)
    observed = pb.ObserverPress(observer_ordinal=1, callback_monotonic_ns=150,
        recipient=recipient, window=window, device=peer_devices[0], source=peer_devices[1],
        xi2_event_type=4, detail=1, event_time_ms=99)
    batch = pb.ProbeReceiptBatch(recipient=recipient, window=window)
    events = [(5, old_devices[0], old_devices[1], 1),
              (3, old_devices[2], old_devices[3], 50),
              (3, old_devices[2], old_devices[3], 62),
              (4, peer_devices[0], peer_devices[1], 1)]
    for index, (event_type, device, source, detail) in enumerate(events):
        batch.receipts.add(receipt_ordinal=11+index, monotonic_ns=120+index*15,
            event_type=event_type, device=device, source=source, window=window,
            detail=detail, event_time_ms=99 if index == 3 else 90+index)
    records = [frame(1, "arm", pb.Arm(accepted_command=arm)),
        frame(2, "native_transition", pb.NativeTransition(snapshots=snapshots)),
        frame(3, "observer_press", observed), frame(4, "probe_receipts", batch)]
    return SimpleNamespace(records=records, arm=arm, bindings=bindings,
        snaps=records[1].native_transition.snapshots, observed=records[2].observer_press,
        batch=records[3].probe_receipts)


def validate(sample, **overrides):
    kwargs = dict(expected_session_id=SID, expected_arm_id=AID,
        expected_native_session_epoch=NATIVE, expected_arm=sample.arm,
        expected_bindings=sample.bindings)
    kwargs.update(overrides)
    return v.validate_records(sample.records, **kwargs)


def artifact(records):
    return b"".join(v._prefix(len(raw)) + raw for raw in
                    (r.SerializeToString(deterministic=True) for r in records))


class V2ContractTests(unittest.TestCase):
    def reject(self, code, mutate):
        sample = fixture()
        mutate(sample)
        with self.assertRaises(v.ObservationV2Error) as caught:
            validate(sample)
        self.assertEqual(caught.exception.code, code)

    def test_positive_structural_control_and_canonical_roundtrip(self):
        sample = fixture()
        self.assertEqual(validate(sample), sample.observed)
        sample.records = v.decode_records(artifact(sample.records))
        self.assertEqual(validate(sample), sample.observed)

    def test_client_role_mapping_matches_production_schema(self):
        security = Path(__file__).resolve().parents[2] / "proto/spatial/security/v1/security.proto"
        role_block = security.read_text().split("enum Role {", 1)[1].split("}", 1)[0]
        actual = dict((name, int(value)) for name, value in
                      re.findall(r"([A-Z_]+)\s*=\s*(\d+)", role_block))
        expected = {"ROLE_UNSPECIFIED": 0, "SUPERVISOR": 1, "BROKER": 2,
                    "COMPOSITOR": 3, "APPLICATION": 4}
        self.assertEqual(actual, expected)
        self.assertEqual({name: pb.ClientIdentity.Role.Value(name) for name in expected}, expected)

    def test_privileged_probe_roles_have_independent_app_owner(self):
        for role in (pb.ClientIdentity.SUPERVISOR, pb.ClientIdentity.COMPOSITOR):
            with self.subTest(role=role):
                sample = fixture(role)
                self.assertEqual(validate(sample), sample.observed)
                self.assertFalse(sample.observed.recipient.HasField("application"))
                self.assertEqual(sample.arm.expected_window_owner.role, pb.ClientIdentity.APPLICATION)
                self.assertNotEqual(sample.observed.recipient.client_id, sample.arm.expected_window_owner.client_id)
                self.assertNotEqual(sample.observed.recipient.client_id, sample.arm.expected_seat_owner.client_id)
                self.assertEqual(sample.snaps[0].devices[0].grab.owner, sample.arm.expected_grab_owner)
                self.assertEqual(sample.arm.expected_grab_owner, sample.arm.expected_recipient)
                self.assertEqual(sample.snaps[0].devices[0].grab.window.owner, sample.arm.expected_window_owner)
                self.assertEqual(sample.observed.window.owner, sample.arm.expected_window_owner)
                self.assertEqual(sample.batch.recipient, sample.observed.recipient)
                for record in sample.records:
                    self.assertLessEqual(len(record.SerializeToString(deterministic=True)), 4096)
                self.assertLessEqual(len(artifact(sample.records)), 16392)
                sample.records = v.decode_records(artifact(sample.records))
                self.assertEqual(validate(sample), sample.observed)

    def test_missing_unknown_roles_and_client_pins_are_rejected(self):
        for name in ("expected_recipient", "expected_seat_owner", "expected_window_owner"):
            for role in (pb.ClientIdentity.ROLE_UNSPECIFIED, 99):
                self.reject("CLIENT_ROLE_INVALID", lambda s: setattr(getattr(s.arm, name), "role", role))
            for field in ("client_id", "native_object_epoch"):
                self.reject("CLIENT_IDENTITY_INVALID", lambda s: setattr(getattr(s.arm, name), field, 0))

    def test_role_labels_and_probe_role_are_not_interchangeable(self):
        for name in ("expected_recipient", "expected_seat_owner"):
            self.reject("NON_APPLICATION_CLIENT_LABEL_PRESENT", lambda s:
                getattr(s.arm, name).application.CopyFrom(s.arm.expected_application))
            self.reject("NON_APPLICATION_CLIENT_LABEL_PRESENT", lambda s:
                getattr(s.arm, name).application.SetInParent())
        self.reject("CLIENT_APPLICATION_INVALID", lambda s:
                    s.arm.expected_window_owner.ClearField("application"))
        self.reject("CLIENT_APPLICATION_INVALID", lambda s:
                    s.arm.expected_window_owner.application.ClearField("application_id"))
        def app_probe(sample):
            sample.arm.expected_recipient.role = pb.ClientIdentity.APPLICATION
            sample.arm.expected_recipient.application.CopyFrom(sample.arm.expected_application)
        self.reject("PROBE_ROLE_INVALID", app_probe)
        self.reject("PROBE_ROLE_INVALID", lambda s:
                    setattr(s.arm.expected_recipient, "role", pb.ClientIdentity.BROKER))

    def test_window_and_seat_owner_roles_and_application_joins(self):
        for role in (pb.ClientIdentity.SUPERVISOR, pb.ClientIdentity.COMPOSITOR, pb.ClientIdentity.BROKER):
            def wrong_window_owner(sample):
                sample.arm.expected_window_owner.role = role
                sample.arm.expected_window_owner.ClearField("application")
            self.reject("WINDOW_OWNER_ROLE_INVALID", wrong_window_owner)
        for role in (pb.ClientIdentity.SUPERVISOR, pb.ClientIdentity.BROKER):
            self.reject("SEAT_OWNER_ROLE_INVALID", lambda s:
                        setattr(s.arm.expected_seat_owner, "role", role))
        def app_seat_owner(sample):
            sample.arm.expected_seat_owner.role = pb.ClientIdentity.APPLICATION
            sample.arm.expected_seat_owner.application.CopyFrom(sample.arm.expected_application)
        self.reject("SEAT_OWNER_ROLE_INVALID", app_seat_owner)
        self.reject("WINDOW_OWNER_APPLICATION_JOIN", lambda s:
                    setattr(s.arm.expected_window_owner.application, "application_id", b"other-app"))
        for field in ("client_id", "native_object_epoch"):
            self.reject("TARGET_WINDOW_OWNER_JOIN", lambda s:
                        setattr(s.arm.expected_window.owner, field, 999))
        self.reject("TARGET_IDENTITY_INVALID", lambda s: s.arm.expected_window.ClearField("owner"))
        self.reject("TARGET_IDENTITY_INVALID", lambda s:
                    s.arm.expected_window.owner.CopyFrom(s.arm.expected_recipient))

    def test_distinct_native_clients_cannot_alias_indices_or_epochs(self):
        names = ("expected_recipient", "expected_seat_owner", "expected_window_owner")
        for left in range(3):
            for right in range(left+1, 3):
                for field, code in (("client_id", "NATIVE_CLIENT_INDEX_ALIAS"),
                                    ("native_object_epoch", "NATIVE_CLIENT_EPOCH_ALIAS")):
                    self.reject(code, lambda s: setattr(getattr(s.arm, names[left]), field,
                                                       getattr(getattr(s.arm, names[right]), field)))

    def test_grab_and_seat_owners_match_explicit_trusted_domains(self):
        for name in ("expected_window_owner", "expected_seat_owner"):
            self.reject("GRAB_IDENTITY_JOIN", lambda s:
                        s.snaps[0].devices[0].grab.owner.CopyFrom(getattr(s.arm, name)))
        for field in ("client_id", "native_object_epoch"):
            self.reject("GRAB_IDENTITY_JOIN", lambda s:
                        setattr(s.snaps[0].devices[0].grab.owner, field, 999))
        self.reject("GRAB_IDENTITY_JOIN", lambda s:
                    s.snaps[0].devices[0].grab.owner.ClearField("role"))
        self.reject("GRAB_IDENTITY_JOIN", lambda s:
                    setattr(s.snaps[0].devices[0].grab.owner.application, "application_id", b"other-app"))
        for name in ("expected_recipient", "expected_window_owner"):
            self.reject("SEAT_CONTEXT_JOIN", lambda s:
                        s.snaps[0].old_seat.owner.CopyFrom(getattr(s.arm, name)))
        self.reject("SEAT_CONTEXT_JOIN", lambda s:
                    s.snaps[3].peer_seat.owner.CopyFrom(s.arm.expected_window_owner))

    def test_explicit_expected_grab_owner_is_required_and_pinned(self):
        self.reject("EXPECTED_GRAB_OWNER_REQUIRED", lambda s: s.arm.ClearField("expected_grab_owner"))
        for field in ("client_id", "native_object_epoch"):
            self.reject("GRAB_OWNER_IDENTITY_INVALID", lambda s:
                        setattr(s.arm.expected_grab_owner, field, 0))
            self.reject("GRAB_OWNER_CONTEXT_JOIN", lambda s:
                        setattr(s.arm.expected_grab_owner, field, 999))
        self.reject("CLIENT_ROLE_INVALID", lambda s: s.arm.expected_grab_owner.ClearField("role"))
        self.reject("NON_APPLICATION_CLIENT_LABEL_PRESENT", lambda s:
                    s.arm.expected_grab_owner.application.CopyFrom(s.arm.expected_application))
        self.reject("GRAB_OWNER_CONTEXT_JOIN", lambda s:
                    s.arm.expected_grab_owner.CopyFrom(s.arm.expected_seat_owner))
        self.reject("GRAB_OWNER_CONTEXT_JOIN", lambda s:
                    setattr(s.arm.expected_grab_owner, "role", pb.ClientIdentity.BROKER))
        def arbitrary_app_grab_owner(sample):
            sample.arm.expected_grab_owner.CopyFrom(sample.arm.expected_window_owner)
            sample.arm.expected_grab_owner.client_id = 999
        self.reject("GRAB_OWNER_CONTEXT_JOIN", arbitrary_app_grab_owner)
        def malformed_app_grab_owner(sample):
            sample.arm.expected_grab_owner.CopyFrom(sample.arm.expected_window_owner)
            sample.arm.expected_grab_owner.ClearField("application")
        self.reject("CLIENT_APPLICATION_INVALID", malformed_app_grab_owner)

    def test_explicit_app_grab_owner_is_a_separate_trusted_structural_choice(self):
        # Structural alternative only. This is not proof of a native APP-owned-grab topology.
        sample = fixture()
        sample.arm.expected_grab_owner.CopyFrom(sample.arm.expected_window_owner)
        sample.records[0].arm.accepted_command.CopyFrom(sample.arm)
        for device in sample.snaps[0].devices:
            if device.active_grab:
                device.grab.owner.CopyFrom(sample.arm.expected_grab_owner)
        self.assertEqual(validate(sample), sample.observed)
        sample.records = v.decode_records(artifact(sample.records))
        self.assertEqual(validate(sample), sample.observed)
        sample = fixture()
        sample.arm.expected_grab_owner.CopyFrom(sample.arm.expected_window_owner)
        sample.records[0].arm.accepted_command.CopyFrom(sample.arm)
        # The sampled probe owner cannot satisfy an explicitly expected APP owner.
        with self.assertRaises(v.ObservationV2Error) as caught:
            validate(sample)
        self.assertEqual(caught.exception.code, "GRAB_IDENTITY_JOIN")

    def test_actual_probe_receipts_and_window_owner_pins_stay_separate(self):
        self.reject("OBSERVER_RECIPIENT_JOIN", lambda s:
                    s.observed.recipient.CopyFrom(s.arm.expected_window_owner))
        self.reject("PROBE_IDENTITY_JOIN", lambda s:
                    s.batch.recipient.CopyFrom(s.arm.expected_window_owner))
        self.reject("OBSERVER_RECIPIENT_JOIN", lambda s: s.observed.recipient.ClearField("role"))
        for field in ("client_id", "native_object_epoch"):
            self.reject("OBSERVER_RECIPIENT_JOIN", lambda s: setattr(s.observed.recipient, field, 999))
            self.reject("PROBE_IDENTITY_JOIN", lambda s: setattr(s.batch.recipient, field, 999))
            self.reject("OBSERVER_WINDOW_JOIN", lambda s: setattr(s.observed.window.owner, field, 999))
        self.reject("OBSERVER_WINDOW_JOIN", lambda s:
                    s.observed.window.owner.CopyFrom(s.arm.expected_recipient))
        self.reject("OBSERVER_WINDOW_JOIN", lambda s: s.observed.window.owner.ClearField("role"))
        self.reject("PROBE_IDENTITY_JOIN", lambda s: setattr(s.batch.window.owner, "native_object_epoch", 999))
        self.reject("RECEIPT_WINDOW_JOIN", lambda s:
                    setattr(s.batch.receipts[0].window.owner, "client_id", 999))
        self.reject("NATIVE_FOCUS_JOIN", lambda s:
                    s.snaps[0].devices[2].native_focus.owner.CopyFrom(s.arm.expected_recipient))
        self.reject("GRAB_IDENTITY_JOIN", lambda s:
                    s.snaps[0].devices[0].grab.window.owner.CopyFrom(s.arm.expected_recipient))

    def test_fresh_v1_decoder_rejects_v2_without_module_collision(self):
        code = "\n".join([
            "import json, sys",
            "sys.path[:] = json.loads(sys.argv[1])",
            "import input_state_observer as old",
            "assert old.PROTOCOL_VERSION == 1",
            "try:",
            "    old.decode_records(sys.stdin.buffer.read())",
            "except old.ObservationError as error:",
            "    print(str(error))",
            "else:",
            "    raise AssertionError('v1 accepted v2')",
        ])
        result = subprocess.run([sys.executable, "-B", "-c", code, json.dumps(sys.path)],
            input=artifact(fixture().records), capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual(result.stdout, b"unknown, duplicate, or noncanonical fields\n")

    def test_bounds_are_locked_for_separate_v2_path(self):
        self.assertEqual((v.FRAME_COUNT, v.MAX_FRAME_BYTES, v.MAX_ARTIFACT_BYTES,
                          v.MAX_RECEIPTS, v.MAX_ARM_INTERVAL_NS), (4, 4096, 16392, 32, 10_000_000_000))

    def test_framing_specific_rejections(self):
        sample = fixture()
        raw = artifact(sample.records)
        cases = [ ("ARTIFACT_BYTES_LIMIT", b"x"*16393),
                  ("FRAME_BYTES_LIMIT", v._prefix(4097)),
                  ("FRAME_LENGTH_TRUNCATED", b"\x80"),
                  ("FRAME_LENGTH_OVERFLOW", b"\x80\x80"),
                  ("FRAME_LENGTH_NONCANONICAL", b"\x81\x00x"),
                  ("PROTOBUF_TRUNCATED", v._prefix(10)+b"x"),
                  ("PROTOBUF_INVALID", b"\x01\xff"),
                  ("FOUR_RECORDS_REQUIRED", artifact(sample.records[:3])),
                  ("RECORD_COUNT_LIMIT", raw+artifact(sample.records[:1])) ]
        for code, data in cases:
            with self.subTest(code=code):
                with self.assertRaises(v.ObservationV2Error) as caught:
                    v.decode_records(data)
                self.assertEqual(caught.exception.code, code)
        first = sample.records[0].SerializeToString(deterministic=True)
        unknown = first + b"\xf8\x07\x01"
        with self.assertRaises(v.ObservationV2Error) as caught:
            v.decode_records(v._prefix(len(unknown))+unknown+artifact(sample.records[1:]))
        self.assertEqual(caught.exception.code, "PROTOBUF_UNKNOWN_FIELDS")
        duplicate = first + b"\x08\x02"
        with self.assertRaises(v.ObservationV2Error) as caught:
            v.decode_records(v._prefix(len(duplicate))+duplicate+artifact(sample.records[1:]))
        self.assertEqual(caught.exception.code, "PROTOBUF_NONCANONICAL")

    def test_record_session_sequence_and_order(self):
        for field, value, code in [("protocol_version", 1, "PROTOCOL_VERSION"),
            ("session_id", b"z"*16, "RECORD_SESSION_JOIN"),
            ("arm_id", b"z"*16, "RECORD_SESSION_JOIN"),
            ("native_session_epoch", 10, "RECORD_SESSION_JOIN"),
            ("stream_sequence", 2, "RECORD_SEQUENCE")]:
            with self.subTest(field=field):
                self.reject(code, lambda s: setattr(s.records[0], field, value))
        self.reject("RECORD_PAYLOAD_ORDER", lambda s: s.records[0].ClearField("arm"))
        self.reject("ACCEPTED_ARM_JOIN", lambda s: setattr(s.records[0].arm.accepted_command,
                                                          "prearm_receipt_watermark", 0))

    def test_trusted_arm_binding_and_identity_joins(self):
        self.reject("ARM_SESSION_JOIN", lambda s: setattr(s.arm, "session_id", b"z"*16))
        self.reject("ARM_SESSION_JOIN", lambda s: setattr(s.arm, "arm_id", b"z"*16))
        self.reject("ARM_INTERVAL_INVALID", lambda s: setattr(s.arm, "deadline_monotonic_ns", 10_000_000_101))
        self.reject("ARM_INTERVAL_INVALID", lambda s: setattr(s.arm, "deadline_monotonic_ns", 100))
        self.reject("CLIENT_IDENTITY_INVALID", lambda s: setattr(s.arm.expected_recipient, "client_id", 0))
        # Revised contract forbids an APP label on the privileged independent probe.
        self.reject("NON_APPLICATION_CLIENT_LABEL_PRESENT", lambda s:
                    s.arm.expected_recipient.application.CopyFrom(s.arm.expected_application))
        self.reject("ACTOR_IDENTITY_INVALID", lambda s: setattr(s.arm.old_actor, "actor_id", b"short"))
        self.reject("TARGET_IDENTITY_INVALID", lambda s: s.arm.expected_application.Clear())
        self.reject("EXPECTED_EVENT_INVALID", lambda s: setattr(s.arm, "expected_xi2_event_type", 2))
        self.reject("EXPECTED_HELD_KEYS_INVALID", lambda s: setattr(s.arm, "expected_modifier_keycode", 99))
        self.reject("TWO_BINDINGS_REQUIRED", lambda s: s.bindings.pop())
        self.reject("BINDING_ACTOR_DUPLICATE", lambda s: s.bindings[1].actor.CopyFrom(s.bindings[0].actor))
        self.reject("BINDING_ACTOR_JOIN", lambda s: setattr(s.bindings[0].actor, "actor_id", b"forged"))
        self.reject("BINDING_SESSION_JOIN", lambda s: setattr(s.bindings[0], "arm_id", b"z"*16))
        for field in ("world_actor_id", "native_seat_id", "presentation_generation"):
            self.reject("BINDING_CONTEXT_JOIN", lambda s: setattr(s.bindings[0], field, 999))
        self.reject("DEVICE_ATTACHMENT_INVALID", lambda s: setattr(s.arm.old_actor_devices[1], "paired_or_attached", 999))
        self.reject("OLD_DEVICE_SET_INVALID", lambda s: setattr(s.arm.old_actor_devices[0], "kind", 0))
        self.reject("DEVICE_ID_OVERLAP", self.overlap_devices)

    @staticmethod
    def overlap_devices(sample):
        sample.arm.ClearField("peer_actor_devices")
        sample.arm.peer_actor_devices.extend(sample.arm.old_actor_devices)

    def test_snapshot_completeness_phase_time_and_context(self):
        self.reject("SNAPSHOT_INCOMPLETE", lambda s: setattr(s.snaps[1], "complete", False))
        self.reject("TRANSITION_PHASE_ORDER", lambda s: setattr(s.snaps[1], "phase", pb.POST_CANCEL))
        self.reject("TRANSITION_TIME_ORDER", lambda s: setattr(s.snaps[1], "monotonic_ns", 109))
        self.reject("TRANSITION_TIME_ORDER", lambda s: setattr(s.snaps[3], "monotonic_ns", 1001))
        self.reject("SEAT_CONTEXT_INCOMPLETE", lambda s: setattr(s.snaps[0].old_seat, "complete", False))
        for field in ("native_seat_id", "world_actor_id", "presentation_generation"):
            self.reject("SEAT_CONTEXT_JOIN", lambda s: setattr(s.snaps[0].old_seat, field, 999))
        self.reject("TARGET_FOCUS_CONTEXT_JOIN", lambda s: setattr(s.snaps[0].old_seat.focus, "spatial_generation", 999))
        self.reject("APPLICATION_CONTROLLER_PHASE_JOIN", lambda s: setattr(s.snaps[2], "controller_native_seat_id", 11))
        self.reject("CANCELLED_CONTEXT_REMAINS", lambda s: setattr(s.snaps[2].old_seat, "application_controller_native_seat_id", 11))
        self.reject("CANCELLED_CONTEXT_REMAINS", lambda s: s.snaps[2].old_seat.focus.CopyFrom(s.arm.expected_window))
        self.reject("TRACKED_INPUT_REMAINS", lambda s: setattr(s.snaps[3].old_seat, "tracked_buttons", 2))
        self.reject("PEER_CONTROLLER_CONTEXT_JOIN", lambda s: setattr(s.snaps[3].peer_seat, "application_controller_native_seat_id", 11))
        self.reject("PEER_PRE_EVENT_TARGET_FOCUS", lambda s: s.snaps[3].peer_seat.target.CopyFrom(s.arm.expected_window))
        self.reject("SNAPSHOT_DEVICE_DUPLICATE", self.duplicate_snapshot_device)
        self.reject("SNAPSHOT_DEVICE_PIN_JOIN", lambda s: setattr(s.snaps[0].devices[0].identity, "native_object_epoch", 999))

    @staticmethod
    def duplicate_snapshot_device(sample):
        sample.snaps[0].devices[1].CopyFrom(sample.snaps[0].devices[0])

    def test_pre_cancel_requires_held_keys_buttons_focus_and_capture(self):
        self.reject("PRE_CANCEL_TRACKED_HELD_JOIN", lambda s: setattr(s.snaps[0].old_seat, "tracked_keys", ZERO))
        self.reject("PRE_CANCEL_TRACKED_HELD_JOIN", lambda s: setattr(s.snaps[0].old_seat, "tracked_buttons", 0))
        def clear_keys(sample):
            for d in sample.snaps[0].devices:
                if d.identity.kind == pb.DeviceIdentity.KEYBOARD:
                    d.down_bitmap = d.postdown_bitmap = ZERO
        self.reject("PRE_CANCEL_NATIVE_HELD_JOIN", clear_keys)
        def clear_grabs(sample):
            for d in sample.snaps[0].devices:
                d.active_grab = False
                d.ClearField("grab")
        self.reject("PRE_CANCEL_CAPTURE_MISSING", clear_grabs)
        def clear_focus(sample):
            d = sample.snaps[0].devices[2]
            d.focus_kind = 1
            d.ClearField("native_focus")
        self.reject("PRE_CANCEL_FOCUS_MISSING", clear_focus)

    def test_bitmap_length_class_and_popcount(self):
        self.reject("NATIVE_BITMAP_INVALID", lambda s: setattr(s.snaps[0].devices[0], "bitmap_valid", False))
        self.reject("NATIVE_BITMAP_INVALID", lambda s: setattr(s.snaps[0].devices[0], "bitmap_bits", 0))
        for field in ("down_bitmap", "postdown_bitmap"):
            for length in (0, 31, 33):
                self.reject("NATIVE_BITMAP_LENGTH", lambda s: setattr(s.snaps[0].devices[0], field, bytes(length)))
        self.reject("NATIVE_BUTTON_POPCOUNT", lambda s: setattr(s.snaps[0].devices[0], "buttons_down", 0))
        self.reject("NATIVE_BUTTON_RANGE", lambda s: setattr(s.snaps[0].devices[0], "down_bitmap", bitmap([9])))
        self.reject("TRACKED_KEY_BITMAP_LENGTH", lambda s: setattr(s.snaps[0].old_seat, "tracked_keys", b""))

    def test_each_native_clear_fact_and_bound_old_devices(self):
        cases = [("native_frozen", True, "OLD_NATIVE_FREEZE_REMAINS"),
            ("frozen_by_other_grab", True, "OLD_NATIVE_FREEZE_REMAINS"),
            ("native_sync_state", 1, "OLD_NATIVE_SYNC_REMAINS"),
            ("pending_events", 1, "OLD_NATIVE_PENDING_REPLAY_REMAINS"),
            ("replay_device", True, "OLD_NATIVE_PENDING_REPLAY_REMAINS"),
            ("postdown_bitmap", bitmap([50]), "OLD_NATIVE_HELD_REMAINS")]
        for phase in (1, 2, 3):
            for field, value, code in cases:
                self.reject(code, lambda s: setattr(s.snaps[phase].devices[2], field, value))
        for field in ("grab_complete", "context_complete", "focus_complete"):
            self.reject("DEVICE_CONTEXT_INCOMPLETE", lambda s: setattr(s.snaps[0].devices[0], field, False))
        self.reject("GRAB_IDENTITY_JOIN", lambda s: setattr(s.snaps[0].devices[0].grab.owner, "client_id", 0))
        self.reject("GRAB_IDENTITY_JOIN", lambda s: setattr(s.snaps[0].devices[0].grab.window, "native_object_epoch", 999))
        self.reject("NATIVE_FOCUS_JOIN", lambda s: setattr(s.snaps[0].devices[2].native_focus, "native_object_epoch", 999))
        for field in ("global_pending", "replay_pending", "replay_in_progress"):
            self.reject("GLOBAL_PENDING_REPLAY_REMAINS", lambda s: setattr(s.snaps[1], field, True))

    def test_native_held_grab_focus_and_duplicate_release_rejections(self):
        def retained_key(sample):
            sample.snaps[1].devices[2].down_bitmap = bitmap([50])
        self.reject("OLD_NATIVE_HELD_REMAINS", retained_key)
        def retained_button(sample):
            d = sample.snaps[1].devices[0]
            d.down_bitmap = bitmap([1])
            d.buttons_down = 1
        self.reject("OLD_NATIVE_HELD_REMAINS", retained_button)
        def retained_grab(sample):
            d = sample.snaps[1].devices[0]
            d.active_grab = True
            d.grab.CopyFrom(sample.snaps[0].devices[0].grab)
        self.reject("OLD_NATIVE_GRAB_REMAINS", retained_grab)
        def retained_focus(sample):
            d = sample.snaps[1].devices[2]
            d.focus_kind = 4
            d.native_focus.CopyFrom(sample.arm.expected_window)
        self.reject("OLD_NATIVE_FOCUS_REMAINS", retained_focus)
        def duplicate_release(sample):
            sample.batch.receipts[2].CopyFrom(sample.batch.receipts[1])
            sample.batch.receipts[2].receipt_ordinal = 13
            sample.batch.receipts[2].monotonic_ns = 150
        self.reject("OLD_RELEASE_DUPLICATE", duplicate_release)
        self.reject("RELEASE_DEVICE_JOIN", lambda s: setattr(s.batch.receipts[1].source, "native_object_epoch", 999))
        self.reject("PEER_RECEIPT_BEFORE_CALLBACK", lambda s: setattr(s.observed, "callback_monotonic_ns", 166))
        self.reject("PROBE_IDENTITY_JOIN", lambda s: setattr(s.batch.recipient, "client_id", 0))
        self.reject("PROBE_IDENTITY_JOIN", lambda s: setattr(s.batch.window, "native_object_epoch", 999))

    def test_observer_exact_identity_and_event_joins(self):
        self.reject("OBSERVER_RECIPIENT_JOIN", lambda s: setattr(s.observed.recipient, "client_id", 0))
        self.reject("OBSERVER_WINDOW_JOIN", lambda s: setattr(s.observed.window, "native_object_epoch", 999))
        for field in ("device_id", "native_object_epoch", "kind", "role", "paired_or_attached"):
            self.reject("OBSERVER_DEVICE_JOIN", lambda s: setattr(s.observed.source, field, 999 if field not in ("kind", "role") else 0))
        self.reject("OBSERVER_EVENT_JOIN", lambda s: setattr(s.observed, "xi2_event_type", 2))
        self.reject("OBSERVER_EVENT_JOIN", lambda s: setattr(s.observed, "detail", 2))
        self.reject("OBSERVER_TIME_ORDER", lambda s: setattr(s.observed, "callback_monotonic_ns", 139))
        for field, code in (("synthetic", "OBSERVER_SYNTHETIC"), ("overflow", "OBSERVER_OVERFLOW"),
                            ("disconnected", "OBSERVER_DISCONNECTED")):
            self.reject(code, lambda s: setattr(s.observed, field, True))

    def test_receipt_watermark_causality_and_exact_release_joins(self):
        self.reject("RECEIPT_ORDINAL", lambda s: setattr(s.batch.receipts[0], "receipt_ordinal", 10))
        self.reject("RECEIPT_ORDINAL", lambda s: setattr(s.batch.receipts[1], "receipt_ordinal", 13))
        self.reject("RECEIPT_TIME_ORDER", lambda s: setattr(s.batch.receipts[0], "monotonic_ns", 109))
        self.reject("RECEIPT_TIME_ORDER", lambda s: setattr(s.batch.receipts[1], "monotonic_ns", 119))
        self.reject("RELEASE_DEVICE_JOIN", lambda s: setattr(s.batch.receipts[0].device, "native_object_epoch", 999))
        self.reject("RELEASE_DEVICE_JOIN", lambda s: s.batch.receipts[0].source.CopyFrom(s.arm.peer_actor_devices[1]))
        self.reject("RELEASE_DETAIL_JOIN", lambda s: setattr(s.batch.receipts[1], "detail", 51))
        self.reject("RECEIPT_WINDOW_JOIN", lambda s: setattr(s.batch.receipts[0].window, "spatial_generation", 999))
        self.reject("RECEIPT_SYNTHETIC", lambda s: setattr(s.batch.receipts[0], "synthetic", True))
        self.reject("PEER_EVENT_TUPLE_JOIN", lambda s: setattr(s.batch.receipts[3], "event_time_ms", 98))
        self.reject("PEER_EVENT_TUPLE_JOIN", lambda s: setattr(s.batch.receipts[3].source, "native_object_epoch", 999))
        self.reject("OLD_RELEASES_MISSING_OR_ORDER", self.peer_before_releases)
        self.reject("OLD_RELEASE_AFTER_PEER", self.release_after_peer)
        self.reject("PEER_EVENT_MISSING", lambda s: s.batch.receipts.pop())
        self.reject("RECEIPT_COUNT_INVALID", self.too_many_receipts)
        for field, code in (("overflow", "PROBE_OVERFLOW"), ("disconnected", "PROBE_DISCONNECTED")):
            self.reject(code, lambda s: setattr(s.batch, field, True))

    @staticmethod
    def peer_before_releases(sample):
        sample.batch.receipts[0].CopyFrom(sample.batch.receipts[3])
        sample.batch.receipts[0].receipt_ordinal = 11

    @staticmethod
    def release_after_peer(sample):
        extra = sample.batch.receipts.add()
        extra.CopyFrom(sample.batch.receipts[0])
        extra.receipt_ordinal = 15
        extra.monotonic_ns = 170

    @staticmethod
    def too_many_receipts(sample):
        for _ in range(29):
            sample.batch.receipts.add()


if __name__ == "__main__":
    unittest.main()
