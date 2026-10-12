"""Reject forged packets and invalid descriptors at the production coordinator receiver."""
from pathlib import Path
import subprocess
import sys
import unittest
import launch_pb2 as pb
import native_scope_pb2 as native
import process_scope_pb2 as process
import issuer_pb2 as identity
import security_pb2 as security
from harness.wire import frame

PROBE = Path(sys.argv.pop(1)).resolve()


class ReceiverTests(unittest.TestCase):
    def run_case(self, mode, success=False):
        expected = pb.AuthorityLaunchRecord(schema_version=2, revision=3, stage=pb.START_ATTEMPTED,
            binding=pb.AuthorityLaunchBinding(supervised=True),
            process=process.ProcessScopeIdentity(launch_id=b'l' * 32),
            native=native.NativeScopeIdentity(launch_id=b'l' * 32, role=security.BROKER),
            authorization=identity.AuthorityLaunchAuthorization(authorization_id=b'a' * 32))
        request = pb.AuthorityChildHandleRequest(protocol_version=1, request_id=1, expected=expected)
        result = subprocess.run([str(PROBE), str(Path(__file__).with_name('child_handle_witness.py')), mode],
            input=frame(request), capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0 if success else 2, result.stderr.decode(errors='replace'))
        self.assertEqual(result.stdout, b'')
        for marker in (b'AddressSanitizer', b'LeakSanitizer', b'runtime error:', b'leaked a descriptor'):
            self.assertNotIn(marker, result.stderr)

    def test_valid_live_direct_child(self): self.run_case('valid', True)
    def test_wrong_request(self): self.run_case('wrong-request')
    def test_wrong_launch(self): self.run_case('wrong-launch')
    def test_wrong_version(self): self.run_case('version')
    def test_error_cannot_carry_rights(self): self.run_case('error-rights')
    def test_plain_file_is_not_process_handle(self): self.run_case('plain-file')
    def test_foreign_parent_rejected(self): self.run_case('wrong-parent')
    def test_dead_handle_rejected(self): self.run_case('dead')
    def test_timeout(self): self.run_case('timeout')
    def test_unknown_field(self): self.run_case('unknown')
    def test_malformed_packet(self): self.run_case('malformed')
    def test_oversize_packet(self): self.run_case('oversize')
    def test_duplicate_descriptors(self): self.run_case('duplicate')
    def test_missing_descriptor(self): self.run_case('missing')
    def test_foreign_sender(self): self.run_case('wrong-sender')


if __name__ == '__main__': unittest.main(verbosity=2)
