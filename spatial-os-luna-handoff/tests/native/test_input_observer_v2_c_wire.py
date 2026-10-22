"""Pure C/Python v2 wire fixture; never native acceptance evidence."""
import os
import subprocess
import unittest
from pathlib import Path

import native_input_observer_v2_pb2 as pb
import test_input_state_observer_v2 as controls
import input_state_observer_v2 as parser


EXIT_OK = 0
EXIT_ROLE = 3
EXIT_FRAME = 4
EXIT_UNPACK = 5
EXIT_TRAILING = 6
EXIT_REJECTED = 7


class CWireInterop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = Path(os.environ["OBSERVER_V2_FIXTURE"])

    def run_fixture(self, args=(), wire=b"", expected=EXIT_OK):
        result = subprocess.run(
            [str(self.exe), *args],
            input=wire,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, expected, result.stderr.decode(errors="replace"))
        diagnostic = result.stderr.decode(errors="replace")
        for marker in ("AddressSanitizer", "LeakSanitizer", "UndefinedBehaviorSanitizer",
                       "runtime error:"):
            self.assertNotIn(marker, diagnostic)
        return result

    @staticmethod
    def frame(message):
        raw = message.SerializeToString(deterministic=True)
        length = len(raw)
        prefix = bytearray()
        while length > 127:
            prefix.append((length & 127) | 128)
            length >>= 7
        prefix.append(length)
        return bytes(prefix) + raw

    @classmethod
    def three_record_wire(cls, records):
        return b"".join(cls.frame(record) for record in records)

    def test_initialized_c_vector_matches_independent_python_bytes(self):
        c_wire = self.run_fixture(("--construct",)).stdout

        sid = b"session-vector1\x00"
        aid = b"arm-vector-ident"
        expected = []
        arm = pb.ObserverArmV2(session_id=sid, arm_id=aid)
        expected.append(self._record(1, sid, aid, "arm", pb.Arm(accepted_command=arm)))

        transition = pb.NativeTransition()
        for phase in (pb.PRE_CANCEL, pb.POST_RELEASES_BEFORE_CONTEXT_CLEAR,
                      pb.POST_CANCEL, pb.POST_PEER_BIND):
            transition.snapshots.add(phase=phase, complete=True)
        expected.append(self._record(2, sid, aid, "native_transition", transition))
        press = pb.ObserverPress(observer_ordinal=1)
        expected.append(self._record(3, sid, aid, "observer_press", press))
        self.assertEqual(c_wire, self.three_record_wire(expected))

    @staticmethod
    def _record(sequence, sid, aid, payload, value):
        record = pb.NativeInputObservationRecordV2(
            protocol_version=2, session_id=sid, arm_id=aid,
            stream_sequence=sequence, native_session_epoch=9)
        getattr(record, payload).CopyFrom(value)
        return record

    def test_python_records_roundtrip_and_independent_fourth_receipt(self):
        sample = controls.fixture()
        wire = self.three_record_wire(sample.records[:3])
        c_wire = self.run_fixture(wire=wire).stdout
        self.assertEqual(c_wire, wire)

        # The independent probe alone constructs the fourth receipt batch.
        artifact = c_wire + self.frame(sample.records[3])
        records = parser.decode_records(artifact)
        self.assertEqual(
            parser.validate_records(
                records,
                expected_session_id=controls.SID,
                expected_arm_id=controls.AID,
                expected_native_session_epoch=controls.NATIVE,
                expected_arm=sample.arm,
                expected_bindings=sample.bindings,
            ),
            sample.observed,
        )

    def test_checked_native_role_map(self):
        for native, wire in ((1, pb.ClientIdentity.APPLICATION),
                             (2, pb.ClientIdentity.SUPERVISOR),
                             (3, pb.ClientIdentity.COMPOSITOR)):
            result = self.run_fixture(("--map-role", str(native)))
            self.assertEqual(int(result.stdout), wire)
        for native in ("0", "4", "-1", "99", "not-a-role"):
            self.run_fixture(("--map-role", native), expected=EXIT_ROLE)

    def test_direct_constructed_malformed_message_graphs_reject_cleanly(self):
        cases = (
            "null-session-data", "null-arm-data", "null-command-data",
            "null-snapshots-array", "null-snapshot", "wrong-record-descriptor",
            "wrong-arm-descriptor", "wrong-snapshot-descriptor",
            "missing-payload", "missing-accepted-command", "nested-null-bytes",
        )
        for case in cases:
            with self.subTest(case=case):
                self.run_fixture(("--direct-negative", case))

    def test_wrong_order_ids_epochs_and_output_bounds_have_stable_codes(self):
        baseline = controls.fixture()
        baseline_wire = self.three_record_wire(baseline.records[:3])
        self.assertEqual(self.run_fixture(wire=baseline_wire).returncode, EXIT_OK)

        for mutate in (
            lambda records: setattr(records[1], "stream_sequence", 7),
            lambda records: setattr(records[2], "session_id", b"x" * 16),
            lambda records: setattr(records[2], "arm_id", b"x" * 16),
            lambda records: setattr(records[2], "native_session_epoch", 10),
            lambda records: records[1].native_transition.snapshots.pop(),
        ):
            sample = controls.fixture()
            records = sample.records[:3]
            mutate(records)
            self.run_fixture(wire=self.three_record_wire(records),
                             expected=EXIT_REJECTED)

        capacity_sample = controls.fixture()
        capacity_wire = self.three_record_wire(capacity_sample.records[:3])
        self.run_fixture(("--capacity", "0"), capacity_wire, EXIT_REJECTED)

        sink_sample = controls.fixture()
        sink_wire = self.three_record_wire(sink_sample.records[:3])
        self.run_fixture(("--sink-fail",), sink_wire, EXIT_REJECTED)

    def test_framing_and_unpack_failures_after_prior_allocations_cleanup(self):
        sample = controls.fixture()
        frames = [self.frame(record) for record in sample.records[:3]]

        self.run_fixture(wire=frames[0] + b"\x05ab", expected=EXIT_FRAME)
        self.run_fixture(wire=frames[0] + frames[1] + b"\x05ab",
                         expected=EXIT_FRAME)
        oversized = b"\x81\x20"  # Canonical varint for 4097.
        self.run_fixture(wire=frames[0] + oversized, expected=EXIT_FRAME)
        self.run_fixture(wire=frames[0] + frames[1] + oversized,
                         expected=EXIT_FRAME)
        self.run_fixture(wire=frames[0] + b"\x01\xff", expected=EXIT_UNPACK)
        self.run_fixture(wire=b"\x80" * 12, expected=EXIT_FRAME)
        self.run_fixture(wire=b"".join(frames) + b"x", expected=EXIT_TRAILING)


if __name__ == "__main__":
    unittest.main()
