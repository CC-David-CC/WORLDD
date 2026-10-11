"""Actual retained native broker with its bounded root storage owner."""
import hashlib
import gc
import json
import os
from pathlib import Path
import re
import select
import socket
import subprocess
import sys
import unittest
from google.protobuf import text_format
import test_session_application as existing
import broker_pb2 as broker
import core_pb2 as core
import launch_pb2 as launch


class NativeStorageSessionTests(existing.SessionApplicationTests):
    def setUp(self):
        super().setUp()
        assert socket.gethostname().split('.')[0] in ('sb16', 'sb16-2')
        self.volume_root = Path(os.environ['SPATIAL_SECURITY_EVIDENCE'])/self._testMethodName
        self.volume_root.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.build = existing.scopes.PROBE.parent
        self.volume_helper = Path(__file__).with_name('storage_session_volume.py')
        self.previous_config = os.environ['SPATIAL_TEST_BROKER_CONFIG']
        self.addCleanup(self.cleanup_volume)
        self.volume_command('prepare', self.previous_config)
        self.execution = self.volume_root/'volume'
        self.configuration = broker.BrokerProcessConfig()
        text_format.Parse((self.volume_root/'broker.textproto').read_text(), self.configuration)
        os.environ['SPATIAL_TEST_BROKER_CONFIG'] = str(self.volume_root/'broker.textproto')

    def volume_command(self, operation, *arguments):
        command = ['sudo', '-n', sys.executable, str(self.volume_helper), operation,
                   str(self.build), str(self.volume_root), *arguments]
        result = subprocess.run(command, capture_output=True, text=True, timeout=45)
        (self.volume_root/(operation+'.log')).write_text(result.stdout+result.stderr)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        return json.loads(result.stdout)

    def cleanup_volume(self):
        os.environ['SPATIAL_TEST_BROKER_CONFIG'] = self.previous_config
        # SQLite's context manager ends its transaction; unreachable fixture connections
        # can retain execution.db until cyclic garbage collection closes them.
        def volume_handles():
            paths = []
            for entry in Path('/proc/self/fd').iterdir():
                try: value = os.readlink(entry)
                except FileNotFoundError: continue
                if value.startswith(str(self.volume_root/'volume')+'/'): paths.append(value)
            return paths
        before = volume_handles(); collected = gc.collect(); after = volume_handles()
        (self.volume_root/'fixture-descriptors.json').write_text(json.dumps(dict(
            before_collection=before, collected=collected, after_collection=after), indent=2))
        self.assertEqual(after, [], 'Fixture retains descriptors into its test volume')
        if (self.volume_root/'volume-setup.json').exists(): self.volume_command('cleanup')

    def broker_config(self, target=38):
        config = super().broker_config(target)
        states = [index for index, value in enumerate(config.launch.arguments) if value == '--state-dir']
        self.assertEqual(len(states), 1)
        config.launch.arguments[states[0]+1] = str(self.execution)
        config.broker_storage_owner.CopyFrom(self.configuration.storage_owner)
        config.broker_storage_target_fd = 254
        config.require_broker_confinement = True
        return config

    def world_config(self):
        # Volume setup and Git bootstrap can exceed the fixture's three-second
        # TLS idle deadline. Reconnect before requesting a fresh signed challenge.
        self.issuer.peer.close()
        self.issuer.peer = self.issuer.connect()
        return super().world_config()

    def active_owner(self):
        units = set(re.findall(r'Storage owner unit: (spatial-storage-owner-[a-f0-9]{32}\.service)', self.logpath.read_text()))
        self.assertEqual(len(units), 1, self.logpath.read_text())
        unit = units.pop()
        inspected = self.volume_command('inspect', unit, str(self.world_ids.workspaced_pid))['owner_witness']
        pid, handles = inspected['owner_pid'], inspected['broker_pidfd']
        group = Path('/sys/fs/cgroup/system.slice')/unit
        io = (group/'io.max').read_text()
        setup = json.loads((self.volume_root/'volume-setup.json').read_text())
        observed = {line.split()[0]:dict(part.split('=') for part in line.split()[1:]) for line in io.splitlines()}
        for major, minor in setup['devices']:
            self.assertEqual(observed[f'{major}:{minor}']['rbps'], str(64*2**20))
            self.assertEqual(observed[f'{major}:{minor}']['wbps'], str(16*2**20))
        (self.volume_root/'owner-kernel.json').write_text(json.dumps(dict(unit=unit, owner_pid=pid,
            broker_pid=self.world_ids.workspaced_pid, broker_pidfd=handles, io_max=io,
            executable_sha256=hashlib.sha256(Path('/usr/local/libexec/spatial-storage-owner').read_bytes()).hexdigest()), indent=2))
        return unit

    def assert_owner_stopped(self, unit):
        state = subprocess.check_output(['systemctl', 'show', unit, '-p', 'MainPID', '-p', 'ActiveState'], text=True)
        self.assertIn('MainPID=0\n', state); self.assertIn('ActiveState=inactive\n', state)
        (self.volume_root/'owner-after.txt').write_text(state)

    def test_native_storage_session_retains_owner_and_confined_application(self):
        self.held_application()
        unit = self.active_owner()
        marker = self.project/'native-storage-checkpoint.txt'; marker.write_text('durable workspace data\n')
        self.act(stop=core.ActorStopRequest()); self.released()
        world = self.request(stop_world=self.owner).cleanup
        self.assertEqual(world.stage, launch.OWNED_SCOPES_STOPPED)
        self.assertEqual(world.process, self.world_record.process)
        self.assertEqual(world.native, self.world_record.native)
        (self.volume_root/'world-cleanup.pb').write_bytes(world.SerializeToString())
        self.finish_session()
        (self.volume_root/'broker-cleanup.pb').write_bytes(self.checked_cleanup().SerializeToString())
        self.assert_owner_stopped(unit)
        self.assertEqual(marker.read_text(), 'durable workspace data\n')
        self.assertTrue((self.app_data/'confinement.json').is_file())
        self.assertFalse(self.broker_process_group().parent.exists())

    def test_native_storage_owner_loss_stops_execution_and_records_cleanup(self):
        self.held_application()
        unit = self.active_owner()
        group = self.broker_process_group().parent
        pids = {int(pid) for path in [group, *(p for p in group.rglob('*') if p.is_dir())]
                for pid in (path/'cgroup.procs').read_text().split()}
        self.assertIn(self.world_ids.workspaced_pid, pids); self.assertGreater(len(pids), 1)
        pins = [os.pidfd_open(pid) for pid in pids]
        try:
            result = subprocess.run(['sudo', '-n', 'systemctl', 'stop', unit], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            for pin in pins: self.assertEqual(select.select([pin], [], [], 8)[0], [pin])
            # Keep the retained recovery interface available after execution loss.
            self.assertIsNone(self.coordinator.poll())
            self.request(expect_error=True, broker=existing.make_request('system.hello', {}))
            stopped = self.request(stop_broker=launch.AuthorityLaunchQuery()).cleanup
            self.assertEqual(stopped, self.checked_cleanup())
            self.assertEqual(stopped.stage, launch.OWNED_SCOPES_STOPPED)
            (self.volume_root/'broker-cleanup.pb').write_bytes(stopped.SerializeToString())
            world = self.request(stop_world=self.owner).cleanup
            self.assertEqual(world.stage, launch.OWNED_SCOPES_STOPPED)
            self.assertEqual(world.process, self.world_record.process)
            self.assertEqual(world.native, self.world_record.native)
            (self.volume_root/'world-cleanup.pb').write_bytes(world.SerializeToString())
            self.finish_session()
            self.assertFalse(group.exists())
            self.assert_owner_stopped(unit)
        finally:
            for pin in pins: os.close(pin)


if __name__ == '__main__':
    suite = unittest.TestSuite(NativeStorageSessionTests(name) for name in NativeStorageSessionTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
