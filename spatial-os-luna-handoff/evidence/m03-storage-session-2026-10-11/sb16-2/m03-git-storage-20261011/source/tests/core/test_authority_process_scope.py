"""Run inside a disposable delegated user service. Observe real kernel process exits."""
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import process_scope_pb2 as pb
from harness.wire import frame, read_message

PROBE = Path(sys.argv.pop(1)).resolve()
RELATIVE = Path('/proc/self/cgroup').read_text().strip().split('::', 1)[1]
ROOT = Path('/sys/fs/cgroup') / RELATIVE.lstrip('/')
assert RELATIVE != '/' and ROOT.stat().st_uid == os.getuid()


class ProcessScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.files = Path(self.temp.name)
        def preserve():
            if os.environ.get('SPATIAL_SCOPE_EVIDENCE'):
                target = Path(os.environ['SPATIAL_SCOPE_EVIDENCE'])/self._testMethodName
                target.mkdir(parents=True, exist_ok=True)
                for log in self.files.glob('*.log'): shutil.copy2(log, target/log.name)
        self.addCleanup(preserve)
        self.root = ROOT / ('test-' + self.files.name)
        self.root.mkdir()
        self.addCleanup(self.cleanup_scopes)

    def cleanup_scopes(self):
        for scope in self.root.iterdir():
            if not scope.is_dir(): continue
            (scope/'cgroup.kill').chmod(0o600)
            (scope/'cgroup.kill').write_text('1\n')
            deadline = time.monotonic()+3
            while 'populated 1' in (scope/'cgroup.events').read_text():
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.01)
            scope.rmdir()
        self.root.rmdir()

    def probe(self, mode='normal', root=None):
        log = (self.files/(mode+str(time.monotonic_ns())+'.log')).open('wb')
        self.addCleanup(log.close)
        proc = subprocess.Popen([str(PROBE), str(root or self.root), mode],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log)
        def cleanup():
            if proc.poll() is None:
                proc.stdin.close()
                try: proc.wait(timeout=3)
                except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=3)
            if not proc.stdin.closed: proc.stdin.close()
            proc.stdout.close()
        self.addCleanup(cleanup)
        return proc

    def call(self, proc, **operation):
        request = pb.ProcessScopeRequest(protocol_version=1, request_id=77, **operation)
        proc.stdin.write(frame(request)); proc.stdin.flush()
        self.assertTrue(select.select([proc.stdout], [], [], 5)[0], 'Scope reply deadline')
        reply = read_message(proc.stdout, pb.ProcessScopeReply)
        self.assertEqual(reply.protocol_version, 1)
        self.assertEqual(reply.request_id, 77)
        return reply

    def launch(self, proc, code):
        reply = self.call(proc, launch=pb.AuthorityProcessLaunch(
            executable='/usr/bin/python3', arguments=['-c', code]))
        self.assertEqual(reply.WhichOneof('outcome'), 'state', reply)
        self.assertEqual(len(reply.state.identity.launch_id), 32)
        self.assertEqual(len(reply.state.identity.boot_id), 36)
        scope = self.root/reply.state.identity.scope_name
        self.assertEqual(scope.stat().st_ino, reply.state.identity.inode)
        return reply.state.identity

    def wait_file(self, path):
        deadline = time.monotonic()+3
        while not path.exists():
            self.assertLess(time.monotonic(), deadline, 'Child readiness deadline')
            time.sleep(.005)

    def pins(self, identity):
        pids = [int(p) for p in (self.root/identity.scope_name/'cgroup.procs').read_text().split()]
        result = [os.pidfd_open(p) for p in pids]
        for fd in result: self.addCleanup(os.close, fd)
        return result

    def stop(self, proc):
        reply = self.call(proc, stop=pb.ProcessScopeQuery())
        self.assertEqual(reply.WhichOneof('outcome'), 'stopped', reply)
        return reply.stopped

    def test_detached_descendants_exit_before_receipt(self):
        ready = self.files/'ready'; proc = self.probe()
        identity = self.launch(proc, f'import os,pathlib,time\npid=os.fork()\nif pid==0:\n os.setsid();pathlib.Path({str(ready)!r}).touch()\ntime.sleep(30)')
        self.wait_file(ready); pins = self.pins(identity); self.assertEqual(len(pins), 2)
        receipt = self.stop(proc)
        self.assertEqual(receipt.identity, identity)
        self.assertEqual(len(select.select(pins, [], [], 0)[0]), 2)
        self.assertFalse((self.root/identity.scope_name).exists())
        self.assertEqual(self.stop(proc), receipt)

    def test_leader_exit_does_not_hide_live_detached_child(self):
        ready = self.files/'ready'; proc = self.probe()
        identity = self.launch(proc, f'import os,pathlib,time\npid=os.fork()\nif pid: os._exit(0)\nos.setsid();pathlib.Path({str(ready)!r}).touch();time.sleep(30)')
        self.wait_file(ready)
        deadline = time.monotonic()+3
        while True:
            state = self.call(proc, state=pb.ProcessScopeQuery()).state
            if state.direct_child_exited: break
            self.assertLess(time.monotonic(), deadline); time.sleep(.005)
        self.assertTrue(state.populated)
        pins = self.pins(identity); self.assertGreaterEqual(len(pins), 1)
        self.stop(proc)
        self.assertEqual(len(select.select(pins, [], [], 0)[0]), len(pins))

    def test_stop_preserves_independent_scope(self):
        first = self.probe(); second = self.probe()
        one = self.launch(first, 'import time;time.sleep(30)')
        two = self.launch(second, 'import time;time.sleep(30)')
        self.assertNotEqual(one.launch_id, two.launch_id)
        pins = self.pins(two); self.stop(first)
        self.assertTrue(self.call(second, state=pb.ProcessScopeQuery()).state.populated)
        self.assertEqual(select.select(pins, [], [], 0)[0], [])
        self.stop(second)

    def test_ordinary_directory_rejected_before_execution(self):
        ready = self.files/'unexpected'; proc = self.probe(root=self.files)
        reply = self.call(proc, launch=pb.AuthorityProcessLaunch(executable='/usr/bin/touch', arguments=[str(ready)]))
        self.assertEqual(reply.WhichOneof('outcome'), 'error')
        self.assertFalse(ready.exists())

    def test_invalid_executable_rejected_without_scope(self):
        proc = self.probe()
        for executable in ['python3', '/bin/true\0bad', '']:
            reply = self.call(proc, launch=pb.AuthorityProcessLaunch(executable=executable))
            self.assertEqual(reply.error.code, pb.ProcessScopeError.INVALID_ARGUMENT)
        self.assertEqual([p for p in self.root.iterdir() if p.is_dir()], [])

    def test_exec_failure_removes_scope(self):
        proc = self.probe()
        reply = self.call(proc, launch=pb.AuthorityProcessLaunch(executable='/no/such/spatial-authority'))
        self.assertEqual(reply.error.code, pb.ProcessScopeError.LAUNCH_FAILED)
        self.assertEqual([p for p in self.root.iterdir() if p.is_dir()], [])

    def test_parent_environment_is_not_exported(self):
        proc = self.probe(); ready = self.files/'environment'
        self.launch(proc, f'import os,pathlib;pathlib.Path({str(ready)!r}).write_text(str(sorted(os.environ)))')
        self.wait_file(ready)
        self.assertEqual(ready.read_text(), "['LANG', 'PATH']")
        self.stop(proc)

    def test_kernel_denied_kill_never_returns_stopped(self):
        self.denied('deny-write')

    def test_kernel_denied_wait_never_returns_stopped(self):
        self.denied('deny-wait')

    def denied(self, mode):
        proc = self.probe(mode); self.launch(proc, 'import time;time.sleep(30)')
        reply = self.call(proc, stop=pb.ProcessScopeQuery())
        self.assertEqual(reply.error.code, pb.ProcessScopeError.CLEANUP_UNCONFIRMED)
        self.assertIn('Cannot open cgroup control' if mode == 'deny-write' else 'Cannot reap authority child', reply.error.message)
        self.assertEqual(proc.wait(timeout=3), 2)

    def test_unknown_protobuf_fields_rejected(self):
        proc = self.probe()
        request = pb.ProcessScopeRequest(protocol_version=1, request_id=77)
        # Field 99 is outside the versioned schema.
        raw = request.SerializeToString()+b'\x98\x06\x01'
        proc.stdin.write(len(raw).to_bytes(4, 'big')+raw); proc.stdin.flush()
        self.assertTrue(select.select([proc.stdout], [], [], 3)[0])
        reply = read_message(proc.stdout, pb.ProcessScopeReply)
        self.assertEqual(reply.error.code, pb.ProcessScopeError.INVALID_ARGUMENT)


if __name__ == '__main__': unittest.main(verbosity=2)
