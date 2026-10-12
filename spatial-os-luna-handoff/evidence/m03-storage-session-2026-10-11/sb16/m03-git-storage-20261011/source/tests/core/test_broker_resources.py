"""Real broker launches, kernel budgets, and retained supervisor cleanup."""
import json
import fcntl
import os
from pathlib import Path
import select
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import unittest
from contextlib import closing
from google.protobuf import text_format
import broker_pb2 as broker
import core_pb2 as core
from harness.git_fixture import GitRepo
from harness.wire import frame, make_request, read_message, result_value
import test_process_scope_resources as resources

scopes = resources.scopes
pb = scopes.pb
BROKER = Path(sys.argv.pop(1)).resolve()
POLICY = ['workspace.select', 'files.read', 'files.write', 'process.launch']
MIB = 1024 * 1024


class Broker:
    def __init__(self, test, policy=None, configured=True, invalid_root=False, handoff=None,
                 state=None, envelope_device=None):
        self.test = test
        self.sequence = 0
        self.closed = False
        self.index = len(test.brokers)
        self.state = Path(state) if state is not None else test.files/f'broker-state-{self.index}'
        self.stream, child = socket.socketpair()
        test.addCleanup(child.close)
        self.stream.settimeout(10)
        self.reader = self.stream.makefile('rb', buffering=0)
        self.log_path = test.files/f'broker-{self.index}.log'
        self.log = self.log_path.open('wb')
        self.scope_log = (test.files/f'owner-{self.index}.log').open('wb')
        resource_fd = os.memfd_create('fixture-resource-policy', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING) if handoff else None
        resource_file = os.fdopen(resource_fd, 'wb', buffering=0) if resource_fd is not None else None
        if resource_file is not None: test.addCleanup(resource_file.close)
        passed = [child.fileno(), self.log.fileno()]
        if resource_fd is not None: passed.append(resource_fd)
        self.owner = subprocess.Popen(
            [str(scopes.PROBE), str(test.root), 'normal', *(str(fd) for fd in passed)],
            pass_fds=passed, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.scope_log)
        test.brokers.append(self)
        prepared = test.call(self.owner, prepare=pb.ProcessScopeQuery())
        test.assertEqual(prepared.WhichOneof('outcome'), 'state', prepared)
        self.root = test.root/prepared.state.identity.scope_name
        config = broker.BrokerResourceConfig(protocol_version=2,
            broker_budget=test.budget(memory_bytes=1024*MIB, processes=128, cpu_microseconds=400000),
            manager_budget=test.budget(memory_bytes=256*MIB, processes=32, cpu_microseconds=200000),
            delegated_cgroup=str(test.root if invalid_root else self.root),
            default_policy=policy or test.policy())
        if envelope_device is not None:
            for budget in (config.broker_budget, config.manager_budget):
                budget.io[0].major, budget.io[0].minor = envelope_device
        self.config_path = test.files/f'broker-{self.index}.textproto'
        self.config_path.write_text(text_format.MessageToString(config))
        self.config_path.chmod(0o600)
        command = [str(BROKER), '--state-dir', str(self.state)]
        if handoff:
            os.write(resource_fd, config.SerializeToString())
            if handoff != 'mutable':
                fcntl.fcntl(resource_fd, fcntl.F_ADD_SEALS,
                    fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE)
            command += ['--resource-config-fd', '5']
            if handoff == 'conflict': command += ['--resource-config', str(self.config_path)]
        elif configured: command += ['--resource-config', str(self.config_path)]
        # The production owner assigns the child to its held scope before exec.
        # This fixture only connects explicit private descriptors to standard I/O.
        environment = {key: os.environ[key] for key in ('ASAN_OPTIONS', 'UBSAN_OPTIONS') if key in os.environ}
        code = ('import os;os.dup2(3,0);os.dup2(3,1);os.dup2(4,2);os.close(3);os.close(4);'
                f'os.environ.update({environment!r});os.execv({str(BROKER)!r},{command!r})')
        started = test.call(self.owner, start=pb.AuthorityProcessLaunch(
            executable='/usr/bin/python3', arguments=['-c', code], inherited_fds=[3, 4, 5] if handoff else [3, 4]))
        child.close()
        if resource_file is not None: resource_file.close()
        test.assertEqual(started.WhichOneof('outcome'), 'state', started)

    def request(self, **operation):
        self.sequence += 1
        if 'management' in operation: operation['management'].request_id = self.sequence
        self.stream.sendall(frame(broker.Request(protocol_version=1, request_id=self.sequence, **operation)))
        reply = read_message(self.reader, broker.Reply)
        self.test.assertEqual((reply.protocol_version, reply.request_id), (1, self.sequence))
        return reply

    def management(self, method, **args):
        reply = self.request(management=make_request(method, args))
        self.test.assertFalse(reply.HasField('error'), reply)
        self.test.assertFalse(reply.management.HasField('error'), reply)
        return result_value(reply.management)

    def setup(self):
        self.management('git.repo.import', id='repo', path=str(self.test.repo.path))
        self.tree = Path(self.management('git.worktree.create', repo='repo', id='tree',
            branch='application', start='main', operation_id='create-tree')['path'])
        self.management('workspace.create', id='workspace', worktree='tree', policy=POLICY)
        for actor in ['a', 'b']:
            self.management('workspace.member.set', id='workspace', actor=actor, rights=POLICY)
        return self

    def launch(self, code='import time;time.sleep(30)', actor='a', profile='development'):
        return self.request(launch=broker.LaunchRequest(workspace='workspace', actor=actor,
            command=core.ProcessLaunchRequest(operation_id=f'launch-{self.sequence}',
                profile=profile, argv=['/usr/bin/python3', '-c', code])))

    def leaves(self):
        return sorted(path.parent for path in self.root.rglob('cgroup.procs')
            if len(path.relative_to(self.root).parts) == 4)

    def wait_exit(self):
        deadline = time.monotonic()+5
        while True:
            reply = self.test.call(self.owner, state=pb.ProcessScopeQuery())
            self.test.assertFalse(reply.HasField('error'), reply)
            if reply.state.direct_child_exited: return
            self.test.assertLess(time.monotonic(), deadline, 'Broker did not exit')
            time.sleep(.01)

    def close(self, normal=True):
        if self.closed: return
        try:
            if normal:
                self.stream.shutdown(socket.SHUT_WR)
                self.wait_exit()
                self.test.assertEqual([p.name for p in self.root.iterdir() if p.is_dir()], [])
                self.test.assertEqual((self.root/'cgroup.subtree_control').read_text().strip(), '')
            self.test.stop(self.owner)
            self.test.assertFalse(self.root.exists())
        finally:
            self.closed = True
            self.reader.close(); self.stream.close()
            self.owner.stdin.close()
            try: self.owner.wait(timeout=3)
            except subprocess.TimeoutExpired: self.owner.kill(); self.owner.wait(timeout=3)
            self.owner.stdout.close(); self.scope_log.close(); self.log.close()


