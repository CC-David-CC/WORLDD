"""Exercise production pre-prune cleanup only on authorized runtime hosts."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest
import uuid

PROBE=Path(sys.argv.pop(1)).resolve()
class BrokerCopyCleanupTests(unittest.TestCase):
    def test_real_preprune_journal_and_scope_boundaries(self):
        self.assertIn(socket.gethostname().split('.')[0], ('sb16','sb16-2'))
        self.assertNotEqual(os.getuid(),0)
        relative=Path('/proc/self/cgroup').read_text().strip().split('::',1)[1]
        current=Path('/sys/fs/cgroup')/relative.lstrip('/')
        parent=current.parent
        required={'cpu','memory','pids','io'}
        self.assertTrue(required.issubset((parent/'cgroup.controllers').read_text().split()))
        (parent/'cgroup.subtree_control').write_text('+cpu +memory +pids +io\n')
        root=parent/('copy-preprune-'+uuid.uuid4().hex)
        root.mkdir()
        try:
            (root/'cgroup.subtree_control').write_text('+cpu +memory +pids +io\n')
            result=subprocess.run([str(PROBE),str(root)],capture_output=True,text=True,timeout=60)
            print(result.stdout,flush=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('BROKER_COPY_CLEANUP_PASS',result.stdout)
            self.assertEqual([item for item in root.iterdir() if item.is_dir()],[])
        finally:
            # Never hide a failed participant by deleting its retained evidence.
            if not any(item.is_dir() for item in root.iterdir()):
                root.rmdir()
            else:
                print('Retained failed cgroup evidence: '+str(root),file=sys.stderr,flush=True)

if __name__=='__main__':unittest.main(verbosity=2)
