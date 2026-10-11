"""A prepared kernel scope precedes execution and selects every inherited descriptor."""
import fcntl
import json
import os
import select
import subprocess
import time
import unittest
import process_scope_pb2 as pb
import test_authority_process_scope as existing


class PreparedLaunchTests(existing.ProcessScopeTests):
    def prepare(self, proc):
        reply=self.call(proc,prepare=pb.ProcessScopeQuery())
        self.assertTrue(reply.HasField('state'),reply)
        self.assertFalse(reply.state.started or reply.state.populated or reply.state.direct_child_exited)
        identity=reply.state.identity
        self.assertEqual(len(identity.launch_id),32)
        scope=self.root/identity.scope_name
        self.assertEqual(scope.stat().st_ino,identity.inode)
        self.assertEqual((scope/'cgroup.procs').read_text(),'')
        return identity

    def start(self,proc,code,targets=()):
        return self.call(proc,start=pb.AuthorityProcessLaunch(executable='/usr/bin/python3',
            arguments=['-c',code],inherited_fds=targets))

    def memfd(self, contents):
        fd=os.memfd_create('prepared-launch-test',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
        os.write(fd,contents)
        fcntl.fcntl(fd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
        self.addCleanup(os.close,fd)
        return fd

    def probe_with_files(self,selected,unselected=()):
        log=(self.files/('descriptors-'+str(time.monotonic_ns())+'.log')).open('wb');self.addCleanup(log.close)
        proc=subprocess.Popen([str(existing.PROBE),str(self.root),'normal',*[str(fd) for fd in selected]],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,pass_fds=tuple(set(selected)|set(unselected)))
        def cleanup():
            if proc.poll() is None:
                proc.stdin.close()
                try:proc.wait(timeout=3)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=3)
            if not proc.stdin.closed:proc.stdin.close()
            proc.stdout.close()
        self.addCleanup(cleanup)
        return proc

    def test_prepared_identity_and_membership_precede_child_execution(self):
        proc=self.probe();identity=self.prepare(proc);ready=self.files/'membership'
        reply=self.start(proc,f'import pathlib,time;p=pathlib.Path({str(ready)!r});t=p.with_suffix(".tmp");t.write_text(pathlib.Path("/proc/self/cgroup").read_text());t.replace(p);time.sleep(30)')
        self.assertTrue(reply.HasField('state'),reply)
        self.assertTrue(reply.state.started)
        self.assertEqual(reply.state.identity,identity)
        self.wait_file(ready);self.assertIn('/'+identity.scope_name+'\n',ready.read_text())
        pins=self.pins(identity);self.assertEqual(len(pins),1)
        self.assertEqual(self.stop(proc).identity,identity)
        self.assertEqual(len(select.select(pins,[],[],0)[0]),1)

    def test_stopped_prepared_scope_cannot_execute(self):
        proc=self.probe();identity=self.prepare(proc);receipt=self.stop(proc)
        self.assertEqual(receipt.identity,identity)
        ready=self.files/'unexpected'
        reply=self.start(proc,f'import pathlib;pathlib.Path({str(ready)!r}).touch()')
        self.assertTrue(reply.HasField('error'),reply);self.assertFalse(ready.exists())
        self.assertFalse((self.root/identity.scope_name).exists())

    def test_second_start_cannot_execute_or_replace_live_identity(self):
        proc=self.probe();identity=self.prepare(proc)
        self.assertTrue(self.start(proc,'import time;time.sleep(30)').HasField('state'))
        ready=self.files/'unexpected';reply=self.start(proc,f'import pathlib;pathlib.Path({str(ready)!r}).touch()')
        self.assertTrue(reply.HasField('error'),reply);self.assertFalse(ready.exists())
        state=self.call(proc,state=pb.ProcessScopeQuery()).state
        self.assertEqual(state.identity,identity);self.assertTrue(state.populated);self.stop(proc)

    def test_only_explicit_descriptors_survive_exec_without_source_collision(self):
        first=self.memfd(b'first');second=self.memfd(b'second');private=self.memfd(b'supervisor-private')
        proc=self.probe_with_files([first,second],[private]);self.prepare(proc);ready=self.files/'descriptors'
        code=('import os,fcntl,json,pathlib,time\n'
              'opened=[]\n'
              'for fd in range(3,512):\n'
              ' try:fcntl.fcntl(fd,fcntl.F_GETFD);opened.append(fd)\n'
              ' except OSError:pass\n'
              f'p=pathlib.Path({str(ready)!r});t=p.with_suffix(".tmp");t.write_text(json.dumps([os.pread(3,100,0).decode(),os.pread(4,100,0).decode(),opened]));t.replace(p)\n'
              'time.sleep(30)')
        reply=self.start(proc,code,[3,4]);self.assertTrue(reply.HasField('state'),reply)
        self.wait_file(ready);self.assertEqual(json.loads(ready.read_text()),['first','second',[3,4]])
        self.assertEqual(os.pread(private,100,0),b'supervisor-private')
        self.assertEqual(os.pread(first,100,0),b'first');self.assertEqual(os.pread(second,100,0),b'second')
        self.stop(proc)

    def test_invalid_targets_are_rejected_before_start(self):
        first=self.memfd(b'first');second=self.memfd(b'second')
        for targets in ([0,4],[1,4],[2,4],[3,256],[3,3]):
            with self.subTest(targets=targets):
                proc=self.probe_with_files([first,second]);self.prepare(proc);ready=self.files/('invalid-'+str(targets))
                reply=self.start(proc,f'import pathlib;pathlib.Path({str(ready)!r}).touch()',targets)
                self.assertEqual(reply.error.code,pb.ProcessScopeError.INVALID_ARGUMENT,reply)
                self.assertFalse(ready.exists())
                self.assertFalse(self.call(proc,state=pb.ProcessScopeQuery()).state.started)
                self.stop(proc)

    def test_mismatched_descriptor_table_is_rejected_before_start(self):
        first=self.memfd(b'first');proc=self.probe_with_files([first]);self.prepare(proc)
        reply=self.start(proc,'raise SystemExit(90)',[])
        self.assertEqual(reply.error.code,pb.ProcessScopeError.INVALID_ARGUMENT,reply)
        self.assertFalse(self.call(proc,state=pb.ProcessScopeQuery()).state.started);self.stop(proc)

    def test_excessive_descriptor_table_is_rejected_before_start(self):
        fd=self.memfd(b'first');proc=self.probe_with_files([fd]*17);self.prepare(proc)
        reply=self.start(proc,'raise SystemExit(90)',range(3,20))
        self.assertEqual(reply.error.code,pb.ProcessScopeError.INVALID_ARGUMENT,reply)
        self.assertFalse(self.call(proc,state=pb.ProcessScopeQuery()).state.started);self.stop(proc)

    def test_failed_exec_requires_cleanup_and_cannot_retry_execution(self):
        proc=self.probe();identity=self.prepare(proc)
        reply=self.call(proc,start=pb.AuthorityProcessLaunch(executable='/no/such/prepared-authority'))
        self.assertEqual(reply.error.code,pb.ProcessScopeError.LAUNCH_FAILED,reply)
        ready=self.files/'unexpected';reply=self.start(proc,f'import pathlib;pathlib.Path({str(ready)!r}).touch()')
        self.assertTrue(reply.HasField('error'),reply);self.assertFalse(ready.exists())
        self.assertEqual(self.stop(proc).identity,identity)


def suite():
    return unittest.TestSuite(PreparedLaunchTests(n) for n in vars(PreparedLaunchTests) if n.startswith('test_'))


if __name__=='__main__':unittest.main(defaultTest='suite',verbosity=2)
