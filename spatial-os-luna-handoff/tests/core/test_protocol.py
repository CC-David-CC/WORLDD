"""Independent binary-wire and geometry boundary cases."""
import io
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import core_pb2 as pb

BINARY = str(Path(sys.argv.pop(1)).resolve())

def raw_frame(payload):
    return struct.pack('!I', len(payload)) + payload

def pack(request):
    return raw_frame(request.SerializeToString())

def hello(ident=1):
    request = pb.Request(protocol_version=1, request_id=ident)
    request.hello.SetInParent()
    return request

def motion():
    request = pb.Request(protocol_version=1, request_id=1)
    request.integrate.pose.position.SetInParent()
    request.integrate.pose.orientation.w = 1
    request.integrate.linear.SetInParent()
    request.integrate.angular.SetInParent()
    request.integrate.seconds = 0
    return request

class ProtocolBoundaryTests(unittest.TestCase):
    def exchange(self, frames, exit_code=0):
        with tempfile.TemporaryDirectory() as root:
            run = subprocess.run([BINARY, '--state-dir', root], input=frames,
                                 capture_output=True, timeout=5)
        self.assertEqual(run.returncode, exit_code, run.stderr)
        data, rows = io.BytesIO(run.stdout), []
        while data.tell() < len(run.stdout):
            header = data.read(4)
            self.assertEqual(len(header), 4, 'Truncated reply header')
            length, = struct.unpack('!I', header)
            self.assertLessEqual(length, 1024 * 1024)
            payload = data.read(length)
            self.assertEqual(len(payload), length, 'Truncated reply payload')
            reply = pb.Reply()
            reply.ParseFromString(payload)
            self.assertEqual(reply.protocol_version, 1)
            self.assertIsNotNone(reply.WhichOneof('outcome'))
            rows.append(reply)
        return rows
    def test_malformed_payload_recovers_at_next_frame(self):
        for payload in [b'\xff', b'\x00', b'\x52\x05x']:
            with self.subTest(payload=payload):
                rows = self.exchange(raw_frame(payload) + pack(hello(29)))
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0].error.code, pb.INVALID_ARGUMENT)
                self.assertEqual(rows[0].request_id, 0)
                self.assertEqual(rows[1].request_id, 29)
                self.assertEqual(rows[1].hello.implementation, 'production')
    def test_integer_ids_preserve_precision(self):
        ids = [0, 1, 2**63 - 1, 2**64 - 1]
        rows = self.exchange(b''.join(pack(hello(i)) for i in ids))
        self.assertEqual([r.request_id for r in rows], ids)
    def test_oversized_frame_closes_before_body_allocation(self):
        self.assertEqual(self.exchange(struct.pack('!I', 1024 * 1024 + 1), exit_code=2), [])
    def test_truncated_frames_are_fatal(self):
        for data in [b'\x00', b'\x00\x00\x00', struct.pack('!I', 5) + b'x']:
            with self.subTest(data=data):
                self.assertEqual(self.exchange(data, exit_code=2), [])
    def test_unknown_fields_rejected_at_both_levels(self):
        for payload in [hello().SerializeToString() + b'\xa0\x06\x01',
                        b'\x08\x01\x10\x01\x52\x03\xa0\x06\x01']:
            reply, = self.exchange(raw_frame(payload))
            self.assertEqual(reply.error.code, pb.INVALID_ARGUMENT)
    def test_wrong_wire_type_is_rejected(self):
        reply, = self.exchange(raw_frame(b'\x09' + b'\x00' * 8 + b'\x52\x00'))
        self.assertEqual(reply.error.code, pb.INVALID_ARGUMENT)
    def test_version_and_unknown_operation(self):
        request = hello()
        request.protocol_version = 2
        reply, = self.exchange(pack(request))
        self.assertEqual(reply.error.code, pb.UNSUPPORTED_VERSION)
        reply, = self.exchange(pack(pb.Request(protocol_version=1, request_id=19)))
        self.assertEqual(reply.request_id, 19)
        self.assertEqual(reply.error.code, pb.UNKNOWN_METHOD)
    def test_nonfinite_numbers_rejected_on_wire(self):
        for value in [float('nan'), float('inf'), -float('inf')]:
            request = motion()
            request.integrate.linear.x = value
            reply, = self.exchange(pack(request))
            self.assertEqual(reply.error.code, pb.INVALID_ARGUMENT)
    def test_missing_motion_fields_rejected(self):
        for field in ['pose', 'linear', 'angular', 'seconds']:
            request = motion()
            request.integrate.ClearField(field)
            reply, = self.exchange(pack(request))
            self.assertEqual(reply.error.code, pb.INVALID_ARGUMENT)
    def test_small_angular_rate_preserves_linear_motion(self):
        request = motion()
        request.integrate.linear.x = 3
        request.integrate.angular.y = 1e-12
        request.integrate.seconds = 2
        reply, = self.exchange(pack(request))
        self.assertEqual(reply.WhichOneof('outcome'), 'pose')
        self.assertAlmostEqual(reply.pose.pose.position.x, 6, places=10)
        self.assertAlmostEqual(reply.pose.pose.position.z, -6e-12, places=20)
    def test_motion_overflow_is_not_success(self):
        request = motion()
        request.integrate.linear.x = 1e308
        request.integrate.seconds = 1e308
        reply, = self.exchange(pack(request))
        self.assertEqual(reply.error.code, pb.INVALID_ARGUMENT)
    def test_world_get_has_no_fake_success(self):
        for ident, code in [('', pb.INVALID_ARGUMENT), ('room-a', pb.UNIMPLEMENTED)]:
            request = pb.Request(protocol_version=1, request_id=1)
            request.world_get.id = ident
            reply, = self.exchange(pack(request))
            self.assertEqual(reply.error.code, code)
    def test_state_directory_must_be_absolute(self):
        run = subprocess.run([BINARY, '--state-dir', 'relative'], capture_output=True, timeout=5)
        self.assertEqual(run.returncode, 2)
        self.assertEqual(run.stdout, b'')

if __name__ == '__main__':
    unittest.main(verbosity=2)
