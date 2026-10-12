"""Fail closed before execution-state mutation when an operator profile is incomplete."""
import fcntl
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
import broker_pb2 as broker

BROKER = Path(sys.argv.pop(1)).resolve()


class ProductionProfileTests(unittest.TestCase):
    def reject(self, arguments, message, pass_fds=()):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)/'uncreated'
            result = subprocess.run([str(BROKER), '--state-dir', str(state), '--production', *arguments],
                                    input=b'', capture_output=True, pass_fds=pass_fds, timeout=8)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(message, result.stderr.decode())
            self.assertFalse(state.exists(), result.stderr.decode())
            for marker in (b'AddressSanitizer', b'LeakSanitizer', b'runtime error:'):
                self.assertNotIn(marker, result.stderr)

    def test_missing_resource_and_storage_handoffs(self):
        self.reject([], 'requires sealed resources and storage ownership')

    def test_mutable_resource_path_cannot_replace_handoff(self):
        self.reject(['--resource-config','/does-not-exist'], 'requires sealed resources and storage ownership')

    def test_fixture_faults_cannot_enter_production(self):
        self.reject(['--resource-config-fd','30','--storage-fd','31','--fixture-faults'],
                    'requires sealed resources and storage ownership')

    def test_migration_cannot_run_as_live_production_broker(self):
        self.reject(['--resource-config-fd','30','--storage-fd','31','--migrate-v3'],
                    'requires sealed resources and storage ownership')

    def test_unsealed_policy_denied_before_state_mutation(self):
        fd = os.memfd_create('unsealed-policy', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        left, right = socket.socketpair()
        try:
            os.write(fd, broker.BrokerResourceConfig(protocol_version=2).SerializeToString())
            self.reject(['--resource-config-fd',str(fd),'--storage-fd',str(left.fileno())],
                        'owned sealed protobuf', (fd,left.fileno()))
        finally: left.close(); right.close(); os.close(fd)

    def test_legacy_policy_denied_before_cgroup_or_state_mutation(self):
        fd = os.memfd_create('legacy-policy', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        left, right = socket.socketpair()
        try:
            os.write(fd, broker.BrokerResourceConfig(protocol_version=1).SerializeToString())
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
            self.reject(['--resource-config-fd',str(fd),'--storage-fd',str(left.fileno())],
                        'aggregate resource envelope', (fd,left.fileno()))
        finally: left.close(); right.close(); os.close(fd)


if __name__ == '__main__': unittest.main()
