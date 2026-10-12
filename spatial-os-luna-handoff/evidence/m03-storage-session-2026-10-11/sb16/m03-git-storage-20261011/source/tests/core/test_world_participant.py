"""Real world participation and independently witnessed revocation completion."""
import io
import os
from pathlib import Path
import sqlite3
import socket
import struct
import subprocess
import threading
import time
import unittest
from google.protobuf import text_format
import core_pb2 as core
import grants_pb2 as grants
import issuer_pb2 as issuer
import world_session_pb2 as session
from harness.wire import frame,make_request,read_message,result_value
from test_world_identity import WorldIdentityFixture,WorldProcess,WORLD
from test_world_authority import RelayingIssuer

class HeldRevocationRelay(RelayingIssuer):
    def __init__(self,fixture):
        self.held=threading.Event();self.release=threading.Event()
        super().__init__(fixture,lambda:None)
    def relay(self,raw):
        upstream=None;stream=raw
        try:
            raw.settimeout(2);stream=self.context.wrap_socket(raw,server_side=True);self.streams.append(stream)
            upstream=self.fixture.connect()
            def exact(size):
                data=b''
                while len(data)<size:
                    part=stream.recv(size-len(data))
                    if not part:raise EOFError()
                    data+=part
                return data
            while not self.stop.is_set():
                size=struct.unpack('!I',exact(4))[0]
                if size>1048576:raise AssertionError('Excessive relay frame')
                request=issuer.IssuerRequest.FromString(exact(size));sequence=request.request_id
                reply=upstream.request(request);reply.request_id=sequence
                if reply.HasField('revocations') and reply.revocations.events:
                    self.held.set()
                    deadline=time.monotonic()+1.0
                    while not self.release.wait(.005):
                        if self.stop.is_set() or time.monotonic()>deadline:break
                stream.sendall(frame(reply))
                if reply.HasField('revocations') and reply.revocations.events:self.delivered.set()
        except (OSError,EOFError):pass
        except BaseException as error:self.errors.append(error)
        finally:
            stream.close()
            if upstream:upstream.close()
    def close(self):
        self.release.set();super().close()


class WorldParticipantFixture(WorldIdentityFixture):
    def setUp(self):
        super().setUp()
        self.world_state=self.root/'participating-world'
        settings=core.RuntimeConfig();text_format.Parse((Path(__file__).resolve().parents[2]/'config/conformance.textproto').read_text(),settings)
        settings.input_timeout=10
        self.runtime_path=self.root/'runtime.textproto';self.runtime_path.write_text(text_format.MessageToString(settings));self.runtime_path.chmod(0o600)
        result=subprocess.run([str(WORLD),'--state-dir',str(self.world_state),'--config',str(self.runtime_path)],input=frame(make_request('world.info',{})),capture_output=True,timeout=6)
        self.assertEqual(result.returncode,0,result.stderr)
        reply=read_message(io.BytesIO(result.stdout),core.Reply);self.assertFalse(reply.HasField('error'),reply)
        self.world_id=result_value(reply)['world_id']
        self.signer,self.public=self.key('world-service')
        self.definition=issuer.AuthorityParticipant(id=b'w'*32,public_key=self.public,domains=[grants.Domain(kind=grants.WORLD,id=self.world_id)])
        peer=self.connect();op=issuer.IssuerOperation(participant_register=issuer.AuthorityParticipantRegister(participant=self.definition,expected_registry_revision=0))
        result=self.execute(peer,self.challenge(peer,op));self.assertTrue(result.HasField('participant'),result);peer.close()
    def start_world(self,relay=None,participate=True,state=None,native=False):
        # Actor queries and participant traffic use separate, independently authenticated TLS connections.
        client=self.config_file();query_path=self.root/'query-client.textproto';query_path.write_bytes(client.read_bytes());query_path.chmod(0o600)
        service=self.config_file(endpoint_port=relay.port if relay else None,io_timeout_ms=3000)
        service_path=self.root/'service-client.textproto';service_path.write_bytes(service.read_bytes());service_path.chmod(0o600)
        participant=issuer.AuthorityParticipantRuntimeConfig(protocol_version=1,issuer_client_config=str(service_path),private_key_path=str(self.signer),participant_id=self.definition.id,registered_revision=1)
        participant_path=self.root/'participant.textproto';participant_path.write_text(text_format.MessageToString(participant));participant_path.chmod(0o600)
        config=session.WorldIdentityConfig(protocol_version=1,issuer_client_config=str(query_path),admission=session.REGISTERED_ACTORS,
            actor_grants=[session.WorldActorGrants(actor_id=self.identity.actor_id,grants=['navigate','observe','interact'])])
        if participate:config.participant_runtime_config=str(participant_path)
        path=self.root/('world-config-'+str(len(self.worlds))+'.textproto');path.write_text(text_format.MessageToString(config));path.chmod(0o600)
        world=WorldProcess(self,path,state or self.world_state,native=native,runtime_config=self.runtime_path);self.worlds.append(world);self.addCleanup(world.close)
        return world
    def activations(self):
        with sqlite3.connect(self.state/'identity.db') as db:return db.execute('SELECT generation,applied_revision,active FROM authority_activation ORDER BY generation').fetchall()
    def barrier(self,revision=1):
        peer=self.connect()
        try:
            op=issuer.IssuerOperation(barrier_read=issuer.AuthorityBarrierRead(revision=revision))
            result=self.execute(peer,self.challenge(peer,op));self.assertTrue(result.HasField('authority_barrier'),result);return result.authority_barrier
        finally:peer.close()
    def revoke_now(self):
        self.owner_peer=self.connect();self.revoke();self.owner_peer.close()


