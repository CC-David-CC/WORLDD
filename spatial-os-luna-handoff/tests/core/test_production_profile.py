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
        self.reject([], 'requires sealed resources, storage ownership, and cleanup bootstrap')

    def test_mutable_resource_path_cannot_replace_handoff(self):
        self.reject(['--resource-config','/does-not-exist'], 'requires sealed resources, storage ownership, and cleanup bootstrap')

    def test_fixture_faults_cannot_enter_production(self):
        self.reject(['--resource-config-fd','30','--storage-fd','31','--fixture-faults'],
                    'requires sealed resources, storage ownership, and cleanup bootstrap')

    def test_migration_cannot_run_as_live_production_broker(self):
        self.reject(['--resource-config-fd','30','--storage-fd','31','--migrate-v3'],
                    'requires sealed resources, storage ownership, and cleanup bootstrap')

    def native_security_fd(self):
        fd = os.memfd_create('native-launch-config', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        config = broker.NativeLaunchConfig(protocol_version=1, display_number=0,
            broker_cookie=b'n'*32, server_id=b's'*32, server_pid=os.getpid(), server_uid=os.getuid())
        os.write(fd, config.SerializeToString())
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS,
                    fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
        return fd

    def production_handoff_descriptors(self):
        storage, storage_peer = socket.socketpair()
        cleanup, cleanup_peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        cleanup.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        native = self.native_security_fd()
        return storage, storage_peer, cleanup, cleanup_peer, native

    def test_unsealed_policy_denied_before_state_mutation(self):
        fd = os.memfd_create('unsealed-policy', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        storage, storage_peer, cleanup, cleanup_peer, native = self.production_handoff_descriptors()
        passed = (fd, storage.fileno(), cleanup.fileno(), native)
        arguments = ['--resource-config-fd',str(fd),'--storage-fd',str(storage.fileno()),
                     '--cleanup-bootstrap-fd',str(cleanup.fileno()),'--native-security-fd',str(native)]
        try:
            os.write(fd, broker.BrokerResourceConfig(protocol_version=2).SerializeToString())
            self.reject(arguments, 'owned sealed protobuf', passed)
        finally:
            storage.close(); storage_peer.close(); cleanup.close(); cleanup_peer.close()
            os.close(native); os.close(fd)

    def test_legacy_policy_denied_before_cgroup_or_state_mutation(self):
        fd = os.memfd_create('legacy-policy', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        storage, storage_peer, cleanup, cleanup_peer, native = self.production_handoff_descriptors()
        passed = (fd, storage.fileno(), cleanup.fileno(), native)
        arguments = ['--resource-config-fd',str(fd),'--storage-fd',str(storage.fileno()),
                     '--cleanup-bootstrap-fd',str(cleanup.fileno()),'--native-security-fd',str(native)]
        try:
            os.write(fd, broker.BrokerResourceConfig(protocol_version=1).SerializeToString())
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
            self.reject(arguments, 'aggregate resource envelope', passed)
        finally:
            storage.close(); storage_peer.close(); cleanup.close(); cleanup_peer.close()
            os.close(native); os.close(fd)


if __name__ == '__main__': unittest.main()
