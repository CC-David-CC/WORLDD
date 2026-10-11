"""Independent kernel accounting and bounded exhaustion of production process scopes."""
import json
import os
from pathlib import Path
import time
import unittest
import shutil
import test_authority_process_scope as scopes

pb = scopes.pb
assert scopes.ROOT.name == 'manager', 'Run with the dedicated resource delegate'
scopes.ROOT = scopes.ROOT.parent
required = {'cpu', 'memory', 'pids', 'io'}
assert required <= set((scopes.ROOT/'cgroup.controllers').read_text().split())
(scopes.ROOT/'cgroup.subtree_control').write_text('+cpu +memory +pids +io\n')
device = Path('/sys/dev/block') / f'{os.major(Path(__file__).stat().st_dev)}:{os.minor(Path(__file__).stat().st_dev)}'
device = device.resolve(strict=True)
if (device/'partition').exists():
    device = device.parent
MAJOR, MINOR = map(int, (device/'dev').read_text().strip().split(':'))


class ResourceScopeTests(unittest.TestCase):
    cleanup_scopes = scopes.ProcessScopeTests.cleanup_scopes
    probe = scopes.ProcessScopeTests.probe
    call = scopes.ProcessScopeTests.call
    stop = scopes.ProcessScopeTests.stop
    pins = scopes.ProcessScopeTests.pins

    def setUp(self):
        scopes.ProcessScopeTests.setUp(self)
        (self.root/'cgroup.subtree_control').write_text('+cpu +memory +pids +io\n')
        def preserve():
            if os.environ.get('SPATIAL_SCOPE_EVIDENCE'):
                target = Path(os.environ['SPATIAL_SCOPE_EVIDENCE'])/self._testMethodName
                target.mkdir(parents=True, exist_ok=True)
                for path in self.files.glob('*.json'): shutil.copy2(path, target/path.name)
        self.addCleanup(preserve)

    def wait_file(self, path, timeout=15):
        deadline = time.monotonic()+timeout
        while not path.exists():
            self.assertLess(time.monotonic(), deadline, 'Independent child witness deadline')
            time.sleep(.01)

    def budget(self, **changes):
        value = dict(memory_bytes=64*1024*1024, swap_bytes=0, processes=16,
                     cpu_microseconds=100000,
                     io=[dict(major=MAJOR, minor=MINOR,
                              read_bytes_per_second=64*1024*1024,
                              write_bytes_per_second=64*1024*1024)])
        value.update(changes)
        return pb.ProcessScopeBudget(**value)

    def prepare(self, proc, budget=None):
        reply = self.call(proc, prepare_budget=budget if budget is not None else self.budget())
        self.assertEqual(reply.WhichOneof('outcome'), 'state', reply)
        self.assertFalse(reply.state.populated)
        self.assertFalse(reply.state.started)
        return self.root/reply.state.identity.scope_name

    def start(self, proc, code):
        reply = self.call(proc, start=pb.AuthorityProcessLaunch(
            executable='/usr/bin/python3', arguments=['-c', code]))
        self.assertEqual(reply.WhichOneof('outcome'), 'state', reply)
        return reply.state.identity

    @staticmethod
    def counters(path):
        return {key:int(value) for key,value in (line.split() for line in path.read_text().splitlines())}

    def test_controls_exist_before_child_execution(self):
        proc = self.probe(); scope = self.prepare(proc)
        self.assertEqual((scope/'memory.max').read_text().strip(), str(64*1024*1024))
        self.assertEqual((scope/'memory.swap.max').read_text().strip(), '0')
        self.assertEqual((scope/'memory.oom.group').read_text().strip(), '1')
        self.assertEqual((scope/'pids.max').read_text().strip(), '16')
        self.assertEqual((scope/'cpu.max').read_text().strip(), '100000 100000')
        self.assertIn(f'{MAJOR}:{MINOR} rbps=67108864 wbps=67108864', (scope/'io.max').read_text())
        marker = self.files/'ready'
        self.start(proc, f'import pathlib,time;pathlib.Path({str(marker)!r}).touch();time.sleep(30)')
        self.wait_file(marker)
        self.assertEqual(len((scope/'cgroup.procs').read_text().split()), 1)
        self.stop(proc)
        self.assertFalse(scope.exists())

    def test_incomplete_and_invalid_budgets_leave_no_scope(self):
        proc = self.probe()
        invalid = [pb.ProcessScopeBudget(), self.budget(memory_bytes=4097), self.budget(processes=0),
                   self.budget(cpu_microseconds=999), self.budget(io=[]), self.budget(swap_bytes=1)]
        for field in ['memory_bytes', 'swap_bytes', 'processes', 'cpu_microseconds']:
            value = self.budget(); value.ClearField(field); invalid.append(value)
        duplicate = self.budget(); duplicate.io.add().CopyFrom(duplicate.io[0]); invalid.append(duplicate)
        zero = self.budget(); zero.io[0].write_bytes_per_second = 0; invalid.append(zero)
        unknown = self.budget(); unknown.MergeFromString(b'\xf8\x07\x01'); invalid.append(unknown)
        for value in invalid:
            with self.subTest(budget=str(value)):
                reply = self.call(proc, prepare_budget=value)
                self.assertEqual(reply.error.code, pb.ProcessScopeError.INVALID_ARGUMENT, reply)
                self.assertEqual([p for p in self.root.iterdir() if p.is_dir()], [])

    def test_missing_io_controller_fails_before_execution(self):
        (self.root/'cgroup.subtree_control').write_text('-io\n')
        proc = self.probe()
        reply = self.call(proc, prepare_budget=self.budget())
        self.assertEqual(reply.error.code, pb.ProcessScopeError.LAUNCH_FAILED, reply)
        marker = self.files/'unexpected'
        reply = self.call(proc, start=pb.AuthorityProcessLaunch(executable='/usr/bin/touch', arguments=[str(marker)]))
        self.assertEqual(reply.error.code, pb.ProcessScopeError.INVALID_STATE, reply)
        self.assertFalse(marker.exists())
        self.assertEqual([p for p in self.root.iterdir() if p.is_dir()], [])

    def test_missing_device_component_does_not_select_device_zero(self):
        proc = self.probe()
        for field in ['major', 'minor']:
            with self.subTest(field=field):
                value = self.budget(); value.io[0].ClearField(field)
                reply = self.call(proc, prepare_budget=value)
                self.assertEqual(reply.error.code, pb.ProcessScopeError.INVALID_ARGUMENT, reply)
                self.assertEqual([p for p in self.root.iterdir() if p.is_dir()], [])

    def test_budget_cannot_be_replaced_after_prepare_or_start(self):
        proc = self.probe(); scope = self.prepare(proc, self.budget(processes=4))
        for started in [False, True]:
            if started: self.start(proc, 'import time;time.sleep(30)')
            reply = self.call(proc, prepare_budget=self.budget(processes=64))
            self.assertEqual(reply.error.code, pb.ProcessScopeError.INVALID_STATE)
            self.assertEqual((scope/'pids.max').read_text().strip(), '4')

    def test_fork_and_detachment_share_one_process_budget(self):
        proc = self.probe(); scope = self.prepare(proc, self.budget(processes=4))
        output = self.files/'fork.json'
        self.start(proc, f'''import errno,json,os,pathlib,time
children=[]; failure=0
for _ in range(12):
 try: pid=os.fork()
 except OSError as error: failure=error.errno; break
 if not pid:
  os.setsid();time.sleep(30);os._exit(0)
 children.append(pid)
pathlib.Path({str(output)!r}).write_text(json.dumps(dict(children=children,error=failure)))
time.sleep(30)
''')
        self.wait_file(output)
        result = json.loads(output.read_text())
        self.assertEqual(result['error'], 11)
        self.assertEqual(len(result['children']), 3)
        self.assertEqual(len((scope/'cgroup.procs').read_text().split()), 4)
        self.assertGreater(self.counters(scope/'pids.events')['max'], 0)
        self.stop(proc)
        self.assertFalse(scope.exists())

    def test_memory_exhaustion_kills_group_and_preserves_sibling(self):
        first = self.probe(); second = self.probe()
        scope = self.prepare(first, self.budget(memory_bytes=32*1024*1024))
        sibling = self.prepare(second)
        heartbeat = self.files/'heartbeat'; go = self.files/'go'
        self.start(second, f'''import pathlib,time
for i in range(300):
 pathlib.Path({str(heartbeat)!r}).write_text(str(i));time.sleep(.05)
''')
        self.start(first, f'''import os,pathlib,time
while not pathlib.Path({str(go)!r}).exists(): time.sleep(.01)
if not os.fork(): os.setsid();time.sleep(30);os._exit(0)
data=bytearray(96*1024*1024)
time.sleep(30)
''')
        self.wait_file(heartbeat); go.touch(); deadline = time.monotonic()+10
        while 'populated 1' in (scope/'cgroup.events').read_text():
            self.assertLess(time.monotonic(), deadline); time.sleep(.02)
        events = self.counters(scope/'memory.events')
        (self.files/'kernel-memory.json').write_text(json.dumps(events))
        self.assertGreater(events['oom_kill'], 0)
        before = heartbeat.read_text(); time.sleep(.15)
        self.assertNotEqual(heartbeat.read_text(), before)
        self.assertIn('populated 1', (sibling/'cgroup.events').read_text())
        self.stop(first); self.stop(second)

    def test_two_busy_children_share_cpu_quota(self):
        proc = self.probe(); scope = self.prepare(proc, self.budget(cpu_microseconds=10000))
        done = self.files/'cpu.json'; before = self.counters(scope/'cpu.stat'); start = time.monotonic()
        self.start(proc, f'''import json,os,pathlib,time
child=os.fork();end=time.monotonic()+2.5
while time.monotonic()<end: pass
if not child: os._exit(0)
os.waitpid(child,0);pathlib.Path({str(done)!r}).write_text(json.dumps(dict(done=True)))
''')
        self.wait_file(done)
        after = self.counters(scope/'cpu.stat'); elapsed = time.monotonic()-start
        self.assertGreater(after['nr_throttled'], before['nr_throttled'])
        used = after['usage_usec']-before['usage_usec']
        (self.files/'kernel-cpu.json').write_text(json.dumps(dict(before=before,after=after,elapsed=elapsed)))
        self.assertGreater(used, 50000)
        self.assertLess(used, elapsed*200000+100000)
        self.stop(proc)

    def test_real_writes_obey_io_budget(self):
        proc = self.probe(); budget = self.budget(); budget.io[0].write_bytes_per_second = 4*1024*1024
        scope = self.prepare(proc, budget); data = self.files/'io-data'; done = self.files/'io.json'
        self.start(proc, f'''import json,os,pathlib,time
fd=os.open({str(data)!r},os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_DSYNC,0o600)
block=b'x'*(128*1024);start=time.monotonic();total=0
for _ in range(128):
 count=os.write(fd,block)
 if count!=len(block): raise RuntimeError('short write')
 total+=count
os.fdatasync(fd);os.close(fd)
pathlib.Path({str(done)!r}).write_text(json.dumps(dict(bytes=total,seconds=time.monotonic()-start)))
''')
        self.wait_file(done, timeout=25); result = json.loads(done.read_text())
        self.assertEqual(result['bytes'], 16*1024*1024)
        self.assertGreater(result['seconds'], 2.8)
        entries = {line.split()[0]:dict(item.split('=') for item in line.split()[1:])
                   for line in (scope/'io.stat').read_text().splitlines()}
        (self.files/'kernel-io.json').write_text(json.dumps(entries))
        self.assertGreaterEqual(int(entries[f'{MAJOR}:{MINOR}']['wbytes']), result['bytes'])
        self.stop(proc)


if __name__ == '__main__':
    unittest.main(verbosity=2)
