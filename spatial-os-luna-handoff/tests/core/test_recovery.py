"""Exercise real worldd exits and independent SQLite recovery checks."""
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from harness.rpc import Client, RpcError, TransportError
from harness.wire import pb

HOST, DAEMON = sys.argv[1:3]
sys.argv[1:3] = []


class RecoveryBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def start(self, state=None, fixture=True, clock=False):
        argv = [DAEMON]
        if fixture:
            argv.append('--fixture-faults')
        if clock:
            argv.append('--fixture-clock')
        client = Client(argv, state or self.root/'world', self.root/'logs', timeout=4)
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
        self.assertEqual(client.call('fault.arm', dict(point='world.before_commit', action='exit')), dict(armed=True))

    def crash(self, client, method, **params):
        with self.assertRaises(TransportError):
            client.call(method, params)
        self.assertEqual(client.proc.wait(timeout=4), 86)

    def stored_rows(self, state):
        with sqlite3.connect(state/'world.db') as database:
            self.assertEqual(database.execute('PRAGMA quick_check').fetchone(), ('ok',))
            return (database.execute('SELECT * FROM metadata').fetchall(),
                    database.execute('SELECT * FROM entities ORDER BY id').fetchall())

    def test_default_and_clock_only_daemons_cannot_arm_faults(self):
        for clock in (False, True):
            with self.subTest(clock=clock):
                client = self.start(self.root/str(clock), fixture=False, clock=clock)
                self.assertNotIn('fixture.faults/v1', client.call('system.hello')['capabilities'])
                self.error('FORBIDDEN', client, 'fault.arm', point='world.before_commit', action='exit')
                self.room(client)
        fixture = self.start()
        self.assertIn('fixture.faults/v1', fixture.call('system.hello')['capabilities'])

    def test_before_commit_preserves_real_rows_for_create_update_and_delete(self):
        for operation in ('create', 'update', 'delete'):
            with self.subTest(operation=operation):
                state = self.root/operation
                client = self.start(state)
                room = self.room(client)
                before = client.call('world.snapshot')
                rows = self.stored_rows(state)
                self.arm(client)
                if operation == 'create':
                    params = dict(entity=dict(id='new-room', kind='room', label='new'))
                else:
                    params = dict(id='room', expected_revision=room['revision'])
                    if operation == 'update':
                        params['patch'] = dict(label='after')
                self.crash(client, 'world.'+operation, **params)
                self.assertEqual(self.stored_rows(state), rows)
                resumed = self.start(state)
                self.assertEqual(resumed.call('world.snapshot'), before)
                resumed.call('world.update', dict(id='room', expected_revision=room['revision'], patch=dict(label='recovered')))
                self.assertEqual(resumed.call('world.get', dict(id='room'))['label'], 'recovered')

    def test_reads_and_rejected_edits_do_not_consume_pending_fault(self):
        client = self.start()
        room = self.room(client)
        before = client.call('world.snapshot')
        self.arm(client)
        self.assertEqual(client.call('world.snapshot'), before)
        self.assertEqual(client.call('world.get', dict(id='room')), room)
        self.error('REVISION_CONFLICT', client, 'world.update', id='room', expected_revision=2, patch=dict(label='bad'))
        self.error('INVALID_ARGUMENT', client, 'world.update', id='room', expected_revision=1, patch=dict(id='changed'))
        self.error('ALREADY_EXISTS', client, 'world.create', entity=dict(id='room', kind='room', label='bad'))
        self.error('BUSY', client, 'fault.arm', point='world.before_commit', action='exit')
        self.error('UNIMPLEMENTED', client, 'fault.arm', point='world.after_commit_before_reply', action='exit')
        self.crash(client, 'world.update', id='room', expected_revision=1, patch=dict(label='after'))
        self.assertEqual(self.start().call('world.snapshot'), before)

    def test_invalid_or_unsupported_arms_do_not_enable_a_fault(self):
        client = self.start()
        for params in [{}, dict(point='world.before_commit'), dict(action='exit'),
                       dict(point='world.before_commit', action='error'),
                       dict(point='world.before_commit', action='exit', errno='ENOSPC'),
                       dict(point='storage.next_write', action='exit'),
                       dict(point='storage.next_write', action='error')]:
            self.error('INVALID_ARGUMENT', client, 'fault.arm', **params)
        for point in ('world.after_commit_before_reply', 'git.after_worktree_before_ready'):
            self.error('UNIMPLEMENTED', client, 'fault.arm', point=point, action='exit')
        self.error('UNIMPLEMENTED', client, 'fault.arm', point='storage.next_write', action='error', errno='ENOSPC')
        for field in ('point', 'action', 'storage_error'):
            request = pb.Request(protocol_version=1)
            request.fault_arm.point = pb.FAULT_WORLD_BEFORE_COMMIT
            request.fault_arm.action = pb.FAULT_EXIT
            setattr(request.fault_arm, field, 999)
            with self.assertRaises(RpcError) as caught:
                client.call_message(request)
            self.assertEqual(caught.exception.code, 'INVALID_ARGUMENT')
        self.room(client)

    def test_fault_is_local_to_one_process_and_never_restored(self):
        first = self.start()
        self.room(first)
        before = first.call('world.snapshot')
        self.arm(first)
        other = self.start(self.root/'other')
        self.room(other)
        self.assertEqual(first.call('world.snapshot'), before)
        first.stop(crash=True)
        resumed = self.start()
        resumed.call('world.update', dict(id='room', expected_revision=1, patch=dict(label='new')))
        self.assertEqual(other.call('world.get', dict(id='room'))['label'], 'before')

    def test_actor_action_cannot_arm_management_fault(self):
        client = self.start()
        room = self.room(client)
        actor = client.call('actor.open', dict(id='a', room='room', grants=[],
                           pose=dict(p=[0, 0, 0], q=[0, 0, 0, 1])))
        self.error('UNKNOWN_METHOD', client, 'actor.act', token=actor['token'], seq=1, op='fault.arm',
                   args=dict(point='world.before_commit', action='exit'))
        client.call('world.update', dict(id='room', expected_revision=room['revision'], patch=dict(label='safe')))

    def test_checkpoint_crash_preserves_saved_actor_pose(self):
        client = self.start(clock=True)
        self.room(client)
        actor = client.call('actor.open', dict(id='a', room='room', grants=['navigate'],
                           pose=dict(p=[0, 0, 0], q=[0, 0, 0, 1])))
        before = client.call('world.snapshot')
        client.call('actor.act', dict(token=actor['token'], seq=1, op='input', args=dict(axes=[0, 0, -1, 0, 0, 0])))
        client.call('clock.advance', dict(seconds=0.1))
        self.assertLess(client.call('actor.inspect', dict(token=actor['token']))['pose']['p'][2], 0)
        self.arm(client)
        self.crash(client, 'world.checkpoint')
        resumed = self.start()
        self.assertEqual(resumed.call('world.snapshot'), before)
        self.error('UNAUTHORIZED', resumed, 'actor.inspect', token=actor['token'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
