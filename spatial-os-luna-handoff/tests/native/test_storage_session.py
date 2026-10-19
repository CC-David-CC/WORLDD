"""Actual retained native broker with its bounded root storage owner."""
import hashlib
from contextlib import closing
import gc
import json
import os
from pathlib import Path
import re
import sqlite3
import select
import signal
import socket
import subprocess
import sys
import unittest
from google.protobuf import text_format
import test_session_application as existing
import proc_identity
import broker_pb2 as broker
import core_pb2 as core
import launch_pb2 as launch


class NativeStorageSessionTests(existing.SessionApplicationTests):
    bootstrap_in_session_delegate = True

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
        metadata = self.execution.stat()
        self.assertEqual(metadata.st_uid, os.getuid(), 'Broker metadata root must belong to its execution identity')
        self.assertEqual(metadata.st_mode & 0o777, 0o700, 'Broker metadata root must remain private')
        self.configuration = broker.BrokerProcessConfig()
        text_format.Parse((self.volume_root/'broker.textproto').read_text(), self.configuration)
        os.environ['SPATIAL_TEST_BROKER_CONFIG'] = str(self.volume_root/'broker.textproto')

    def volume_command(self, operation, *arguments):
        command = ['sudo', '-n', sys.executable, '-B', str(self.volume_helper), operation,
                   str(self.build), str(self.volume_root), str(self.volume_root.parent), *arguments]
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

    def test_cold_start_cleanup_binding_smoke(self):
        self.held_application()
        execution_db = self.execution/'execution.db'
        journal_db = self.execution/'file-operations/file-operations.db'
        self.assertTrue(execution_db.is_file())
        self.assertTrue(journal_db.is_file())
        with closing(sqlite3.connect(execution_db)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 8)
        with closing(sqlite3.connect(journal_db)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 8)
        self.act(stop=core.ActorStopRequest()); self.released()
        world = self.request(stop_world=self.owner).cleanup
        self.assertEqual(world.stage, launch.OWNED_SCOPES_STOPPED)
        self.finish_session()
        self.assertEqual(self.checked_cleanup().stage, launch.OWNED_SCOPES_STOPPED)

    def test_failed_cleanup_retains_owner_for_explicit_retry(self):
        self.held_application()
        self.active_owner()
        self.act(stop=core.ActorStopRequest())
        self.released()
        self.request(stop_world=self.owner)
        database = self.execution/'file-operations'/'file-operations.db'
        self.assertTrue(database.is_file(), 'The cold broker must create its real execution journal before cleanup')
        with closing(sqlite3.connect(database, timeout=0, isolation_level=None)) as lock:
            lock.execute('BEGIN EXCLUSIVE')
            failed = self.request(expect_error=True, stop_broker=launch.AuthorityLaunchQuery())
            self.assert_cleanup_failure_reason(failed)
            self.assert_route_fenced(self.request(expect_error=True, broker=existing.make_request('system.hello', {})))
            self.assert_retirement_denied(self.request(expect_error=True, retire_broker=launch.AuthorityLaunchQuery()))
            self.check_active_participants()
            lock.execute('ROLLBACK')
        stopped = self.request(stop_broker=launch.AuthorityLaunchQuery()).cleanup
        self.assertEqual(stopped, self.checked_cleanup())
        self.assertEqual(stopped.stage, launch.OWNED_SCOPES_STOPPED)
        retirement = self.request(retire_broker=launch.AuthorityLaunchQuery())
        self.assertTrue(retirement.HasField('retirement'))
        self.finish_session()

    def retain_supervisor_pidfd(self):
        children = Path(f'/proc/{self.coordinator.pid}/task/{self.coordinator.pid}/children').read_text().split()
        expected_exe = str((self.build/'spatial-authority-supervisor').resolve(strict=True))
        pins = []
        supervisors = []
        try:
            for value in children:
                pid = int(value)
                try:
                    pin = os.pidfd_open(pid)
                except ProcessLookupError:
                    continue
                pins.append(pin)
                if select.select([pin], [], [], 0)[0]:
                    continue
                try:
                    identity = proc_identity.inspect_process_identity(pid)
                except ProcessLookupError:
                    if select.select([pin], [], [], 0)[0]:
                        continue
                    raise RuntimeError('pinned child is absent from proc during identity inspection')
                if select.select([pin], [], [], 0)[0]:
                    continue
                if identity['ppid'] != self.coordinator.pid:
                    continue
                if identity['exe'] == expected_exe:
                    supervisors.append((pid, pin))
            self.assertEqual(len(supervisors), 1, children)
            pid, selected = supervisors[0]
            for pin in pins:
                if pin != selected:
                    os.close(pin)
            return pid, selected
        except BaseException:
            for pin in pins:
                try:
                    os.close(pin)
                except OSError:
                    pass
            raise

    def prepare_locked_cleanup(self):
        self.held_application()
        unit = self.active_owner()
        self.act(stop=core.ActorStopRequest())
        self.released()
        self.request(stop_world=self.owner)
        database = self.execution/'file-operations'/'file-operations.db'
        self.assertTrue(database.is_file(), 'Cold broker startup must create its real journal')
        lock = sqlite3.connect(database, timeout=0, isolation_level=None)
        lock.execute('BEGIN EXCLUSIVE')
        return unit, lock

    def assert_cleanup_failure_reason(self, reply):
        self.assertTrue(reply.HasField('error'))
        self.assertRegex(reply.error.message.lower(), r'(cleanup|pre-prune|prune|deadline|locked|busy)')

    def assert_route_fenced(self, reply):
        self.assertTrue(reply.HasField('error'))
        self.assertIn('session supervisor ownership lost', reply.error.message.lower())

    def assert_retirement_denied(self, reply):
        self.assertTrue(reply.HasField('error'))
        self.assertIn('broker retirement requires retained checked cleanup', reply.error.message.lower())

    def assert_pending_without_receipts(self):
        record = self.read_record()
        self.assertEqual(record.stage, launch.CLEANUP_PENDING)
        self.assertFalse(record.HasField('process_stopped'))
        self.assertFalse(record.HasField('native_stopped'))
        self.check_active_participants()

    def disconnect_coordinator_and_wait_supervisor(self, supervisor_pidfd):
        self.coordinator.terminate()
        self.assertEqual(self.coordinator.wait(timeout=8), -signal.SIGTERM)
        self.assertEqual(select.select([supervisor_pidfd], [], [], 35)[0], [supervisor_pidfd],
            'Disconnected supervisor did not finish bounded best-effort teardown')

    def test_cleanup_parent_disconnect_preserves_pending_without_receipt(self):
        unit, lock = self.prepare_locked_cleanup()
        _supervisor_pid, supervisor_pidfd = self.retain_supervisor_pidfd()
        try:
            failed = self.request(expect_error=True, stop_broker=launch.AuthorityLaunchQuery())
            self.assert_cleanup_failure_reason(failed)
            self.assert_pending_without_receipts()
            self.disconnect_coordinator_and_wait_supervisor(supervisor_pidfd)
        finally:
            lock.execute('ROLLBACK'); lock.close(); os.close(supervisor_pidfd)
        self.assert_pending_without_receipts()
        self.assert_owner_stopped(unit)

    def test_pending_supervisor_signal_exits_without_receipt_or_retirement(self):
        unit, lock = self.prepare_locked_cleanup()
        _supervisor_pid, supervisor_pidfd = self.retain_supervisor_pidfd()
        try:
            failed = self.request(expect_error=True, stop_broker=launch.AuthorityLaunchQuery())
            self.assert_cleanup_failure_reason(failed)
            self.assert_pending_without_receipts()
            signal.pidfd_send_signal(supervisor_pidfd, signal.SIGTERM)
            self.assertEqual(select.select([supervisor_pidfd], [], [], 35)[0], [supervisor_pidfd],
                'SIGTERM did not release the pending supervisor through bounded teardown')
            self.assertIsNone(self.coordinator.poll(), 'The management parent should remain connected for these checks')
            self.assert_route_fenced(self.request(expect_error=True, broker=existing.make_request('system.hello', {})))
            self.assert_retirement_denied(self.request(expect_error=True, retire_broker=launch.AuthorityLaunchQuery()))
            self.check_active_participants()
            lock.execute('ROLLBACK'); lock.close()
            self.assert_pending_without_receipts()
            self.assert_owner_stopped(unit)
            self.coordinator.terminate()
            self.assertEqual(self.coordinator.wait(timeout=8), -signal.SIGTERM)
        finally:
            if lock: lock.close()
            os.close(supervisor_pidfd)

    def test_cleanup_retry_exhaustion_stays_pending_and_rejects_stop(self):
        unit, lock = self.prepare_locked_cleanup()
        _supervisor_pid, supervisor_pidfd = self.retain_supervisor_pidfd()
        try:
            for attempt in range(3):
                with self.subTest(cleanup_attempt=attempt + 1):
                    reply = self.request(expect_error=True, stop_broker=launch.AuthorityLaunchQuery())
                    self.assert_cleanup_failure_reason(reply)
            self.assert_pending_without_receipts()
            self.assert_route_fenced(self.request(expect_error=True, broker=existing.make_request('system.hello', {})))
            self.assert_retirement_denied(self.request(expect_error=True, retire_broker=launch.AuthorityLaunchQuery()))
            self.check_active_participants()
            self.assertEqual(select.select([supervisor_pidfd], [], [], 0.1)[0], [],
                'Exhausted supervisor must retain its pending owner while the parent stays connected')
            rejected = self.request(expect_error=True, stop_broker=launch.AuthorityLaunchQuery())
            self.assertIn('retry limit exhausted', rejected.error.message.lower())
            self.assertIsNone(self.coordinator.poll())
            self.assertEqual(select.select([supervisor_pidfd], [], [], 0.1)[0], [])
            self.assert_pending_without_receipts()
            lock.execute('ROLLBACK'); lock.close()
            self.disconnect_coordinator_and_wait_supervisor(supervisor_pidfd)
        finally:
            if lock: lock.close()
            os.close(supervisor_pidfd)
        self.assert_pending_without_receipts()
        self.assert_owner_stopped(unit)

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
