"""Real TLS actors share durable world state without sharing admission authority."""
import os
import socket
import ssl
import subprocess
import time
import unittest
from google.protobuf import text_format
import core_pb2 as core
import issuer_pb2 as issuer
import world_session_pb2 as session
import grants_pb2 as grant
import test_world_authority as authority
from harness.oracles import pose
from test_world_bootstrap import BootstrapWorldFixture
from test_world_identity import WorldProcess, WORLD
from test_world_remote import TlsActorPeer


class PersistentTlsWorld(WorldProcess):
    def __init__(self, fixture, local, native=False, address='127.0.0.1'):
        self.sequence = 0
        self.peers = []
        # This constructor bypasses WorldProcess.__init__; it owns no broker relay.
        self.broker_channel = None
        self.state_path = local.state_path
        self.config_path = local.config_path
        self.runtime_path = local.runtime_path
        self.native = native
        number = len(fixture.worlds)
        self.log = (fixture.root / f'shared-world-{number}.log').open('wb')
        self.server_key, self.server_cert = fixture.certificate(f'shared-server-{number}', 'world.test')
        tls = session.WorldActorTlsConfig(protocol_version=1, certificate_path=str(self.server_cert),
            private_key_path=str(self.server_key), client_ca_path=str(fixture.service_cert),
            io_timeout_ms=5000, idle_timeout_ms=3000, connection_limit=16)
        path = fixture.root / f'shared-tls-{number}.textproto'
        path.write_text(text_format.MessageToString(tls))
        path.chmod(0o600)
        listener = socket.socket()
        listener.bind((address, 0))
        listener.listen(16)
        self.address, self.port = listener.getsockname()
        command = [str(WORLD), '--state-dir', str(self.state_path), '--identity-config', str(self.config_path),
            '--config', str(self.runtime_path), '--actor-listen-fd', str(listener.fileno()),
            '--actor-tls-config', str(path)]
        if native:
            command += ['--display', os.environ['SPATIAL_TEST_DISPLAY']]
        try:
            self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self.log, pass_fds=(listener.fileno(),))
        finally:
            listener.close()

    def connect(self, fixture):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
        context.load_verify_locations(cafile=str(self.server_cert))
        context.load_cert_chain(str(fixture.service_cert), str(fixture.service_key))
        raw = socket.create_connection((self.address, self.port), timeout=5)
        try:
            stream = context.wrap_socket(raw, server_hostname='world.test')
        except BaseException:
            raw.close()
            raise
        peer = TlsActorPeer(stream)
        self.peers.append(peer)
        return peer


