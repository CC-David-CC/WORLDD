"""Remote world geometry must not export live applications, even with observation grants."""
import os
import socket
import ssl
import struct
import subprocess
import time
import unittest
from unittest.mock import patch
from google.protobuf import text_format
import core_pb2 as core
import world_session_pb2 as session
import test_world_authority as authority
from test_actor_pixels import ActorPixelTests,grant
from test_world_identity import WorldProcess,SessionPeer,WORLD
from test_world_remote import RemoteWorldProcess
from world_process_lifecycle_fixture import WorldProcessLifecycleMixin
from harness.oracles import pose
from harness.wire import frame


class HybridWorldProcess(WorldProcess):
    connect=RemoteWorldProcess.connect

    def __init__(self,fixture,config,state,native=False,count=2,runtime_config=None,broker=None):
        self.sequence=0;self.broker_channel=None;self.log=(fixture.root/f'hybrid-{len(fixture.worlds)}.log').open('wb')
        self.server_key,self.server_cert=fixture.certificate('hybrid-world','world.test')
        tls=session.WorldActorTlsConfig(protocol_version=1,certificate_path=str(self.server_cert),
            private_key_path=str(self.server_key),client_ca_path=str(fixture.service_cert),
            io_timeout_ms=700,idle_timeout_ms=3000,connection_limit=8)
        path=fixture.root/'hybrid-tls.textproto';path.write_text(text_format.MessageToString(tls));path.chmod(0o600)
        listener=socket.socket();listener.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,4096)
        listener.bind(('127.0.0.1',0));listener.listen(8);self.port=listener.getsockname()[1]
        pairs=[socket.socketpair() for _ in range(count)];self.peers=[SessionPeer(p[0]) for p in pairs]
        command=[str(WORLD),'--state-dir',str(state),'--identity-config',str(config),
            '--actor-listen-fd',str(listener.fileno()),'--actor-tls-config',str(path)]
        if runtime_config:command+=['--config',str(runtime_config)]
        if native:command+=['--display',os.environ['SPATIAL_TEST_DISPLAY']]
        if broker:command+=['--workspace-fd',str(broker.stream.fileno()),'--workspace-pid',str(broker.proc.pid)]
        for _,server in pairs:command+=['--actor-fd',str(server.fileno())]
        try:
            self.proc=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,
                pass_fds=(listener.fileno(),)+tuple(p[1].fileno() for p in pairs)+(tuple([broker.stream.fileno()]) if broker else ()))
            self._initialize_process_lifecycle()
        finally:
            listener.close()
            for _,server in pairs:server.close()


class RemoteNativeTests(ActorPixelTests):
    def setup_remote_pixels(self):
        with patch.object(authority,'WorldProcess',HybridWorldProcess):self.setup_world()
        empty=self.pixels();app,surface=self.probe()
        self.grant_viewer([grant.WORKSPACE_ACCESS]);self.grant_viewer([grant.APPLICATION_OBSERVE],app)
        local=self.pixels();self.assertNotEqual(local.image.rgb8,empty.image.rgb8)
        self.assertIn(surface['id'],{item.id for item in local.visible})
        self.assertTrue(self.viewer.call(close=session.WorldSessionClose()).HasField('closed'))
        remote=self.world_process.connect(self);self.assertTrue(self.authenticate_second(remote).HasField('opened'))
        return remote,empty,app,surface

    def test_remote_geometry_omits_authorized_live_application_pixels_and_metadata(self):
        remote,empty,app,surface=self.setup_remote_pixels()
        value=self.pixels(remote);self.assertEqual(value.image.rgb8,empty.image.rgb8)
        self.assertNotIn(surface['id'],{item.id for item in value.visible})
        self.world_process.management('world.create',entity=dict(id='remote-world-panel',kind='panel',label='shared geometry',
            room=self.world_info['entry_room'],pose=pose((0,0,-1)),size=[2,2],color=[71,131,193]))
        value=self.pixels(remote);self.assertEqual(self.rgb(value,63,47),(71,131,193))
        self.assertEqual({item.id for item in value.visible},{'remote-world-panel'})
        self.assertTrue(self.world_process.management('surface.get',id=surface['id'])['mapped'])

    def test_same_actor_restores_local_application_observation_after_remote_session(self):
        remote,empty,app,surface=self.setup_remote_pixels()
        self.assertEqual(self.pixels(remote).image.rgb8,empty.image.rgb8)
        self.assertTrue(remote.call(close=session.WorldSessionClose()).HasField('closed'))
        self.assertTrue(self.authenticate_second(self.viewer).HasField('opened'))
        restored=self.pixels();self.assertIn(surface['id'],{item.id for item in restored.visible})
        self.assertEqual(self.rgb(restored,58,42),(255,0,0))

    def test_stalled_large_reply_closes_remote_channel_and_preserves_local_actor(self):
        remote,empty,app,surface=self.setup_remote_pixels()
        self.pixels(remote)  # Prove a complete image succeeds before the stalled write.
        remote.stream.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,4096)
        remote.sequence+=1;remote.action_sequence+=1
        request=session.WorldSessionRequest(protocol_version=1,request_id=remote.sequence,
            action=core.ActorActionRequest(seq=remote.action_sequence,
                observe=core.ActorObserveRequest(width=512,height=512,include_pixels=True)))
        remote.stream.sendall(frame(request));time.sleep(1.1)
        data=b''
        while True:
            try:chunk=remote.stream.recv(65536)
            except (ssl.SSLEOFError,ConnectionResetError):break
            if not chunk:break
            data+=chunk
            self.assertLess(len(data),1048581)
        self.assertGreaterEqual(len(data),4)
        expected=struct.unpack('!I',data[:4])[0];self.assertLess(len(data)-4,expected)
        self.assertTrue(self.peer.call(inspect=session.WorldSessionInspect()).HasField('state'))
        self.assertTrue(self.world_process.management('surface.get',id=surface['id'])['mapped'])


class HybridWorldProcessLifecycleRegression(WorldProcessLifecycleMixin, unittest.TestCase):
    def test_hybrid_world_process_initializes_and_records_lifecycle(self):
        from unittest.mock import patch
        fixture = self.Fixture(self.root)
        with patch.dict(HybridWorldProcess.__init__.__globals__, {"WORLD": self.helper}):
            world = HybridWorldProcess(
                fixture, self.root / "identity.textproto", self.root / "state", count=1
            )
        self.assert_lifecycle_close(world)


if __name__=='__main__':
    tests=unittest.TestSuite(RemoteNativeTests(name) for name in RemoteNativeTests.__dict__ if name.startswith('test_'))
    tests.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(HybridWorldProcessLifecycleRegression))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(tests).wasSuccessful())
