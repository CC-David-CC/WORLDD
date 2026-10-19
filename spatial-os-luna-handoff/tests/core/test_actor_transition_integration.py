"""Production actor transitions against enrolled issuer and world processes.

Runtime execution belongs exclusively to the qualification hosts.
"""
import io
import os
from pathlib import Path
import sqlite3
import struct
import subprocess
import sys
import unittest

# Existing fixtures consume executable arguments during import.
_identityd, _issuer_client, _worldd, _driver = sys.argv[1:5]
_saved_argv = sys.argv[:]
sys.argv = [sys.argv[0], _identityd, _issuer_client, _worldd]
from test_world_identity import WorldIdentityFixture, WorldProcess, receive
import test_workspace_authority as workspace_fixture
sys.argv = _saved_argv
import actor_transition_integration_pb2 as integration
import actor_transition_pb2 as transition
import core_pb2 as core
import world_session_pb2 as session
from harness.wire import MAX_FRAME, finite, frame, read_message

DRIVER = Path(_driver)
sys.argv = [sys.argv[0]]


def spawn(x):
    return core.Pose(position=core.Vec3(x=x, y=2, z=3), orientation=core.Quaternion(w=1))


def select_workspace(sequence=1):
    return core.ActorActionRequest(seq=sequence, workspace_select=core.WorkspaceIdRequest(id='one'))


