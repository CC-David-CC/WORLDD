"""Exercise production pre-prune cleanup only on authorized runtime hosts."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest
import uuid

PROBE=Path(sys.argv.pop(1)).resolve()
ONLY_PARTICIPANT_MODE=None
if len(sys.argv)>1 and sys.argv[1]=='--only-participant-mode':
    if len(sys.argv)<3:
        raise SystemExit('--only-participant-mode requires a mode')
    ONLY_PARTICIPANT_MODE=sys.argv.pop(2)
    sys.argv.pop(1)
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
            argv=[str(PROBE),str(root)]
            expected='BROKER_COPY_CLEANUP_PASS'
            if ONLY_PARTICIPANT_MODE is not None:
                argv.extend(['--only-participant-mode',ONLY_PARTICIPANT_MODE])
                expected='BROKER_COPY_CLEANUP_SELECTED_PASS'
            try:
                result=subprocess.run(argv,capture_output=True,text=True,timeout=60)
            except subprocess.TimeoutExpired as error:
                def display(value):
                    return value.decode(errors='replace') if isinstance(value,bytes) else (value or '')
                captured_out=display(error.stdout)
                captured_err=display(error.stderr)
                print('probe_timeout_after_seconds=60',file=sys.stderr,flush=True)
                if captured_out:
                    print('probe_partial_stdout_begin',file=sys.stderr,flush=True)
                    print(captured_out,file=sys.stderr,flush=True)
                    print('probe_partial_stdout_end',file=sys.stderr,flush=True)
                if captured_err:
                    print('probe_partial_stderr_begin',file=sys.stderr,flush=True)
                    print(captured_err,file=sys.stderr,flush=True)
                    print('probe_partial_stderr_end',file=sys.stderr,flush=True)
                self.fail('broker cleanup probe exceeded the unchanged 60 second cap; partial output preserved above')
            print(result.stdout,flush=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn(expected,result.stdout)
            self.assertEqual([item for item in root.iterdir() if item.is_dir()],[])
        finally:
            # Never hide a failed participant by deleting its retained evidence.
            if not any(item.is_dir() for item in root.iterdir()):
                root.rmdir()
            else:
                print('Retained failed cgroup evidence: '+str(root),file=sys.stderr,flush=True)

if __name__=='__main__':unittest.main(verbosity=2)
