"""Real SQLite write faults and recovery through the production world daemon."""
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from harness.rpc import Client, RpcError

DAEMON, PROBE = sys.argv[1:3]
sys.argv[1:3] = []


class StorageFaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def start(self, name='world', enabled=True, clock=False):
        argv = [DAEMON]
        if enabled:
            argv.append('--fixture-faults')
        if clock:
            argv.append('--fixture-clock')
        client = Client(argv, self.root/name, self.root/'logs', timeout=4)
        self.addCleanup(client.stop)
        client.call('system.hello')
        return client

    def error(self, code, client, method, **params):
        with self.assertRaises(RpcError) as caught:
            client.call(method, params)
        self.assertEqual(caught.exception.code, code)

    def room(self, client, ident='room'):
        return client.call('world.create', dict(entity=dict(id=ident, kind='room', label='before')))

    def arm(self, client):
        self.assertEqual(client.call('fault.arm', dict(point='storage.next_write', action='error', errno='ENOSPC')),
                         dict(armed=True))

    def test_real_vfs_lifecycles_delegate_io_and_isolate_pending_faults(self):
        result = subprocess.run([PROBE, str(self.root/'probe')], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('16 VFS lifecycle', result.stdout)

    def test_create_update_delete_errors_preserve_rows_and_allow_real_retry(self):
        for operation in ('create', 'update', 'delete'):
            with self.subTest(operation=operation):
                client = self.start(operation)
                self.room(client)
                before = client.call('world.snapshot')
                self.arm(client)
                if operation == 'create':
                    params = dict(entity=dict(id='new-room', kind='room', label='after'))
                else:
                    params = dict(id='room', expected_revision=1)
                    if operation == 'update':
                        params['patch'] = dict(label='after')
                self.error('STORAGE_ERROR', client, 'world.'+operation, **params)
                self.assertEqual(client.call('world.snapshot'), before)
                self.assertIsNone(client.proc.poll())
                client.stop(crash=True)
                with sqlite3.connect(self.root/operation/'world.db') as database:
                    self.assertEqual(database.execute('PRAGMA quick_check').fetchone(), ('ok',))
                    self.assertEqual(database.execute('SELECT world_revision FROM metadata').fetchone()[0], before['world_revision'])
                resumed = self.start(operation)
                self.assertEqual(resumed.call('world.snapshot'), before)
                resumed.call('world.'+operation, params)
                self.assertEqual(resumed.call('world.info')['world_revision'], before['world_revision']+1)

    def test_reads_rejections_and_rearming_preserve_next_write_failure(self):
        client = self.start()
        self.room(client)
        before = client.call('world.snapshot')
        self.arm(client)
        self.assertEqual(client.call('world.snapshot'), before)
        self.error('REVISION_CONFLICT', client, 'world.update', id='room', expected_revision=2, patch=dict(label='bad'))
        self.error('INVALID_ARGUMENT', client, 'world.update', id='room', expected_revision=1, patch=dict(id='changed'))
        self.error('ALREADY_EXISTS', client, 'world.create', entity=dict(id='room', kind='room', label='bad'))
        self.error('BUSY', client, 'fault.arm', point='storage.next_write', action='error', errno='ENOSPC')
        for point in ('world.before_commit', 'world.after_commit_before_reply'):
            self.error('BUSY', client, 'fault.arm', point=point, action='exit')
        self.error('STORAGE_ERROR', client, 'world.update', id='room', expected_revision=1, patch=dict(label='failed'))
        self.assertEqual(client.call('world.snapshot'), before)
        client.call('world.update', dict(id='room', expected_revision=1, patch=dict(label='success')))
        self.arm(client)
        self.error('STORAGE_ERROR', client, 'world.update', id='room', expected_revision=2, patch=dict(label='failed-again'))
        self.assertEqual(client.call('world.get', dict(id='room'))['label'], 'success')

    def test_fault_does_not_cross_worlds_or_survive_restart(self):
        first = self.start()
        self.room(first)
        self.arm(first)
        other = self.start('other')
        self.room(other)
        first.stop(crash=True)
        resumed = self.start()
        resumed.call('world.update', dict(id='room', expected_revision=1, patch=dict(label='after')))
        self.assertEqual(other.call('world.get', dict(id='room'))['label'], 'before')

    def test_normal_daemon_rejects_storage_faults_and_retains_writes(self):
        client = self.start(enabled=False)
        self.error('FORBIDDEN', client, 'fault.arm', point='storage.next_write', action='error', errno='ENOSPC')
        self.room(client)

    def test_checkpoint_write_failure_keeps_live_pose_and_old_durable_pose(self):
        client = self.start(clock=True)
        self.room(client)
        actor = client.call('actor.open', dict(id='a', room='room', grants=['navigate'],
                           pose=dict(p=[0, 0, 0], q=[0, 0, 0, 1])))
        before = client.call('world.snapshot')
        client.call('actor.act', dict(token=actor['token'], seq=1, op='input', args=dict(axes=[0, 0, -1, 0, 0, 0])))
        client.call('clock.advance', dict(seconds=0.1))
        live = client.call('actor.inspect', dict(token=actor['token']))['pose']
        self.assertLess(live['p'][2], 0)
        self.arm(client)
        self.error('STORAGE_ERROR', client, 'world.checkpoint')
        self.assertEqual(client.call('world.snapshot'), before)
        self.assertEqual(client.call('actor.inspect', dict(token=actor['token']))['pose'], live)
        client.call('world.checkpoint')
        client.stop(crash=True)
        resumed = self.start()
        self.assertEqual(resumed.call('world.get', dict(id='a'))['pose'], live)


if __name__ == '__main__':
    unittest.main(verbosity=2)
