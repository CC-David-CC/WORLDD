"""Production session handoff, native application budgets, and retained cleanup."""
import json
import os
from pathlib import Path
import select
import sys
import time
import unittest
import test_session_application as existing
import broker_pb2 as broker
import launch_pb2 as launch
import process_scope_pb2 as process

base = existing.existing.base
scopes = base.baseline.supervisor.owned.existing
if scopes.CGROUP.name == 'manager': scopes.CGROUP = scopes.CGROUP.parent
assert scopes.CGROUP.name.startswith('spatial-m03-resources-'), 'Use the dedicated resource delegate'
assert {'cpu', 'memory', 'pids', 'io'} <= set((scopes.CGROUP / 'cgroup.controllers').read_text().split())
(scopes.CGROUP / 'cgroup.subtree_control').write_text('+cpu +memory +pids +io\n')
BASELINE = None
if sys.argv[1:2] == ['--baseline-supervisor']:
    BASELINE = Path(sys.argv[2]); del sys.argv[1:3]
MIB = 1024 * 1024


class SessionResourceTests(existing.SessionApplicationTests):
    def setUp(self):
        super().setUp()
        (self.cgroup / 'cgroup.subtree_control').write_text('+cpu +memory +pids +io\n')

    def cleanup_cgroups(self):
        for path in [p for p in self.cgroup.iterdir() if p.is_dir()]:
            (path / 'cgroup.kill').write_text('1\n')
            deadline = time.monotonic() + 3
            while 'populated 1' in (path / 'cgroup.events').read_text():
                self.assertLess(time.monotonic(), deadline); time.sleep(.01)
            for child in sorted((p for p in path.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
                child.rmdir()
            path.rmdir()
        self.cgroup.rmdir()

    def broker_config(self, target=38):
        config = super().broker_config(target)
        device = Path('/sys/dev/block') / f'{os.major(self.project.stat().st_dev)}:{os.minor(self.project.stat().st_dev)}'
        device = device.resolve(strict=True)
        if (device / 'partition').exists(): device = device.parent
        self.major, self.minor = map(int, (device / 'dev').read_text().split(':'))
        def budget(memory, processes, cpu, writes):
            return process.ProcessScopeBudget(memory_bytes=memory*MIB, swap_bytes=0,
                processes=processes, cpu_microseconds=cpu,
                io=[dict(major=self.major, minor=self.minor,
                    read_bytes_per_second=64*MIB, write_bytes_per_second=writes*MIB)])
        config.broker_resources.CopyFrom(broker.BrokerResourceConfig(protocol_version=2,
            broker_budget=budget(1024, 128, 400000, 32),
            manager_budget=budget(256, 32, 200000, 16),
            default_policy=broker.WorkspaceResourcePolicy(total=budget(512, 64, 200000, 32),
                actor_default=budget(256, 32, 100000, 16),
                profiles=[broker.ApplicationResourceProfile(name='development', maximum=budget(128, 16, 100000, 8))])))
        # SessionBroker must allocate its separate administration channel elsewhere.
        config.broker_resource_target_fd = 255
        return config

    def broker_process_group(self):
        return super().broker_process_group()

    def preserve(self, name, data):
        target = os.environ.get('SPATIAL_SECURITY_EVIDENCE')
        if target:
            Path(target).mkdir(parents=True, exist_ok=True)
            (Path(target) / (self._testMethodName + '-' + name + '.json')).write_text(json.dumps(data, indent=2))

    def test_supervisor_binds_sealed_resource_policy(self):
        proc, channel = self.supervisor_process(self.broker_config(), supervisor_binary=BASELINE)
        initial = self.message(channel, launch.AuthorityLaunchReply)
        self.assertTrue(initial.HasField('view'), initial)
        self.assertEqual(initial.view.record.stage, launch.START_ATTEMPTED)
        connection = base.BrokerPipe(self, proc)
        self.assertEqual(connection.call('system.hello')['implementation'], 'production')
        manager = self.broker_process_group()
        pid = int((manager / 'cgroup.procs').read_text().strip())
        pin = os.pidfd_open(pid)
        try:
            self.assertNotIn(255, self.observed_descriptors(pid, pin))
            self.assertEqual((manager.parent / 'cgroup.procs').read_text().strip(), '')
            self.preserve('bound', dict(scope=str(manager.parent), broker_pid=pid,
                descendants=[p.name for p in manager.parent.iterdir() if p.is_dir()]))
            stopped = self.control(channel, stop=launch.AuthorityLaunchQuery())
            self.assertEqual(stopped.view.record.stage, launch.OWNED_SCOPES_STOPPED)
            self.assertEqual(select.select([pin], [], [], 5)[0], [pin])
            self.assertFalse(manager.parent.exists())
        finally: os.close(pin)
        channel.close(); self.assertEqual(proc.wait(timeout=8), 0)

    def rejected_before_ownership(self, change, message):
        config = self.broker_config(); change(config)
        proc, channel = self.supervisor_process(config)
        self.assertNotEqual(proc.wait(timeout=8), 0)
        self.assertEqual(channel.recv(1), b'')
        logs = list(self.root.glob('supervisor-*.log')); self.assertEqual(len(logs), 1)
        self.assertIn(message, logs[0].read_text())
        self.assertFalse((self.journal / 'launch.db').exists())
        self.assertEqual([p for p in self.cgroup.iterdir() if p.is_dir()], [])

    def test_resource_descriptor_cannot_collide_with_participant(self):
        self.rejected_before_ownership(lambda c: setattr(c, 'broker_resource_target_fd', c.participant_target_fd),
            'Invalid broker resource descriptor target')

    def test_resource_template_cannot_select_a_cgroup(self):
        self.rejected_before_ownership(lambda c: setattr(c.broker_resources, 'delegated_cgroup', str(self.cgroup)),
            'Broker resource template must not select a cgroup')

    def test_resource_arguments_cannot_override_owned_handoff(self):
        self.rejected_before_ownership(lambda c: c.launch.arguments.extend(['--resource-config', '/tmp/unowned-policy']),
            'Supervisor owns broker resource arguments')

    def storage_template(self, config):
        config.broker_storage_owner.protocol_version = 1
        config.broker_storage_owner.volume_root = str(self.execution)
        config.broker_storage_owner.owner_configuration = '/nonexistent-storage-policy'
        config.broker_storage_target_fd = 254

    def test_storage_descriptor_cannot_collide_with_participant(self):
        def change(config):
            self.storage_template(config); config.broker_storage_target_fd = config.participant_target_fd
        self.rejected_before_ownership(change, 'Invalid broker storage descriptor target')

    def test_storage_descriptor_cannot_collide_with_resource_policy(self):
        def change(config):
            self.storage_template(config); config.broker_storage_target_fd = config.broker_resource_target_fd
        self.rejected_before_ownership(change, 'Invalid broker storage descriptor target')

    def test_storage_arguments_cannot_override_owned_handoff(self):
        def change(config):
            self.storage_template(config); config.launch.arguments.extend(['--storage-fd', '100'])
        self.rejected_before_ownership(change, 'Supervisor owns broker storage arguments')

    def test_storage_volume_must_match_broker_state(self):
        def change(config):
            self.storage_template(config); config.broker_storage_owner.volume_root = '/different-volume'
        self.rejected_before_ownership(change, 'Broker state must match its storage-owner volume')

    def test_storage_template_requires_reserved_descriptor(self):
        def change(config):
            self.storage_template(config); config.ClearField('broker_storage_target_fd')
        self.rejected_before_ownership(change, 'Invalid supervisor configuration')

    def test_native_application_has_kernel_budgets_inside_broker_scope(self):
        self.held_application()
        root = self.broker_process_group().parent
        leaves = [p.parent for p in root.rglob('cgroup.procs') if len(p.relative_to(root).parts) == 4]
        self.assertEqual(len(leaves), 1)
        leaf = leaves[0]; actor = leaf.parent; workspace = actor.parent
        self.assertEqual((root / 'cgroup.procs').read_text().strip(), '')
        self.assertEqual((workspace / 'memory.max').read_text().strip(), str(512*MIB))
        self.assertEqual((actor / 'memory.max').read_text().strip(), str(256*MIB))
        self.assertEqual((leaf / 'memory.max').read_text().strip(), str(128*MIB))
        self.assertEqual((leaf / 'pids.max').read_text().strip(), '16')
        self.assertEqual((leaf / 'cpu.max').read_text().strip(), '100000 100000')
        self.assertEqual((leaf / 'memory.swap.max').read_text().strip(), '0')
        self.assertIn(f'{self.major}:{self.minor} rbps={64*MIB} wbps={8*MIB}', (leaf / 'io.max').read_text())
        members = set(map(int, (leaf / 'cgroup.procs').read_text().split()))
        self.assertGreaterEqual(len(members), 4)
        self.assertFalse(members.intersection([self.world_ids.workspaced_pid, self.world_ids.worldd_pid, self.world_ids.spatialwm_pid]))
        self.preserve('kernel', {str(path.relative_to(root)):
            {name:(path/name).read_text() for name in ['memory.max', 'memory.swap.max', 'pids.max', 'cpu.max', 'io.max', 'cgroup.procs']}
            for path in [workspace, actor, leaf]})
        cleanup = self.request(stop_world=self.owner).cleanup
        self.assertEqual(cleanup.stage, launch.OWNED_SCOPES_STOPPED)
        self.finish_session()
        self.assertFalse(root.exists())


if __name__ == '__main__':
    names = sys.argv[1:] or [name for cls in (SessionResourceTests, existing.SessionApplicationTests)
        for name in cls.__dict__ if name.startswith('test_')]
    suite = unittest.TestSuite(SessionResourceTests(name) for name in names)
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
