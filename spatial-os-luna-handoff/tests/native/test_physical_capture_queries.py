"""Kernel-origin and XI property checks on an isolated qualified native server."""
import fcntl
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest

PROBE = Path(sys.argv.pop(1)).resolve()
import test_security_identity as native
import compositor_native_pb2 as compositor
import security_pb2 as security


class PhysicalCaptureQueryTests(native.SecurityIdentityTests):
    def test_native_removed_device_errors_and_capture_preflight(self):
        self.start()
        grant = self.supervisor.issue(security.COMPOSITOR, b'', b'')
        config = compositor.NativeCompositorConfig(protocol_version=1, display_number=self.display,
            compositor_cookie=grant.cookie, server_id=self.supervisor.inspect().server_id,
            server_pid=self.server.pid, server_uid=os.getuid())
        fd = os.memfd_create('physical-query-native', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        try:
            os.write(fd, config.SerializeToString())
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS,
                fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
            result = subprocess.run([str(PROBE), str(fd)], pass_fds=(fd,),
                capture_output=True, text=True, timeout=20)
        finally:
            os.close(fd)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(re.findall(r'^PASS (.+)$', result.stdout, re.M), [
            'removed_device_query', 'unrelated_error_forwarded', 'prior_error_preserved',
            'bounded_property_payload', 'invalid_sources_before_capture'])
        for marker in ('AddressSanitizer', 'LeakSanitizer', 'runtime error:'):
            self.assertNotIn(marker, result.stderr)


if __name__ == '__main__':
    suite = unittest.TestSuite([PhysicalCaptureQueryTests('test_native_removed_device_errors_and_capture_preflight')])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
