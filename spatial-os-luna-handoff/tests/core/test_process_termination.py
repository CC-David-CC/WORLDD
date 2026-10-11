"""Real process trees and kernel-denied cleanup; no simulated termination result."""
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
import unittest
from google.protobuf import text_format
import core_pb2 as core
import broker_pb2 as broker
import process_scope_pb2 as scope
from harness.wire import frame,read_message

PROBE=Path(sys.argv.pop(1))
CGROUP=Path('/sys/fs/cgroup')/Path('/proc/self/cgroup').read_text().strip().split('::',1)[1].lstrip('/')
assert CGROUP.name=='manager', 'Run with the dedicated resource delegate'
CGROUP=CGROUP.parent
(CGROUP/'cgroup.subtree_control').write_text('+cpu +memory +pids +io\n')

class ProcessTerminationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.cgroup=CGROUP/('termination-'+self.root.name);self.cgroup.mkdir()
        def cleanup_group():
            (self.cgroup/'cgroup.kill').write_text('1\n')
            deadline=time.monotonic()+3
            while 'populated 1' in (self.cgroup/'cgroup.events').read_text():
                self.assertLess(time.monotonic(),deadline);time.sleep(.01)
            for path in sorted((p for p in self.cgroup.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True):path.rmdir()
            self.cgroup.rmdir()
        self.addCleanup(cleanup_group)
        device=(Path('/sys/dev/block')/f'{os.major(self.root.stat().st_dev)}:{os.minor(self.root.stat().st_dev)}').resolve(strict=True)
        if (device/'partition').exists():device=device.parent
        major,minor=map(int,(device/'dev').read_text().strip().split(':'))
        budget=scope.ProcessScopeBudget(memory_bytes=128*1024*1024,swap_bytes=0,processes=16,cpu_microseconds=100000,
            io=[dict(major=major,minor=minor,read_bytes_per_second=64*1024*1024,write_bytes_per_second=16*1024*1024)])
        config=broker.BrokerResourceConfig(protocol_version=1,delegated_cgroup=str(self.cgroup),
            default_policy=broker.WorkspaceResourcePolicy(total=budget,actor_default=budget,
                profiles=[broker.ApplicationResourceProfile(name='development',maximum=budget)]))
        self.config=self.root/'resource.textproto';self.config.write_text(text_format.MessageToString(config));self.config.chmod(0o600)
        def preserve():
            if os.environ.get('SPATIAL_SCOPE_EVIDENCE'):
                import shutil
                target=Path(os.environ['SPATIAL_SCOPE_EVIDENCE'])/self._testMethodName;target.mkdir(parents=True,exist_ok=True)
                for path in self.root.rglob('*.log'):shutil.copy2(path,target/path.name)
                shutil.copy2(self.config,target/self.config.name)
        self.addCleanup(preserve)
        for n in ['project','repository','state','state/home','state/tmp','state/cache','state/config','state/data']:
            (self.root/n).mkdir()
    def run_boundary(self,mode,code):
        log=(self.root/'probe.log').open('wb');self.addCleanup(log.close)
        command=[str(PROBE),str(self.root/'project'),str(self.root/'repository'),str(self.root/'state'),mode,str(self.config)]
        # Trusted test provisioning; the production boundary still performs all application launches.
        enter=f'import pathlib,os;pathlib.Path({str(self.cgroup/"cgroup.procs")!r}).write_text("0\\n");os.execv({str(PROBE)!r},{command!r})'
        proc=subprocess.Popen(['/usr/bin/python3','-c',enter],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log)
        def cleanup():
            if proc.poll() is None:proc.kill();proc.wait(timeout=3)
            proc.stdin.close();proc.stdout.close()
        self.addCleanup(cleanup)
        request=core.ProcessLaunchRequest(argv=['/usr/bin/python3','-c',code],operation_id='fixture',profile='development')
        proc.stdin.write(frame(request));proc.stdin.flush()
        self.assertTrue(select.select([proc.stdout],[],[],8)[0],'Production boundary startup deadline')
        ready=read_message(proc.stdout,broker.AppEvent);self.assertTrue(ready.started,ready)
        deadline=time.monotonic()+3
        while not (self.root/'project/ready').exists():
            self.assertLess(time.monotonic(),deadline,'Application did not start');time.sleep(.005)
        return proc
    def stop(self,proc):
        proc.stdin.write(frame(core.ActorStopRequest()));proc.stdin.flush()
        try:return proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill();proc.wait(timeout=3);self.fail('Cleanup did not report its kernel failure within the deadline')
    def descendants(self,parent):
        found=[];todo=[parent]
        while todo:
            p=todo.pop()
            try:children=[int(n) for n in Path(f'/proc/{p}/task/{p}/children').read_text().split()]
            except FileNotFoundError:continue
            for child in children:
                fd=os.pidfd_open(child);self.addCleanup(os.close,fd);found.append(fd);todo.append(child)
        return found
    def test_wait_error_cannot_be_reported_as_confirmed_stop(self):
        proc=self.run_boundary('deny-wait',"import pathlib,time;pathlib.Path('ready').touch();time.sleep(30)")
        self.assertEqual(self.stop(proc),2)
        self.assertIn('cleanup unconfirmed:',(self.root/'probe.log').read_text())
    def test_signal_error_returns_failure_without_waiting_for_live_child(self):
        proc=self.run_boundary('deny-signal',"import pathlib,time;pathlib.Path('ready').touch();time.sleep(30)")
        self.assertEqual(self.stop(proc),2)
        self.assertIn('cleanup unconfirmed:',(self.root/'probe.log').read_text())
    def test_confirmed_stop_has_already_terminated_detached_descendants(self):
        proc=self.run_boundary('normal',"import os,pathlib,time\npid=os.fork()\nif pid==0:\n os.setsid();pathlib.Path('ready').touch()\n while True: time.sleep(.1)\nelse: os.waitpid(pid,0)")
        descriptors=self.descendants(proc.pid);self.assertGreaterEqual(len(descriptors),4)
        self.assertEqual(self.stop(proc),0)
        self.assertEqual(len(select.select(descriptors,[],[],0)[0]),len(descriptors),'Stop returned before descendant exit')
    def test_already_exited_process_can_be_stopped_twice(self):
        proc=self.run_boundary('normal',"import pathlib;pathlib.Path('ready').touch()")
        time.sleep(.05);self.assertEqual(self.stop(proc),0)
    def test_destructor_wait_error_cannot_return_broker_success(self):
        proc=self.run_boundary('destructor-deny-wait',"import pathlib,time;pathlib.Path('ready').touch();time.sleep(30)")
        self.assertEqual(self.stop(proc),2)
        self.assertIn('application cleanup unconfirmed:',(self.root/'probe.log').read_text())
    def test_destructor_signal_error_cannot_return_broker_success(self):
        proc=self.run_boundary('destructor-deny-signal',"import pathlib,time;pathlib.Path('ready').touch();time.sleep(30)")
        self.assertEqual(self.stop(proc),2)
        self.assertIn('application cleanup unconfirmed:',(self.root/'probe.log').read_text())

if __name__=='__main__':unittest.main(verbosity=2)
