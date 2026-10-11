"""Preserve loaded-module evidence for the unchanged native startup regression."""
import os
from pathlib import Path
import subprocess
import unittest
import test_security_identity as native


class StartupDiagnostic(native.SecurityIdentityTests):
    def start(self, *args, **kwargs):
        result = super().start(*args, **kwargs)
        target = Path(os.environ['SPATIAL_DIAGNOSTIC_OUTPUT'])
        target.mkdir(parents=True, exist_ok=False)
        (target / 'maps.txt').write_bytes(Path('/proc/' + str(self.server.pid) + '/maps').read_bytes())
        (target / 'pid.txt').write_text(str(self.server.pid) + '\n')
        with (target / 'mesa-packages.txt').open('wb') as output:
            subprocess.run(['dpkg-query', '-W', 'libgl1-mesa-dri', 'libglx-mesa0', 'libllvm*'],
                           stdout=output, stderr=subprocess.STDOUT, check=False)
        return result


if __name__ == '__main__':
    suite = unittest.TestSuite([StartupDiagnostic('test_bootstrap_binds_supervisor_before_native_requests')])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
