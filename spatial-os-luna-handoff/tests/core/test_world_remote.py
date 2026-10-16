"""Draft remote actor acceptance: real TLS, issuer proof, persistent grants, and world actions."""
import json
import os
import socket
import ssl
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from google.protobuf import text_format
import core_pb2 as core
import issuer_pb2 as issuer
import world_session_pb2 as session
from harness.wire import frame,make_request
from harness.oracles import pose
from test_world_identity import WorldProcess,SessionPeer,WORLD
from test_world_bootstrap import BootstrapWorldFixture


class TlsActorPeer(SessionPeer):
    def request(self,request):
        self.sequence+=1;request.request_id=self.sequence
        self.stream.sendall(frame(request))
        def exact(size):
            data=b''
            while len(data)<size:
                part=self.stream.recv(size-len(data))
                if not part:raise EOFError('Remote actor channel closed')
                data+=part
            return data
        # SSL recv handles already buffered plaintext. Polling only the socket would miss it.
        size=struct.unpack('!I',exact(4))[0]
        if size>1048576:raise AssertionError('Excessive remote actor reply')
        reply=session.WorldSessionReply.FromString(exact(size))
        if reply.protocol_version!=1 or reply.request_id!=self.sequence:
            raise AssertionError('Wrong remote actor reply identity')
        return reply


class RemoteWorldProcess(WorldProcess):
    def __init__(self,fixture,local):
        self.sequence=0;self.peers=[];self.broker_channel=None
        self.log_path=fixture.root/f'remote-world-{len(fixture.worlds)}.log'
        self.log=self.log_path.open('wb')
        self.server_key,self.server_cert=fixture.certificate('world-server','world.test')
        config=session.WorldActorTlsConfig(protocol_version=1,certificate_path=str(self.server_cert),
            private_key_path=str(self.server_key),client_ca_path=str(fixture.service_cert),
            io_timeout_ms=700,idle_timeout_ms=3000,connection_limit=8)
        config_path=fixture.root/'world-tls.textproto'
        config_path.write_text(text_format.MessageToString(config));config_path.chmod(0o600)
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(16)
        self.port=listener.getsockname()[1]
        command=[str(WORLD),'--state-dir',str(local.state_path),'--identity-config',str(local.config_path),
            '--config',str(local.runtime_path),'--actor-listen-fd',str(listener.fileno()),
            '--actor-tls-config',str(config_path)]
        try:
            self.proc=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,
                pass_fds=(listener.fileno(),))
            self._initialize_process_lifecycle()
        finally:listener.close()
        self.state_path=local.state_path

    def connect(self,fixture):
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version=context.maximum_version=ssl.TLSVersion.TLSv1_3
        context.load_verify_locations(cafile=str(self.server_cert))
        context.load_cert_chain(str(fixture.service_cert),str(fixture.service_key))
        raw=socket.create_connection(('127.0.0.1',self.port),timeout=2)
        try:stream=context.wrap_socket(raw,server_hostname='world.test')
        except BaseException:raw.close();raise
        peer=TlsActorPeer(stream);self.peers.append(peer)
        return peer


class RemoteWorldTests(BootstrapWorldFixture):
    def remote_world(self):
        local=self.scoped_world(authenticate=False);local.close()
        world=RemoteWorldProcess(self,local);self.worlds.append(world)
        try:world.management('system.hello')
        except (EOFError,BrokenPipeError) as error:
            self.fail('Remote listener failed before an actor could connect: '+world.log_path.read_text())
        return world

    def test_remote_proof_uses_persistent_world_authority_and_real_actor(self):
        world=self.remote_world();peer=world.connect(self)
        self.assertEqual(peer.stream.version(),'TLSv1.3')
        opened=self.authenticate(peer);self.assertTrue(opened.HasField('opened'),opened)
        self.assertEqual(opened.opened.identity.actor_id,self.identity.actor_id)
        self.assertEqual(opened.opened.world_id,self.world_info['world_id'])
        state=peer.call(inspect=session.WorldSessionInspect()).state
        self.assertEqual(state.id,opened.opened.actor_id)
        self.assertEqual(world.management('world.get',id=state.id)['id'],state.id)
        self.assertFalse(state.workspace)
        self.assertTrue(self.navigation(peer).HasField('action'))
        self.assertTrue(peer.act(stop=core.ActorStopRequest()).HasField('action'))

    def test_tls_identity_alone_does_not_authenticate_an_actor(self):
        world=self.remote_world();peer=world.connect(self)
        reply=peer.call(inspect=session.WorldSessionInspect())
        self.assertTrue(reply.HasField('error'),reply);self.assertEqual(reply.error.code,core.UNAUTHORIZED)
        self.assertTrue(self.authenticate(peer).HasField('opened'))

    def test_remote_private_admission_remains_separate_from_valid_identity(self):
        world=self.remote_world();actor=self.enroll_bootstrap(name='remote-guest',profile='empty-v1')
        peer=world.connect(self);reply=self.authenticate_bootstrap(peer,actor)
        self.assertTrue(reply.HasField('error'),reply);self.assertEqual(reply.error.code,core.FORBIDDEN)
        self.assertEqual(len(world.management('world.list')['entities']),1)

    def test_remote_channel_cannot_execute_trusted_management_request(self):
        world=self.remote_world();peer=world.connect(self)
        self.assertTrue(self.authenticate(peer).HasField('opened'))
        before=world.management('world.list')
        request=make_request('world.create',{'entity':{'id':'network-management-injection','kind':'object',
            'room':self.world_info['entry_room'],'pose':pose((0,0,-2))}})
        reply=peer.request(request)
        self.assertTrue(reply.HasField('error'),reply)
        self.assertIn(reply.error.code,(core.INVALID_ARGUMENT,core.UNKNOWN_METHOD))
        self.assertEqual(world.management('world.list'),before)

    def test_remote_authentication_proof_is_bound_to_its_channel(self):
        world=self.remote_world();first=world.connect(self);second=world.connect(self)
        start=first.call(begin=session.WorldAuthenticationBegin(credential=self.record.credential))
        self.assertTrue(start.HasField('challenge'),start)
        proof=self.proof(start.challenge,self.actor_key,self.actor_public)
        complete=issuer.AuthenticationComplete(nonce=start.challenge.nonce,proof=proof)
        denied=second.call(complete=complete)
        self.assertTrue(denied.HasField('error'),denied);self.assertEqual(denied.error.code,core.UNAUTHORIZED)
        self.assertTrue(first.call(complete=complete).HasField('opened'))


