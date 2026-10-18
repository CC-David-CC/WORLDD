"""Exercise production named and owned ProcessScope cgroups in a delegated runtime."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

PROBE=Path(sys.argv.pop(1)).resolve()
RELATIVE=Path('/proc/self/cgroup').read_text().strip().split('::',1)[1]
ROOT=Path('/sys/fs/cgroup')/RELATIVE.lstrip('/')
assert ROOT.stat().st_uid==os.getuid()

class NamedScopeRegression(unittest.TestCase):
    def test_named_collision_drains_and_owned_reuses(self):
        with tempfile.TemporaryDirectory() as temp:
            root=ROOT/('named-scope-'+Path(temp).name)
            root.mkdir()
            try:
                result=subprocess.run([str(PROBE),str(root)],capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn('named_collision_preserved=1 drain_callback=1 named_removed=1 recovery32=1 owner_exit=1 owned_reused=1',result.stdout)
                owned=root/'authority-broker-0123456789abcdef0123456789abcdef'
                self.assertTrue(owned.is_dir(), 'owned broker cgroup should remain reusable')
                self.assertIn('populated 0', (owned/'cgroup.events').read_text())
                self.assertEqual((owned/'cgroup.procs').read_text().strip(), '')
                owned.rmdir()
                self.assertEqual([entry for entry in root.iterdir() if entry.is_dir()],[])
            finally:
                for child in list(root.iterdir()):
                    if child.is_dir():
                        (child/'cgroup.kill').write_text('1\n')
                        deadline=time.monotonic()+3
                        while 'populated 1' in (child/'cgroup.events').read_text():
                            if time.monotonic()>=deadline:
                                raise AssertionError('fixture child cgroup did not drain during cleanup')
                            time.sleep(.01)
                        child.rmdir()
                root.rmdir()

if __name__=='__main__': unittest.main(verbosity=2)
