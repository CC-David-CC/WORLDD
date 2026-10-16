"""Two private authorities and one shared authority remain independent across hosts."""
import io
import os
from pathlib import Path
import shlex
import socket
import subprocess
import unittest
from google.protobuf import text_format

import core_pb2 as core
import grants_pb2 as grants
import issuer_pb2 as issuer
import world_session_pb2 as session
import world_topology_pb2 as topology
from harness.wire import frame, make_request, read_message, result_value
from harness.oracles import pose
import test_world_remote_keys as existing
from test_world_identity import WorldProcess, WORLD, receive
from test_world_shared_state import PersistentTlsWorld
from test_world_shared_crosshost import RemotePeer


class RemotePrivateWorld(WorldProcess):
    def __init__(self, fixture):
        self.fixture = fixture
        self.remote_host = os.environ['SPATIAL_REMOTE_HOST']
        fixture.assertIn(self.remote_host, ('sb16', 'sb16-2'))
        self.address = '10.0.7.94' if self.remote_host == 'sb16-2' else '10.0.7.49'
        self.directory = subprocess.check_output(['ssh', self.remote_host,
            'umask 077; mktemp -d /tmp/spatial-m05-topology-XXXXXXXX'], text=True, timeout=10).strip()
        if not self.directory.startswith('/tmp/spatial-m05-topology-') or '/' in self.directory[5:]:
            raise ValueError('Invalid remote world directory')
        self.credentials = self.directory
        self.launcher = str(Path(os.environ['SPATIAL_REMOTE_WITNESS']).with_name('world_topology_launcher.py'))
        self.proc = None
        self.broker_channel = None
        fixture.addCleanup(self.remove)
        initialized = self.run([str(WORLD), '--state-dir', self.directory + '/state'],
                               frame(make_request('world.info', {})))
        info = read_message(io.BytesIO(initialized), core.Reply)
        fixture.assertFalse(info.HasField('error'), info)
        self.identity = result_value(info)['world_id']
        self.server_key, self.server_cert = fixture.certificate('remote-private-server', 'world.test')
        for source, name in ((self.server_key, 'server.key'), (self.server_cert, 'server.crt'),
            (fixture.server_cert, 'issuer.crt'), (fixture.service_cert, 'client.crt'),
            (fixture.service_key, 'client.key')):
            self.copy(source, name)
        client_path = fixture.config_file(address=os.environ['SPATIAL_WORLD_ADDRESS'])
        client = text_format.Parse(client_path.read_text(), issuer.IssuerTlsClientConfig())
        client.server_ca_path = self.directory + '/issuer.crt'
        client.certificate_path = self.directory + '/client.crt'
        client.private_key_path = self.directory + '/client.key'
        self.config('client.textproto', client)
        config = session.WorldIdentityConfig(protocol_version=1, admission=session.EXPLICIT_ACTORS,
            issuer_client_config=str(client_path), authority_directory=self.directory + '/authority')
        fixture.configure_participant(config, [grants.Domain(kind=grants.WORLD, id=self.identity)])
        runtime = text_format.Parse(Path(config.participant_runtime_config).read_text(),
                                    issuer.AuthorityParticipantRuntimeConfig())
        self.copy(Path(runtime.private_key_path), 'authority.pem')
        runtime.private_key_path = self.directory + '/authority.pem'
        runtime.issuer_client_config = self.directory + '/client.textproto'
        self.config('participant.textproto', runtime)
        config.issuer_client_config = runtime.issuer_client_config
        config.participant_runtime_config = self.directory + '/participant.textproto'
        self.config('identity.textproto', config)
        fixture.world_info = dict(world_id=self.identity)
        seed = session.WorldAuthoritySeed(protocol_version=1, authority_directory=config.authority_directory,
                                          roots=[fixture.terms(fixture.second)])
        self.config('seed.textproto', seed)
        self.run(['mkdir', '-m', '700', '--', config.authority_directory])
        self.run([str(WORLD), '--state-dir', self.directory + '/state',
                  '--init-authority', self.directory + '/seed.textproto'])
        self.copy(WORLD.parent / 'runtime.textproto', 'runtime.textproto')
        self.config('tls.textproto', session.WorldActorTlsConfig(protocol_version=1,
            certificate_path=self.directory + '/server.crt', private_key_path=self.directory + '/server.key',
            client_ca_path=self.directory + '/client.crt', io_timeout_ms=5000, idle_timeout_ms=5000,
            connection_limit=16))
        self.start()

    def run(self, command, data=b''):
        result = subprocess.run(['ssh', self.remote_host, shlex.join(command)], input=data,
                                capture_output=True, timeout=15)
        self.fixture.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def copy(self, source, name):
        subprocess.run(['scp', '-q', str(source), f'{self.remote_host}:{self.directory}/{name}'],
                       check=True, timeout=10)

    def config(self, name, message):
        source = self.fixture.root / ('remote-' + name)
        source.write_text(text_format.MessageToString(message)); source.chmod(0o600)
        self.copy(source, name)

    def launcher_command(self):
        return ['env', 'PYTHONPATH=' + os.environ['SPATIAL_REMOTE_PYTHONPATH'] + ':' + str(WORLD.parents[3]),
                'python3', self.launcher, self.directory]

    def start(self):
        self.sequence = 0
        self.peers = []
        self.generation = getattr(self, 'generation', 0) + 1
        self.log = (self.fixture.root / f'topology-remote-world-{self.generation}.log').open('wb')
        command = self.launcher_command() + ['--world', str(WORLD), '--address', self.address]
        self.proc = subprocess.Popen(['ssh', self.remote_host, shlex.join(command)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log)
        self._initialize_process_lifecycle()
        endpoint = receive(lambda n: os.read(self.proc.stdout.fileno(), n), self.proc.stdout, topology.WorldEndpoint)
        self.fixture.assertEqual((endpoint.protocol_version, endpoint.host, endpoint.address),
                                  (1, self.remote_host, self.address))
        self.fixture.assertGreater(endpoint.process_id, 1)
        self.fixture.assertGreater(endpoint.port, 0)
        self.port = endpoint.port
        target = Path(os.environ['SPATIAL_SECURITY_EVIDENCE'])
        target.mkdir(parents=True, exist_ok=True)
        (target / f'topology-endpoint-{self.generation}.pb').write_bytes(endpoint.SerializeToString())
        self.fixture.assertEqual(self.management('world.info')['world_id'], self.identity)

    def connect(self, fixture):
        peer = RemotePeer(self, fixture); self.peers.append(peer)
        return peer

    def remove(self):
        if self.proc is not None:
            running = self.proc.poll() is None
            self.close()
            if running:
                self.fixture.assertEqual(self.proc.returncode, 0)
        self.run(self.launcher_command() + ['--cleanup'])


class WorldTopologyTests(existing.RemoteKeyWorldTests):
    def launch(self, address='0.0.0.0'):
        return super().launch(address)

    def test_three_live_worlds_keep_private_authority_during_shared_loss(self):
        self.enroll_second()
        private = self.new_world()
        local_actor = self.open_owner(private)
        remote = RemotePrivateWorld(self)
        self.worlds.append(remote)
        remote_actor = remote.connect(self)
        self.assertTrue(self.authenticate_second(remote_actor).HasField('opened'))
        self.denied(self.authenticate_second(private.connect(self)), core.FORBIDDEN)
        outsider = PersistentTlsWorld.connect(remote, self)
        self.denied(self.authenticate(outsider), core.FORBIDDEN)
        shared = self.new_world(admission=session.REGISTERED_ACTORS, native=True)
        owner = self.open_owner(shared)
        viewer = shared.connect(self)
        opened = self.authenticate(viewer, self.second_record, self.second_key, self.second_public,
            pose=core.Pose(position=core.Vec3(x=.6, z=-1), orientation=core.Quaternion(w=1)))
        self.assertTrue(opened.HasField('opened'))
        self.assertFalse(self.own(viewer).grants)
        self.assertFalse(viewer.call(inspect=session.WorldSessionInspect()).state.workspace)
        self.world_info = shared.management('world.info')
        delegated = self.delegate(owner, self.root_grant(owner), self.terms(self.second,
            rights=(grants.WORLD_ENTER, grants.WORLD_OBSERVE, grants.WORLD_MANIPULATE), depth=0))
        self.assertTrue(delegated.HasField('grant'))
        self.assertGreater(self.observed_object(owner, opened.opened.actor_id).visible_pixels, 0)
        identities = [world.management('world.info')['world_id'] for world in (private, remote, shared)]
        self.assertEqual(len(set(identities)), 3)
        for index, world in enumerate((private, remote, shared)):
            self.assertIsNone(world.proc.poll())
            world.management('world.create', entity=dict(id='local-marker', kind='room', label=f'world-{index}'))
        shared.management('world.create', entity=dict(id='shared-object', kind='object',
            room=self.world_info['entry_room'], pose=pose((0, 0, -2))))
        self.assertEqual(self.observed_object(owner).pose, self.observed_object(viewer).pose)
        self.assertTrue(viewer.act(object_grab=core.ObjectGrabRequest(id='shared-object')).HasField('action'))
        moved = core.Pose(position=core.Vec3(x=.25, z=-2), orientation=core.Quaternion(w=1))
        self.assertTrue(viewer.act(object_move=core.ObjectMoveRequest(pose=moved)).HasField('action'))
        self.assertTrue(viewer.act(object_release=core.ObjectReleaseRequest(commit=True)).HasField('action'))
        self.assertEqual(self.observed_object(owner).pose, moved)
        for world in (private, remote, shared):
            world.management('world.checkpoint')
        pending = shared.connect(self)
        challenge = pending.call(begin=session.WorldAuthenticationBegin(credential=self.second_record.credential)).challenge
        stale = issuer.AuthenticationComplete(nonce=challenge.nonce,
            proof=self.proof(challenge, self.second_key, self.second_public))
        # Open fresh private sessions after provisioning, within their normal idle budgets.
        local_actor.close()
        remote_actor.close()
        local_actor = self.open_owner(private)
        remote_actor = remote.connect(self)
        self.assertTrue(self.authenticate_second(remote_actor).HasField('opened'))
        # Stop the authority before closing clients, so both peers must observe transport loss.
        shared.proc.stdin.close()
        self.assertEqual(shared.proc.wait(timeout=5), 0)
        with self.assertRaises((OSError, EOFError)):
            owner.call(inspect=session.WorldSessionInspect())
        viewer.assert_disconnected(self)
        shared.close()
        with self.assertRaises(OSError):
            socket.create_connection((shared.address, shared.port), timeout=.5)
        # Both private actors still exercise the production actor path during shared unavailability.
        for peer in (local_actor, remote_actor):
            self.assertTrue(peer.call(inspect=session.WorldSessionInspect()).HasField('state'))
        for index, world in enumerate((private, remote)):
            self.assertIsNone(world.proc.poll())
            self.assertEqual(world.management('world.get', id='local-marker')['label'], f'world-{index}')
        restored = self.start_remote(shared, native=True)
        self.assertEqual(restored.management('world.info')['world_id'], identities[2])
        fresh = restored.connect(self)
        self.denied(fresh.call(complete=stale), core.UNAUTHORIZED)
        self.assertTrue(self.authenticate_second(fresh).HasField('opened'))
        self.assertEqual(self.observed_object(fresh).pose, moved)
        self.assertTrue(fresh.act(object_grab=core.ObjectGrabRequest(id='shared-object')).HasField('action'))
        self.assertTrue(fresh.act(object_release=core.ObjectReleaseRequest(commit=False)).HasField('action'))
        remote.close(); remote.start()
        self.assertEqual(remote.management('world.get', id='local-marker')['label'], 'world-1')
        self.assertTrue(self.authenticate_second(remote.connect(self)).HasField('opened'))
        private.close()
        local_restored = self.start_remote(private)
        self.assertEqual(local_restored.management('world.info')['world_id'], identities[0])
        self.assertEqual(local_restored.management('world.get', id='local-marker')['label'], 'world-0')
        self.open_owner(local_restored)


if __name__ == '__main__':
    unittest.main(defaultTest='WorldTopologyTests.test_three_live_worlds_keep_private_authority_during_shared_loss',
                  verbosity=2)
