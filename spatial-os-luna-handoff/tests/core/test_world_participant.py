"""Real world participation and independently witnessed revocation completion."""
import io
import json
import os
from pathlib import Path
import sqlite3
import socket
import select
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
from test_world_identity import WorldIdentityFixture,WorldProcess,WORLD,receive
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


class CheckedParticipantParent:
    """Forward real issuer authority; hold or corrupt only one private progress receipt."""
    def __init__(self,fixture):
        self.fixture=fixture;self.parent,self.child=socket.socketpair();self.parent.settimeout(3)
        self.stop=threading.Event();self.armed=threading.Event();self.held=threading.Event()
        self.release=threading.Event();self.warmed=threading.Event();self.errors=[];self.mode=None
        self.sequence=0;self.progress_requests=0;self.active=None;self.closed=False
        incarnation=os.urandom(32);self.peer=fixture.connect()
        reply=self.peer.call(authority_authentication_begin=issuer.AuthorityAuthenticationBegin(
            participant_id=fixture.definition.id,registered_revision=1,service_incarnation=incarnation))
        fixture.assertTrue(reply.HasField('challenge'),reply)
        challenge=reply.challenge
        reply=self.peer.call(authority_authentication_complete=issuer.AuthorityAuthenticationComplete(
            nonce=challenge.nonce,proof=fixture.proof(challenge,fixture.signer,fixture.public)))
        fixture.assertTrue(reply.HasField('authority_authentication'),reply)
        self.auth=issuer.AuthorityAuthenticationResult();self.auth.CopyFrom(reply.authority_authentication)
        self.config=issuer.AuthorityParticipantRuntimeConfig(protocol_version=1,
            participant_id=fixture.definition.id,registered_revision=1,service_incarnation=incarnation,
            supervisor_channel_fd=self.child.fileno(),supervisor_issuer_id=fixture.info.issuer_id)
        fixture.addCleanup(self.close)
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def send(self,request_id=0,**outcome):
        self.parent.sendall(frame(issuer.ParticipantUpdate(protocol_version=1,request_id=request_id,**outcome)))
    def read_feed(self,initial=False):
        query=issuer.RevocationRead() if initial else issuer.RevocationRead(after_revision=self.cursor)
        reply=self.peer.call(revocations=query);self.fixture.assertTrue(reply.HasField('revocations'),reply)
        self.cursor=reply.revocations.through_revision
        self.send(revocations=reply.revocations)
        return reply.revocations
    def run(self):
        try:
            self.send(authenticated=self.auth);self.read_feed(initial=True)
            while not self.stop.is_set():
                if not select.select([self.parent],[],[],.05)[0]:
                    self.read_feed();continue
                command=receive(self.parent.recv,self.parent,issuer.ParticipantCommand)
                operation=command.WhichOneof('operation')
                if operation=='progress':
                    self.progress_requests+=1
                    batch=self.read_feed();self.sequence+=1
                    progress=issuer.AuthorityProgress(activation=self.active,feed_sequence=self.sequence,
                        through_revision=batch.through_revision,head_revision=batch.head_revision)
                    if self.armed.is_set():
                        self.held.set()
                        while not self.release.wait(.005):
                            if self.stop.is_set():return
                        if self.mode=='generation':progress.activation.generation+=1
                        elif self.mode=='cursor':progress.through_revision+=1;progress.head_revision+=1
                        elif self.mode=='replay':progress.feed_sequence-=1
                    self.send(command.request_id,progress=progress);self.warmed.set()
                else:
                    names={'activate':('authority_activate','activated'),
                        'applied':('authority_applied','applied'),'release':('authority_release','released')}
                    request,outcome=names[operation]
                    reply=self.peer.call(**{request:getattr(command,operation)})
                    if reply.HasField('error'):self.send(command.request_id,error=reply.error)
                    else:
                        self.fixture.assertTrue(reply.HasField('authority_activation'),reply)
                        self.active=issuer.AuthorityActivationState();self.active.CopyFrom(reply.authority_activation)
                        self.send(command.request_id,**{outcome:self.active})
                        if operation=='release':return
        except EOFError:pass
        except OSError as error:
            if not self.stop.is_set():self.errors.append(error)
        except BaseException as error:self.errors.append(error)
    def close(self):
        if self.closed:return
        self.closed=True;self.stop.set();self.release.set()
        self.thread.join(3);self.fixture.assertFalse(self.thread.is_alive(),'Private parent did not stop')
        self.parent.close();self.child.close()
        # Reconcile only after every child using this exact incarnation has stopped.
        if self.active is not None and self.active.active:
            self.fixture.assertTrue(all(world.proc.poll() is not None for world in self.fixture.worlds))
            reply=self.peer.call(authority_release=issuer.AuthorityRelease(
                generation=self.active.generation,applied_revision=self.active.applied_revision))
            self.fixture.assertTrue(reply.HasField('authority_activation'),reply)
        self.peer.close()
        if self.errors:raise self.errors[0]
    def disconnect(self):
        """Drop the checked participant channel without acknowledging release."""
        self.stop.set();self.release.set()
        try:self.parent.shutdown(socket.SHUT_RDWR)
        except OSError:pass
        self.thread.join(3)
        self.fixture.assertFalse(self.thread.is_alive(),'Private parent did not stop after channel loss')
        self.parent.close()


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
    def start_world(self,relay=None,participate=True,state=None,native=False,private_parent=None,fixture_faults=False,
                    native_fd=None,broker=None,actor_grants=None):
        # Actor queries and participant traffic use separate, independently authenticated TLS connections.
        client=self.config_file();query_path=self.root/'query-client.textproto';query_path.write_bytes(client.read_bytes());query_path.chmod(0o600)
        service=self.config_file(endpoint_port=relay.port if relay else None,io_timeout_ms=3000)
        service_path=self.root/'service-client.textproto';service_path.write_bytes(service.read_bytes());service_path.chmod(0o600)
        participant=issuer.AuthorityParticipantRuntimeConfig(protocol_version=1,issuer_client_config=str(service_path),private_key_path=str(self.signer),participant_id=self.definition.id,registered_revision=1)
        if private_parent is not None:participant=private_parent.config
        participant_path=self.root/'participant.textproto';participant_path.write_text(text_format.MessageToString(participant));participant_path.chmod(0o600)
        selected_grants=actor_grants if actor_grants is not None else ['navigate','observe','interact']
        config=session.WorldIdentityConfig(protocol_version=1,issuer_client_config=str(query_path),admission=session.REGISTERED_ACTORS,
            actor_grants=[session.WorldActorGrants(actor_id=self.identity.actor_id,grants=selected_grants)])
        if self.retained_movement_enabled():config.enable_retained_movement=True
        if participate:config.participant_runtime_config=str(participant_path)
        path=self.root/('world-config-'+str(len(self.worlds))+'.textproto');path.write_text(text_format.MessageToString(config));path.chmod(0o600)
        world=WorldProcess(self,path,state or self.world_state,native=native,runtime_config=self.runtime_path,
            fixture_faults=fixture_faults,native_fd=native_fd,broker=broker,
            participant_fd=private_parent.child.fileno() if private_parent else None);self.worlds.append(world);self.addCleanup(world.close)
        if private_parent:private_parent.child.close()
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
    def test_retained_motion_stops_when_checked_participant_is_lost(self):
        self.retained_movement_override=True
        # The ten-second fixture expiry cannot explain cancellation in this test.
        settings=text_format.Parse(self.runtime_path.read_text(),core.RuntimeConfig())
        self.assertEqual(settings.input_timeout,10)
        parent=CheckedParticipantParent(self)
        world=self.start_world(private_parent=parent);world.management('system.hello')
        peer=world.peers[0];opened=self.authenticate(peer)
        self.assertTrue(opened.HasField('opened'),opened);actor=opened.opened.actor_id
        self.assertTrue(parent.warmed.wait(2))
        self.assertEqual(parent.progress_requests,1,'Retained mode needs one initial checked progress receipt')
        time.sleep(.05)
        initial=world.management('world.get',id=actor)['pose']
        peer.motion_refresh(core.ActorInputRequest(axes=[1,0,0,0,0,0]))
        moving=self.until(lambda:world.management('world.get',id=actor)['pose'],
            lambda pose:pose['p'][0]>initial['p'][0])
        self.assertEqual(parent.progress_requests,1,'A movement refresh requested another progress round trip')
        failed_at=time.monotonic();parent.disconnect()
        deadline=time.monotonic()+2
        while 'participant_failed' not in Path(world.log.name).read_text():
            if time.monotonic()>deadline:self.fail('World did not detect checked participant loss')
            time.sleep(.01)
        try:peer.motion_refresh(core.ActorInputRequest(axes=[-1,0,0,0,0,0]))
        except OSError:pass
        self.assertTrue(select.select([peer.stream],[],[],2)[0],'Participant loss left the actor channel live')
        self.assertEqual(peer.stream.recv(1),b'','Participant loss did not close the retained actor channel')
        stopped=world.management('world.get',id=actor)['pose']
        time.sleep(.15)
        self.assertEqual(world.management('world.get',id=actor)['pose'],stopped,
            'Participant loss left previously authorized motion running')
        self.assertLess(time.monotonic()-failed_at,settings.input_timeout,
            'Input expiry could explain the observed cancellation')
        self.assertGreater(moving['p'][0],initial['p'][0])
        world.close();self.assertEqual(world.proc.returncode,2)
        self.assertEqual(self.activations()[-1][2],1,'Failure discarded its unresolved issuer obligation')
        parent.close();self.assertEqual(self.activations()[-1][2],0)

    def test_pending_retained_check_preserves_frame_without_motion(self):
        self.retained_movement_override=True
        parent=CheckedParticipantParent(self);parent.armed.set()
        world=self.start_world(private_parent=parent)
        world.management('system.hello');peer=world.peers[0]
        opened=self.authenticate(peer);self.assertTrue(opened.HasField('opened'),opened)
        actor=opened.opened.actor_id;self.assertTrue(parent.held.wait(2))
        before=world.management('world.get',id=actor)['pose']
        peer.sequence+=1;peer.action_sequence+=1
        request=session.WorldSessionRequest(protocol_version=1,request_id=peer.sequence,
            action=core.ActorActionRequest(seq=peer.action_sequence,input=core.ActorInputRequest(axes=[1,0,0,0,0,0])))
        peer.stream.sendall(frame(request));self.assertTrue(parent.held.wait(2))
        self.assertFalse(select.select([peer.stream],[],[],.15)[0],'Unchecked movement produced a receipt')
        self.assertEqual(world.management('world.get',id=actor)['pose'],before)
        parent.release.set()
        reply=receive(peer.stream.recv,peer.stream,session.WorldSessionReply)
        self.assertEqual(reply.request_id,peer.sequence)
        self.assertTrue(reply.HasField('action') and reply.action.accepted,reply)
        self.assertGreater(world.management('world.get',id=actor)['pose']['p'][0],before['p'][0])
        self.assertTrue(peer.act(stop=core.ActorStopRequest()).action.accepted)
        world.close();self.assertEqual(world.proc.returncode,0);parent.close()

    def test_invalid_initial_retained_proofs_never_apply_or_fall_back(self):
        self.retained_movement_override=True
        settings=text_format.Parse(self.runtime_path.read_text(),core.RuntimeConfig())
        settings.input_timeout=10;self.runtime_path.write_text(text_format.MessageToString(settings))
        for mode in ('generation','cursor','replay'):
            with self.subTest(mode=mode):
                parent=CheckedParticipantParent(self);parent.mode=mode;parent.armed.set()
                world=self.start_world(private_parent=parent);world.management('system.hello')
                peer=world.peers[0];opened=self.authenticate(peer)
                self.assertTrue(opened.HasField('opened'),opened);actor=opened.opened.actor_id
                self.assertTrue(parent.held.wait(2),'Initial retained proof was not requested')
                self.assertEqual(parent.progress_requests,1)
                before=world.management('world.get',id=actor)['pose']
                peer.motion_refresh(core.ActorInputRequest(axes=[1,0,0,0,0,0]))
                self.assertTrue(select.select([peer.stream],[],[],1)[0],
                    'Motion without the initial checked proof was not rejected')
                self.assertEqual(peer.stream.recv(1),b'','Missing initial proof used ordinary action dispatch')
                self.assertEqual(world.management('world.get',id=actor)['pose'],before,
                    'Motion applied before the initial checked proof')
                parent.release.set()
                self.assertTrue(parent.warmed.wait(2))
                deadline=time.monotonic()+2
                while 'Invalid private authority progress identity or cursor' not in Path(world.log.name).read_text():
                    if time.monotonic()>deadline:self.fail('Invalid initial retained proof did not disable world identity')
                    time.sleep(.01)
                self.assertEqual(world.management('world.get',id=actor)['pose'],before)
                world.close();self.assertEqual(world.proc.returncode,2)
                self.assertEqual(self.activations()[-1][2],1,'Failure must preserve the unresolved issuer obligation')
                parent.close();self.assertEqual(self.activations()[-1][2],0)

    def test_committed_revoke_blocks_fresh_movement_while_feed_is_held(self):
        # This asserts legacy checked-dispatch behavior. Retained M10 refresh has a separate test.
        self.retained_movement_override=False
        settings=text_format.Parse(self.runtime_path.read_text(),core.RuntimeConfig())
        settings.input_timeout=.25;self.runtime_path.write_text(text_format.MessageToString(settings))
        relay=HeldRevocationRelay(self);world=self.start_world(relay);world.management('system.hello')
        peer=world.peers[0];self.assertTrue(self.authenticate(peer).HasField('opened'))
        self.assertTrue(peer.act(input=core.ActorInputRequest(axes=[1,0,0,0,0,0])).HasField('action'))
        # Give the retained connection time to publish its initial checked progress.
        time.sleep(.3)
        self.revoke_now();self.assertTrue(relay.held.wait(.3));self.assertFalse(relay.delivered.is_set())
        self.assertEqual(self.barrier().pending_count,1)
        peer.sequence+=1;peer.action_sequence+=1
        request=session.WorldSessionRequest(protocol_version=1,request_id=peer.sequence,
            action=core.ActorActionRequest(seq=peer.action_sequence,input=core.ActorInputRequest(axes=[1,0,0,0,0,0])))
        started=time.monotonic();peer.stream.sendall(frame(request));early=None
        try:
            if select.select([peer.stream],[],[],.15)[0]:
                early=receive(peer.stream.recv,peer.stream,session.WorldSessionReply)
            accepted=early is not None and early.HasField('action') and early.action.accepted
            evidence=os.environ.get('SPATIAL_SECURITY_EVIDENCE')
            if evidence:
                target=Path(evidence);target.mkdir(parents=True,exist_ok=True)
                (target/(self._testMethodName+'-ordering.json')).write_text(json.dumps({
                    'new_motion_after_committed_revoke':True,'feed_still_held':not relay.delivered.is_set(),
                    'early_reply':None if early is None else early.WhichOneof('outcome'),
                    'early_accepted':accepted,'elapsed_seconds':time.monotonic()-started},indent=2)+'\n')
            self.assertFalse(accepted,'Fresh movement accepted after committed revocation while feed was held')
        finally:relay.release.set()
        reply=early if early is not None else receive(peer.stream.recv,peer.stream,session.WorldSessionReply)
        self.denied(reply,core.UNAUTHORIZED)
        self.until(self.barrier,lambda value:value.complete)
        world.close();self.assertEqual(world.proc.returncode,0)

    def test_retained_refresh_uses_last_checked_feed_until_revocation_delivery(self):
        self.retained_movement_override=True
        settings=text_format.Parse(self.runtime_path.read_text(),core.RuntimeConfig())
        settings.input_timeout=10;self.runtime_path.write_text(text_format.MessageToString(settings))
        relay=HeldRevocationRelay(self);world=self.start_world(relay);world.management('system.hello')
        peer=world.peers[0];opened=self.authenticate(peer)
        self.assertTrue(opened.HasField('opened'),opened);actor=opened.opened.actor_id
        # Wait for the initial checked feed before testing retained refreshes.
        time.sleep(.3)
        start=world.management('world.get',id=actor)['pose']['p']
        peer.motion_refresh(core.ActorInputRequest(axes=[1,0,0,0,0,0]))
        moving=self.until(lambda:world.management('world.get',id=actor)['pose']['p'],
            lambda position:position[0]>start[0])
        self.assertGreater(moving[0],start[0])
        self.revoke_now();self.assertTrue(relay.held.wait(.3));self.assertFalse(relay.delivered.is_set())
        self.assertEqual(self.barrier().pending_count,1)
        before=world.management('world.get',id=actor)['pose']['p']
        refresh_times=[]
        for _ in range(8):
            refresh_times.append(time.monotonic())
            peer.motion_refresh(core.ActorInputRequest(axes=[0,1,0,0,0,0]))
            time.sleep(.04)
        self.assertLess(max(b-a for a,b in zip(refresh_times,refresh_times[1:])),.25,
            'Refresh source exceeded the 250 ms intent expiry')
        self.assertFalse(relay.delivered.is_set(),'Revocation event was delivered during the held-feed interval')
        after=world.management('world.get',id=actor)['pose']['p']
        self.assertGreater(after[1],before[1],'Retained refresh waited for the held authority response')
        relay.release.set();self.assertTrue(relay.delivered.wait(.3))
        self.until(self.barrier,lambda value:value.complete)
        stopped=world.management('world.get',id=actor)['pose'];time.sleep(.15)
        self.assertEqual(world.management('world.get',id=actor)['pose'],stopped,
            'Consumed revocation left movement active')
        try:peer.motion_refresh(core.ActorInputRequest(axes=[0,-1,0,0,0,0]))
        except OSError:pass
        time.sleep(.1)
        self.assertEqual(world.management('world.get',id=actor)['pose'],stopped,
            'A post-revocation refresh restarted movement')
        world.close();self.assertEqual(world.proc.returncode,0)

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
