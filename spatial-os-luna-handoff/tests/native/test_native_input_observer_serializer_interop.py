# C protobuf-c encoder interoperability checks. Run on an assigned test host.
import copy
import pathlib
import subprocess
import sys
import unittest

import native_input_observer_pb2 as pb
from input_state_observer import ObservationError, decode_and_validate

FIXTURE = pathlib.Path(sys.argv.pop(1)).resolve()
SESSION_ID = bytes(range(1, 17))
ARM_ID = bytes(range(33, 49))


def frame(raw):
    size = len(raw)
    prefix = bytearray()
    while size > 127:
        prefix.append((size & 127) | 128)
        size >>= 7
    prefix.append(size)
    return bytes(prefix) + raw


def split_frames(data):
    records = []
    offset = 0
    while offset < len(data):
        start = offset
        size = shift = 0
        while True:
            if offset >= len(data):
                raise AssertionError("truncated frame length")
            byte = data[offset]
            offset += 1
            size |= (byte & 127) << shift
            shift += 7
            if byte < 128:
                break
        raw = data[offset:offset + size]
        if len(raw) != size:
            raise AssertionError("truncated protobuf frame")
        offset += size
        records.append((pb.NativeInputObservationRecord.FromString(raw), data[start:offset]))
    return records


def expected_arm():
    arm = pb.Arm()
    arm.actor_id = bytes(range(1, 33))
    arm.issuer_id = bytes(range(41, 73))
    arm.peer_actor_id = bytes(range(81, 113))
    arm.peer_issuer_id = bytes(range(121, 153))
    arm.arm_monotonic_ns = 1000
    arm.deadline_monotonic_ns = 1200
    arm.expected_xi2_event_type = 4
    arm.expected_detail = 1
    arm.prearm_receipt_watermark = 50
    for base, epoch, pairings in ((10, 30, (12, 10, 10, 12)),
                                  (20, 40, (22, 20, 20, 22))):
        target = arm.old_actor_devices if base == 10 else arm.peer_actor_devices
        for i in range(4):
            device = target.add()
            device.seat_role = pb.ActorDeviceIdentity.MASTER if i in (0, 2) else pb.ActorDeviceIdentity.SLAVE
            device.kind = pb.ActorDeviceIdentity.POINTER if i < 2 else pb.ActorDeviceIdentity.KEYBOARD
            device.device_id = base + i
            device.object_epoch = epoch + i
            device.paired_or_attached = pairings[i]
    arm.expected_recipient.client_id = 5
    arm.expected_recipient.object_epoch = 21
    arm.expected_recipient.application_label.workspace = b"ws1"
    arm.expected_recipient.application_label.application = b"app"
    arm.expected_window.window_id = 100
    arm.expected_window.object_epoch = 20
    arm.expected_window.application_label.workspace = b"ws1"
    arm.expected_window.application_label.application = b"app"
    arm.expected_window.spatial_generation = 3
    arm.expected_peer_device.CopyFrom(arm.peer_actor_devices[0])
    arm.expected_peer_source.CopyFrom(arm.peer_actor_devices[1])
    return arm


def fixture_batch():
    batch = pb.ProbeReceiptBatch()
    batch.context.recipient_object_epoch = 21
    batch.context.window_object_epoch = 20
    batch.context.device_object_epoch = 40
    batch.context.source_object_epoch = 41
    receipt = batch.receipts.add()
    receipt.receipt_ordinal = 51
    receipt.event_type = 4
    receipt.xid = 100
    receipt.detail = 1
    receipt.time = 77
    receipt.deviceid = 20
    receipt.sourceid = 21
    record = pb.NativeInputObservationRecord(protocol_version=1,
        session_id=SESSION_ID, arm_id=ARM_ID, stream_sequence=3, native_session=9)
    record.probe_receipts.CopyFrom(batch)
    return record


class SerializerInterop(unittest.TestCase):
    def produce(self, case, capacity=None):
        args = [str(FIXTURE), case]
        if capacity is not None:
            args.append(str(capacity))
        return subprocess.run(args, capture_output=True, check=False)

    def complete(self, case="valid"):
        result = self.produce(case)
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = split_frames(result.stdout)
        self.assertEqual(len(parsed), 2)
        return result.stdout, [parsed[0][0], parsed[1][0], fixture_batch()], parsed

    def validate(self, artifact):
        return decode_and_validate(artifact, expected_session_id=SESSION_ID,
            expected_arm_id=ARM_ID, expected_arm=expected_arm(),
            expected_native_session=9)

    def artifact_from(self, records):
        return b"".join(frame(record.SerializeToString(deterministic=True))
                       for record in records)

    def test_generated_protobuf_c_valid_artifact(self):
        wire, records, parsed = self.complete()
        self.assertEqual(parsed[0][0].WhichOneof("payload"), "arm")
        self.assertEqual(parsed[1][0].WhichOneof("payload"), "observer_press")
        self.assertEqual(parsed[0][0].arm, expected_arm())
        self.validate(wire + frame(records[2].SerializeToString(deterministic=True)))

    def test_native_held_key_and_button_are_encoded_then_rejected(self):
        for case, state_index in (("held-key", 2), ("held-button", 0)):
            with self.subTest(case=case):
                wire, records, parsed = self.complete(case)
                states = parsed[1][0].observer_press.old_actor_state
                self.assertTrue(any(states[state_index].down_bitmap),
                                "native held bitmap was not encoded")
                artifact = wire + frame(records[2].SerializeToString(deterministic=True))
                with self.assertRaisesRegex(ObservationError,
                                            "old native key/button remains held"):
                    self.validate(artifact)

    def test_native_record_rejections_have_no_output(self):
        for case in ("stale-session", "stale-pin", "bad-role", "bad-label",
                     "bad-tuple", "bad-time", "bad-phase", "synthetic-type",
                     "wrong-type", "truncated-packet", "length-mismatch"):
            with self.subTest(case=case):
                result = self.produce(case)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, b"")

    def test_python_stale_and_malformed_pins_rejected_for_intended_reason(self):
        _, baseline, _ = self.complete()
        cases = []
        stale = copy.deepcopy(baseline); stale[1].native_session += 1
        cases.append((stale, "native session mismatch"))
        role = copy.deepcopy(baseline)
        role[0].arm.old_actor_devices[0].seat_role = pb.ActorDeviceIdentity.SLAVE
        cases.append((role, "arm mismatch"))
        label = copy.deepcopy(baseline)
        label[0].arm.expected_window.application_label.application = b"other"
        cases.append((label, "arm mismatch"))
        tuple_case = copy.deepcopy(baseline)
        tuple_case[1].observer_press.event.source.device_id += 1
        cases.append((tuple_case, "observer device/source lifetime mismatch"))
        time_case = copy.deepcopy(baseline)
        time_case[1].observer_press.callback_monotonic_ns = 999
        cases.append((time_case, "observer outside arm interval"))
        for records, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(ObservationError) as caught:
                    self.validate(self.artifact_from(records))
                self.assertEqual(str(caught.exception), reason)

    def test_exact_capacity_and_no_partial_output(self):
        valid, _, _ = self.complete()
        size = len(valid)
        self.assertGreater(size, 0)
        exact = self.produce("capacity-exact", size)
        self.assertEqual(exact.returncode, 0, exact.stderr)
        self.assertEqual(exact.stdout, valid)
        short = self.produce("capacity-short", size - 1)
        self.assertEqual(short.returncode, 0, short.stderr)
        self.assertEqual(short.stdout, b"")


if __name__ == "__main__":
    unittest.main()