class SharedWorldStateTests(BootstrapWorldFixture):
    enroll_second = authority.WorldAuthorityTests.enroll_second
    authenticate_second = authority.WorldAuthorityTests.authenticate_second

    def launch(self, address='127.0.0.1'):
        self.config.connection_limit = 32
        self.config.io_timeout_ms = 5000
        return super().launch(address)

    def config_file(self, endpoint_port=None, **overrides):
        overrides.setdefault('io_timeout_ms', 5000)
        return super().config_file(endpoint_port, **overrides)

    def connect(self, *args, **kwargs):
        peer = super().connect(*args, **kwargs)
        peer.stream.settimeout(5)
        return peer

    def start_remote(self, local, native=False):
        world = PersistentTlsWorld(self, local, native)
        self.worlds.append(world)
        world.management('system.hello')
        return world

    def new_world(self, admission=session.EXPLICIT_ACTORS, native=False):
        local = self.scoped_world(admission=admission, authenticate=False)
        local.close()
        return self.start_remote(local, native)

    def open_owner(self, world):
        peer = world.connect(self)
        opened = self.authenticate(peer)
        self.assertTrue(opened.HasField('opened'), opened)
        self.assertEqual(opened.opened.world_id, world.management('world.info')['world_id'])
        self.assertFalse(peer.call(inspect=session.WorldSessionInspect()).state.workspace)
        return peer

    def observed_object(self, peer, name='shared-object'):
        reply = peer.act(observe=core.ActorObserveRequest(width=127, height=95))
        self.assertTrue(reply.HasField('action'), reply)
        return next(item for item in reply.action.observation.visible if item.id == name)

    def test_two_tls_actors_share_committed_object_and_recover_same_world(self):
        self.enroll_second()
        world = self.new_world(native=True)
        owner = self.open_owner(world)
        self.world_info = world.management('world.info')
        root = self.root_grant(owner)
        delegated = self.delegate(owner, root, self.terms(self.second,
            rights=(grant.WORLD_ENTER, grant.WORLD_OBSERVE, grant.WORLD_MANIPULATE), depth=0))
        self.assertTrue(delegated.HasField('grant'), delegated)
        viewer = world.connect(self)
        opened = self.authenticate_second(viewer)
        self.assertTrue(opened.HasField('opened'), opened)
        self.assertNotEqual(self.identity.actor_id, self.second.actor_id)
        world.management('world.create', entity=dict(id='shared-object', kind='object',
            room=self.world_info['entry_room'], pose=pose((0, 0, -2))))
        self.assertEqual(self.observed_object(owner).pose, self.observed_object(viewer).pose)
        self.assertTrue(owner.act(object_grab=core.ObjectGrabRequest(id='shared-object')).HasField('action'))
        self.denied(viewer.act(object_grab=core.ObjectGrabRequest(id='shared-object')), core.BUSY)
        moved = core.Pose(position=core.Vec3(x=.25, z=-2), orientation=core.Quaternion(w=1))
        self.assertTrue(owner.act(object_move=core.ObjectMoveRequest(pose=moved)).HasField('action'))
        self.assertTrue(owner.act(object_release=core.ObjectReleaseRequest(commit=True)).HasField('action'))
        self.assertEqual(self.observed_object(viewer).pose, moved)
        before = viewer.call(inspect=session.WorldSessionInspect()).state
        pending = world.connect(self)
        challenge = pending.call(begin=session.WorldAuthenticationBegin(credential=self.second_record.credential)).challenge
        old_proof = issuer.AuthenticationComplete(nonce=challenge.nonce,
            proof=self.proof(challenge, self.second_key, self.second_public))
        world.management('world.checkpoint')
        world.close()
        with self.assertRaises(OSError):
            socket.create_connection((world.address, world.port), timeout=.5)
        restarted = self.start_remote(world, native=True)
        self.assertEqual(restarted.management('world.info')['world_id'], self.world_info['world_id'])
        fresh = restarted.connect(self)
        self.denied(fresh.call(complete=old_proof), core.UNAUTHORIZED)
        self.assertTrue(self.authenticate_second(fresh).HasField('opened'))
        after = fresh.call(inspect=session.WorldSessionInspect()).state
        self.assertEqual((after.id, after.room, after.pose), (before.id, before.room, before.pose))
        self.assertFalse(after.workspace)
        self.assertFalse(any(after.axes))
        self.assertEqual(self.observed_object(fresh).pose, moved)
        self.assertTrue(fresh.act(object_grab=core.ObjectGrabRequest(id='shared-object')).HasField('action'))
        self.assertTrue(fresh.act(object_release=core.ObjectReleaseRequest(commit=False)).HasField('action'))

    def test_private_worlds_keep_distinct_identity_and_independent_persistent_state(self):
        self.enroll_second()
        first, second = self.new_world(), self.new_world()
        identities = [world.management('world.info')['world_id'] for world in (first, second)]
        self.assertNotEqual(*identities)
        for number, world in enumerate((first, second)):
            world.management('world.create', entity=dict(id='local-room', kind='room', label=f'private-{number}'))
            peer = world.connect(self)
            opened = self.authenticate(peer, room='local-room',
                pose=core.Pose(position=core.Vec3(x=number+1), orientation=core.Quaternion(w=1)))
            self.assertTrue(opened.HasField('opened'))
            self.denied(self.authenticate_second(world.connect(self)), core.FORBIDDEN)
            world.management('world.checkpoint')
        for number, world in enumerate((first, second)):
            world.close()
            restored = self.start_remote(world)
            self.assertEqual(restored.management('world.info')['world_id'], identities[number])
            self.assertEqual(restored.management('world.get', id='local-room')['label'], f'private-{number}')
            peer = self.open_owner(restored)
            state = peer.call(inspect=session.WorldSessionInspect()).state
            self.assertEqual(state.room, 'local-room')
            self.assertEqual(state.pose.position.x, number+1)
            self.denied(self.authenticate_second(restored.connect(self)), core.FORBIDDEN)

    def test_actor_population_is_visible_in_shared_room(self):
        self.enroll_second()
        world = self.new_world(native=True)
        owner = self.open_owner(world)
        self.world_info = world.management('world.info')
        delegated = self.delegate(owner, self.root_grant(owner), self.terms(self.second,
            rights=(grant.WORLD_ENTER, grant.WORLD_OBSERVE, grant.WORLD_NAVIGATE), depth=0))
        self.assertTrue(delegated.HasField('grant'), delegated)
        viewer = world.connect(self)
        location = core.Pose(position=core.Vec3(z=-2), orientation=core.Quaternion(w=1))
        opened = self.authenticate(viewer, self.second_record, self.second_key,
                                   self.second_public, pose=location)
        self.assertTrue(opened.HasField('opened'), opened)
        population = owner.act(observe=core.ActorObserveRequest(width=127, height=95))
        self.assertTrue(population.HasField('action'), population)
        actors = {item.id: item for item in population.action.observation.visible if item.kind == core.ACTOR}
        self.assertIn(opened.opened.actor_id, actors)
        self.assertEqual(actors[opened.opened.actor_id].pose, location)
        self.assertGreater(actors[opened.opened.actor_id].visible_pixels, 0)
        self.assertNotIn(population.action.observation.actor, actors)
        self.assertTrue(viewer.act(input=core.ActorInputRequest(axes=[1, 0, 0, 0, 0, 0])).HasField('action'))
        time.sleep(.1)
        self.assertTrue(viewer.act(stop=core.ActorStopRequest()).HasField('action'))
        moved = viewer.call(inspect=session.WorldSessionInspect()).state.pose
        self.assertGreater(moved.position.x, location.position.x)
        self.assertEqual(self.observed_object(owner, opened.opened.actor_id).pose, moved)
        world.management('world.checkpoint')
        world.close()
        restored = self.start_remote(world, native=True)
        owner = self.open_owner(restored)
        self.assertEqual(self.observed_object(owner, opened.opened.actor_id).pose, moved)
        restored.management('world.create', entity=dict(id='occluder', kind='panel', label='Occluder',
            room=self.world_info['entry_room'], pose=pose((0, 0, -1)), size=[4, 4], color=[32, 48, 64]))
        hidden = owner.act(observe=core.ActorObserveRequest(width=127, height=95))
        self.assertTrue(hidden.HasField('action'), hidden)
        self.assertNotIn(opened.opened.actor_id, [item.id for item in hidden.action.observation.visible])

    def test_public_admission_does_not_create_private_admission_or_action_authority(self):
        self.enroll_second()
        public = self.new_world(admission=session.REGISTERED_ACTORS)
        private = self.new_world()
        peer = public.connect(self)
        self.assertTrue(self.authenticate_second(peer).HasField('opened'))
        self.assertFalse(peer.call(inspect=session.WorldSessionInspect()).state.workspace)
        self.assertFalse(self.own(peer).grants)
        self.denied(self.navigation(peer), core.FORBIDDEN)
        self.denied(peer.act(observe=core.ActorObserveRequest()), core.FORBIDDEN)
        self.denied(self.authenticate_second(private.connect(self)), core.FORBIDDEN)


if __name__ == '__main__':
    unittest.main(verbosity=2)
