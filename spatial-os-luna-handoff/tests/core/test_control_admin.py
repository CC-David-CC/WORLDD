"""Exercise the production client against independent OS-process packet witnesses."""
from pathlib import Path
import struct
import subprocess
import sys
import unittest
import broker_pb2 as pb
import core_pb2 as core
import security_pb2 as security

PROBE = Path(sys.argv.pop(1)).resolve()
WITNESS = Path(__file__).with_name('control_admin_witness.py')


class TransportTests(unittest.TestCase):
    def setUp(self):
        owner = pb.ControlOwner(issuer_id=b'i' * 32)
        owner.launch.participant_id = b'p' * 32
        owner.launch.registered_revision = 1
        owner.launch.generation = 2
        owner.launch.service_incarnation = b's' * 32
        owner.launch.launch_id = b'l' * 32
        owner.launch.supervision_revision = 3
        owner.launch.authorization_id = b'a' * 32
        owner.native.launch_id = b'l' * 32
        owner.native.server_id = b'n' * 32
        owner.native.credential_id = b'c' * 32
        owner.native.role = security.COMPOSITOR
        self.request = pb.ControlSupervisorRequest(protocol_version=1, request_id=83, inspect=owner)

    def run_mode(self, mode, accepted=False):
        packet = self.request.SerializeToString()
        result = subprocess.run([str(PROBE), str(WITNESS), mode],
                                input=struct.pack('!I', len(packet)) + packet,
                                capture_output=True, timeout=12)
        for marker in (b'AddressSanitizer', b'LeakSanitizer', b'runtime error:'):
            self.assertNotIn(marker, result.stderr)
        self.assertEqual(result.returncode, 0 if accepted else 2, result.stderr)
        if not accepted:
            self.assertEqual(result.stdout, b'')
            self.assertIn(b'administrative transport rejected:', result.stderr)
            return None
        length, = struct.unpack('!I', result.stdout[:4])
        self.assertEqual(length, len(result.stdout) - 4)
        return pb.ControlSupervisorReply.FromString(result.stdout[4:])

    def test_exact_child_reply(self):
        reply = self.run_mode('valid', True)
        self.assertEqual(reply.state.owner, self.request.inspect)

    def test_remote_error_stays_error(self):
        reply = self.run_mode('error', True)
        self.assertEqual(reply.error.code, core.NOT_FOUND)
        self.assertFalse(reply.HasField('state'))

    def test_other_process_cannot_reply(self): self.run_mode('wrong-sender')
    def test_reply_descriptors_rejected(self): self.run_mode('rights')
    def test_oversize_rejected(self): self.run_mode('oversize')
    def test_malformed_rejected(self): self.run_mode('malformed')
    def test_wrong_request_rejected(self): self.run_mode('correlation')
    def test_wrong_owner_rejected(self): self.run_mode('owner')
    def test_wrong_version_rejected(self): self.run_mode('version')
    def test_child_exit_is_uncertain(self): self.run_mode('exit')
    def test_reply_timeout_is_uncertain(self): self.run_mode('timeout')


    def test_async_exact_child_reply(self):self.run_mode('async-valid',True)
    def test_async_delayed_reply_keeps_polls_bounded_and_stream_owned(self):self.run_mode('async-delay',True)
    def test_async_remote_error_stays_error(self):
        reply=self.run_mode('async-error',True);self.assertEqual(reply.error.code,core.NOT_FOUND)
    def test_async_other_process_cannot_reply(self):self.run_mode('async-wrong-sender')
    def test_async_reply_descriptors_rejected(self):self.run_mode('async-rights')
    def test_async_oversize_rejected(self):self.run_mode('async-oversize')
    def test_async_malformed_rejected(self):self.run_mode('async-malformed')
    def test_async_wrong_request_rejected(self):self.run_mode('async-correlation')
    def test_async_wrong_owner_rejected(self):self.run_mode('async-owner')
    def test_async_wrong_version_rejected(self):self.run_mode('async-version')
    def test_async_child_exit_is_uncertain(self):self.run_mode('async-exit')
    def test_async_polling_keeps_original_absolute_deadline(self):self.run_mode('async-timeout')
    def test_async_fence_uses_checked_owner_and_receipt(self):
        owner=pb.ControlOwner();owner.CopyFrom(self.request.inspect)
        self.request.ClearField('inspect');self.request.fence.CopyFrom(owner)
        reply=self.run_mode('async-valid',True);self.assertTrue(reply.state.channel_fenced)
    def test_async_registration_is_rejected_before_descriptorless_send(self):
        owner=pb.ControlOwner();owner.CopyFrom(self.request.inspect)
        self.request.ClearField('inspect');getattr(self.request,'register').CopyFrom(owner)
        self.run_mode('async-valid')


if __name__ == '__main__':
    unittest.main(verbosity=2)
