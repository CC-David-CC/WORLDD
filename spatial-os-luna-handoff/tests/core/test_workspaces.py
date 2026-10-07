"""Independent ownership, persistence, policy, and transport checks for workspaces."""
import io
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import broker_pb2 as broker_pb
from harness.git_fixture import GitRepo
from harness.rpc import Client, RpcError
from harness.wire import frame, make_request, read_message, pb

HOST, BROKER, WORLD = sys.argv[1:4]
sys.argv[1:4] = []
RIGHTS = ['workspace.select', 'files.read', 'files.write', 'process.launch']


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.state = self.root / 'runtime'
        self.repo = GitRepo(self.root); self.sequences = {}; self.start()
        self.a = self.actor('a'); self.b = self.actor('b')

    def start(self):
        self.client = Client([HOST], self.state, self.root / 'logs', timeout=5)
        self.addCleanup(self.client.stop); self.client.call('system.hello')

    def call(self, method, **args): return self.client.call(method, args)

    def error(self, code, method, **args):
        with self.assertRaises(RpcError) as error: self.call(method, **args)
        self.assertEqual(error.exception.code, code)

    def actor(self, ident):
        value = self.call('actor.open', id=ident, grants=['navigate', 'interact', 'world.write', 'git.write', 'process.launch'])
        self.sequences[value['token']] = 0; return value

    def act(self, actor, op, **args):
        self.sequences[actor['token']] += 1
        return self.call('actor.act', token=actor['token'], seq=self.sequences[actor['token']], op=op, args=args)

    def actor_error(self, code, actor, op, **args):
        with self.assertRaises(RpcError) as error: self.act(actor, op, **args)
        self.assertEqual(error.exception.code, code)

    def selected(self, actor): return self.call('actor.inspect', token=actor['token'])['workspace']

    def workspace(self, ident='one', policy=RIGHTS):
        self.call('git.repo.import', id='repo-' + ident, path=str(self.repo.path))
        work = self.call('git.worktree.create', repo='repo-' + ident, id='work-' + ident,
                         branch='branch-' + ident, start='main', operation_id='create-' + ident)
        ws = self.call('workspace.create', id=ident, worktree=work['id'], policy=policy)
        return ws, work

    def grant(self, actor, workspace='one', rights=RIGHTS):
        return self.call('workspace.member.set', id=workspace, actor=actor['id'], rights=rights)

    def select(self, actor, workspace='one'):
        self.grant(actor, workspace); self.call('workspace.available', id=workspace, available=True)
        self.act(actor, 'workspace.select', id=workspace)

    def registry(self): return sqlite3.connect(self.state / 'execution/execution.db')
    def world_db(self): return sqlite3.connect(self.state / 'world.db')

    def test_workspace_authority_has_no_world_or_room_owner_and_creates_no_geometry(self):
        before = self.call('world.snapshot')['entities']; ws, work = self.workspace()
        self.assertEqual(ws, dict(id='one', worktree='work-one', policy=RIGHTS, revision=1))
        self.assertIsNone(self.selected(self.a)); self.assertEqual(self.call('world.snapshot')['entities'], before)
        self.assertEqual(self.call('workspace.get', id='one'), ws)
        with self.registry() as db:
            names = [row[1] for row in db.execute('PRAGMA table_info(workspaces)')]
            self.assertNotIn('room', names); self.assertNotIn('world', names)
            self.assertEqual(db.execute('SELECT worktree,repo FROM workspaces').fetchone(), (work['id'], 'repo-one'))
        with self.world_db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM world_workspaces').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM actor_workspaces').fetchone()[0], 0)

    def test_policy_and_membership_intersect_without_using_global_actor_grants(self):
        self.workspace(policy=['files.read']); self.grant(self.a)
        self.call('workspace.available', id='one', available=True)
        self.actor_error('FORBIDDEN', self.a, 'workspace.select', id='one')
        self.workspace('two', policy=['workspace.select', 'files.read']); self.select(self.a, 'two')
        self.actor_error('FORBIDDEN', self.a, 'process.launch', argv=['/usr/bin/true'], operation_id='denied', profile='development')
        self.actor_error('FORBIDDEN', self.b, 'workspace.select', id='two')
        self.actor_error('NO_WORKSPACE', self.b, 'process.launch', argv=['/usr/bin/true'], operation_id='none', profile='development')
        self.assertEqual(self.selected(self.a), 'two'); self.assertIsNone(self.selected(self.b))

    def test_membership_validates_actor_and_rights_before_modifying_authority(self):
        ws, _ = self.workspace()
        self.error('NOT_FOUND', 'workspace.member.set', id='one', actor='absent', rights=RIGHTS)
        room = self.call('world.info')['entry_room']
        self.error('INVALID_ARGUMENT', 'workspace.member.set', id='one', actor=room, rights=RIGHTS)
        for rights in [[pb.WORKSPACE_SELECT, pb.WORKSPACE_SELECT], [0], [99]]:
            request = make_request('workspace.member.set', {'id': 'one', 'actor': 'a', 'rights': []})
            request.workspace_member_set.rights.extend(rights)
            with self.assertRaises(RpcError) as caught: self.client.call_message(request)
            self.assertEqual(caught.exception.code, 'INVALID_ARGUMENT')
        self.assertEqual(self.call('workspace.get', id='one'), ws)
        with self.registry() as db: self.assertEqual(db.execute('SELECT count(*) FROM workspace_members').fetchone()[0], 0)
        self.grant(self.a); changed = self.call('workspace.get', id='one')
        self.assertEqual(changed['revision'], 2); self.grant(self.a)
        self.assertEqual(self.call('workspace.get', id='one'), changed)

    def test_binding_protects_worktree_and_repository_from_other_actor_git_actions(self):
        _, work = self.workspace(); before = Path(work['path'], 'README.txt').read_bytes()
        self.error('IN_USE', 'git.worktree.remove', id=work['id'], operation_id='remove-bound')
        self.actor_error('IN_USE', self.b, 'git.worktree.create', repo='repo-one', id='other', branch='other', start='main', operation_id='actor-override')
        self.assertEqual(Path(work['path'], 'README.txt').read_bytes(), before)
        with self.registry() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM removals').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM operations').fetchone()[0], 1)

    def test_selection_is_explicit_durable_and_independent_for_each_actor(self):
        self.workspace(); self.grant(self.a)
        self.actor_error('UNAVAILABLE', self.a, 'workspace.select', id='one')
        self.call('workspace.available', id='one', available=True)
        self.assertIsNone(self.selected(self.a)); self.act(self.a, 'workspace.select', id='one')
        self.assertEqual(self.selected(self.a), 'one'); self.assertIsNone(self.selected(self.b))
        self.client.stop(crash=True); self.start(); self.a = self.actor('a'); self.b = self.actor('b')
        self.assertEqual(self.selected(self.a), 'one'); self.assertIsNone(self.selected(self.b))
        self.act(self.a, 'workspace.clear'); self.assertIsNone(self.selected(self.a))
        self.client.stop(crash=True); self.start(); self.a = self.actor('a')
        self.assertIsNone(self.selected(self.a))

    def test_revocation_clears_offline_selection_and_does_not_affect_other_members(self):
        self.workspace(); self.select(self.a); self.select(self.b)
        self.call('actor.close', token=self.a['token']); self.grant(self.a, rights=[])
        self.assertEqual(self.selected(self.b), 'one'); self.a = self.actor('a')
        self.assertIsNone(self.selected(self.a)); self.actor_error('FORBIDDEN', self.a, 'workspace.select', id='one')
        self.call('workspace.available', id='one', available=False)
        self.assertIsNone(self.selected(self.b))
        self.call('workspace.available', id='one', available=True); self.assertIsNone(self.selected(self.b))

    def test_room_transition_keeps_workspace_and_runtime_processes(self):
        self.workspace(); self.select(self.a); identity = self.call('runtime.identities')
        entry = self.call('world.info')['entry_room']
        self.call('world.create', entity={'id': 'other', 'kind': 'room', 'label': 'Other'})
        self.call('world.create', entity={'id': 'door', 'kind': 'portal', 'room': entry,
            'pose': {'p': [0, 0, -.1], 'q': [0, 0, 0, 1]}, 'size': [2, 2],
            'destination': {'room': 'other', 'pose': {'p': [0, 0, 0], 'q': [0, 0, 0, 1]}}})
        self.act(self.a, 'input', axes=[0, 0, -1, 0, 0, 0]); self.call('clock.advance', seconds=.2)
        self.assertEqual(self.call('actor.inspect', token=self.a['token'])['room'], 'other')
        self.assertEqual(self.selected(self.a), 'one'); self.assertEqual(self.call('runtime.identities'), identity)

    def test_selection_write_failure_keeps_the_previous_context(self):
        self.workspace(); self.workspace('two'); self.select(self.a)
        self.grant(self.a, 'two'); self.call('workspace.available', id='two', available=True)
        before = self.call('world.snapshot')
        with self.world_db() as db:
            db.execute("CREATE TRIGGER deny_selection BEFORE INSERT ON actor_workspaces BEGIN SELECT RAISE(FAIL,'witness'); END")
        self.actor_error('STORAGE_ERROR', self.a, 'workspace.select', id='two')
        self.assertEqual(self.selected(self.a), 'one'); self.assertEqual(self.call('world.snapshot'), before)

    def test_failed_world_cleanup_does_not_restore_revoked_broker_authority(self):
        self.workspace(); self.select(self.a)
        with self.world_db() as db:
            db.execute("CREATE TRIGGER deny_clear BEFORE DELETE ON actor_workspaces BEGIN SELECT RAISE(FAIL,'witness'); END")
        self.error('STORAGE_ERROR', 'workspace.member.set', id='one', actor='a', rights=[])
        self.error('STORAGE_ERROR', 'actor.inspect', token=self.a['token'])
        with self.registry() as db: self.assertEqual(db.execute("SELECT rights FROM workspace_members WHERE actor='a'").fetchone()[0], 0)
        with self.world_db() as db: db.execute('DROP TRIGGER deny_clear')
        self.assertIsNone(self.selected(self.a)); self.actor_error('FORBIDDEN', self.a, 'workspace.select', id='one')

    def test_world_unavailability_rolls_back_when_selection_cleanup_fails(self):
        self.workspace(); self.select(self.a); before = self.call('world.snapshot')
        with self.world_db() as db:
            db.execute("CREATE TRIGGER deny_clear BEFORE DELETE ON actor_workspaces BEGIN SELECT RAISE(FAIL,'witness'); END")
        self.error('STORAGE_ERROR', 'workspace.available', id='one', available=False)
        self.assertEqual(self.selected(self.a), 'one'); self.assertEqual(self.call('world.snapshot'), before)
        with self.world_db() as db: self.assertEqual(db.execute("SELECT available FROM world_workspaces WHERE id='one'").fetchone()[0], 1)

    def test_private_authorization_envelope_is_rejected_at_the_public_boundary(self):
        private = broker_pb.Request(protocol_version=1, request_id=7)
        private.access.workspace = 'one'; private.access.actor = 'a'
        hello = make_request('system.hello', {}); hello.request_id = 8
        result = subprocess.run([HOST, '--state-dir', str(self.root / 'separate')],
                                input=frame(private) + frame(hello), capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        stream = io.BytesIO(result.stdout); denied = read_message(stream, pb.Reply); following = read_message(stream, pb.Reply)
        self.assertEqual(denied.request_id, 7); self.assertEqual(denied.error.code, pb.INVALID_ARGUMENT)
        self.assertEqual(following.request_id, 8); self.assertEqual(following.hello.implementation, 'production')

    def test_explicit_database_migrations_preserve_existing_world_and_git_rows(self):
        self.call('git.repo.import', id='repo', path=str(self.repo.path))
        work = self.call('git.worktree.create', repo='repo', id='work', branch='feature', start='main', operation_id='create')
        before = self.call('world.snapshot'); self.client.stop()
        with self.registry() as db:
            db.execute('DROP TABLE workspace_members'); db.execute('DROP TABLE workspaces'); db.execute('PRAGMA user_version=2')
        with self.world_db() as db:
            db.execute('DROP TABLE actor_workspaces'); db.execute('DROP TABLE world_workspaces'); db.execute('PRAGMA user_version=1')
        for binary, state, flag in [(BROKER, self.state / 'execution', '--migrate-v2'), (WORLD, self.state, '--migrate-v1')]:
            denied = subprocess.run([binary, '--state-dir', str(state)], input=b'', capture_output=True, timeout=5)
            self.assertNotEqual(denied.returncode, 0); self.assertIn(flag.encode(), denied.stderr)
            for _ in range(2):
                migrated = subprocess.run([binary, '--state-dir', str(state), flag], input=b'', capture_output=True, timeout=5)
                self.assertEqual(migrated.returncode, 0, migrated.stderr)
        self.start(); self.assertEqual(self.call('world.snapshot'), before)
        self.assertEqual(self.call('git.status', worktree='work')['head'], work['head'])
        self.assertEqual(self.call('workspace.create', id='after', worktree='work', policy=RIGHTS)['revision'], 1)

    def test_current_world_schema_rejects_corrupt_entity_protobuf_without_repair(self):
        self.client.stop()
        with self.world_db() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)
            db.execute('UPDATE entities SET data=?', (b'\xff',))
        before = (self.state / 'world.db').read_bytes()
        result = subprocess.run([WORLD, '--state-dir', str(self.state)], input=b'', capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0); self.assertEqual(result.stdout, b'')
        self.assertNotIn(b'migration', result.stderr)
        self.assertEqual((self.state / 'world.db').read_bytes(), before)


if __name__ == '__main__': unittest.main()
