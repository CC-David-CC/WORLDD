"""Pending Stop ownership against a real paused compositor and native X witness."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import time
import unittest
from unittest.mock import patch
import core_pb2 as core
import geometry_native_pb2 as native_witness
import world_session_pb2 as session
from harness.oracles import pose
from harness.wire import frame
from geometry_process_identity import collect, start_ticks
from test_world_identity import WORLD, WorldIdentityFixture, receive


class ActorStopLifetimeTests(WorldIdentityFixture):
    def configure_world_participant(self,config,state):
        config.admitted_actor_ids.append(self.other_claims.actor_id)
        config.actor_grants.add(actor_id=self.other_claims.actor_id,grants=['navigate'])
        super().configure_world_participant(config,state)

    def held_with_other_actor(self):
        self.retained_movement_override=True
        key,public=self.key('stop-lifetime-other')
        record=self.decide(self.owner_peer,self.enroll(self.owner_peer,self.intent(public),key,public)).enrollment
        self.other_claims=self.claims(record,self.info)
        native_fd=self.setup_native_input_workspace()
        with patch.dict(os.environ,{'SPATIAL_TRACE_STOP':'1'}):
            world=self.world(grants=['navigate','interact','observe','process.launch'],native=True,count=3,
                broker=self._native_input_relay,native_fd=native_fd)
        first,other,rebind=world.peers
        opened=self.authenticate(first);self.assertTrue(opened.HasField('opened'),opened)
        world.management('workspace.available',id='one',available=True)
        selected=first.act(workspace_select=core.WorkspaceIdRequest(id='one'))
        self.assertTrue(selected.HasField('action'),selected)
        self.assertTrue(selected.action.accepted,selected)
        spawn=core.Pose();spawn.position.x=1;spawn.orientation.w=1
        self.assertTrue(self.authenticate(other,record,key,public,pose=spawn).HasField('opened'))
        self.other_x=other.call(inspect=session.WorldSessionInspect()).state.pose.position.x
        probe=self.brokered_native_probe(world,first)
        found=self.until(lambda:world.management('surface.find',probe_id=probe.id),lambda value:value['surface'] is not None)['surface']
        world.management('surface.place',id=found['id'],room=world.management('world.info')['entry_room'],pose=pose((0,0,-2)),size=[2,2])
        self.assertTrue(first.act(aim=core.ActorAimRequest(direction=core.Vec3(z=-1))).action.accepted)
        self.assertTrue(first.act(pointer=core.ActorPointerRequest(button=1,pressed=True)).action.accepted)
        self.assertTrue(first.act(key=core.ActorKeyRequest(keysym='Shift_L',pressed=True)).action.accepted)
        probe.wait(lambda row:row.get('event')=='key' and row['pressed'])
        probe.wait(lambda row:row.get('event')=='button' and row['pressed'])
        with probe.condition:offset=len(probe.events)
        children=Path('/proc/'+str(world.proc.pid)+'/task/'+str(world.proc.pid)+'/children').read_text().split()
        expected=(WORLD.parent/'spatialwm').resolve(strict=True)
        identities=collect([(int(pid),start_ticks(int(pid))) for pid in children],protocol=native_witness)
        digest=hashlib.sha256(expected.read_bytes()).digest()
        compositors=[item.pid for item in identities if item.executable_sha256==digest]
        self.assertEqual(len(compositors),1,compositors)
        return world,first,other,rebind,probe,offset,compositors[0],opened.opened.actor_id

    @contextmanager
    def paused(self,pid):
        pin=os.pidfd_open(pid)
        try:
            signal.pidfd_send_signal(pin,signal.SIGSTOP)
            def state():
                return next(line.split()[1] for line in Path('/proc/'+str(pid)+'/status').read_text().splitlines() if line.startswith('State:'))
            self.until(state,lambda value:value in ('T','t'))
            self.assertEqual(select.select([pin],[],[],0)[0],[])
            yield
        finally:
            if not select.select([pin],[],[],0)[0]:signal.pidfd_send_signal(pin,signal.SIGCONT)
            os.close(pin)

    def diagnostic(self,world):
        world.log.flush();return Path(world.log.name).read_text()

    def submissions(self,world):
        return re.findall(r'role=cancel_submit id=(\d+) .*phase=renderer_request .*value=(\d+)',self.diagnostic(world))

    def submit_stop(self,world,peer):
        peer.sequence+=1;peer.action_sequence+=1
        request=session.WorldSessionRequest(protocol_version=1,request_id=peer.sequence,
            action=core.ActorActionRequest(seq=peer.action_sequence,stop=core.ActorStopRequest()))
        peer.stream.sendall(frame(request))
        self.until(lambda:self.submissions(world),lambda rows:len(rows)==1)
        self.assertFalse(select.select([peer.stream],[],[],.05)[0],'Stop claimed success without its native receipt')
        return request.request_id

    def other_moves(self,other):
        started=time.monotonic();reply=other.act(input=core.ActorInputRequest(axes=[1,0,0,0,0,0]))
        elapsed=time.monotonic()-started
        self.assertTrue(reply.HasField('action') and reply.action.accepted,reply)
        self.assertLess(elapsed,.25,'Another actor movement waited for pending native cancellation')
        time.sleep(.05)
        self.assertTrue(other.act(stop=core.ActorStopRequest()).action.accepted)
        return elapsed

    def released(self,probe,offset):
        key=probe.wait(lambda row:row.get('event')=='key' and not row['pressed'],after=offset,timeout=2)
        button=probe.wait(lambda row:row.get('event')=='button' and not row['pressed'],after=offset,timeout=2)
        self.assertIsNone(probe.proc.poll())
        return {'key':key,'button':button}

    def retain(self,world,probe,observations):
        path=Path(os.environ['SPATIAL_SECURITY_EVIDENCE']);path.mkdir(parents=True,exist_ok=True)
        probe.log.flush();(path/(self._testMethodName+'-native-events.jsonl')).write_bytes(probe.path.read_bytes())
        observations['native_submissions']=self.submissions(world)
        (path/(self._testMethodName+'-lifetime.json')).write_text(json.dumps(observations,indent=2)+'\n')

    def test_pending_native_stop_preserves_other_actor_movement_and_needs_checked_release(self):
        world,first,other,_,probe,offset,pid,_=self.held_with_other_actor()
        with self.paused(pid):
            request=self.submit_stop(world,first);elapsed=self.other_moves(other)
            self.assertFalse(select.select([first.stream],[],[],.05)[0])
        reply=receive(first.stream.recv,first.stream,session.WorldSessionReply)
        self.assertEqual((reply.protocol_version,reply.request_id),(1,request))
        self.assertTrue(reply.HasField('action') and reply.action.accepted,reply)
        release=self.released(probe,offset)
        self.assertGreater(other.call(inspect=session.WorldSessionInspect()).state.pose.position.x,self.other_x)
        self.retain(world,probe,{'movement_seconds_while_native_paused':elapsed,'independent_release':release})
        world.close();self.assertEqual(world.proc.returncode,0)

    def test_disconnect_retains_close_barrier_and_refuses_early_actor_rebind(self):
        world,first,other,rebind,probe,offset,pid,actor_id=self.held_with_other_actor()
        with self.paused(pid):
            self.submit_stop(world,first);first.close()
            self.until(lambda:self.diagnostic(world),lambda text:'reason=poll_error_or_hangup' in text or 'reason=poll_transport_lost' in text)
            denied=self.authenticate(rebind)
            self.assertTrue(denied.HasField('error'),denied);self.assertEqual(denied.error.code,core.IN_USE,denied)
            elapsed=self.other_moves(other)
        release=self.released(probe,offset)
        self.until(lambda:self.submissions(world),lambda rows:len(rows)==2)
        reopened=self.until(lambda:self.authenticate(rebind),lambda reply:reply.HasField('opened'))
        self.assertEqual(reopened.opened.actor_id,actor_id)
        self.assertTrue(rebind.act(input=core.ActorInputRequest(axes=[0]*6)).action.accepted)
        self.retain(world,probe,{'early_rebind_code':denied.error.code,'rebound_actor_id':actor_id,
            'movement_seconds_while_native_paused':elapsed,'independent_release':release})
        world.close();self.assertEqual(world.proc.returncode,0)

    def test_revocation_during_pending_cancel_upgrades_close_without_stale_success(self):
        world,first,other,_,probe,offset,pid,_=self.held_with_other_actor()
        pool=ThreadPoolExecutor(max_workers=1);self.addCleanup(pool.shutdown,wait=True)
        with self.paused(pid):
            request=self.submit_stop(world,first);elapsed=self.other_moves(other)
            revoked=pool.submit(self.revoke)
            self.until(lambda:self.diagnostic(world),lambda text:'reason=credential_revocation' in text)
            reply=receive(first.stream.recv,first.stream,session.WorldSessionReply)
            self.assertEqual((reply.protocol_version,reply.request_id),(1,request))
            self.assertTrue(reply.HasField('error'),reply);self.assertEqual(reply.error.code,core.UNAUTHORIZED,reply)
        release=self.released(probe,offset);revoked.result(timeout=5)
        self.until(lambda:self.submissions(world),lambda rows:len(rows)==2)
        self.assertTrue(other.act(input=core.ActorInputRequest(axes=[0]*6)).action.accepted)
        self.retain(world,probe,{'denial_code':reply.error.code,'movement_seconds_before_revocation':elapsed,
            'independent_release':release})
        world.close();self.assertEqual(world.proc.returncode,0)


if __name__=='__main__':unittest.main(verbosity=2)
