"""Adversarial structural controls. These fixtures provide no native evidence."""
import unittest
from types import SimpleNamespace
import native_input_observer_v2_pb2 as v2_pb
import native_input_lifecycle_v3_pb2 as pb
import input_state_observer_v2 as v2
import input_state_observer_v3 as v3
import test_input_state_observer_v2 as controls


def fixture(cause=pb.TARGET_DESTRUCTION):
    old = controls.fixture()
    arm = pb.LifecycleArmV3(common=old.arm, peer_window=old.arm.expected_window, cause=cause)
    if cause == pb.TARGET_DESTRUCTION:
        arm.peer_window.xid += 1
        arm.peer_window.native_object_epoch += 1
        arm.peer_window.spatial_generation += 1
    transition = pb.LifecycleTransitionV3(cancellation=old.snaps[:3], peer_bind=old.snaps[3])
    transition.peer_bind.monotonic_ns = 160
    retirement = transition.retirement
    retirement.monotonic_ns = 150
    retirement.cause = cause
    retirement.old_window.CopyFrom(arm.common.expected_window)
    retirement.old_native_seat_id = arm.common.old_native_seat_id
    retirement.complete = True
    state = pb.LifecyclePeerStateV3(monotonic_ns=180, complete=True)
    if cause == pb.TARGET_DESTRUCTION:
        retirement.resource_free_monotonic_ns = transition.cancellation[0].monotonic_ns
        retirement.destroy_hook_return_monotonic_ns = 150
        retirement.old_window_resource_absent = True
        retirement.spatial_generation_invalidated_monotonic_ns = 140
        retirement.invalidated_spatial_generation = 600
        retirement.destroy_hook_observed = retirement.destroy_hook_result = True
        state.old_window_resource_absent = True
        state.live_old.CopyFrom(transition.cancellation[2])
        state.live_old.monotonic_ns = 180
        state.live_old.phase = v2_pb.POST_PEER_BIND
        state.live_old.controller_native_seat_id = arm.common.peer_native_seat_id
    else:
        retirement.old_native_seat_absent = retirement.all_old_device_ids_absent = True
        for index, device in enumerate(arm.common.old_actor_devices):
            retirement.removals.add(identity=device, remove_result=0, monotonic_ns=140+index,
                                    removal_observed=True)
        transition.peer_bind.ClearField("old_seat")
        del transition.peer_bind.devices[:4]
        state.retired_old.CopyFrom(pb.DeviceRetirementCheckV3(monotonic_ns=180,
            retired_devices=arm.common.old_actor_devices, old_native_seat_id=arm.common.old_native_seat_id,
            old_native_seat_absent=True, all_old_device_ids_absent=True, complete=True))
    press = v2_pb.ObserverPress()
    press.CopyFrom(old.observed)
    press.window.CopyFrom(arm.peer_window)
    press.callback_monotonic_ns = 180
    batch = v2_pb.ProbeReceiptBatch()
    batch.CopyFrom(old.batch)
    batch.window.CopyFrom(arm.peer_window)
    batch.receipts[-1].window.CopyFrom(arm.peer_window)
    batch.receipts[-1].monotonic_ns = 200
    payloads = (pb.LifecycleArmAcceptedV3(accepted_command=arm), transition,
                pb.LifecyclePeerPressV3(press=press, state=state), batch)
    records = []
    for index, (name, payload) in enumerate(zip(v3.PAYLOADS, payloads), 1):
        record = pb.NativeInputLifecycleRecordV3(protocol_version=3, session_id=controls.SID,
            arm_id=controls.AID, stream_sequence=index, native_session_epoch=controls.NATIVE)
        getattr(record, name).CopyFrom(payload)
        records.append(record)
    return SimpleNamespace(arm=arm, bindings=old.bindings, records=records,
        transition=records[1].native_transition, state=records[2].observer_press.state,
        press=records[2].observer_press.press, batch=records[3].probe_receipts)


def validate(sample, **changes):
    args = dict(expected_session_id=controls.SID, expected_arm_id=controls.AID,
        expected_native_session_epoch=controls.NATIVE, expected_arm=sample.arm,
        expected_bindings=sample.bindings)
    args.update(changes)
    return v3.validate_records(sample.records, **args)


def artifact(records):
    return b"".join(v2._prefix(len(raw)) + raw for raw in
        (record.SerializeToString(deterministic=True) for record in records))