class RealActorTransitionTests(WorldIdentityFixture):
    # Reuse production broker, authority seeding, and registered world fixtures.
    principal = workspace_fixture.WorkspaceAuthorityTests.principal
    terms = workspace_fixture.WorkspaceAuthorityTests.terms
    workspace_terms = workspace_fixture.WorkspaceAuthorityTests.workspace_terms
    make_broker = workspace_fixture.WorkspaceAuthorityTests.make_broker
    workspace = workspace_fixture.WorkspaceAuthorityTests.workspace
    own = workspace_fixture.WorkspaceAuthorityTests.own
    revoke_grant = workspace_fixture.WorkspaceAuthorityTests.revoke_grant
    enroll_second = workspace_fixture.WorkspaceAuthorityTests.enroll_second
    authenticate_second = workspace_fixture.WorkspaceAuthorityTests.authenticate_second

    def ready(self):
        # Two worlds plus a broker each use independent production issuer connections.
        self.config.connection_limit = 32
        return super().ready()

    def setUp(self):
        super().setUp()
        self.brokers = []
        self.addCleanup(lambda: ([world.close() for world in reversed(self.worlds)],
                                 [broker.close() for broker in reversed(self.brokers)]))
        self.channels = {}
        self._defer_initial_actor_auth = False
        def preserve_protobuf_evidence():
            target = os.environ.get('SPATIAL_SECURITY_EVIDENCE')
            if target:
                directory = Path(target); directory.mkdir(parents=True, exist_ok=True)
                for path in self.root.glob('*.pb'):
                    (directory/(self._testMethodName+'-'+path.name)).write_bytes(path.read_bytes())
        self.addCleanup(preserve_protobuf_evidence)

    def scoped_world(self, *args, **kwargs):
        # Allocate genuine additional socketpairs before worldd starts.
        if self._defer_initial_actor_auth:
            kwargs['authenticate'] = False
        old = workspace_fixture.WorkspaceAuthorityTests.scoped_world(self, *args, **kwargs)
        old.close()
        world = WorldProcess(self, old.config_path, old.state_path, count=6,
                             runtime_config=old.runtime_path, broker=kwargs.get('broker'))
        world.state_path = old.state_path
        world.config_path = old.config_path
        self.worlds.append(world)
        world.management('system.hello')
        return world

    def channel(self, world):
        index = self.channels.get(world, 0)
        self.assertLess(index, len(world.peers), 'Fixture requires another fresh actor socketpair')
        self.channels[world] = index + 1
        return world.peers[index]

    def request(self, source, steps, source_room=None, source_pose=None, initial_actions=()):
        peer = self.channel(source)
        request = integration.ActorTransitionIntegrationRequest(protocol_version=1,
            actor_identity=self.identity.actor_id, source_world_id=source.management('world.info')['world_id'],
            source_fd=peer.stream.fileno(), credential=self.record.credential,
            signing_key_path=str(self.actor_key), initial_actions=initial_actions)
        if source_room is not None: request.source_room = source_room
        if source_pose is not None: request.source_pose.CopyFrom(source_pose)
        fds = [peer.stream.fileno()]
        for destination, room, pose, sequence, actions in steps:
            peer = self.channel(destination)
            fds.append(peer.stream.fileno())
            step = request.steps.add(destination_world_id=destination.management('world.info')['world_id'],
                destination_fd=peer.stream.fileno(), transition_id=os.urandom(32),
                departure_sequence=sequence, after_actions=actions)
            if room is not None: step.destination_room = room
            if pose is not None: step.destination_pose.CopyFrom(pose)
        self.assertEqual(len(fds), len(set(fds)))
        return request, tuple(fds)

    def run_driver(self, source, steps, hook=None, **spawn_options):
        request, fds = self.request(source, steps, **spawn_options)
        log_path = self.root/(self._testMethodName+'-driver.log')
        with log_path.open('wb') as log:
            process = subprocess.Popen([str(DRIVER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=log, pass_fds=fds)
            # Transfer ownership. Parent copies must not retain abandoned or active sessions.
            for world in self.worlds:
                for peer in world.peers:
                    if peer.stream.fileno() in fds: peer.close()
            try:
                process.stdin.write(frame(request)); process.stdin.flush()
                for index in range(len(steps) + 1):
                    reply = receive(lambda n: os.read(process.stdout.fileno(), n), process.stdout,
                                    integration.ActorTransitionIntegrationReply)
                    clean = integration.ActorTransitionIntegrationReply(); clean.CopyFrom(reply)
                    clean.DiscardUnknownFields()
                    self.assertEqual(clean.SerializeToString(deterministic=True),
                                     reply.SerializeToString(deterministic=True))
                    finite(reply)
                    (self.root/f'driver-progress-{index}.pb').write_bytes(reply.SerializeToString())
                    self.assertEqual(reply.protocol_version, 1)
                    self.assertFalse(reply.driver_error, reply.driver_error)
                    self.assertEqual(len(reply.results), index)
                    self.assertEqual(len(reply.observations), index + 1)
                    self.assertEqual(reply.source_opened.identity.actor_id, self.identity.actor_id)
                    canonical = 'identity:'+self.identity.issuer_id.hex()+':'+self.identity.actor_id.hex()
                    self.assertEqual(reply.source_opened.actor_id, canonical)
                    for result in reply.results:
                        if result.source_inspection_confirmed: self.assertEqual(result.actor_id, canonical)
                        self.assertTrue(result.host_presentation_selection_pending)
                    if hook: hook(index, reply)
                    if index < len(steps):
                        self.assertFalse(reply.done)
                        process.stdin.write(frame(integration.ActorTransitionIntegrationContinue(protocol_version=1)))
                        process.stdin.flush()
                    else: self.assertTrue(reply.done)
                process.stdin.close()
                self.assertEqual(process.wait(timeout=10), 0, log_path.read_text(errors='replace'))
                self.assertEqual(process.stdout.read(), b'', 'unexpected trailing driver frame')
                return reply
            finally:
                if process.poll() is None: process.kill(); process.wait(timeout=3)
                for stream in (process.stdin, process.stdout):
                    if not stream.closed: stream.close()

    def room(self, world, name):
        world.management('world.create', entity={'id': name, 'kind': 'room', 'label': name})
        return name

    def assert_state(self, observation, world, room, pose, workspace=None):
        self.assertFalse(observation.client_failed, observation.inspection_error)
        self.assertFalse(observation.inspection_error)
        self.assertTrue(observation.HasField('state'))
        self.assertEqual(observation.world_id, world.management('world.info')['world_id'])
        self.assertEqual(observation.state.room, room)
        self.assertEqual(observation.state.pose, pose)
        self.assertEqual(observation.state.workspace if observation.state.HasField('workspace') else None, workspace)

    def durable(self, world, actor_id):
        with sqlite3.connect('file:'+str(world.state_path/'world.db')+'?mode=ro', uri=True) as db:
            row = db.execute('SELECT data FROM entities WHERE id=?', (actor_id,)).fetchone()
        self.assertIsNotNone(row)
        (self.root/(world.state_path.name+'-durable-actor.pb')).write_bytes(row[0])
        return core.Entity.FromString(row[0])

    def assert_active(self, result):
        self.assertEqual(result.phase, transition.DESTINATION_ACTIVE, result)
        self.assertTrue(result.source_inspection_confirmed)
        self.assertTrue(result.destination_preparation_confirmed)
        self.assertTrue(result.source_departure_confirmed)
        self.assertTrue(result.destination_activation_confirmed)
        self.assertTrue(result.HasField('departure_world_revision'))
        self.assertFalse(result.effect_uncertain)

    def test_a_b_a_b_inspects_every_context_and_durable_departure(self):
        a = self.world(count=4, grants=['navigate']); b = self.world(count=4, grants=['navigate'])
        room_a = self.room(a, 'a-room'); room_b = self.room(b, 'b-room')
        expected = [(a, room_a, spawn(11)), (b, room_b, spawn(22)),
                    (a, room_a, spawn(11)), (b, room_b, spawn(22))]
        def witness(index, reply):
            self.assert_state(reply.observations[-1], *expected[index])
            if index:
                result = reply.results[-1]; self.assert_active(result)
                source, room, pose = expected[index-1]
                saved = self.durable(source, result.actor_id)
                self.assertEqual(saved.room, room); self.assertEqual(saved.pose, pose)
                self.assertEqual(result.departure_world_revision, source.management('world.info')['world_revision'])
        reply = self.run_driver(a, [(b, room_b, spawn(22), 1, ()), (a, None, None, 1, ()),
                                    (b, None, None, 1, ())], hook=witness, source_room=room_a, source_pose=spawn(11))
        # Stop the real world, then reopen the same persistent identity and inspect through authentication.
        a.close(); restored = self.world(state=a.state_path)
        opened = self.authenticate(restored.peers[0]).opened
        self.assertEqual(opened.actor_id, reply.source_opened.actor_id)
        actual = restored.peers[0].call(inspect=session.WorldSessionInspect()).state
        self.assertEqual(actual.room, room_a); self.assertEqual(actual.pose, spawn(11))
        self.assertEqual(list(actual.axes), [0]*6)

    def test_departure_receipt_saves_live_motion_and_survives_restart(self):
        source = self.world(grants=['navigate']); destination = self.world()
        def wait_for_motion(index, reply):
            if index == 0: __import__('time').sleep(.08)
        reply = self.run_driver(source, [(destination, None, None, 2, ())], hook=wait_for_motion,
                                initial_actions=(core.ActorActionRequest(seq=1,
                                    input=core.ActorInputRequest(axes=[1, 0, 0, 0, 0, 0])),))
        result = reply.results[0]; self.assert_active(result)
        saved = self.durable(source, result.actor_id)
        self.assertGreater(saved.pose.position.x, 0)
        self.assertEqual(result.departure_world_revision, source.management('world.info')['world_revision'])
        self.assertEqual(saved.world_revision, result.departure_world_revision)
        source.close(); restored = self.world(state=source.state_path)
        self.assertTrue(self.authenticate(restored.peers[0]).HasField('opened'))
        state = restored.peers[0].call(inspect=session.WorldSessionInspect()).state
        self.assertEqual(state.pose, saved.pose); self.assertEqual(state.room, saved.room)
        self.assertEqual(list(state.axes), [0]*6)

    def test_new_actor_omitted_spawn_uses_destination_entry(self):
        source = self.world(); destination = self.world()
        source_room = self.room(source, 'source-only')
        entry = destination.management('world.info')['entry_room']
        reply = self.run_driver(source, [(destination, None, None, 1, ())],
                                source_room=source_room, source_pose=spawn(77))
        self.assert_active(reply.results[0])
        self.assert_state(reply.observations[1], destination, entry,
                          core.Pose(position=core.Vec3(), orientation=core.Quaternion(w=1)))

    def test_new_actor_explicit_spawn_uses_destination_context(self):
        source = self.world(); destination = self.world()
        room = self.room(destination, 'explicit-destination')
        reply = self.run_driver(source, [(destination, room, spawn(33), 1, ())], source_pose=spawn(77))
        self.assert_active(reply.results[0])
        self.assert_state(reply.observations[1], destination, room, spawn(33))

    def test_private_denial_preserves_inspect_and_next_transition(self):
        source = self.world(); private = self.world(admitted=False); destination = self.world()
        reply = self.run_driver(source, [(private, None, None, 1, ()), (destination, None, None, 1, ())])
        rejected = reply.results[0]
        self.assertEqual(rejected.phase, transition.PREPARATION_REJECTED)
        self.assertEqual(rejected.server_error.code, core.FORBIDDEN)
        self.assertTrue(rejected.effect_uncertain)
        self.assertFalse(rejected.source_departure_confirmed)
        self.assertFalse(rejected.destination_activation_confirmed)
        self.assertEqual(reply.observations[0].state, reply.observations[1].state)
        self.assertFalse(reply.observations[1].client_failed)
        self.assertFalse(reply.observations[1].inspection_error)
        self.assert_active(reply.results[1])
        self.assertEqual(len(private.management('world.list')['entities']), 1)

    def test_per_world_workspace_selection_is_independent(self):
        a = self.workspace(); b = self.world(count=4)
        reply = self.run_driver(a, [(b, None, None, 2, ()), (a, None, None, 1, ()),
                                    (b, None, None, 1, ()), (a, None, None, 1, ())],
                                initial_actions=(select_workspace(),))
        for result in reply.results: self.assert_active(result)
        selections = [item.state.workspace if item.state.HasField('workspace') else None for item in reply.observations]
        self.assertEqual(selections, ['one', None, 'one', None, 'one'])

    def workspace_two(self):
        # Build a second real broker/worktree with a distinct workspace authority.
        root = self.root/'workspace-two'; root.mkdir(mode=0o700)
        execution = root/'execution'
        first = workspace_fixture.BrokerProcess(self, execution)
        repository = workspace_fixture.GitRepo(root)
        first.call('git.repo.import', id='repo-two', path=str(repository.path))
        first.call('git.worktree.create', repo='repo-two', id='tree-two', branch='branch-two',
                   start='main', operation_id='tree-two')
        first.call('workspace.create', id='two', worktree='tree-two',
                   policy=workspace_fixture.POLICY)
        first.close()
        directory = root/'workspace-authority'; directory.mkdir(mode=0o700)
        terms = self.workspace_terms(); terms.scope.domain.id = 'two'
        seed = workspace_fixture.broker.WorkspaceAuthoritySeed(
            protocol_version=1, workspace='two', authority_directory=str(directory), roots=[terms])
        seed_path = root/'workspace-seed.textproto'
        seed_path.write_text(workspace_fixture.text_format.MessageToString(seed)); seed_path.chmod(0o600)
        result = subprocess.run([str(workspace_fixture.BROKER), '--state-dir', str(execution),
                                 '--init-authority', str(seed_path)], capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        client_path = root/'workspace-issuer.textproto'
        client_path.write_bytes(self.config_file().read_bytes()); client_path.chmod(0o600)
        config = workspace_fixture.broker.WorkspaceAuthorityConfig(
            protocol_version=1, issuer_client_config=str(client_path),
            bindings=[workspace_fixture.broker.WorkspaceAuthorityBinding(
                workspace='two', authority_directory=str(directory))])
        self.configure_participant(config, [workspace_fixture.grant.Domain(
            kind=workspace_fixture.grant.WORKSPACE, id='two')])
        path = root/'workspace-policy.textproto'
        path.write_text(workspace_fixture.text_format.MessageToString(config)); path.chmod(0o600)
        broker = workspace_fixture.BrokerProcess(self, execution, path)
        world = self.scoped_world(session.REGISTERED_ACTORS, broker=broker)
        world.management('workspace.available', id='two', available=True)
        return world

    def test_valid_workspace_selection_is_saved_independently_in_both_worlds(self):
        self._defer_initial_actor_auth = True
        try:
            a = self.workspace(); b = self.workspace_two()
        finally:
            self._defer_initial_actor_auth = False
        room_a = self.room(a, 'workspace-one-room'); room_b = self.room(b, 'workspace-two-room')
        pose_a, pose_b = spawn(14), spawn(28)
        select_two = core.ActorActionRequest(
            seq=1, workspace_select=core.WorkspaceIdRequest(id='two'))
        expected = [(a, room_a, pose_a, 'one'), (b, room_b, pose_b, 'two'),
                    (a, room_a, pose_a, 'one'), (b, room_b, pose_b, 'two'),
                    (a, room_a, pose_a, 'one'), (b, room_b, pose_b, 'two')]

        def witness(index, reply):
            world, room, pose, workspace = expected[index]
            self.assert_state(reply.observations[-1], world, room, pose, workspace)
            if index:
                self.assert_active(reply.results[-1])

        reply = self.run_driver(a, [(b, room_b, pose_b, 2, (select_two,)),
                                    (a, None, None, 2, ()),
                                    (b, None, None, 1, ()),
                                    (a, None, None, 1, ()),
                                    (b, None, None, 1, ())],
                                hook=witness, source_room=room_a, source_pose=pose_a,
                                initial_actions=(select_workspace(),))
        self.assertEqual(len(reply.results), 5)
        self.assertEqual([item.world_id for item in reply.observations],
                         [a.management('world.info')['world_id'],
                          b.management('world.info')['world_id'],
                          a.management('world.info')['world_id'],
                          b.management('world.info')['world_id'],
                          a.management('world.info')['world_id'],
                          b.management('world.info')['world_id']])

    def test_unavailable_workspace_clears_saved_selection_on_return(self):
        a = self.workspace(); b = self.world()
        def change(index, reply):
            if index == 1: a.management('workspace.available', id='one', available=False)
        reply = self.run_driver(a, [(b, None, None, 2, ()), (a, None, None, 1, ())],
                                initial_actions=(select_workspace(),), hook=change)
        for result in reply.results: self.assert_active(result)
        self.assertEqual(reply.observations[0].state.workspace, 'one')
        self.assertFalse(reply.observations[-1].state.HasField('workspace'))
        a.management('workspace.available', id='one', available=True)
        opened = self.authenticate(self.channel(a)); self.assertTrue(opened.HasField('opened'))
        self.assertFalse(a.peers[self.channels[a]-1].call(inspect=session.WorldSessionInspect()).state.HasField('workspace'))

    def test_revoked_workspace_grant_clears_saved_selection_on_return(self):
        self.enroll_second()
        a = self.workspace(roots=lambda: [self.workspace_terms(), self.workspace_terms(self.second)])
        # The original actor is offline. A distinct administrator supplies the real grant revocation.
        original_peer = self.channel(a)
        self.assertTrue(self.authenticate(original_peer).HasField('opened'))
        original = next(record for record in self.own(original_peer).grants if not record.parent_id)
        self.assertTrue(original_peer.call(depart=session.WorldSessionDepart(
            seq=1, transition_id=os.urandom(32))).HasField('departed'))
        admin = self.channel(a)
        self.assertTrue(self.authenticate_second(admin).HasField('opened'))
        b = self.world()
        def change(index, reply):
            if index == 1:
                self.revoke_grant(admin, original)
        reply = self.run_driver(a, [(b, None, None, 2, ()), (a, None, None, 1, ())],
                                initial_actions=(select_workspace(),), hook=change)
        for result in reply.results: self.assert_active(result)
        self.assertEqual(reply.observations[0].state.workspace, 'one')
        self.assertFalse(reply.observations[-1].state.HasField('workspace'))
# Crash/restart cases use retained native ownership in test_actor_transition_recovery.py.
# See docs/M06_ACTOR_INTEGRATION_TESTS.txt for the explicit coverage mapping.


class DriverFramingTests(unittest.TestCase):
    def reject(self, data, reason):
        process = subprocess.run([str(DRIVER)], input=data, capture_output=True, timeout=5)
        self.assertEqual(process.returncode, 2)
        reply = read_message(io.BytesIO(process.stdout), integration.ActorTransitionIntegrationReply)
        self.assertIn(reason, reply.driver_error)
        self.assertFalse(reply.HasField('source_opened'))

    def test_oversized_frame(self): self.reject(struct.pack('!I', MAX_FRAME+1), 'size limit')
    def test_truncated_header(self): self.reject(b'\x00\x01', 'Truncated frame header')
    def test_truncated_payload(self): self.reject(b'\x00\x00\x00\x02\x08', 'Truncated frame payload')
    def test_malformed_payload(self): self.reject(b'\x00\x00\x00\x01\xff', 'malformed protobuf')
    def test_trailing_malformed_payload(self): self.reject(b'\x00\x00\x00\x03\x08\x01\xff', 'malformed protobuf')
    def test_unknown_field(self): self.reject(b'\x00\x00\x00\x05\x08\x01\xa0\x06\x01', 'Unknown protobuf fields')
    def test_nested_unknown_field(self):
        request = integration.ActorTransitionIntegrationRequest(protocol_version=1)
        request.credential.ParseFromString(b'\xa0\x06\x01')
        self.reject(frame(request), 'Unknown protobuf fields')


if __name__ == '__main__':
    unittest.main(verbosity=2)
