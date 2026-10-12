"""Real worldd lifecycle, storage, and wire counterexamples. Run only on Linux."""
import os
from pathlib import Path
import select
import signal
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from harness.rpc import Client, RpcError, TransportError
from harness.wire import pb, make_request

HOST, DAEMON = sys.argv[1:3]
sys.argv[1:3] = []


class WorldDaemonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'world'
        self.clients = []

    def start(self, binary=HOST, state=None):
        client = Client([binary], state or self.state, self.root / 'logs', timeout=4)
        self.addCleanup(client.stop)
        self.clients.append(client)
        client.call('system.hello')
        return client

    def room(self, client, ident='room'):
        return client.call('world.create', {'entity': {'id': ident, 'kind': 'room', 'label': ident}})

    def error(self, code, client, method, **params):
        with self.assertRaises(RpcError) as caught:
            client.call(method, params)
        self.assertEqual(caught.exception.code, code)

    def test_worker_is_real_separate_executable_and_owns_database(self):
        client = self.start()
        identity = client.call('runtime.identities')
        worker = identity['worldd_pid']
        self.assertNotEqual(worker, client.proc.pid)
        self.assertEqual(Path(f'/proc/{worker}/exe').resolve(), Path(DAEMON).resolve())
        self.assertIn(f'PPid:\t{client.proc.pid}\n', Path(f'/proc/{worker}/status').read_text())
        database = (self.state / 'world.db').resolve()
        self.assertTrue(any(p.resolve() == database for p in Path(f'/proc/{worker}/fd').iterdir()))
        self.assertFalse(any(p.resolve() == database for p in Path(f'/proc/{client.proc.pid}/fd').iterdir()))

    def test_idle_proxy_closes_when_worker_dies_and_recovers_state(self):
        client = self.start()
        self.room(client)
        before = client.call('world.snapshot')
        worker = client.call('runtime.identities')['worldd_pid']
        os.kill(worker, signal.SIGKILL)
        self.assertNotEqual(client.proc.wait(timeout=4), 0)
        with self.assertRaises(TransportError):
            client.call('world.snapshot')
        replacement = self.start()
        self.assertEqual(replacement.call('world.snapshot'), before)

    def test_parent_only_kill_terminates_worker_and_releases_lock(self):
        client = self.start()
        self.room(client)
        worker = client.call('runtime.identities')['worldd_pid']
        pidfd = os.pidfd_open(worker)
        try:
            os.kill(client.proc.pid, signal.SIGKILL)
            client.proc.wait(timeout=4)
            poller = select.poll()
            poller.register(pidfd, select.POLLIN)
            self.assertTrue(poller.poll(4000), 'worldd outlived its proxy')
        finally:
            os.close(pidfd)
        self.assertEqual(self.start().call('world.get', {'id': 'room'})['id'], 'room')

    def test_duplicate_authority_refused_without_mutation(self):
        client = self.start()
        before = client.call('world.snapshot')
        other = subprocess.run([DAEMON, '--state-dir', str(self.state)], input=b'',
                               capture_output=True, timeout=5)
        self.assertNotEqual(other.returncode, 0)
        self.assertEqual(other.stdout, b'')
        self.assertEqual(client.call('world.snapshot'), before)

    def test_standalone_worldd_restart_delete_and_independent_worlds(self):
        client = self.start(DAEMON)
        first = client.call('world.info')
        self.assertEqual(client.call('runtime.identities')['worldd_pid'], client.proc.pid)
        room = self.room(client)
        client.call('world.delete', {'id': room['id'], 'expected_revision': room['revision']})
        before = client.call('world.snapshot')
        client.stop(crash=True)
        client = self.start(DAEMON)
        self.assertEqual(client.call('world.snapshot'), before)
        self.error('NOT_FOUND', client, 'world.get', id=room['id'])
        other = self.start(DAEMON, self.root / 'other')
        self.assertNotEqual(first['world_id'], other.call('world.info')['world_id'])
        entry = client.call('world.get', {'id': first['entry_room']})
        self.error('IN_USE', client, 'world.delete', id=entry['id'], expected_revision=entry['revision'])

    def test_zero_field_replacement_and_immutable_zero_presence(self):
        client = self.start()
        room = self.room(client)
        original = {'id': 'object', 'kind': 'object', 'room': room['id'],
                    'pose': {'p': [3, 2, 1], 'q': [0, 0, 0, 1]}}
        obj = client.call('world.create', {'entity': original})
        zero = {'p': [0, 0, 0], 'q': [0, 0, 0, 1]}
        updated = client.call('world.update', {'id': obj['id'], 'expected_revision': 1, 'patch': {'pose': zero}})
        self.assertEqual(updated['pose'], zero)
        before = client.call('world.snapshot')
        for field, value in [('id', ''), ('kind', 0), ('revision', 0), ('world_revision', 0)]:
            request = make_request('world.update', {'id': obj['id'], 'expected_revision': updated['revision'], 'patch': {}})
            setattr(request.world_update.patch, field, value)
            with self.assertRaises(RpcError) as caught:
                client.call_message(request)
            self.assertEqual(caught.exception.code, 'INVALID_ARGUMENT')
        self.assertEqual(client.call('world.snapshot'), before)

    def test_portal_references_and_invalid_enum_rejected_atomically(self):
        client = self.start()
        self.room(client, 'one')
        self.room(client, 'two')
        pose = {'p': [0, 0, 0], 'q': [0, 0, 0, 1]}
        portal = {'id': 'portal', 'kind': 'portal', 'room': 'one', 'pose': pose,
                  'size': [2, 3], 'destination': {'room': 'two', 'pose': pose}}
        client.call('world.create', {'entity': portal})
        self.error('IN_USE', client, 'world.delete', id='two', expected_revision=1)
        before = client.call('world.snapshot')
        self.error('NOT_FOUND', client, 'world.update', id='portal', expected_revision=1,
                   patch={'destination': {'room': 'missing', 'pose': pose}})
        self.error('INVALID_ARGUMENT', client, 'world.update', id='portal', expected_revision=1,
                   patch={'room': 'portal'})
        invalid = make_request('world.create', {'entity': {'id': 'bad', 'kind': 'room', 'label': 'x'}})
        invalid.world_create.entity.kind = 999
        with self.assertRaises(RpcError) as caught:
            client.call_message(invalid)
        self.assertEqual(caught.exception.code, 'INVALID_ARGUMENT')
        self.assertEqual(client.call('world.snapshot'), before)

    def test_sqlite_write_failure_rolls_back_entity_and_world_revision(self):
        client = self.start()
        self.room(client)
        before = client.call('world.snapshot')
        client.stop()
        # A real SQLite abort after the metadata update exercises rollback.
        with sqlite3.connect(self.state / 'world.db') as db:
            db.execute("CREATE TRIGGER fail_update BEFORE UPDATE ON entities BEGIN SELECT RAISE(ABORT,'test storage failure'); END")
        client = self.start()
        self.error('STORAGE_ERROR', client, 'world.update', id='room', expected_revision=1, patch={'label': 'must rollback'})
        self.assertEqual(client.call('world.snapshot'), before)
        client.stop(crash=True)
        self.assertEqual(self.start().call('world.snapshot'), before)

    def test_unknown_schema_and_corrupt_protobuf_fail_closed(self):
        client = self.start()
        client.stop()
        with sqlite3.connect(self.state / 'world.db') as db:
            db.execute('PRAGMA user_version=999')
        original = (self.state / 'world.db').read_bytes()
        result = subprocess.run([DAEMON, '--state-dir', str(self.state)], input=b'', capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.state / 'world.db').read_bytes(), original)
        with sqlite3.connect(self.state / 'world.db') as db:
            db.execute('PRAGMA user_version=1')
            db.execute("UPDATE entities SET data=?", (b'\xff',))
        result = subprocess.run([DAEMON, '--state-dir', str(self.state)], input=b'', capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b'')

    def test_snapshot_limit_is_typed_and_does_not_truncate_or_mutate(self):
        client = self.start()
        for i in range(17):
            client.call('world.create', {'entity': {'id': f'large-{i}', 'kind': 'room', 'label': 'x' * 65536}})
        before = client.call('world.info')
        self.error('RESOURCE_EXHAUSTED', client, 'world.snapshot')
        self.assertEqual(client.call('world.info'), before)
        self.assertEqual(len(client.call('world.get', {'id': 'large-16'})['label']), 65536)

    def test_database_symlink_is_refused(self):
        self.state.mkdir()
        outside = self.root / 'unrelated'
        outside.write_bytes(b'do not change')
        (self.state / 'world.db').symlink_to(outside)
        result = subprocess.run([DAEMON, '--state-dir', str(self.state)], input=b'', capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(outside.read_bytes(), b'do not change')


if __name__ == '__main__':
    unittest.main(verbosity=2)
