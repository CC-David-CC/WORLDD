"""Shared-state regression with one actor TLS connection on each real host.

SSH performs trusted test orchestration. It does not execute actor actions.
The controller signs fixture actor proofs; the witness originates remote TLS.
This is not yet the full M05 topology or independent actor-key provisioning test.
"""
import os
import socket
from pathlib import Path
import shlex
import subprocess
import unittest

from harness.wire import frame
import world_session_pb2 as session
import core_pb2 as core
import grants_pb2 as grant
from harness.oracles import pose
from test_world_identity import SessionPeer, receive
import test_world_shared_state as baseline


class RemotePeer(SessionPeer):
    def __init__(self, world, fixture):
        self.sequence = self.action_sequence = 0
        self.expected_exit = 0
        self.log = (fixture.root / f'witness-{len(fixture.worlds)}-{len(world.peers)}.log').open('wb')
        command = ['env', 'PYTHONPATH=' + os.environ['SPATIAL_REMOTE_PYTHONPATH'], 'python3',
                   os.environ['SPATIAL_REMOTE_WITNESS'], world.address, str(world.port), world.credentials]
        self.proc = subprocess.Popen(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                                      world.remote_host, shlex.join(command)],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log)

    def request(self, request):
        self.sequence += 1
        request.request_id = self.sequence
        self.proc.stdin.write(frame(request))
        self.proc.stdin.flush()
        reply = receive(lambda n: os.read(self.proc.stdout.fileno(), n), self.proc.stdout,
                        session.WorldSessionReply)
        if reply.protocol_version != 1 or reply.request_id != self.sequence:
            raise AssertionError('Wrong witness reply identity')
        return reply

    def close(self):
        if not self.proc.stdin.closed:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=3)
            raise
        finally:
            self.proc.stdout.close()
            self.log.close()
        if self.proc.returncode != self.expected_exit:
            raise AssertionError(f'TLS witness exited with {self.proc.returncode}')

    def assert_disconnected(self, fixture):
        with fixture.assertRaises(EOFError):
            self.call(inspect=session.WorldSessionInspect())
        fixture.assertEqual(self.proc.wait(timeout=8), 1)
        self.expected_exit = 1


class CrossHostWorld(baseline.PersistentTlsWorld):
    def __init__(self, fixture, local, native):
        self.remote_host = os.environ['SPATIAL_REMOTE_HOST']
        if self.remote_host not in ('sb16', 'sb16-2'):
            raise ValueError('Unauthorized witness host')
        super().__init__(fixture, local, native, address=os.environ['SPATIAL_WORLD_ADDRESS'])
        self.credentials = subprocess.check_output(
            ['ssh', self.remote_host, 'umask 077; mktemp -d /tmp/spatial-m05-witness-XXXXXXXX'],
            text=True, timeout=10).strip()
        if not self.credentials.startswith('/tmp/spatial-m05-witness-') or '/' in self.credentials[5:]:
            raise ValueError('Invalid remote fixture directory')
        fixture.addCleanup(self.remove_credentials)
        for source, name in ((self.server_cert, 'server.crt'),
                             (fixture.service_cert, 'client.crt'), (fixture.service_key, 'client.key')):
            subprocess.run(['scp', '-q', str(source), f'{self.remote_host}:{self.credentials}/{name}'],
                           check=True, timeout=10)

    def remove_credentials(self):
        paths = [self.credentials + '/' + name for name in ('server.crt', 'client.crt', 'client.key')]
        command = shlex.join(['rm', '-f', '--', *paths]) + ' && ' + shlex.join(['rmdir', '--', self.credentials])
        subprocess.run(['ssh', self.remote_host, command], check=True, timeout=10)

    def connect(self, fixture):
        peer = RemotePeer(self, fixture)
        self.peers.append(peer)
        return peer


class SharedCrossHostTests(baseline.SharedWorldStateTests):
    def start_remote(self, local, native=False):
        world = CrossHostWorld(self, local, native)
        self.worlds.append(world)
        world.management('system.hello')
        return world

    def open_owner(self, world):
        peer = baseline.PersistentTlsWorld.connect(world, self)
        opened = self.authenticate(peer)
        self.assertTrue(opened.HasField('opened'), opened)
        self.assertEqual(opened.opened.world_id, world.management('world.info')['world_id'])
        self.assertFalse(peer.call(inspect=session.WorldSessionInspect()).state.workspace)
        return peer

    def test_abrupt_authority_loss_requires_supervised_retirement(self):
        self.enroll_second()
        world = self.new_world(native=True)
        owner = self.open_owner(world)
        self.world_info = world.management('world.info')
        delegated = self.delegate(owner, self.root_grant(owner), self.terms(self.second,
            rights=(grant.WORLD_ENTER, grant.WORLD_OBSERVE, grant.WORLD_MANIPULATE), depth=0))
        self.assertTrue(delegated.HasField('grant'), delegated)
        viewer = world.connect(self)
        self.assertTrue(self.authenticate_second(viewer).HasField('opened'))
        world.management('world.create', entity=dict(id='shared-object', kind='object',
            room=self.world_info['entry_room'], pose=pose((0, 0, -2))))
        self.assertTrue(viewer.act(object_grab=core.ObjectGrabRequest(id='shared-object')).HasField('action'))
        committed = core.Pose(position=core.Vec3(x=.25, z=-2), orientation=core.Quaternion(w=1))
        self.assertTrue(viewer.act(object_move=core.ObjectMoveRequest(pose=committed)).HasField('action'))
        self.assertTrue(viewer.act(object_release=core.ObjectReleaseRequest(commit=True)).HasField('action'))
        self.assertEqual(self.observed_object(owner).pose, committed)
        # Leave a different preview and a live lease at the exact crash boundary.
        self.assertTrue(viewer.act(object_grab=core.ObjectGrabRequest(id='shared-object')).HasField('action'))
        preview = core.Pose(position=core.Vec3(x=-.25, z=-2), orientation=core.Quaternion(w=1))
        self.assertTrue(viewer.act(object_move=core.ObjectMoveRequest(pose=preview)).HasField('action'))
        self.assertEqual(self.observed_object(owner).pose, preview)
        world.proc.kill()
        self.assertEqual(world.proc.wait(timeout=5), -9)
        with self.assertRaises((OSError, EOFError)):
            owner.call(inspect=session.WorldSessionInspect())
        viewer.assert_disconnected(self)
        with self.assertRaises(OSError):
            socket.create_connection((world.address, world.port), timeout=.5)
        world.close()
        # A direct fixture launch cannot supply supervised cleanup evidence.
        rejected = CrossHostWorld(self, world, native=True)
        self.worlds.append(rejected)
        self.assertNotEqual(rejected.proc.wait(timeout=5), 0)
        self.assertIn('Service already has an active generation; cleanup is required',
                      Path(rejected.log.name).read_text())
        rejected.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