class LifecycleContractTests(unittest.TestCase):
    def reject(self, code, change, cause=pb.TARGET_DESTRUCTION):
        sample = fixture(cause)
        change(sample)
        with self.assertRaises(v3.ObservationV3Error) as caught:
            validate(sample)
        self.assertEqual(caught.exception.code, code)

    def test_three_lifecycle_structural_controls_roundtrip(self):
        for cause in v3.CAUSES:
            with self.subTest(cause=cause):
                sample = fixture(cause)
                self.assertEqual(validate(sample), sample.press)
                sample.records = v3.decode_records(artifact(sample.records))
                self.assertEqual(validate(sample), sample.press)

    def test_legacy_control_wire_encodings_remain_identical(self):
        import native_input_observer_control_v2_pb2 as control
        request = control.ObserverSetupRequestV2(session_id=controls.SID, arm_id=controls.AID,
            target_xid=500, target_spatial_generation=502)
        legacy_setup = control.ObserverControlEnvelopeV2(setup_request=request)
        new_setup = pb.LifecycleControlEnvelopeV3(legacy_setup_request=request)
        self.assertEqual(legacy_setup.SerializeToString(deterministic=True),
                         new_setup.SerializeToString(deterministic=True))
        arm = fixture().arm.common
        self.assertEqual(control.ObserverControlEnvelopeV2(arm=arm).SerializeToString(deterministic=True),
                         pb.LifecycleControlEnvelopeV3(legacy_arm=arm).SerializeToString(deterministic=True))

    def test_legacy_reply_wire_encodings_remain_identical(self):
        import native_input_observer_control_v2_pb2 as control
        hello = control.ObserverEndpointHelloV2(protocol_version=2, session_id=controls.SID,
            native_session_epoch=9, server_pid=12, server_uid=1000, server_gid=1000)
        pins = control.ObserverSetupReplyV2(session_id=controls.SID, arm_id=controls.AID,
            native_session_epoch=9, old_native_seat_id=11, peer_native_seat_id=12)
        accepted = v2_pb.Arm(accepted_command=fixture().arm.common)
        for left, right in ((control.ObserverEndpointReplyV2(hello=hello),
                             pb.LifecycleEndpointReplyV3(hello=hello)),
                            (control.ObserverEndpointReplyV2(setup_reply=pins),
                             pb.LifecycleEndpointReplyV3(legacy_setup_reply=pins)),
                            (control.ObserverEndpointReplyV2(arm_accepted=accepted),
                             pb.LifecycleEndpointReplyV3(legacy_arm_accepted=accepted))):
            self.assertEqual(left.SerializeToString(deterministic=True), right.SerializeToString(deterministic=True))

    def test_untrusted_survivor_application_is_rejected(self):
        self.reject("PEER_WINDOW_IDENTITY_INVALID",
            lambda s: setattr(s.arm.peer_window.application, "application_id", b"other"))

    def test_simultaneous_owner_pins_cannot_alias_clients_or_epochs(self):
        self.reject("PEER_WINDOW_OWNER_IDENTITY_ALIAS",
            lambda s: setattr(s.arm.peer_window.owner, "native_object_epoch", 999))
        self.reject("PEER_WINDOW_OWNER_EPOCH_ALIAS",
            lambda s: setattr(s.arm.peer_window.owner, "client_id", 99))
        def privileged_epoch(s):
            s.arm.peer_window.owner.client_id = 99
            s.arm.peer_window.owner.native_object_epoch = s.arm.common.expected_recipient.native_object_epoch
        self.reject("PEER_WINDOW_OWNER_EPOCH_ALIAS", privileged_epoch)

    def test_same_xid_cannot_substitute_new_window_epoch(self):
        self.reject("PEER_WINDOW_NUMERICAL_ALIAS",
            lambda s: setattr(s.arm.peer_window, "xid", s.arm.common.expected_window.xid))

    def test_destroy_requires_separate_surviving_window(self):
        self.reject("DESTROY_SURVIVOR_REQUIRED",
            lambda s: s.arm.peer_window.CopyFrom(s.arm.common.expected_window))

    def test_cross_actor_binding_swap_is_rejected(self):
        self.reject("BINDING_CONTEXT_JOIN", lambda s: setattr(s.bindings[0], "native_seat_id", 12))

    def test_producer_arm_cannot_replace_trusted_cause(self):
        self.reject("ACCEPTED_ARM_JOIN", lambda s: setattr(s.records[0].arm.accepted_command,
                                                         "cause", pb.ACTOR_DISCONNECT))

    def test_missing_native_capture_is_rejected(self):
        def change(s):
            for d in s.transition.cancellation[0].devices:
                d.active_grab = False
                d.ClearField("grab")
        self.reject("PRE_CANCEL_CAPTURE_MISSING", change)

    def test_retained_button_is_rejected_after_release(self):
        def change(s):
            d = s.transition.cancellation[1].devices[0]
            d.down_bitmap = d.postdown_bitmap = controls.bitmap([1])
            d.buttons_down, d.button_state = 1, 256
        self.reject("OLD_NATIVE_HELD_REMAINS", change)

    def test_retained_modifier_is_rejected_after_release(self):
        def change(s):
            d = s.transition.cancellation[1].devices[2]
            d.down_bitmap = d.postdown_bitmap = controls.bitmap([62])
        self.reject("OLD_NATIVE_HELD_REMAINS", change)

    def test_retained_grab_is_rejected_after_release(self):
        def change(s):
            d = s.transition.cancellation[1].devices[0]
            d.active_grab = True
            d.grab.CopyFrom(s.transition.cancellation[0].devices[0].grab)
        self.reject("OLD_NATIVE_GRAB_REMAINS", change)

    def test_retained_focus_is_rejected_after_release(self):
        def change(s):
            d = s.transition.cancellation[1].devices[2]
            d.focus_kind = 4
            d.native_focus.CopyFrom(s.arm.common.expected_window)
        self.reject("OLD_NATIVE_FOCUS_REMAINS", change)

    def test_frozen_pending_and_replay_state_are_rejected(self):
        for field, code in (("native_frozen", "OLD_NATIVE_FREEZE_REMAINS"),
                            ("frozen_by_other_grab", "OLD_NATIVE_FREEZE_REMAINS"),
                            ("pending_events", "OLD_NATIVE_PENDING_REPLAY_REMAINS"),
                            ("replay_device", "OLD_NATIVE_PENDING_REPLAY_REMAINS")):
            with self.subTest(field=field):
                self.reject(code, lambda s: setattr(s.transition.cancellation[1].devices[0], field, 1))

    def test_retirement_cannot_precede_native_clearance(self):
        self.reject("RETIREMENT_TIME_ORDER", lambda s: setattr(s.transition.retirement, "monotonic_ns", 129))

    def test_missing_destroy_completion_is_rejected(self):
        self.reject("DESTRUCTION_BOUNDARY_MISSING",
            lambda s: setattr(s.transition.retirement, "destroy_hook_return_monotonic_ns", 0))

    def test_destroy_generation_boundary_and_hook_result_are_required(self):
        self.reject("DESTRUCTION_GENERATION_BOUNDARY", lambda s: setattr(s.transition.retirement,
            "spatial_generation_invalidated_monotonic_ns", 129))
        self.reject("DESTRUCTION_GENERATION_BOUNDARY", lambda s: setattr(s.transition.retirement,
            "invalidated_spatial_generation", s.arm.common.expected_window.spatial_generation))
        for value in (1, fixture().arm.peer_window.spatial_generation):
            with self.subTest(generation=value):
                self.reject("DESTRUCTION_GENERATION_BOUNDARY", lambda s: setattr(s.transition.retirement,
                    "invalidated_spatial_generation", value))
        for field in ("destroy_hook_observed", "destroy_hook_result"):
            with self.subTest(field=field):
                self.reject("DESTRUCTION_HOOK_FAILED", lambda s: setattr(s.transition.retirement, field, False))

    def test_wrong_destroyed_epoch_is_rejected(self):
        self.reject("RETIREMENT_IDENTITY_JOIN",
            lambda s: setattr(s.transition.retirement.old_window, "native_object_epoch", 999))

    def test_missing_removal_receipt_is_rejected(self):
        self.reject("DEVICE_REMOVAL_SET_JOIN", lambda s: s.transition.retirement.removals.pop(), pb.ACTOR_DISCONNECT)

    def test_failed_or_unobserved_removal_is_rejected(self):
        for field, value in (("remove_result", 8), ("removal_observed", False)):
            with self.subTest(field=field):
                self.reject("DEVICE_REMOVAL_FAILED", lambda s: setattr(s.transition.retirement.removals[0],
                    field, value), pb.ACTOR_REVOCATION)

    def test_wrong_removed_device_epoch_is_rejected(self):
        self.reject("DEVICE_REMOVAL_PIN_JOIN", lambda s: setattr(s.transition.retirement.removals[0].identity,
            "native_object_epoch", 999), pb.ACTOR_DISCONNECT)

    def test_still_present_device_or_seat_is_rejected(self):
        for field in ("old_native_seat_absent", "all_old_device_ids_absent"):
            with self.subTest(field=field):
                self.reject("OLD_DEVICES_NOT_RETIRED", lambda s: setattr(s.transition.retirement,
                    field, False), pb.ACTOR_REVOCATION)

    def test_peer_bind_cannot_precede_retirement(self):
        self.reject("PEER_BIND_BEFORE_RETIREMENT", lambda s: setattr(s.transition.peer_bind, "monotonic_ns", 149))

    def test_fake_all_clear_snapshot_cannot_replace_retirement(self):
        def change(s):
            s.state.live_old.CopyFrom(s.transition.cancellation[2])
            s.state.live_old.monotonic_ns = s.state.monotonic_ns
        self.reject("RETIRED_FIRST_PEER_STATE_REQUIRED", change, pb.ACTOR_DISCONNECT)

    def test_cached_clearance_cannot_replace_fresh_first_peer_sample(self):
        self.reject("FIRST_PEER_STATE_NOT_FRESH", lambda s: setattr(s.state.live_old, "monotonic_ns", 130))

    def test_reintroduced_old_key_at_first_peer_is_rejected(self):
        def change(s):
            d = s.state.live_old.devices[2]
            d.down_bitmap = d.postdown_bitmap = controls.bitmap([62])
        self.reject("OLD_NATIVE_HELD_REMAINS", change)

    def test_current_retirement_requires_same_epochs_and_absence(self):
        self.reject("RETIREMENT_CHECK_DEVICE_PIN", lambda s: setattr(s.state.retired_old.retired_devices[0],
            "native_object_epoch", 999), pb.ACTOR_REVOCATION)
        self.reject("RETIREMENT_CHECK_ABSENCE", lambda s: setattr(s.state.retired_old,
            "all_old_device_ids_absent", False), pb.ACTOR_DISCONNECT)

    def test_first_peer_callback_before_binding_is_rejected(self):
        def change(s):
            s.press.callback_monotonic_ns = s.state.monotonic_ns = 159
        self.reject("OBSERVER_TIME_ORDER", change)

    def test_peer_press_must_use_survivor(self):
        self.reject("OBSERVER_WINDOW_JOIN", lambda s: s.press.window.CopyFrom(s.arm.common.expected_window))

    def test_independent_receipts_keep_each_actual_window(self):
        self.reject("RELEASE_WINDOW_JOIN", lambda s: s.batch.receipts[0].window.CopyFrom(s.arm.peer_window))
        self.reject("PEER_EVENT_TUPLE_JOIN", lambda s: s.batch.receipts[-1].window.CopyFrom(s.arm.common.expected_window))

    def test_late_old_release_is_rejected(self):
        def change(s):
            event = s.batch.receipts.add()
            event.CopyFrom(s.batch.receipts[0])
            event.receipt_ordinal = 15
            event.monotonic_ns = 201
        self.reject("OLD_RELEASE_AFTER_PEER", change)

    def test_receipt_gap_and_callback_disagreement_are_rejected(self):
        self.reject("RECEIPT_ORDINAL", lambda s: setattr(s.batch.receipts[1], "receipt_ordinal", 99))
        self.reject("PEER_EVENT_TUPLE_JOIN", lambda s: setattr(s.batch.receipts[-1], "event_time_ms", 100))
        self.reject("PEER_EVENT_TUPLE_JOIN", lambda s: setattr(s.batch.receipts[-1].source, "native_object_epoch", 999))

    def test_synthetic_and_disconnected_evidence_is_rejected(self):
        self.reject("OBSERVER_INVALID", lambda s: setattr(s.press, "synthetic", True))
        self.reject("PROBE_INVALID", lambda s: setattr(s.batch, "disconnected", True))
        self.reject("RECEIPT_SYNTHETIC", lambda s: setattr(s.batch.receipts[-1], "synthetic", True))

    def test_expired_observer_and_missing_releases_are_rejected(self):
        def expired(s):
            s.press.callback_monotonic_ns = s.state.monotonic_ns = 1001
        self.reject("OBSERVER_TIME_ORDER", expired)
        def missing(s):
            del s.batch.receipts[0]
            for index, event in enumerate(s.batch.receipts):
                event.receipt_ordinal = 11+index
        self.reject("OLD_RELEASES_MISSING_OR_ORDER", missing)

    def test_unknown_noncanonical_truncated_and_extra_frames_are_rejected(self):
        sample = fixture()
        data = artifact(sample.records)
        raw = sample.records[0].SerializeToString(deterministic=True) + b"\xf8\x07\x01"
        rest = artifact(sample.records[1:])
        for bad, code in ((v2._prefix(len(raw))+raw+rest, "PROTOBUF_UNKNOWN_FIELDS"),
                          (data[:-1], "PROTOBUF_TRUNCATED"),
                          (data+data, "RECORD_COUNT_LIMIT"),
                          (b"\x81\x00\x00", "FRAME_LENGTH_NONCANONICAL")):
            with self.subTest(code=code), self.assertRaises(v3.ObservationV3Error) as caught:
                v3.decode_records(bad)
            self.assertEqual(caught.exception.code, code)

    def test_bounds_cannot_be_relaxed_by_caller(self):
        for limits in (v3.Limits(max_frame_bytes=8193), v3.Limits(max_artifact_bytes=32777),
                       v3.Limits(max_receipts=33)):
            with self.subTest(limits=limits), self.assertRaises(v3.ObservationV3Error) as caught:
                validate(fixture(), limits=limits)
            self.assertEqual(caught.exception.code, "LIMITS_INVALID")


if __name__ == "__main__":
    unittest.main(verbosity=2)
