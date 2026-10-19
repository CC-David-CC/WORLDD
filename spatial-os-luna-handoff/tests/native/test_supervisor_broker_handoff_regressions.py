"""Prepared correction tests. Run only after the unchanged-source handoff baseline."""
import json
import os
from pathlib import Path
import select
import signal
import shutil
import sqlite3
import subprocess
import time
import unittest
from unittest.mock import patch
from google.protobuf import text_format

import test_supervisor_broker_handoff as baseline
import test_native_launch as native_launch
import broker_pb2 as broker
import launch_pb2 as launch
import issuer_pb2 as identity
import process_scope_pb2 as process
import security_pb2 as security
from harness.git_fixture import GitRepo
from harness.wire import frame, make_request, result_value

scopes = baseline.supervisor.owned.existing
if os.environ.get('SPATIAL_TEST_BROKER_CONFIG'):
    if scopes.CGROUP.name == 'manager': scopes.CGROUP = scopes.CGROUP.parent
    assert scopes.CGROUP.name.startswith('spatial-m03-resources-')


class BrokerPipe:
    """Transport only. Every request reaches the production workspaced child."""
    def __init__(self, fixture, proc):
        self.fixture = fixture
        self.proc = proc
        self.state = fixture.root / 'execution'
        self.sequence = 0

    def request(self, **operation):
        self.sequence += 1
        request = broker.Request(protocol_version=1, request_id=self.sequence, **operation)
        self.proc.stdin.write(frame(request))
        self.proc.stdin.flush()
        reply = self.fixture.message(self.proc.stdout, broker.Reply)
        self.fixture.assertEqual(reply.request_id, self.sequence)
        return reply

    def call(self, method, **arguments):
        request = make_request(method, arguments)
        request.request_id = self.sequence + 1
        reply = self.request(management=request)
        self.fixture.assertTrue(reply.HasField('management'), reply)
        self.fixture.assertEqual(reply.management.request_id, request.request_id)
        self.fixture.assertFalse(reply.management.HasField('error'), reply)
        return result_value(reply.management)


