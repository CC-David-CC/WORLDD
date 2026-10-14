"""Check real compositor GLX contexts against the authenticated native server."""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import unittest
import test_security_identity as native

GLX_PROBE = Path(sys.argv.pop(1)).resolve()


class GlxLifecycleTests(native.SecurityIdentityTests):
    def test_compositor_contexts_render_and_release(self):
        supervisor = self.start()
        credential = supervisor.issue(native.pb.COMPOSITOR, b'', b'')
        authority = self.root / 'compositor.Xauthority'
        values = (b'', str(self.display).encode(), native.AUTH, credential.cookie)
        authority.write_bytes(struct.pack('!H', 65535) + b''.join(
            struct.pack('!H', len(value)) + value for value in values))
        authority.chmod(0o600)
        # The server uses the candidate EGL vendor. The client uses installed GLX.
        env = {**os.environ, 'DISPLAY': ':' + str(self.display),
               'XAUTHORITY': str(authority), 'LIBGL_ALWAYS_SOFTWARE': '1'}
        for key in ('LD_LIBRARY_PATH', '__EGL_VENDOR_LIBRARY_FILENAMES', 'LD_DEBUG'):
            env.pop(key, None)
        result = subprocess.run([str(GLX_PROBE), '32'], env=env,
                                capture_output=True, timeout=30)
        output = Path(os.environ['SPATIAL_SECURITY_EVIDENCE'])
        output.mkdir(parents=True, exist_ok=True)
        (output / 'glx-client.stdout').write_bytes(result.stdout)
        (output / 'glx-client.stderr').write_bytes(result.stderr)
        (output / 'glx-client.json').write_text(json.dumps({
            'command': [str(GLX_PROBE), '32'], 'exit_code': result.returncode,
            'client_driver': 'installed GLX; private server EGL override removed',
            'cycles': 32}, indent=2) + '\n')
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertIn(b'cycles=32 pixel_checks=32 PASS', result.stdout)


if __name__ == '__main__':
    suite = unittest.TestSuite([
        GlxLifecycleTests('test_compositor_contexts_render_and_release')])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
