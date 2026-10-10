"""Production participation worker against real TLS issuer and independent storage witnesses."""
import os
from pathlib import Path
import select
import sqlite3
import socket
import ssl
import threading
import struct
import subprocess
import sys
import time
import unittest
from google.protobuf import text_format
import core_pb2 as core
import grants_pb2 as grants
import issuer_pb2 as pb
from harness.wire import frame
from test_issuer_client import ClientFixture, FaultPeer

PROBE = Path(sys.argv.pop(1))

class RuntimeProcess:
    def __init__(self, fixture, config, domains, mode=None):
        self.fixture=fixture;self.log=fixture.root/('participant-'+str(len(fixture.connections))+'.log')
        self.stderr=self.log.open('wb')
        self.proc=subprocess.Popen([str(PROBE),*([mode] if mode else []),str(config),*domains],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.stderr)
        fixture.connections.append(self)
    def send(self, request_id=1, **operation):
        self.proc.stdin.write(frame(pb.ParticipantCommand(request_id=request_id,**operation)));self.proc.stdin.flush()
    def receive(self, timeout=4):
        deadline=time.monotonic()+timeout
        def exact(size):
            data=b''
            while len(data)<size:
                if not select.select([self.proc.stdout],[],[],max(0,deadline-time.monotonic()))[0]:raise AssertionError('Participant update deadline expired')
                part=os.read(self.proc.stdout.fileno(),size-len(data))
                if not part:raise AssertionError('Participant closed: '+self.log.read_text())
                data+=part
            return data
        size=struct.unpack('!I',exact(4))[0]
        if size>1048576:raise AssertionError('Excessive participant update')
        result=pb.ParticipantUpdate.FromString(exact(size));self.fixture.assertEqual(result.protocol_version,1);return result
    def close(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            try:self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(timeout=2);raise
        for stream in (self.proc.stdin,self.proc.stdout,self.stderr):
            if not stream.closed:stream.close()
        log=self.log.read_text()
        target=os.environ.get('SPATIAL_SECURITY_EVIDENCE')
        if target:
            Path(target).mkdir(parents=True,exist_ok=True)
            (Path(target)/(self.fixture._testMethodName+'-'+self.log.name)).write_bytes(self.log.read_bytes())
        self.fixture.assertNotIn('Sanitizer',log);self.fixture.assertNotIn('runtime error:',log)

class ActivationRaceRelay:
    """Forward real issuer traffic; commit one revocation before forwarding activation."""
    def __init__(self,fixture,revoke):
        self.fixture=fixture;self.revoke=revoke;self.done=False;self.stop=threading.Event();self.errors=[];self.stream=None
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_3
        context.load_cert_chain(str(fixture.server_cert),str(fixture.server_key));context.load_verify_locations(str(fixture.service_cert));context.verify_mode=ssl.CERT_REQUIRED
        self.context=context;self.listener=socket.socket();self.listener.bind(('127.0.0.1',0));self.listener.listen(1);self.listener.settimeout(.1);self.port=self.listener.getsockname()[1]
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start();fixture.addCleanup(self.close)
    def run(self):
        upstream=None
        try:
            while not self.stop.is_set():
                try:raw,_=self.listener.accept();break
                except socket.timeout:continue
            else:return
            raw.settimeout(2);self.stream=self.context.wrap_socket(raw,server_side=True);upstream=self.fixture.connect()
            def exact(size):
                data=b''
                while len(data)<size:
                    part=self.stream.recv(size-len(data))
                    if not part:raise EOFError()
                    data+=part
                return data
            while not self.stop.is_set():
                size=struct.unpack('!I',exact(4))[0]
                if size>1048576:raise AssertionError('Excessive relay frame')
                request=pb.IssuerRequest.FromString(exact(size));sequence=request.request_id
                if request.HasField('authority_activate') and not self.done:self.revoke();self.done=True
                reply=upstream.request(request);reply.request_id=sequence;self.stream.sendall(frame(reply))
        except (OSError,EOFError):pass
        except BaseException as error:self.errors.append(error)
        finally:
            if self.stream:self.stream.close()
            if upstream:upstream.close()
    def close(self):
        self.stop.set();self.listener.close()
        if self.stream:
            try:self.stream.shutdown(socket.SHUT_RDWR)
            except OSError:pass
        self.thread.join(3)
        if self.thread.is_alive():raise AssertionError('Activation relay did not stop')
        if self.errors:raise self.errors[0]

class ParticipantRuntimeTests(ClientFixture):
    def setUp(self):
        super().setUp();self.owner_peer=self.ready();self.signer,self.public=self.key('participant')
        self.definition=pb.AuthorityParticipant(id=b'p'*32,public_key=self.public,domains=[grants.Domain(kind=grants.WORLD,id='world-one')])
        op=pb.IssuerOperation(participant_register=pb.AuthorityParticipantRegister(participant=self.definition,expected_registry_revision=0))
        result=self.execute(self.owner_peer,self.challenge(self.owner_peer,op));self.assertTrue(result.HasField('participant'),result)
        self.registration=result.participant
    def start(self, domains=None, endpoint_port=None, mode=None, **changes):
        config=pb.AuthorityParticipantRuntimeConfig(protocol_version=1,issuer_client_config=str(self.config_file(endpoint_port)),
            private_key_path=str(self.signer),participant_id=self.definition.id,registered_revision=1)
        for name,value in changes.items():setattr(config,name,value)
        path=self.root/('participant-config-'+str(len(self.connections))+'.textproto')
        path.write_text(text_format.MessageToString(config));path.chmod(0o600)
        return RuntimeProcess(self,path,domains or ['world','world-one'],mode)
    def ready_runtime(self, **changes):
        runtime=self.start(**changes);auth=runtime.receive();self.assertTrue(auth.HasField('authenticated'),auth)
        self.assertEqual(auth.authenticated.registration,self.registration)
        baseline=runtime.receive();self.assertTrue(baseline.HasField('revocations'),baseline);self.assertTrue(baseline.revocations.initial)
        return runtime,auth.authenticated,baseline.revocations
    def activate(self,runtime,revision=0,request_id=1):
        runtime.send(request_id,activate=pb.AuthorityActivate(synchronized_revision=revision));update=runtime.receive()
        self.assertEqual(update.request_id,request_id);self.assertTrue(update.HasField('activated'),update);return update.activated
    def rows(self):
        with sqlite3.connect(self.state/'identity.db') as db:return db.execute('SELECT generation,service_incarnation,applied_revision,active FROM authority_activation ORDER BY generation').fetchall()
    def revoke(self,revision=0):
        peer=self.connect();self.actor_key,self.actor_public=self.key('actor-'+str(revision))
        record=self.decide(peer,self.enroll(peer)).enrollment;claims=self.claims(record,self.info)
        op=pb.IssuerOperation(identity_revoke=pb.IdentityRevoke(target=pb.IdentityReference(kind=pb.ACTOR_TARGET,id=claims.actor_id),expected_security_revision=revision,reason='worker regression'))
        result=self.execute(peer,self.challenge(peer,op));self.assertTrue(result.HasField('identity_status'),result);return claims
    def barrier(self,revision=1):
        peer=self.connect();op=pb.IssuerOperation(barrier_read=pb.AuthorityBarrierRead(revision=revision))
        result=self.execute(peer,self.challenge(peer,op));self.assertTrue(result.HasField('authority_barrier'),result);return result.authority_barrier
    def event(self,runtime,revision):
        update=runtime.receive();self.assertTrue(update.HasField('revocations'),update)
        self.assertEqual(update.revocations.through_revision,revision);return update.revocations
    def error(self,runtime,code):
        update=runtime.receive();self.assertTrue(update.HasField('error'),update);self.assertEqual(update.error.code,code,update)
        self.assertEqual(runtime.proc.wait(timeout=2),2);return update
    def test_activation_race_requires_explicit_resynchronization_and_new_request(self):
        relay=ActivationRaceRelay(self,self.revoke)
        runtime,auth,baseline=self.ready_runtime(endpoint_port=relay.port)
        runtime.send(10,activate=pb.AuthorityActivate(synchronized_revision=baseline.through_revision))
        update=runtime.receive();self.assertTrue(update.HasField('error'),update)
        self.assertEqual((update.request_id,update.error.code),(10,core.REVISION_CONFLICT));self.assertTrue(relay.done)
        self.event(runtime,1);self.assertEqual(self.rows(),[]);self.assertIsNone(runtime.proc.poll())
        # The losing activation was absent from the committed barrier; it must synchronize first.
        self.assertEqual(self.barrier().participant_count,0)
        state=self.activate(runtime,revision=1,request_id=11)
        self.assertEqual((state.generation,state.applied_revision,state.head_revision),(1,1,1))
        self.assertEqual(state.service_incarnation,auth.service_incarnation)
        self.revoke(1);self.event(runtime,2);self.assertFalse(self.barrier(2).complete)
        runtime.close()

    def test_command_queue_overflow_is_permanent_before_activation(self):
        peer=FaultPeer(self,stall_handshake=True)
        runtime=self.start(endpoint_port=peer.port,mode='--queue-overflow')
        self.error(runtime,core.RESOURCE_EXHAUSTED);self.assertEqual(self.rows(),[])

    def test_authentication_and_baseline_do_not_activate_execution(self):
        runtime,auth,baseline=self.ready_runtime();self.assertEqual(baseline.through_revision,0)
        self.assertEqual(len(auth.service_incarnation),32);self.assertEqual(len(auth.connection_binding),32)
        self.assertEqual(self.rows(),[]);runtime.close();self.assertEqual(self.rows(),[])
    def test_real_barrier_waits_for_explicit_application_acknowledgement(self):
        runtime,auth,baseline=self.ready_runtime();state=self.activate(runtime);self.assertEqual(state.service_incarnation,auth.service_incarnation)
        self.revoke();self.event(runtime,1);status=self.barrier()
        self.assertFalse(status.complete);self.assertEqual((status.participant_count,status.pending_count),(1,1));self.assertEqual(self.rows()[0][2:],(0,1))
        runtime.send(2,applied=pb.AuthorityApplied(generation=state.generation,from_revision=0,through_revision=1));update=runtime.receive()
        self.assertTrue(update.HasField('applied'),update);self.assertEqual((update.request_id,update.applied.applied_revision),(2,1))
        self.assertTrue(self.barrier().complete)
        runtime.send(3,release=pb.AuthorityRelease(generation=state.generation,applied_revision=1));update=runtime.receive()
        self.assertTrue(update.HasField('released'),update);self.assertFalse(update.released.active);self.assertEqual(runtime.proc.wait(timeout=2),0)
        self.assertEqual(self.rows()[0][2:],(1,0))
    def test_destructor_preserves_pending_obligation_and_blocks_replacement(self):
        runtime,auth,_=self.ready_runtime();self.activate(runtime);self.revoke();self.event(runtime,1);runtime.close()
        self.assertFalse(self.barrier().complete);self.assertEqual(self.rows()[0][2:],(0,1))
        other,other_auth,baseline=self.ready_runtime();self.assertNotEqual(auth.service_incarnation,other_auth.service_incarnation)
        other.send(5,activate=pb.AuthorityActivate(synchronized_revision=baseline.through_revision));self.error(other,core.BUSY)
        self.assertEqual(len(self.rows()),1);self.assertFalse(self.barrier().complete)
    def test_clean_release_allows_new_incarnation_and_generation(self):
        runtime,auth,_=self.ready_runtime();state=self.activate(runtime)
        runtime.send(2,release=pb.AuthorityRelease(generation=state.generation,applied_revision=0));self.assertTrue(runtime.receive().HasField('released'));self.assertEqual(runtime.proc.wait(timeout=2),0)
        other,next_auth,_=self.ready_runtime();next_state=self.activate(other)
        self.assertEqual(next_state.generation,2);self.assertNotEqual(auth.service_incarnation,next_auth.service_incarnation)
        self.assertEqual([r[3] for r in self.rows()],[0,1])
    def test_domain_mismatch_never_activates(self):
        for domains in [['world','another'],['workspace','world-one'],['world','world-one','workspace','extra']]:
            with self.subTest(domains=domains):self.error(self.start(domains=domains),core.INVALID_ARGUMENT);self.assertEqual(self.rows(),[])
    def test_wrong_service_key_never_authenticates(self):
        key,_=self.key('unregistered');self.error(self.start(private_key_path=str(key)),core.UNAUTHORIZED);self.assertEqual(self.rows(),[])
    def test_wrong_registration_revision_never_authenticates(self):
        self.error(self.start(registered_revision=2),core.UNAUTHORIZED);self.assertEqual(self.rows(),[])
    def test_future_revision_cannot_be_acknowledged(self):
        runtime,_,_=self.ready_runtime();state=self.activate(runtime)
        runtime.send(7,applied=pb.AuthorityApplied(generation=state.generation,from_revision=0,through_revision=1))
        result=self.error(runtime,core.INVALID_ARGUMENT);self.assertEqual(result.request_id,7);self.assertEqual(self.rows()[0][2:],(0,1))
    def test_application_acknowledgement_cannot_skip_committed_events(self):
        runtime,_,_=self.ready_runtime();state=self.activate(runtime)
        self.revoke();self.event(runtime,1);self.revoke(1);self.event(runtime,2)
        runtime.send(8,applied=pb.AuthorityApplied(generation=state.generation,from_revision=0,through_revision=2));self.error(runtime,core.INVALID_ARGUMENT)
        self.assertFalse(self.barrier(1).complete);self.assertFalse(self.barrier(2).complete)
    def test_release_cannot_discard_unapplied_revocation(self):
        runtime,_,_=self.ready_runtime();state=self.activate(runtime);self.revoke();self.event(runtime,1)
        runtime.send(9,release=pb.AuthorityRelease(generation=state.generation,applied_revision=0));self.error(runtime,core.BUSY)
        self.assertFalse(self.barrier().complete);self.assertEqual(self.rows()[0][2:],(0,1))
    def test_transport_loss_never_releases_or_reconnects(self):
        runtime,_,_=self.ready_runtime();self.activate(runtime);proc=self.daemons[-1][0];proc.stdin.close();self.assertEqual(proc.wait(timeout=2),0)
        self.error(runtime,core.UNAVAILABLE);self.assertEqual(self.rows()[0][2:],(0,1))
        self.launch();self.assertEqual(runtime.proc.poll(),2);self.assertEqual(len(self.rows()),1)
    def test_exposed_private_key_is_rejected_before_service_authentication(self):
        self.signer.chmod(0o644);self.error(self.start(),core.UNAVAILABLE);self.assertEqual(self.rows(),[])

    def test_stalled_transport_records_operation_and_cause_without_reconnect(self):
        peer = FaultPeer(self, stall_handshake=True)
        runtime = self.start(endpoint_port=peer.port)
        update = self.error(runtime, core.UNAVAILABLE)
        self.assertEqual(update.error.message, 'Authority participant channel failed; reconciliation is required')
        self.assertEqual(peer.connections, 1); self.assertEqual(self.rows(), [])
        lines = runtime.log.read_text().splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn('stage=issuer_connect', lines[0])
        self.assertIn('cause=Issuer client deadline exceeded', lines[0])

    def test_peer_failure_diagnostic_is_bounded_and_cannot_add_log_lines(self):
        message = 'peer rejection\n\r\t' + 'x' * 600
        def reject(peer, request, reply):
            self.assertTrue(request.HasField('authority_authentication_begin'))
            reply.error.CopyFrom(core.Error(code=core.FORBIDDEN, message=message))
            peer.stream.sendall(frame(reply))
        peer = FaultPeer(self, response=reject)
        runtime = self.start(endpoint_port=peer.port)
        update = self.error(runtime, core.FORBIDDEN)
        self.assertEqual(update.error.message, message)
        self.assertEqual(peer.connections, 1); self.assertEqual(self.rows(), [])
        lines = runtime.log.read_text().splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn('stage=authentication_begin', lines[0])
        self.assertIn('cause=peer rejection???', lines[0])
        self.assertLessEqual(len(lines[0]), 400)

if __name__=='__main__':unittest.main(verbosity=2)
