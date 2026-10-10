"""Use a disposable delegated service and real cgroup/process handles."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PROBE = Path(sys.argv.pop(1)).resolve()
RELATIVE = Path('/proc/self/cgroup').read_text().strip().split('::', 1)[1]
ROOT = Path('/sys/fs/cgroup') / RELATIVE.lstrip('/')
assert RELATIVE != '/' and ROOT.stat().st_uid == os.getuid()


class HandleTests(unittest.TestCase):
    def check_scenario(self, scenario):
        with tempfile.TemporaryDirectory() as temporary:
            root = ROOT / Path(temporary).name
            root.mkdir()
            try:
                result = subprocess.run([str(PROBE), str(root), scenario], capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
                for marker in (b'AddressSanitizer', b'LeakSanitizer', b'runtime error:'):
                    self.assertNotIn(marker, result.stderr)
                self.assertEqual(list(root.iterdir()), [], 'Production cleanup left a scope')
            finally:
                # Cleanup failure stays a failure; never hide a populated leftover scope.
                root.rmdir()

    def test_prepared_scope_rejects_handle(self): self.check_scenario('prepared')
    def test_live_handle_preserves_original_and_observes_exit(self): self.check_scenario('live')
    def test_stopped_scope_rejects_handle(self): self.check_scenario('stopped')
    def test_exited_child_rejects_handle(self): self.check_scenario('exited')
    def test_surviving_descendant_does_not_replace_direct_child(self): self.check_scenario('descendant')


if __name__ == '__main__':
    unittest.main(verbosity=2)