class WorldProcessLifecycleRegression(unittest.TestCase):
    class Fixture:
        def __init__(self, root):
            self.root = root
            self.worlds = []
            self.service_cert = root / "service.crt"

        def certificate(self, name, common_name):
            return self.root / (name + ".key"), self.root / (name + ".crt")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="world-process-lifecycle-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.helper = self.root / "wait-for-stdin"
        self.helper.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "for _ in sys.stdin.buffer:\n"
            "    pass\n"
        )
        self.helper.chmod(0o700)

    def emergency_reap(self, world):
        if world.proc.poll() is None:
            world.proc.kill()
            world.proc.wait(timeout=3)
        for stream in (world.proc.stdin, world.proc.stdout, world.log):
            if stream is not None and not stream.closed:
                stream.close()

    def assert_lifecycle_close(self, world):
        self.addCleanup(self.emergency_reap, world)
        self.assertIsNone(world.proc.poll())
        evidence_path = self.root / ("lifecycle-" + str(len(list(self.root.glob("lifecycle-*.json")))) + ".json")
        evidence_path.write_text("{}\n")
        world.lifecycle_evidence = evidence_path
        pid = world.proc.pid

        world.close()

        start_ticks = world.start_ticks
        self.assertIsInstance(start_ticks, int)
        self.assertGreater(start_ticks, 0)
        self.assertEqual(world.proc.poll(), 0)
        self.assertEqual(world.proc.returncode, 0)
        self.assertTrue(world.proc.stdin.closed)
        self.assertTrue(world.proc.stdout.closed)
        self.assertTrue(world.log.closed)
        lifecycle = json.loads(evidence_path.read_text())["worldd_lifecycle"]
        self.assertEqual(lifecycle["before_world_close"]["pid"], pid)
        self.assertEqual(lifecycle["before_world_close"]["start_ticks"], start_ticks)
        self.assertIsNone(lifecycle["before_world_close"]["poll"])
        self.assertEqual(lifecycle["after_world_close"]["pid"], pid)
        self.assertEqual(lifecycle["after_world_close"]["start_ticks"], start_ticks)
        self.assertEqual(lifecycle["after_world_close"]["poll"], 0)
        self.assertEqual(lifecycle["after_world_close"]["returncode"], 0)
        self.assertEqual(len(lifecycle["stderr_sha256"]), 64)

    def test_base_world_process_initializes_and_records_lifecycle(self):
        from unittest.mock import patch
        fixture = self.Fixture(self.root)
        with patch.dict(WorldProcess.__init__.__globals__, {"WORLD": self.helper}):
            world = WorldProcess(fixture, self.root / "identity.textproto", self.root / "state", count=1)
        self.assert_lifecycle_close(world)

    def test_remote_world_process_initializes_and_records_lifecycle(self):
        from unittest.mock import patch
        fixture = self.Fixture(self.root)
        local = SimpleNamespace(
            state_path=self.root / "state",
            config_path=self.root / "identity.textproto",
            runtime_path=self.root / "runtime.textproto",
        )
        with patch.dict(RemoteWorldProcess.__init__.__globals__, {"WORLD": self.helper}):
            world = RemoteWorldProcess(fixture, local)
        self.assert_lifecycle_close(world)

    def test_persistent_tls_world_initializes_and_records_lifecycle(self):
        from unittest.mock import patch
        from test_world_shared_state import PersistentTlsWorld
        fixture = self.Fixture(self.root)
        local = SimpleNamespace(
            state_path=self.root / "state",
            config_path=self.root / "identity.textproto",
            runtime_path=self.root / "runtime.textproto",
        )
        with patch.dict(PersistentTlsWorld.__init__.__globals__, {"WORLD": self.helper}):
            world = PersistentTlsWorld(fixture, local)
        self.assert_lifecycle_close(world)

    def test_hybrid_world_process_initializes_and_records_lifecycle(self):
        from unittest.mock import patch
        from test_world_remote_native import HybridWorldProcess
        fixture = self.Fixture(self.root)
        with patch.dict(HybridWorldProcess.__init__.__globals__, {"WORLD": self.helper}):
            world = HybridWorldProcess(
                fixture, self.root / "identity.textproto", self.root / "state", count=1
            )
        self.assert_lifecycle_close(world)

if __name__=='__main__':unittest.main(verbosity=2)
