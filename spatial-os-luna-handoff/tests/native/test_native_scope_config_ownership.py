"""The NativeScope caller retains its sealed configuration descriptor."""
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import unittest

WITNESS = str(Path(sys.argv.pop(1)).resolve())
SOURCE = Path(sys.argv.pop(1)).resolve()
sys.path.insert(0, str(SOURCE / 'tests/native'))
import native_scope_pb2 as scope
import security_pb2 as security
import test_security_identity as native


class NativeConfigOwnershipTests(native.SecurityIdentityTests):
    def test_caller_owned_configuration_and_reused_descriptor_survive_native_scope(self):
        self.start()
        config = scope.NativeScopeConfig(protocol_version=1, display_number=self.display,
            supervisor_cookie=self.cookie, server_id=self.supervisor.inspect().server_id,
            server_pid=self.server.pid, server_uid=os.getuid(), launch_id=os.urandom(32), role=security.BROKER)
        fd = os.memfd_create('native-config-ownership-witness', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        try:
            os.write(fd, config.SerializeToString())
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
            run = subprocess.run([WITNESS, str(fd)], pass_fds=(fd,), capture_output=True, text=True, timeout=10)
        finally:
            os.close(fd)
        print(run.stdout, end='', flush=True)
        print(run.stderr, end='', flush=True)
        self.assertEqual(run.returncode, 0, 'NativeScope must borrow its caller-owned sealed descriptor')

    def rejected_config(self, mode):
        # The seal check precedes native connection and does not require a server.
        fd = os.memfd_create('native-rejected-config-witness', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        try:
            os.write(fd, b'not a sealed configuration')
            run = subprocess.run([WITNESS, str(fd), mode], pass_fds=(fd,), capture_output=True, text=True, timeout=10)
        finally:
            os.close(fd)
        print(run.stdout, end='', flush=True); print(run.stderr, end='', flush=True)
        self.assertEqual(run.returncode, 0, 'Rejected configuration descriptor ownership differs from the public interface')

    def test_native_scope_rejection_preserves_caller_configuration(self):
        self.rejected_config('scope-reject')

    def test_native_launch_rejection_still_consumes_configuration(self):
        self.rejected_config('launch-reject')


if __name__ == '__main__':
    suite = unittest.TestSuite(NativeConfigOwnershipTests(name) for name in NativeConfigOwnershipTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