class BrokerResourceTests(unittest.TestCase):
    call = scopes.ProcessScopeTests.call
    stop = scopes.ProcessScopeTests.stop
    budget = resources.ResourceScopeTests.budget
    wait_file = resources.ResourceScopeTests.wait_file

    def setUp(self):
        self.brokers = []
        scopes.ProcessScopeTests.setUp(self)
        (self.root/'cgroup.subtree_control').write_text('+cpu +memory +pids +io\n')
        self.repo = GitRepo(self.files)
        def preserve():
            if os.environ.get('SPATIAL_SCOPE_EVIDENCE'):
                target = Path(os.environ['SPATIAL_SCOPE_EVIDENCE'])/self._testMethodName
                target.mkdir(parents=True, exist_ok=True)
                for path in self.files.glob('*.json'): shutil.copy2(path, target/path.name)
                for path in self.files.glob('*.textproto'): shutil.copy2(path, target/path.name)
                for instance in self.brokers:
                    for path in instance.state.rglob('setup.log'):
                        name = f'broker-{instance.index}-{path.parent.name}-setup.log'
                        shutil.copy2(path, target/name)
        self.addCleanup(preserve)
        self.addCleanup(lambda: [item.close(normal=False) for item in reversed(self.brokers)])

    def cleanup_scopes(self):
        for path in [p for p in self.root.iterdir() if p.is_dir()]:
            (path/'cgroup.kill').write_text('1\n')
            deadline = time.monotonic()+3
            while 'populated 1' in (path/'cgroup.events').read_text():
                self.assertLess(time.monotonic(), deadline); time.sleep(.01)
            for child in sorted((p for p in path.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
                child.rmdir()
            path.rmdir()
        self.root.rmdir()

    def policy(self, total=256, actor=128, application=64):
        return broker.WorkspaceResourcePolicy(
            total=self.budget(memory_bytes=total*MIB, processes=32, cpu_microseconds=200000),
            actor_default=self.budget(memory_bytes=actor*MIB, processes=16, cpu_microseconds=100000),
            profiles=[broker.ApplicationResourceProfile(name=name,
                maximum=self.budget(memory_bytes=application*MIB, processes=8, cpu_microseconds=50000,
                    io=[dict(major=resources.MAJOR, minor=resources.MINOR,
                        read_bytes_per_second=32*MIB, write_bytes_per_second=8*MIB)]))
                for name in ['development', 'read-only']])

    def launched(self, reply):
        self.assertTrue(reply.HasField('launched'), reply)
        return reply.launched.process_id

    def wait(self, instance, process):
        return instance.management('process.wait', id=process, timeout_seconds=5)

    def records(self, instance):
        path = instance.state/'execution.db'
        self.assertTrue(path.is_file())
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
            return db.execute('SELECT id,state FROM processes ORDER BY id').fetchall()

    def test_hierarchy_intersection_and_private_controls(self):
        policy = self.policy()
        policy.actors.add(fixture_actor='a', maximum=self.budget(memory_bytes=96*MIB,
            processes=12, cpu_microseconds=25000,
            io=[dict(major=resources.MAJOR, minor=resources.MINOR,
                read_bytes_per_second=16*MIB, write_bytes_per_second=4*MIB)]))
        instance = Broker(self, policy).setup()
        process = self.launched(instance.launch('''import json,os,pathlib,time
fds=[]
for fd in range(3,256):
 try: os.fstat(fd);fds.append(fd)
 except OSError: pass
pathlib.Path('observed.json').write_text(json.dumps(dict(cwd=os.getcwd(),fds=fds,
 cgroup=pathlib.Path('/sys/fs/cgroup').exists(),status=pathlib.Path('/proc/self/status').read_text())))
time.sleep(30)
'''))
        self.wait_file(instance.tree/'observed.json')
        observed = json.loads((instance.tree/'observed.json').read_text())
        self.assertEqual(observed['cwd'], '/project'); self.assertEqual(observed['fds'], [])
        self.assertFalse(observed['cgroup']); self.assertIn('NoNewPrivs:\t1', observed['status'])
        leaves = instance.leaves(); self.assertEqual(len(leaves), 1)
        leaf = leaves[0]; actor = leaf.parent; workspace = actor.parent
        self.assertEqual(workspace.parent, instance.root)
        self.assertEqual((workspace/'memory.max').read_text().strip(), str(256*MIB))
        self.assertEqual((actor/'memory.max').read_text().strip(), str(96*MIB))
        self.assertEqual((leaf/'memory.max').read_text().strip(), str(64*MIB))
        self.assertEqual((leaf/'pids.max').read_text().strip(), '8')
        self.assertEqual((leaf/'memory.swap.max').read_text().strip(), '0')
        self.assertEqual((leaf/'cpu.max').read_text().strip(), '25000 100000')
        self.assertIn(f'{resources.MAJOR}:{resources.MINOR} rbps={16*MIB} wbps={4*MIB}', (leaf/'io.max').read_text())
        self.assertGreaterEqual(len((leaf/'cgroup.procs').read_text().split()), 3)
        self.assertEqual((workspace/'cgroup.procs').read_text().strip(), '')
        self.assertEqual((actor/'cgroup.procs').read_text().strip(), '')
        evidence = dict(observed=observed, process=process, hierarchy={str(path.relative_to(instance.root)):
            {name:(path/name).read_text() for name in ['memory.max','memory.swap.max','pids.max','cpu.max','io.max','cgroup.procs']}
            for path in [workspace, actor, leaf]})
        (self.files/'kernel.json').write_text(json.dumps(evidence, indent=2))
        instance.close()

    def test_workspace_capacity_returns_after_checked_exit(self):
        instance = Broker(self, self.policy(total=128)).setup()
        one = self.launched(instance.launch("import pathlib,time\nwhile not pathlib.Path('exit').exists(): time.sleep(.01)"))
        self.launched(instance.launch(actor='b'))
        before = self.records(instance)
        denied = instance.launch(); self.assertEqual(denied.error.code, core.RESOURCE_EXHAUSTED, denied)
        self.assertEqual(self.records(instance), before)
        (instance.tree/'exit').touch(); self.assertEqual(self.wait(instance, one)['exit_code'], 0)
        self.launched(instance.launch())
        self.assertEqual(len(instance.leaves()), 2)
        instance.close()

    def test_actor_capacity_preserves_other_actor(self):
        instance = Broker(self, self.policy(actor=64)).setup()
        self.launched(instance.launch())
        denied = instance.launch(); self.assertEqual(denied.error.code, core.RESOURCE_EXHAUSTED, denied)
        self.launched(instance.launch(actor='b')); self.assertEqual(len(instance.leaves()), 2)
        instance.close()

    def test_application_writes_obey_broker_io_budget(self):
        policy = self.policy()
        policy.profiles[0].maximum.io[0].write_bytes_per_second = 4*MIB
        instance = Broker(self, policy).setup()
        process = self.launched(instance.launch('''import json,mmap,os,pathlib,time
while not pathlib.Path('io-go').exists(): time.sleep(.01)
buffer=mmap.mmap(-1,1024*1024);buffer.write(b'x'*(1024*1024))
fd=os.open('io-data',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_DIRECT,0o600)
started=time.monotonic()
for _ in range(16):
 assert os.write(fd,buffer)==len(buffer)
os.fsync(fd);os.close(fd);buffer.close()
pathlib.Path('io-result.json').write_text(json.dumps(dict(seconds=time.monotonic()-started)))
while not pathlib.Path('io-release').exists(): time.sleep(.01)
'''))
        leaves = instance.leaves(); self.assertEqual(len(leaves), 1)
        leaf = leaves[0]
        def written(label, required=False):
            raw = (leaf/'io.stat').read_text()
            (self.files/(label+'-io.json')).write_text(json.dumps(dict(raw=raw)))
            for line in raw.splitlines():
                device, *values = line.split()
                if device == f'{resources.MAJOR}:{resources.MINOR}':
                    counters = dict(value.split('=', 1) for value in values)
                    if 'wbytes' in counters: return int(counters['wbytes'])
            self.assertFalse(required, 'Completed writes require a kernel byte counter')
            return 0
        before = written('before'); (instance.tree/'io-go').touch()
        self.wait_file(instance.tree/'io-result.json', timeout=20)
        result = json.loads((instance.tree/'io-result.json').read_text())
        result.update(kernel_bytes=written('after', required=True)-before, configured_bps=4*MIB)
        self.assertEqual((instance.tree/'io-data').stat().st_size, 16*MIB)
        self.assertGreaterEqual(result['kernel_bytes'], 16*MIB)
        self.assertGreaterEqual(result['seconds'], 2.5)
        self.assertLess(result['seconds'], 20)
        (self.files/'application-io.json').write_text(json.dumps(result, indent=2))
        (instance.tree/'io-release').touch()
        self.assertEqual(self.wait(instance, process)['exit_code'], 0)
        instance.close()

    def test_read_only_authority_survives_resource_integration(self):
        instance = Broker(self).setup()
        instance.management('workspace.member.set', id='workspace', actor='a', rights=[r for r in POLICY if r != 'files.write'])
        process = self.launched(instance.launch('''import pathlib,sys
try: pathlib.Path('forbidden').write_text('bad')
except PermissionError: sys.exit(0)
except OSError as error: sys.exit(0 if error.errno==30 else 9)
sys.exit(8)
'''))
        self.assertEqual(self.wait(instance, process)['exit_code'], 0)
        self.assertFalse((instance.tree/'forbidden').exists())
        instance.close()

    def test_missing_configuration_denies_before_pending_record(self):
        prepared = Broker(self).setup()
        prepared.close()
        instance = Broker(self, configured=False, state=prepared.state)
        reply = instance.launch(); self.assertEqual(reply.error.code, core.UNAVAILABLE, reply)
        self.assertEqual(self.records(instance), [])
        instance.close()

    def test_missing_configuration_denies_git_before_repository_mutation(self):
        instance = Broker(self, configured=False)
        reply = instance.request(management=make_request('git.repo.import',
            dict(id='repo', path=str(self.repo.path))))
        self.assertEqual(reply.management.error.code, core.UNAVAILABLE, reply)
        with closing(sqlite3.connect(instance.state/'execution.db')) as database:
            self.assertEqual(database.execute('SELECT count(*) FROM repositories').fetchone(), (0,))
            self.assertEqual(database.execute('SELECT count(*) FROM operations').fetchone(), (0,))
        self.assertEqual(list((instance.state/'repositories').iterdir()), [])
        instance.close()

    def test_sealed_resource_handoff_launches_and_is_consumed(self):
        instance = Broker(self, handoff='sealed').setup()
        process = self.launched(instance.launch('pass'))
        self.assertEqual(self.wait(instance, process)['exit_code'], 0)
        pid = int((instance.root/'broker-manager/cgroup.procs').read_text())
        links = [os.readlink(path) for path in Path(f'/proc/{pid}/fd').iterdir()]
        self.assertFalse(any('fixture-resource-policy' in path for path in links), links)
        instance.close()

    def test_mutable_resource_handoff_fails_before_store_mutation(self):
        instance = Broker(self, handoff='mutable'); instance.wait_exit()
        self.assertFalse(instance.state.exists())
        self.assertIn('owned sealed protobuf', instance.log_path.read_text())
        instance.close(normal=False)

    def test_sealed_resource_handoff_rejects_wrong_owned_root(self):
        instance = Broker(self, handoff='sealed', invalid_root=True); instance.wait_exit()
        self.assertFalse(instance.state.exists())
        self.assertIn('exact owned cgroup', instance.log_path.read_text())
        instance.close(normal=False)

    def test_conflicting_resource_arguments_cannot_override_handoff(self):
        instance = Broker(self, handoff='conflict'); instance.wait_exit()
        self.assertFalse(instance.state.exists())
        instance.close(normal=False)

    def test_wrong_root_fails_before_any_store_mutation(self):
        instance = Broker(self, invalid_root=True)
        instance.wait_exit()
        self.assertFalse(instance.state.exists())
        self.assertIn('exact owned cgroup', instance.log_path.read_text())
        instance.close(normal=False)

    def test_missing_io_controller_fails_before_store_mutation(self):
        (self.root/'cgroup.subtree_control').write_text('-io\n')
        instance = Broker(self)
        instance.wait_exit()
        self.assertFalse(instance.state.exists())
        self.assertIn('Required controller is unavailable', instance.log_path.read_text())
        instance.close(normal=False)

    def test_uncovered_storage_device_denies_before_pending_record(self):
        prepared = Broker(self).setup()
        prepared.close()
        policy, device = self.uncovered_policy()
        instance = Broker(self, policy, state=prepared.state, envelope_device=device)
        reply = instance.launch(); self.assertEqual(reply.error.code, core.INVALID_ARGUMENT, reply)
        self.assertEqual(self.records(instance), [])
        self.assertEqual(instance.leaves(), [])
        instance.close()

    def uncovered_policy(self):
        policy = self.policy()
        # Use an existing whole device so io.max accepts the negative fixture.
        # The selected device must not back this workspace.
        other = next(path for path in sorted(Path('/sys/dev/block').iterdir())
            if not (path/'partition').exists() and path.name != f'{resources.MAJOR}:{resources.MINOR}'
            and int((path/'size').read_text()) > 0)
        device = tuple(map(int, other.name.split(':')))
        for budget in [policy.total, policy.actor_default, *(p.maximum for p in policy.profiles)]:
            budget.io[0].major, budget.io[0].minor = device
        return policy, device

    def test_uncovered_git_device_denies_before_repository_mutation(self):
        policy, device = self.uncovered_policy()
        instance = Broker(self, policy, envelope_device=device)
        reply = instance.request(management=make_request('git.repo.import',
            dict(id='repo', path=str(self.repo.path))))
        self.assertEqual(reply.management.error.code, core.INVALID_ARGUMENT, reply)
        with closing(sqlite3.connect(instance.state/'execution.db')) as database:
            self.assertEqual(database.execute('SELECT count(*) FROM repositories').fetchone(), (0,))
            self.assertEqual(database.execute('SELECT count(*) FROM operations').fetchone(), (0,))
        self.assertEqual(list((instance.state/'repositories').iterdir()), [])
        instance.close()

    def test_mismatched_io_policy_fails_before_store_mutation(self):
        policy = self.policy()
        for budget in [policy.total, policy.actor_default, *(p.maximum for p in policy.profiles)]:
            budget.io[0].minor = resources.MINOR+1
        instance = Broker(self, policy)
        instance.wait_exit()
        self.assertFalse(instance.state.exists())
        self.assertIn('Resource policies must cover the same I/O devices', instance.log_path.read_text())
        instance.close(normal=False)

    def test_revocation_returns_capacity_and_preserves_independent_application(self):
        instance = Broker(self, self.policy(total=128)).setup()
        one = self.launched(instance.launch())
        two = self.launched(instance.launch(actor='b'))
        leaves = instance.leaves(); self.assertEqual(len(leaves), 2)
        instance.management('workspace.member.set', id='workspace', actor='a', rights=[])
        self.assertEqual(dict(self.records(instance))[one], 'revoked')
        self.assertEqual(dict(self.records(instance))[two], 'running')
        self.assertEqual(len(instance.leaves()), 1)
        instance.management('workspace.member.set', id='workspace', actor='a', rights=POLICY)
        process = self.launched(instance.launch('pass'))
        self.assertEqual(self.wait(instance, process)['exit_code'], 0)
        instance.close()

    def test_broker_crash_cleanup_preserves_independent_broker(self):
        first = Broker(self).setup(); second = Broker(self).setup()
        self.launched(first.launch('''import os,pathlib,time
if not os.fork(): os.setsid()
pathlib.Path('ready-'+str(os.getpid())).touch();time.sleep(30)
'''))
        self.launched(second.launch(actor='b'))
        deadline = time.monotonic()+5
        while len(list(first.tree.glob('ready-*'))) < 2:
            self.assertLess(time.monotonic(), deadline); time.sleep(.01)
        pids = [int(p) for leaf in first.leaves() for p in (leaf/'cgroup.procs').read_text().split()]
        pins = [os.pidfd_open(p) for p in pids]
        for fd in pins: self.addCleanup(os.close, fd)
        manager_pid = int((first.root/'broker-manager/cgroup.procs').read_text().strip())
        manager_pin = os.pidfd_open(manager_pid); self.addCleanup(os.close, manager_pin)
        signal.pidfd_send_signal(manager_pin, signal.SIGKILL)
        first.wait_exit(); first.close(normal=False)
        self.assertEqual(len(select.select(pins, [], [], 0)[0]), len(pins))
        self.assertIn('populated 1', (second.root/'cgroup.events').read_text())
        process = self.launched(second.launch('pass'))
        self.assertEqual(self.wait(second, process)['exit_code'], 0)
        second.close()


if __name__ == '__main__': unittest.main(verbosity=2)
