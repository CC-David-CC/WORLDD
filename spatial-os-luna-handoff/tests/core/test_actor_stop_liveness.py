"""Authenticated cancellation needs no fresh permission acquisition."""
import test_world_identity as existing
import json
import os
from pathlib import Path
import time
import unittest
import concurrent.futures
import select
from google.protobuf import text_format
import core_pb2 as core
import issuer_pb2 as issuer
import world_session_pb2 as session
from harness.issuer_progress import DelayedInfoIssuer


class ActorStopLivenessTests(existing.WorldIdentityTests):
    def configure_world_participant(self,config,state):
        client=text_format.Parse(Path(config.issuer_client_config).read_text(),issuer.IssuerTlsClientConfig())
        client.port=self.stop_relay.port
        path=self.root/'stop-liveness-client.textproto'
        path.write_text(text_format.MessageToString(client));path.chmod(0o600)
        config.issuer_client_config=str(path)
        super().configure_world_participant(config,state)

    def checked_stop(self,controlled):
        self.stop_relay=DelayedInfoIssuer(self,delayed_operation='credential_check',actor_id=self.identity.actor_id)
        world=self.world(grants=['navigate']);peer=world.peers[0]
        self.assertTrue(self.authenticate(peer).HasField('opened'))
        self.assertTrue(peer.act(input=core.ActorInputRequest(axes=[1,0,0,0,0,0])).HasField('action'))
        self.assertEqual(list(peer.call(inspect=session.WorldSessionInspect()).state.axes),[1,0,0,0,0,0])
        self.stop_relay.delay_armed.set();started=time.monotonic()
        if controlled:
            peer.action_sequence+=1
            reply=peer.call(control_action=core.ActorActionRequest(seq=peer.action_sequence,stop=core.ActorStopRequest()))
            accepted=reply.HasField('control_action') and reply.control_action.HasField('accepted') and reply.control_action.accepted.accepted
        else:
            reply=peer.act(stop=core.ActorStopRequest())
            accepted=reply.HasField('action') and reply.action.accepted
        elapsed=time.monotonic()-started
        target=os.environ.get('SPATIAL_SECURITY_EVIDENCE')
        if target:
            p=Path(target);p.mkdir(parents=True,exist_ok=True)
            (p/(self._testMethodName+'-stop-timing.json')).write_text(json.dumps({'elapsed_seconds':elapsed,
                'delayed_credential_check_started':self.stop_relay.delay_entered.is_set(),'reply':str(reply)},indent=2)+'\n')
        self.assertTrue(accepted,reply)
        self.assertLess(elapsed,.35,'Authenticated Stop waited for fresh credential authorization')
        self.assertFalse(self.stop_relay.delay_entered.is_set())
        self.stop_relay.delay_armed.clear()
        self.assertEqual(list(peer.call(inspect=session.WorldSessionInspect()).state.axes),[0]*6)
        # The same fixture must still delay a consequential fresh input action.
        # Cancellation cannot turn into a general permission-cache bypass.
        self.stop_relay.delay_armed.set();started=time.monotonic()
        self.assertTrue(peer.act(input=core.ActorInputRequest(axes=[0]*6)).HasField('action'))
        self.assertTrue(self.stop_relay.delay_entered.is_set())
        self.assertTrue(self.stop_relay.delay_finished.is_set())
        self.assertGreaterEqual(time.monotonic()-started,4)
        self.assertEqual(list(peer.call(inspect=session.WorldSessionInspect()).state.axes),[0]*6)

    def test_authenticated_stop_does_not_wait_for_fresh_credentials(self):
        self.checked_stop(False)

    def test_authenticated_controlled_stop_does_not_wait_for_fresh_credentials(self):
        self.checked_stop(True)

    def other_authentication(self):
        key,public=self.key('other-stop-actor')
        record=self.decide(self.owner_peer,self.enroll(self.owner_peer,self.intent(public),key,public)).enrollment
        claims=self.claims(record,self.info)
        self.stop_relay=DelayedInfoIssuer(self,delayed_operation='credential_check',actor_id=claims.actor_id)
        world=self.world(admission=session.REGISTERED_ACTORS,grants=['navigate'])
        first,other=world.peers
        self.assertTrue(self.authenticate(first).HasField('opened'))
        self.assertTrue(first.act(input=core.ActorInputRequest(axes=[1,0,0,0,0,0])).HasField('action'))
        self.stop_relay.delay_armed.set()
        pool=concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.addCleanup(pool.shutdown,wait=True)
        pending=pool.submit(other.call,begin=session.WorldAuthenticationBegin(credential=record.credential))
        self.assertTrue(self.stop_relay.delay_entered.wait(3),'Other actor authentication did not enter its held reply')
        self.assertFalse(self.stop_relay.delay_finished.is_set())
        return world,first,pending

    def stop_during_other_authentication(self,controlled):
        world,peer,other=self.other_authentication();peer.action_sequence+=1
        action=core.ActorActionRequest(seq=peer.action_sequence,stop=core.ActorStopRequest())
        started=time.monotonic();reply=peer.call(**{'control_action' if controlled else 'action':action})
        self.assertLess(time.monotonic()-started,.35,'Stop waited for another actor credential exchange')
        if controlled:self.assertTrue(reply.control_action.accepted.accepted,reply)
        else:self.assertTrue(reply.action.accepted,reply)
        self.assertFalse(self.stop_relay.delay_finished.is_set(),'Stop completed only after foreign authority resumed')
        self.assertTrue(other.result(timeout=5).HasField('challenge'))
        self.assertEqual(list(peer.call(inspect=session.WorldSessionInspect()).state.axes),[0]*6)
        self.assertIsNone(world.proc.poll())

    def test_stop_completes_during_another_actor_authentication(self):self.stop_during_other_authentication(False)
    def test_controlled_stop_completes_during_another_actor_authentication(self):self.stop_during_other_authentication(True)

    def test_cancellation_gate_rejects_invalid_context_and_sequence_before_effects(self):
        _,peer,other=self.other_authentication()
        seq=peer.action_sequence+1
        cases=[
            (session.WorldSessionRequest(protocol_version=1,action=core.ActorActionRequest(seq=seq,token='chosen-token',stop=core.ActorStopRequest())),core.INVALID_ARGUMENT),
            (session.WorldSessionRequest(protocol_version=1,action=core.ActorActionRequest(seq=peer.action_sequence,stop=core.ActorStopRequest())),core.STALE_SEQUENCE),
            (session.WorldSessionRequest(protocol_version=2,action=core.ActorActionRequest(seq=seq,stop=core.ActorStopRequest())),core.UNSUPPORTED_VERSION),
        ]
        unknown=session.WorldSessionRequest(protocol_version=1,action=core.ActorActionRequest(seq=seq,stop=core.ActorStopRequest()))
        unknown.action.MergeFromString(b'\xa0\x06\x01');cases.append((unknown,core.INVALID_ARGUMENT))
        for request,code in cases:
            started=time.monotonic();reply=peer.request(request)
            self.assertLess(time.monotonic()-started,.35)
            self.assertTrue(reply.HasField('error'),reply);self.assertEqual(reply.error.code,code,reply)
            self.assertFalse(self.stop_relay.delay_finished.is_set())
        # None of those rejected requests may consume the next actor sequence.
        peer.action_sequence=seq;reply=peer.call(action=core.ActorActionRequest(seq=seq,stop=core.ActorStopRequest()))
        self.assertTrue(reply.action.accepted,reply);self.assertFalse(self.stop_relay.delay_finished.is_set())
        self.assertTrue(other.result(timeout=5).HasField('challenge'))

    def test_non_stop_frame_is_not_skipped_to_reach_later_stop(self):
        _,peer,other=self.other_authentication();requests=[]
        for action in [dict(unfocus=core.ActorStopRequest()),dict(stop=core.ActorStopRequest())]:
            peer.sequence+=1;peer.action_sequence+=1
            requests.append(session.WorldSessionRequest(protocol_version=1,request_id=peer.sequence,
                action=core.ActorActionRequest(seq=peer.action_sequence,**action)))
        from harness.wire import frame
        peer.stream.sendall(b''.join(frame(value) for value in requests))
        self.assertFalse(select.select([peer.stream],[],[],.35)[0],'Cancellation bypassed an earlier non-Stop frame')
        self.assertFalse(self.stop_relay.delay_finished.is_set())
        self.assertTrue(other.result(timeout=5).HasField('challenge'))
        for request in requests:
            reply=existing.receive(peer.stream.recv,peer.stream,session.WorldSessionReply)
            self.assertEqual(reply.request_id,request.request_id);self.assertTrue(reply.action.accepted,reply)


if __name__=='__main__':
    names=[name for name in ActorStopLivenessTests.__dict__ if name.startswith('test_')]
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(
        unittest.TestSuite(ActorStopLivenessTests(name) for name in names)).wasSuccessful())