class WorldParticipantTests(WorldParticipantFixture):
    def test_actor_serving_world_requires_participant_configuration(self):
        world=self.start_world(participate=False);self.assertEqual(world.proc.wait(timeout=4),2)
        self.assertIn('participant configuration is required',Path(world.log.name).read_text());self.assertEqual(self.activations(),[])
    def test_world_activates_before_admitting_actor_and_cleanly_releases(self):
        world=self.start_world();world.management('system.hello');self.assertEqual(self.activations(),[(1,0,1)])
        opened=self.authenticate(world.peers[0]);self.assertTrue(opened.HasField('opened'),opened)
        world.close();self.assertEqual(world.proc.returncode,0);self.assertEqual(self.activations(),[(1,0,0)])
    def test_barrier_stays_pending_until_held_event_stops_real_actor_motion(self):
        relay=HeldRevocationRelay(self);world=self.start_world(relay);world.management('system.hello');peer=world.peers[0]
        opened=self.authenticate(peer);self.assertTrue(opened.HasField('opened'),opened);actor=opened.opened.actor_id
        self.assertTrue(peer.act(input=core.ActorInputRequest(axes=[1,0,0,0,0,0])).HasField('action'))
        self.revoke_now();self.assertTrue(relay.held.wait(.3));self.assertFalse(relay.delivered.is_set())
        pending=self.barrier();self.assertEqual((pending.participant_count,pending.pending_count,pending.complete),(1,1,False))
        start=world.management('world.get',id=actor)['pose']['p'][0];time.sleep(.1);finish=world.management('world.get',id=actor)['pose']['p'][0]
        self.assertFalse(relay.delivered.is_set());self.assertGreater(finish-start,.02)
        relay.release.set();self.assertTrue(relay.delivered.wait(.3))
        completed=self.until(self.barrier,lambda value:value.complete);self.assertEqual(completed.participant_count,1)
        stopped=world.management('world.get',id=actor)['pose']['p'][0];time.sleep(.08);still=world.management('world.get',id=actor)['pose']['p'][0]
        self.assertAlmostEqual(stopped,still,places=8);self.assertEqual(self.activations(),[(1,1,1)])
        world.close();self.assertEqual(world.proc.returncode,0);self.assertEqual(self.activations(),[(1,1,0)])
    def test_clean_restart_preserves_world_and_uses_new_generation(self):
        first=self.start_world();first.management('system.hello');first.close();self.assertEqual(first.proc.returncode,0)
        second=self.start_world();self.assertEqual(second.management('world.info')['world_id'],self.world_id)
        self.assertEqual(self.activations(),[(1,0,0),(2,0,1)]);second.close();self.assertEqual(second.proc.returncode,0)
    def test_service_cannot_activate_a_different_world(self):
        other=self.start_world(state=self.root/'another-world');self.assertEqual(other.proc.wait(timeout=4),2)
        self.assertIn('participation is unavailable',Path(other.log.name).read_text());self.assertEqual(self.activations(),[])
    def test_crash_retains_obligation_and_rejects_unfenced_replacement(self):
        world=self.start_world();world.management('system.hello');self.assertTrue(self.authenticate(world.peers[0]).HasField('opened'))
        world.proc.kill();world.proc.wait(timeout=2);world.close();self.revoke_now()
        self.assertEqual((self.barrier().participant_count,self.barrier().pending_count),(1,1))
        replacement=self.start_world();self.assertEqual(replacement.proc.wait(timeout=4),2)
        self.assertEqual(self.activations(),[(1,0,1)]);self.assertFalse(self.barrier().complete)

if __name__=='__main__':unittest.main(verbosity=2)