class BrokerHandoffRegressionTests(baseline.BrokerHandoffTests):
    launch = native_launch.NativeLaunchTests.launch
    record = native_launch.NativeLaunchTests.record
    release = native_launch.NativeLaunchTests.release

    def setUp(self):
        super().setUp()
        if os.environ.get('SPATIAL_TEST_BROKER_CONFIG'):
            self.assertTrue({'cpu','memory','pids','io'} <= set((self.cgroup/'cgroup.controllers').read_text().split()))
            (self.cgroup/'cgroup.subtree_control').write_text('+cpu +memory +pids +io\n')

    def broker_config(self, target=38):
        config = self.config()
        config.broker_native_target_fd = target
        config.launch.CopyFrom(process.AuthorityProcessLaunch(
            executable=str(baseline.supervisor.owned.existing.PROBE.with_name('workspaced')),
            arguments=['--state-dir', str(self.root / 'execution'), '--native-security-fd', str(target)]))
        resource_path = os.environ.get('SPATIAL_TEST_BROKER_CONFIG')
        if resource_path:
            resource = text_format.Parse(Path(resource_path).read_text(), broker.BrokerProcessConfig())
            config.broker_resources.CopyFrom(resource.resources)
            config.broker_resource_target_fd = 255
        return config

    def started_broker(self):
        proc, channel = self.supervisor_process(self.broker_config())
        initial = self.message(channel, launch.AuthorityLaunchReply)
        self.assertTrue(initial.HasField('view'), initial)
        self.assertEqual(initial.view.record.stage, launch.START_ATTEMPTED)
        self.assertEqual(initial.view.record, self.read_record())
        self.assertFalse(initial.view.HasField('ready'), 'Management must not receive native credentials')
        self.broker = BrokerPipe(self, proc)
        self.assertEqual(self.broker.call('system.hello')['implementation'], 'production')
        group = self.cgroup / initial.view.record.process.scope_name
        if os.environ.get('SPATIAL_TEST_BROKER_CONFIG'): group /= 'broker-manager'
        members = (group / 'cgroup.procs').read_text().split()
        self.assertEqual(len(members), 1)
        child_pid = int(members[0])
        pin = os.pidfd_open(child_pid)
        self.addCleanup(os.close, pin)
        # NativeLaunch consumes this descriptor and disables unprivileged inspection.
        self.assertNotIn(38, self.observed_descriptors(child_pid, pin))
        return proc, channel, initial.view.record, child_pid, pin

    def observed_descriptors(self, pid, pin):
        # The pidfd must remain live across observation. PID reuse cannot pass.
        self.assertEqual(select.select([pin], [], [], 0)[0], [])
        observer = subprocess.run(
            ['sudo', '-n', 'python3', '-c',
             'import json, os, sys; '
             'print(json.dumps(sorted(int(fd) for fd in os.listdir("/proc/" + str(int(sys.argv[1])) + "/fd"))))',
             str(pid)], capture_output=True, text=True, timeout=5, check=True)
        self.assertEqual(select.select([pin], [], [], 0)[0], [])
        descriptors = json.loads(observer.stdout)
        self.assertTrue({0, 1, 2}.issubset(descriptors), descriptors)
        return descriptors

    def test_privileged_observer_detects_open_and_closed_fd_on_non_dumpable_process(self):
        code = (
            'import ctypes, os, sys; '
            'fd = os.open("/dev/null", os.O_RDONLY); os.dup2(fd, 38); '
            'assert ctypes.CDLL(None).prctl(4, 0, 0, 0, 0) == 0; '
            'print("open", flush=True); sys.stdin.readline(); '
            'os.close(38); print("closed", flush=True); sys.stdin.readline()')
        child = subprocess.Popen(['python3', '-c', code], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def cleanup():
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)
        self.addCleanup(cleanup)
        pin = os.pidfd_open(child.pid)
        self.addCleanup(os.close, pin)
        self.assertTrue(select.select([child.stdout], [], [], 5)[0])
        self.assertEqual(child.stdout.readline().strip(), 'open')
        self.assertIn(38, self.observed_descriptors(child.pid, pin))
        child.stdin.write('close\n'); child.stdin.flush()
        self.assertTrue(select.select([child.stdout], [], [], 5)[0])
        self.assertEqual(child.stdout.readline().strip(), 'closed')
        self.assertNotIn(38, self.observed_descriptors(child.pid, pin))

    def create_application(self):
        repository = GitRepo(self.root)
        self.broker.call('git.repo.import', id='repo', path=str(repository.path))
        self.broker.call('git.worktree.create', repo='repo', id='tree', branch='app', start='main', operation_id='tree')
        self.broker.call('workspace.create', id='workspace-one', worktree='tree', policy=native_launch.POLICY)
        self.broker.call('workspace.member.set', id='workspace-one', actor='human', rights=native_launch.POLICY)
        application = self.launch()
        observed = self.record(application)
        client = self.connect(bytes.fromhex(observed['cookie']))
        identity = client.inspect()
        self.assertEqual(identity.role, security.APPLICATION)
        self.assertEqual(identity.workspace, b'workspace-one')
        self.assertEqual(identity.application, application.process_id.encode())
        self.assertEqual(identity.server_id, self.supervisor.inspect().server_id)
        self.assertEqual(observed['sockets'], ['X' + str(self.display)])
        self.assertNotEqual(observed['net'], os.readlink('/proc/self/ns/net'))
        self.assertIn('NoNewPrivs:\t1', observed['status'])
        self.assertEqual(observed['inherited_fds'], [])
        self.assertTrue(observed['auth_readonly'])
        self.assertNotEqual(bytes.fromhex(observed['cookie']), self.cookie)
        return application, observed, client

    def parent_journal_evidence(self):
        journal_path = self.root/'execution/file-operations/file-operations.db'
        if not journal_path.is_file():
            return {'journal_path': str(journal_path), 'exists': False, 'active': [], 'retained': []}
        with sqlite3.connect(f'file:{journal_path}?mode=ro', uri=True) as journal:
            active = journal.execute('SELECT state,root_device,root_inode,boot_id,identity,root_path FROM git_worker_parent').fetchall()
            retained = journal.execute('SELECT state,root_device,root_inode,boot_id,identity,root_path FROM retained_git_worker_parents ORDER BY root_device,root_inode,boot_id,identity,root_path').fetchall()
        def describe(row):
            identity = process.ProcessScopeIdentity.FromString(row[4])
            root = Path(row[5])
            worker_parent = root/'spatial-git-workers'
            population_path = worker_parent/'cgroup.procs'
            return {'state': row[0], 'root_device': row[1], 'root_inode': row[2],
                    'boot_id': row[3], 'root_path': row[5], 'root_exists': root.exists(),
                    'worker_scope': identity.scope_name(), 'worker_device': identity.device(),
                    'worker_inode': identity.inode(), 'worker_boot_id': identity.boot_id(),
                    'worker_launch_id_bytes': len(identity.launch_id()),
                    'worker_parent_exists': worker_parent.exists(),
                    'population': population_path.read_text().split() if population_path.is_file() else None}
        return {'journal_path': str(journal_path), 'exists': True,
                'active': [describe(row) for row in active], 'retained': [describe(row) for row in retained]}

    def checked_cleanup_with_parent_evidence(self):
        evidence = self.parent_journal_evidence()
        try:
            record = self.checked_cleanup()
        except AssertionError as error:
            self.fail(f'{error}; pre-cleanup parent journal evidence={evidence!r}')
        return record, evidence

    def recovery_broker_config(self, previous):
        config = self.broker_config()
        journal = self.root/'recovery-supervisor-journal'
        journal.mkdir(mode=0o700)
        config.journal_directory = str(journal)
        config.child_participant_config = str(self.root/'recovery-supervised-child.textproto')
        config.binding.service_incarnation = os.urandom(32)
        runtime = identity.AuthorityParticipantRuntimeConfig()
        text_format.Parse(Path(config.participant.runtime_config).read_text(), runtime)
        runtime.service_incarnation = config.binding.service_incarnation
        runtime_path = self.root/'recovery-runtime.textproto'
        runtime_path.write_text(text_format.MessageToString(runtime))
        runtime_path.chmod(0o600)
        config.participant.runtime_config = str(runtime_path)
        config.authorization.previous_generation = previous.authorization.request.previous_generation
        return config, journal

    def test_owned_broker_launches_confined_native_application_and_stop_closes_only_its_scope(self):
        proc, channel, initial, _, pin = self.started_broker()
        _, observed, client = self.create_application()
        independent = self.supervisor.issue(workspace=b'independent', application=b'witness')
        survivor = self.connect(independent.cookie)
        self.assertEqual(select.select([pin], [], [], 0)[0], [])
        reply = self.control(channel, stop=launch.AuthorityLaunchQuery())
        self.assertEqual(reply.view.record.stage, launch.OWNED_SCOPES_STOPPED)
        self.assertEqual(proc.wait(timeout=5), 0)
        self.assertEqual(select.select([pin], [], [], 0)[0], [pin])
        self.assert_closed(client)
        with self.assertRaises(native_launch.native.Denied):
            self.connect(bytes.fromhex(observed['cookie']))
        self.assertEqual(survivor.inspect().credential_id, independent.id)
        record, parent_evidence = self.checked_cleanup_with_parent_evidence()
        journal_path = self.root/'execution/file-operations/file-operations.db'
        self.assertEqual(record.native, initial.native, parent_evidence)
        self.assertEqual(len(parent_evidence['active']), 1, parent_evidence)
        parent = parent_evidence['active'][0]
        self.assertEqual(parent['state'], 2, parent_evidence)
        self.assertEqual((parent['root_device'], parent['root_inode'], parent['boot_id']),
                         (record.process.device, record.process.inode, record.process.boot_id), parent_evidence)
        self.assertEqual(parent['root_path'], str(self.cgroup/record.process.scope_name), parent_evidence)
        self.assertFalse(parent['root_exists'], parent_evidence)
        self.assertEqual(parent['worker_scope'], 'spatial-git-workers', parent_evidence)
        self.assertGreater(parent['worker_device'], 0, parent_evidence)
        self.assertGreater(parent['worker_inode'], 0, parent_evidence)
        self.assertEqual(parent['worker_boot_id'], parent['boot_id'], parent_evidence)
        self.assertEqual(parent['worker_launch_id_bytes'], 32, parent_evidence)
        self.assertFalse(parent['worker_parent_exists'], parent_evidence)
        self.assertIsNone(parent['population'], parent_evidence)
        self.assertEqual(parent_evidence['retained'], [], parent_evidence)
        self.assertFalse(reply.view.HasField('ready'))

        # A fresh production broker reopens the same execution state. Its startup
        # recovery must retire this exact missing-root generation before serving.
        recovery_config, recovery_journal = self.recovery_broker_config(initial)
        recovered, recovery_channel = self.supervisor_process(recovery_config, start_server=False)
        recovered_start = self.message(recovery_channel, launch.AuthorityLaunchReply)
        self.assertTrue(recovered_start.HasField('view'), (recovered_start, parent_evidence))
        self.assertEqual(recovered_start.view.record.stage, launch.START_ATTEMPTED, parent_evidence)
        recovered_pipe = BrokerPipe(self, recovered)
        try:
            recovered_hello = recovered_pipe.call('system.hello')
        except Exception as error:
            self.fail(f'Production recovery broker did not serve hello: {error}; before={parent_evidence!r}; after={self.parent_journal_evidence()!r}')
        self.assertEqual(recovered_hello['implementation'], 'production', parent_evidence)
        recovery_evidence = self.parent_journal_evidence()
        with sqlite3.connect(f'file:{journal_path}?mode=ro', uri=True) as journal:
            self.assertEqual(journal.execute('SELECT count(*) FROM git_worker_parent').fetchone()[0], 0, recovery_evidence)
            self.assertEqual(journal.execute('SELECT count(*) FROM retained_git_worker_parents').fetchone()[0], 0, recovery_evidence)
        recovery_stop = self.control(recovery_channel, stop=launch.AuthorityLaunchQuery())
        self.assertTrue(recovery_stop.HasField('view'), recovery_stop)
        self.assertEqual(recovery_stop.view.record.stage, launch.OWNED_SCOPES_STOPPED)
        self.assertEqual(recovered.wait(timeout=5), 0)
        with sqlite3.connect(f'file:{recovery_journal/"launch.db"}?mode=ro', uri=True) as journal:
            saved_record = launch.AuthorityLaunchRecord.FromString(
                journal.execute('SELECT record FROM launch WHERE singleton=1').fetchone()[0])
        self.assertEqual(saved_record.stage, launch.OWNED_SCOPES_STOPPED)
        self.assertFalse((self.cgroup/saved_record.process.scope_name).exists())
        for path in self.journal.iterdir():
            self.assertNotIn(self.cookie, path.read_bytes())
            self.assertNotIn(bytes.fromhex(observed['cookie']), path.read_bytes())

    def test_broker_death_preserves_issuer_state_and_revokes_native_descendants(self):
        proc, channel, _, child_pid, pin = self.started_broker()
        _, observed, client = self.create_application()
        before = self.activation_rows()
        os.kill(child_pid, signal.SIGKILL)
        reply = self.message(channel, launch.AuthorityLaunchReply)
        self.assertTrue(reply.HasField('error'), reply)
        self.assertEqual(proc.wait(timeout=5), 2)
        self.assertEqual(select.select([pin], [], [], 0)[0], [pin])
        self.checked_cleanup()
        self.assertEqual(self.activation_rows(), before)
        self.assert_closed(client)
        with self.assertRaises(native_launch.native.Denied):
            self.connect(bytes.fromhex(observed['cookie']))

    def test_invalid_native_targets_fail_before_scope_ownership(self):
        for target in (0, 1, 2, 37, 256):
            with self.subTest(target=target):
                proc, _ = self.supervisor_process(self.broker_config(target), start_server=not hasattr(self, 'server'))
                self.assertEqual(proc.wait(timeout=5), 2)
                self.assertFalse((self.journal / 'launch.db').exists())
                self.assertFalse(self.child_config.exists())
                self.assertEqual(self.activation_rows(), [])

    def test_external_private_bootstrap_descriptor_is_rejected_before_scope_ownership(self):
        config = self.broker_config()
        config.launch.arguments.extend(['--cleanup-bootstrap-fd', '255'])
        proc, _ = self.supervisor_process(config)
        self.assertEqual(proc.wait(timeout=5), 2)
        diagnostic = '\n'.join(p.read_text(errors='replace') for p in self.root.glob('supervisor-*.log'))
        self.assertIn('Supervisor owns the production broker profile', diagnostic)
        self.assertFalse((self.journal / 'launch.db').exists())
        self.assertFalse(self.child_config.exists())
        self.assertEqual(self.activation_rows(), [])

    def mapped_broker(self, targets, with_resources=False):
        config = self.broker_config()
        if not with_resources:
            # Existing descriptor-only checks cover the participant and native handoff.
            # This diagnostic broker runs hello only. Resource-bearing launches have separate checks below.
            config.ClearField('broker_resources')
            config.ClearField('broker_resource_target_fd')
        sources = []
        for target in targets:
            fd = os.memfd_create('selected-handoff-fixture', os.MFD_CLOEXEC)
            sources.append(fd)
            self.addCleanup(os.close, fd)
            os.write(fd, b'explicit fixture input')
            config.inherited_source_fds.append(fd)
            config.launch.inherited_fds.append(target)
        original = subprocess.Popen
        executable = str(baseline.supervisor.owned.existing.PROBE.with_name('spatial-authority-supervisor'))
        def spawn(arguments, *args, **kwargs):
            if str(arguments[0]) == executable:
                kwargs['pass_fds'] = (*kwargs.get('pass_fds', ()), *sources)
            return original(arguments, *args, **kwargs)
        with patch.object(subprocess, 'Popen', side_effect=spawn):
            return self.supervisor_process(config)

    def test_native_target_cannot_replace_an_explicit_descriptor(self):
        proc, _ = self.mapped_broker([38])
        self.assertEqual(proc.wait(timeout=5), 2)
        self.assertFalse((self.journal / 'launch.db').exists())
        self.assertFalse(self.child_config.exists())

    def test_descriptor_capacity_reserves_both_generated_descriptors(self):
        proc, _ = self.mapped_broker(range(60, 75))
        self.assertEqual(proc.wait(timeout=5), 2)
        self.assertFalse((self.journal / 'launch.db').exists())
        self.assertFalse(self.child_config.exists())

    def test_exact_descriptor_capacity_launches_real_broker(self):
        proc, channel = self.mapped_broker(range(60, 74))
        initial = self.message(channel, launch.AuthorityLaunchReply)
        self.assertTrue(initial.HasField('view'), initial)
        self.assertEqual(BrokerPipe(self, proc).call('system.hello')['implementation'], 'production')
        reply = self.control(channel, stop=launch.AuthorityLaunchQuery())
        self.assertEqual(reply.view.record.stage, launch.OWNED_SCOPES_STOPPED)
        self.assertEqual(proc.wait(timeout=5), 0)
        self.checked_cleanup()

    def test_resource_descriptor_capacity_reserves_all_four_generated_descriptors(self):
        proc, _ = self.mapped_broker(range(60, 74), with_resources=True)
        self.assertEqual(proc.wait(timeout=5), 2)
        diagnostic = '\n'.join(p.read_text(errors='replace') for p in self.root.glob('supervisor-*.log'))
        self.assertIn('Invalid supervisor configuration', diagnostic)
        self.assertFalse((self.journal / 'launch.db').exists())
        self.assertFalse(self.child_config.exists())

    def test_exact_resource_descriptor_capacity_launches_real_broker(self):
        # Four generated descriptors leave room for twelve explicit sources.
        proc, channel = self.mapped_broker(range(60, 72), with_resources=True)
        initial = self.message(channel, launch.AuthorityLaunchReply)
        self.assertTrue(initial.HasField('view'), initial)
        self.assertEqual(BrokerPipe(self, proc).call('system.hello')['implementation'], 'production')
        reply = self.control(channel, stop=launch.AuthorityLaunchQuery())
        self.assertEqual(reply.view.record.stage, launch.OWNED_SCOPES_STOPPED)
        self.assertEqual(proc.wait(timeout=5), 0)
        self.checked_cleanup()

    def test_one_over_resource_descriptor_capacity_fails_before_scope_ownership(self):
        proc, _ = self.mapped_broker(range(60, 73), with_resources=True)
        self.assertEqual(proc.wait(timeout=5), 2)
        diagnostic = '\n'.join(p.read_text(errors='replace') for p in self.root.glob('supervisor-*.log'))
        self.assertIn('Invalid supervisor configuration', diagnostic)
        self.assertFalse((self.journal / 'launch.db').exists())
        self.assertFalse(self.child_config.exists())
        self.assertEqual(self.activation_rows(), [])

    def test_compositor_scope_cannot_supply_a_broker_credential(self):
        original = baseline.supervisor.native_pb.NativeScopeConfig
        def compositor_config(*args, **kwargs):
            kwargs['role'] = security.COMPOSITOR
            return original(*args, **kwargs)
        with patch.object(baseline.supervisor.native_pb, 'NativeScopeConfig', side_effect=compositor_config):
            proc, _ = self.supervisor_process(self.broker_config())
        self.assertEqual(proc.wait(timeout=5), 2)
        diagnostic = '\n'.join(p.read_text(errors='replace') for p in self.root.glob('supervisor-*.log'))
        self.assertIn('Broker handoff requires a broker native scope', diagnostic)
        self.assertFalse((self.journal / 'launch.db').exists())
        self.assertFalse((self.root / 'execution').exists())
        self.assertEqual(self.activation_rows(), [])

    def cleanup_cgroups(self):
        if getattr(self, 'preserve_cgroup_evidence', False):
            return
        super().cleanup_cgroups()

    def test_cold_bootstrap_root_mismatch_preserves_unbound_cleanup_evidence(self):
        self.assertTrue(os.environ.get('SPATIAL_TEST_BROKER_CONFIG'))
        self.assertTrue(os.environ.get('SPATIAL_SECURITY_EVIDENCE'),
                        'Cold failure evidence must be retained outside the fixture tree')
        config = self.broker_config()
        self.assertTrue(config.HasField('broker_resources'))
        real_parent = self.root / 'execution-real'
        real_parent.mkdir()
        alias_parent = self.root / 'execution-alias'
        alias_parent.symlink_to(real_parent, target_is_directory=True)
        state_index = list(config.launch.arguments).index('--state-dir')
        config.launch.arguments[state_index + 1] = str(alias_parent / 'execution')
        candidate = {}
        def exact_cleanup():
            if candidate.get('done'):
                return
            scope = candidate.get('scope')
            if scope is None:
                self.preserve_cgroup_evidence = True
                self._preserve_missing_cold_identity(candidate)
                self.fail('No exact candidate cgroup identity; preserving evidence')
            try:
                self.assertEqual(scope.parent, self.cgroup)
                self.assertFalse(scope.is_symlink())
                st = scope.stat(follow_symlinks=False)
                self.assertEqual(st.st_uid, os.getuid())
                self.assertEqual((st.st_dev, st.st_ino), candidate['identity'])
                self.assertEqual((scope/'cgroup.procs').read_text().split(), [])
                self.assertEqual(self._cgroup_value(scope/'cgroup.events','populated'),'0')
                self.assertEqual(self._cgroup_value(scope/'cgroup.stat','nr_descendants'),'0')
                self.assertFalse(any(x.is_dir() for x in scope.iterdir()))
                scope.rmdir()
                candidate['scope'] = None
                candidate['done'] = True
            except Exception:
                self.preserve_cgroup_evidence = True
                raise
        self.addCleanup(exact_cleanup)
        candidate['started'] = True
        proc, channel = self.supervisor_process(config)
        reply = self.message(channel, launch.AuthorityLaunchReply)
        candidate['reply'] = reply
        self.assertTrue(reply.HasField('error'), reply)
        self.assertEqual(reply.error.message,'Cleanup bootstrap descriptor names a different state root',reply)
        self.assertFalse(reply.error.HasField('cleanup_record'),reply)
        self.assertEqual(proc.wait(timeout=10),2)
        record = self.read_record()
        self.assertEqual(record.stage,launch.CLEANUP_PENDING)
        self.assertTrue(record.HasField('process'))
        self.assertFalse(record.HasField('process_stopped'),record)
        self.assertFalse(record.HasField('native_stopped'),record)
        scope_name = record.process.scope_name
        self.assertTrue(scope_name)
        self.assertNotIn(scope_name, ('.', '..'))
        self.assertNotIn('/', scope_name)
        self.assertNotIn('\\', scope_name)
        scope = self.cgroup / scope_name
        st = scope.stat(follow_symlinks=False)
        journal_identity = (record.process.device, record.process.inode)
        self.assertEqual((st.st_dev, st.st_ino), journal_identity,
                         'Candidate cgroup path differs from the durable ProcessScope identity')
        candidate.update(scope=scope, identity=journal_identity)
        self.assertEqual((scope/'cgroup.procs').read_text().split(),[])
        self.assertEqual(self._cgroup_value(scope/'cgroup.events','populated'),'0')
        self.assertEqual(self._cgroup_value(scope/'cgroup.stat','nr_descendants'),'0')
        self.assertFalse(any(x.is_dir() for x in scope.iterdir()))
        try:
            self._preserve_cold_failure(reply,record,scope,st)
        except Exception:
            self.preserve_cgroup_evidence = True
            raise
        exact_cleanup()

    def _preserve_missing_cold_identity(self, candidate):
        base = os.environ.get('SPATIAL_SECURITY_EVIDENCE')
        if not base:
            return
        evidence = Path(base) / ('cold-bootstrap-missing-identity-' + str(time.monotonic_ns()))
        evidence.mkdir(parents=True, mode=0o700)
        reply = candidate.get('reply')
        if reply is not None:
            (evidence/'error.pb').write_bytes(reply.SerializeToString())
        for path in self.journal.iterdir():
            if path.is_file():
                shutil.copyfile(path, evidence/('journal-'+path.name))
        for path in self.root.glob('supervisor-*.log'):
            if path.is_file():
                shutil.copyfile(path, evidence/path.name)

    @staticmethod
    def _cgroup_value(path,key):
        values=dict(line.split(maxsplit=1) for line in path.read_text().splitlines())
        if key not in values: raise AssertionError(f'Missing {key} in {path}')
        return values[key]

    def _preserve_cold_failure(self,reply,record,scope,identity):
        base=os.environ.get('SPATIAL_SECURITY_EVIDENCE')
        if not base: self.fail('SPATIAL_SECURITY_EVIDENCE is required')
        evidence=Path(base)/('cold-bootstrap-root-mismatch-'+str(time.monotonic_ns()))
        evidence.mkdir(parents=True,mode=0o700)
        (evidence/'error.pb').write_bytes(reply.SerializeToString())
        (evidence/'launch-record.pb').write_bytes(record.SerializeToString())
        details={'path':str(scope),'device':identity.st_dev,'inode':identity.st_ino,
            'scope_name':record.process.scope_name,'process_stopped':record.HasField('process_stopped'),
            'native_stopped':record.HasField('native_stopped'),
            'procs':(scope/'cgroup.procs').read_text().split(),
            'events':dict(line.split(maxsplit=1) for line in (scope/'cgroup.events').read_text().splitlines()),
            'stat':dict(line.split(maxsplit=1) for line in (scope/'cgroup.stat').read_text().splitlines())}
        (evidence/'cgroup.json').write_text(json.dumps(details,sort_keys=True,indent=2)+'\n')
        for path in self.journal.iterdir():
            if path.is_file(): shutil.copyfile(path,evidence/('journal-'+path.name))
        for path in self.root.glob('supervisor-*.log'):
            if path.is_file(): shutil.copyfile(path,evidence/path.name)

    def test_failed_broker_exec_records_checked_native_cleanup_without_activation(self):
        config = self.broker_config()
        config.launch.executable = '/nonexistent-supervised-workspaced'
        proc, channel = self.supervisor_process(config)
        reply = self.message(channel, launch.AuthorityLaunchReply)
        self.assertTrue(reply.HasField('error'), reply)
        self.assertIn('Authority exec failed', reply.error.message)
        self.assertEqual(proc.wait(timeout=5), 2)
        self.checked_cleanup()
        self.assertEqual(self.activation_rows(), [])


if __name__ == '__main__':
    suite = unittest.TestSuite(BrokerHandoffRegressionTests(name)
        for name in BrokerHandoffRegressionTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
