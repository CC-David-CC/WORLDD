"""Authenticated application input through real actor and native paths."""
import json
import os
import struct
import select
import signal
import time
import unittest
from unittest.mock import patch
import core_pb2 as core
import grants_pb2 as grant
import issuer_pb2 as issuer
import broker_pb2 as pb
import world_session_pb2 as session
import test_application_observation as observation
import test_application_access as access
from harness.oracles import pose
from harness.wire import frame, make_request
from test_world_identity import receive


class DirectBroker:
    def __init__(self, broker):self.stream=broker.stream;self.proc=broker.proc
    def close(self):pass


class WorldApplicationControlTests(observation.ApplicationObservationTests):
    def setup_world(self,*args,**kwargs):
        with patch.object(observation,'BrokerRelay',DirectBroker):
            return super().setup_world(*args,**kwargs)

    def probe(self,position=(0,0,-2),profile='development'):
        self.probe_number+=1;name='controlled-'+str(self.probe_number)
        code=("import subprocess,time\nfrom pathlib import Path\n"
              "out=open('/state/data/probe.jsonl','w')\n"
              f"p=subprocess.Popen(['/project/probe','--id',{name!r}],stdin=subprocess.PIPE,stdout=out,stderr=out)\n"
              "sent=0\n"
              "while not Path('/state/data/release').exists():\n"
              " if p.poll() is not None:raise SystemExit(p.returncode or 1)\n"
              " commands=Path('/state/data/commands')\n"
              " if commands.exists():\n"
              "  data=commands.read_bytes();end=data.rfind(b'\\n')+1\n"
              "  if end>sent:p.stdin.write(data[sent:end]);p.stdin.flush();sent=end\n"
              " time.sleep(.01)\n"
              "p.terminate();p.wait();out.close()\n")
        result=self.peer.act(process_launch=core.ProcessLaunchRequest(operation_id=name,profile=profile,
            argv=['/usr/bin/python3','-c',code]))
        self.assertTrue(result.HasField('action'),result)
        app=pb.ApplicationRef(workspace=b'one',application=result.action.process.process_id.encode(),server_id=self.server_id)
        def retain():
            log=self.data(app)/'probe.jsonl'
            if log.exists() and os.environ.get('SPATIAL_SECURITY_EVIDENCE'):
                target=access.Path(os.environ['SPATIAL_SECURITY_EVIDENCE']);target.mkdir(parents=True,exist_ok=True)
                (target/(self._testMethodName+'-'+name+'.log')).write_bytes(log.read_bytes())
        self.addCleanup(retain)
        try:
            value=self.until(lambda:self.world_process.management('surface.find',probe_id=name),lambda r:r['surface'] is not None)['surface']
        except AssertionError as error:
            log=self.data(app)/'probe.jsonl'
            self.fail(str(error)+'\n'+(log.read_text() if log.exists() else 'No native probe output'))
        self.world_process.management('surface.place',id=value['id'],room=self.world_info['entry_room'],pose=pose(position),size=[1,1])
        self.until(lambda:self.probe_events(app),lambda rows:any(r.get('event')=='ready' and not r.get('xi2') for r in rows))
        return app,value

    def probe_events(self,app):
        log=self.data(app)/'probe.jsonl'
        if not log.exists():return []
        return [json.loads(line) for line in log.read_text().split('\n')[:-1] if line.startswith('{')]

    def probe_command(self,app,name,*args):
        self.sequence+=1
        with (self.data(app)/'commands').open('a') as output:
            output.write(' '.join(map(str,[name,self.sequence,*args]))+'\n')
        return self.until(lambda:self.probe_events(app),lambda rows:any(r.get('event')=='ack' and r.get('seq')==self.sequence for r in rows))

    def native_device(self,app):
        # Independent supervisor observation. Confined clients cannot enumerate XI devices.
        window=next(r['xid'] for r in self.probe_events(app) if r.get('event')=='ready')
        root=self.native.supervisor;name=b'XInputExtension'
        head,_=root.request(98,struct.pack('<HH',len(name),0)+name)
        self.assertTrue(head[8])
        head,_=root.request(head[9],struct.pack('<I',window),minor=45)
        self.assertTrue(head[8])
        return struct.unpack_from('<H',head,10)[0]

    def control_action(self,peer,**action):
        result=peer.act(**action);self.assertTrue(result.HasField('action'),result);return result.action

    def actor_aim(self,peer=None,x=0,y=0,z=-1):
        return self.control_action(peer or self.viewer,aim=core.ActorAimRequest(direction=core.Vec3(x=x,y=y,z=z))).aim

    def actor_pointer(self,pressed,peer=None):
        return self.control_action(peer or self.viewer,pointer=core.ActorPointerRequest(button=1,pressed=pressed))

    def send_key_event(self,name,pressed,peer=None):
        return self.control_action(peer or self.viewer,key=core.ActorKeyRequest(keysym=name,pressed=pressed))

    def grant_control(self,app,readonly=False):
        self.grant_viewer(access.READ if readonly else [grant.WORKSPACE_ACCESS,*access.workspace.PROJECT])
        observe=self.grant_viewer([grant.APPLICATION_OBSERVE],app)
        control=self.grant_viewer([grant.APPLICATION_CONTROL],app)
        return observe,control

    def begin_control(self,profile='development',readonly=False):
        self.setup_world();app,surface=self.probe(profile=profile)
        observe,control=self.grant_control(app,readonly);self.actor_aim();self.actor_pointer(True)
        self.until(lambda:self.probe_events(app),lambda rows:any(r.get('event')=='button' and r.get('pressed') for r in rows))
        return app,surface,observe,control

    def actor_state(self,peer=None):return (peer or self.viewer).call(inspect=session.WorldSessionInspect()).state

    def assert_released(self,app,inspect=True):
        def released(rows):
            return any(r.get('event')=='button' and not r.get('pressed') for r in rows) and any(r.get('event')=='key' and not r.get('pressed') for r in rows)
        self.until(lambda:self.probe_events(app),released)
        if inspect:
            value=self.actor_state();self.assertFalse(value.held_keys);self.assertFalse(value.held_buttons);self.assertFalse(value.HasField('focus'))

    def m09_hold_native_input(self,app):
        self.until(lambda:self.probe_events(app),lambda rows:any(r.get('event')=='focus' and r.get('focused') for r in rows))
        before=len(self.probe_events(app))
        self.send_key_event('Shift_L',True);self.send_key_event('a',True)
        self.actor_aim(x=.05,z=-1)
        rows=self.until(lambda:self.probe_events(app),lambda rows:
            any(r.get('event')=='key' and r.get('pressed') and r.get('keysym') in (ord('a'),ord('A')) for r in rows[before:]) and
            any(r.get('event')=='motion' for r in rows[before:]))
        presses=[r for r in rows[before:] if r.get('event')=='key' and r.get('pressed')]
        codes={r['keycode'] for r in presses if r.get('keysym') in (ord('a'),ord('A'),0xffe1)}
        self.assertEqual(len(codes),2,presses)
        self.assertTrue(any(r.get('event')=='button' and r.get('pressed') and r.get('button')==1 for r in rows[:before]))
        return before,codes

    def m09_assert_native_release(self,app,before,codes,inspect=True):
        def balanced(rows):
            tail=rows[before:]
            released={r.get('keycode') for r in tail if r.get('event')=='key' and not r.get('pressed')}
            return (codes <= released and
                any(r.get('event')=='button' and r.get('button')==1 and not r.get('pressed') for r in tail) and
                any(r.get('event')=='focus' and not r.get('focused') for r in tail))
        rows=self.until(lambda:self.probe_events(app),balanced)
        tail=rows[before:]
        key_releases={r.get('keycode') for r in tail if r.get('event')=='key' and not r.get('pressed')}
        self.assertTrue(codes <= key_releases,tail)
        self.assertTrue(any(r.get('event')=='button' and r.get('button')==1 and not r.get('pressed') for r in tail),tail)
        self.assertTrue(any(r.get('event')=='focus' and not r.get('focused') for r in tail),tail)
        if inspect:
            state=self.actor_state()
            self.assertFalse(state.held_keys);self.assertFalse(state.held_buttons);self.assertFalse(state.HasField('focus'))
        released_indexes=[index for index,row in enumerate(rows[before:],before)
            if (row.get('event')=='key' and not row.get('pressed') and row.get('keycode') in codes) or
               (row.get('event')=='button' and row.get('button')==1 and not row.get('pressed')) or
               (row.get('event')=='focus' and not row.get('focused'))]
        self.assertTrue(released_indexes,tail)
        return max(released_indexes)

    def m09_peer_first_native_event(self,app,peer,before,codes,x=0,z=-1):
        self.actor_aim(peer,x=x,z=z)
        result=peer.act(pointer=core.ActorPointerRequest(button=1,pressed=True))
        if result.HasField('error'):
            self.assertEqual(result.error.code,core.BUSY,result)
            # BUSY proves this request did not acquire the surface group.
            self.m09_assert_native_release(app,before,codes,inspect=False)
            result=peer.act(pointer=core.ActorPointerRequest(button=1,pressed=True))
        self.assertTrue(result.HasField('action'),result)
        rows=self.until(lambda:self.probe_events(app),lambda rows:
            any(r.get('event')=='button' and r.get('button')==1 and r.get('pressed') for r in rows[before:]))
        peer_press=next(index for index,row in enumerate(rows[before:],before)
            if row.get('event')=='button' and row.get('button')==1 and row.get('pressed'))
        releases=[index for index,row in enumerate(rows[before:],before)
            if (row.get('event')=='key' and not row.get('pressed') and row.get('keycode') in codes) or
               (row.get('event')=='button' and row.get('button')==1 and not row.get('pressed')) or
               (row.get('event')=='focus' and not row.get('focused'))]
        self.assertEqual(len({rows[index].get('keycode') for index in releases
            if rows[index].get('event')=='key'}),len(codes),rows[before:])
        self.assertTrue(any(rows[index].get('event')=='button' for index in releases),rows[before:])
        self.assertTrue(any(rows[index].get('event')=='focus' for index in releases),rows[before:])
        self.assertTrue(all(index<peer_press for index in releases),rows[before:])
        self.send_key_event('b',True,peer);self.send_key_event('b',False,peer);self.actor_pointer(False,peer)
        rows=self.until(lambda:self.probe_events(app),lambda rows:
            any(r.get('event')=='key' and r.get('pressed') and r.get('text_hex')=='62' for r in rows[before:]))
        self.assertTrue(any(r.get('event')=='button' and r.get('pressed') and r.get('button')==1 for r in rows[before:]))
        return rows

    def m09_release_probe(self,app):
        (self.data(app)/'release').touch()
        result=self.world_process.management('process.wait',id=app.application.decode(),timeout_seconds=5)
        self.assertEqual(result['exit_code'],0)

    def m09_run_balanced_transfer(self,trigger):
        app,surface,observe,control=self.begin_control();before,codes=self.m09_hold_native_input(app)
        self.addCleanup(self.m09_release_probe,app)
        log_boundary=len(self.probe_events(app))
        if trigger=='voluntary':self.control_action(self.viewer,unfocus=core.ActorStopRequest())
        elif trigger=='disconnect':self.viewer.close()
        elif trigger=='revoke':self.revoke_grant(self.peer,control)
        elif trigger in ('unmap','destroy'):
            self.probe_command(app,trigger)
            self.until(lambda:self.probe_events(app),lambda rows:
                any(r.get('event')==trigger and r.get('xid')==next(r['xid'] for r in rows if r.get('event')=='ready')
                    for r in rows[before:]))
        else:raise AssertionError('unknown M09 transfer trigger')
        if trigger in ('disconnect','revoke'):
            # These paths must make progress without a harness wait for release.
            self.m09_peer_first_native_event(app,self.peer,log_boundary,codes)
            self.m09_assert_native_release(app,before,codes,inspect=False)
        elif trigger=='destroy':
            self.m09_assert_native_release(app,before,codes,inspect=False)
            self.probe_command(app,'sync')
            other,other_surface=self.probe((.6,0,-2))
            self.addCleanup(self.m09_release_probe,other)
            self.actor_aim(self.peer,x=.6,z=-2)
            self.actor_pointer(True,self.peer)
            self.until(lambda:self.probe_events(other),lambda rows:
                any(r.get('event')=='button' and r.get('button')==1 and r.get('pressed') for r in rows))
        else:
            if trigger=='unmap':self.probe_command(app,'map')
            self.m09_peer_first_native_event(app,self.peer,log_boundary,codes)
            self.m09_assert_native_release(app,before,codes)

    def test_authorized_native_input_without_workspace_selection(self):
        app,surface,observe,control=self.begin_control();self.assertFalse(self.actor_state().workspace)
        self.actor_pointer(False);self.send_key_event('a',True);self.send_key_event('a',False)
        rows=self.until(lambda:self.probe_events(app),lambda rows:any(r.get('event')=='key' and r.get('text_hex')=='61' for r in rows))
        self.assertGreater(self.native_device(app),0)

    def test_observation_does_not_grant_control(self):
        self.setup_world();app,surface=self.probe();self.grant_viewer([grant.WORKSPACE_ACCESS]);self.grant_viewer([grant.APPLICATION_OBSERVE],app)
        self.actor_aim();self.denied(self.viewer.act(pointer=core.ActorPointerRequest(button=1,pressed=True)),core.FORBIDDEN)
        self.assertFalse(any(r.get('event')=='button' for r in self.probe_events(app)))

    def test_readonly_actor_cannot_control_writable_application(self):
        self.setup_world();app,surface=self.probe();self.grant_control(app,readonly=True);self.actor_aim()
        self.denied(self.viewer.act(pointer=core.ActorPointerRequest(button=1,pressed=True)),core.FORBIDDEN)
        self.assertFalse(any(r.get('event')=='button' for r in self.probe_events(app)))

    def test_readonly_actor_can_control_readonly_application(self):
        app,surface,observe,control=self.begin_control(profile='read-only',readonly=True)
        self.actor_pointer(False);self.send_key_event('a',True);self.send_key_event('a',False)
        self.until(lambda:self.probe_events(app),lambda rows:any(r.get('text_hex')=='61' for r in rows))

    def test_one_application_has_one_actor_controller(self):
        app,surface,observe,control=self.begin_control();self.actor_aim(self.peer)
        self.denied(self.peer.act(pointer=core.ActorPointerRequest(button=1,pressed=True)),core.BUSY)
        self.assertTrue(self.actor_state().held_buttons);self.assertFalse(self.actor_state(self.peer).held_buttons)

    def test_unfocus_releases_native_input_before_handoff(self):
        app,surface,observe,control=self.begin_control();self.send_key_event('Shift_L',True)
        self.control_action(self.viewer,unfocus=core.ActorStopRequest());self.assert_released(app)
        self.actor_aim(self.peer);self.actor_pointer(True,self.peer);self.actor_pointer(False,self.peer);self.send_key_event('a',True,self.peer);self.send_key_event('a',False,self.peer)
        self.until(lambda:self.probe_events(app),lambda rows:any(r.get('text_hex')=='61' for r in rows))

    def test_idle_control_revocation_releases_input_and_preserves_execution(self):
        app,surface,observe,control=self.begin_control();self.send_key_event('a',True)
        self.revoke_grant(self.peer,control);self.assert_released(app)
        self.assertTrue(self.world_process.management('surface.get',id=surface['id'])['mapped'])
        self.assertIn(surface['id'],self.visible())
        self.actor_aim();self.denied(self.viewer.act(pointer=core.ActorPointerRequest(button=1,pressed=True)),core.FORBIDDEN)

    def test_observation_revocation_cancels_control(self):
        app,surface,observe,control=self.begin_control();self.send_key_event('a',True)
        self.revoke_grant(self.peer,observe);self.assert_released(app)
        self.assertNotIn(surface['id'],self.visible());self.assertIn(surface['id'],self.visible(self.peer))

    def test_independent_control_grant_survives_other_revocation(self):
        app,surface,observe,control=self.begin_control();self.grant_viewer([grant.APPLICATION_CONTROL],app)
        self.revoke_grant(self.peer,control);self.send_key_event('a',True);self.send_key_event('a',False)
        self.assertTrue(self.actor_state().held_buttons);self.actor_pointer(False)

    def test_stop_releases_application_reservation(self):
        app,surface,observe,control=self.begin_control();self.send_key_event('a',True)
        self.control_action(self.viewer,stop=core.ActorStopRequest());self.assert_released(app)
        self.actor_aim(self.peer);self.actor_pointer(True,self.peer);self.actor_pointer(False,self.peer)

    def test_scroll_uses_real_native_events(self):
        self.setup_world();app,surface=self.probe();self.grant_control(app);self.actor_aim()
        self.control_action(self.viewer,scroll=core.ActorScrollRequest(steps_y=2))
        rows=self.until(lambda:self.probe_events(app),lambda rows:sum(r.get('event')=='button' and r.get('button')==4 and not r.get('pressed') for r in rows)==2)
        self.assertEqual(sum(r.get('event')=='button' and r.get('button')==4 and r.get('pressed') for r in rows),2)
        self.assertFalse(self.actor_state().held_buttons)

    def test_independent_applications_allow_simultaneous_actors(self):
        self.setup_world();app,left=self.probe((-.6,0,-2));other,right=self.probe((.6,0,-2));self.grant_control(app)
        self.actor_aim(x=-.6,z=-2);self.actor_pointer(True)
        self.actor_aim(self.peer,x=.6,z=-2);self.actor_pointer(True,self.peer)
        self.assertEqual(self.actor_state().focus,left['id']);self.assertEqual(self.actor_state(self.peer).focus,right['id'])
        self.send_key_event('a',True);self.send_key_event('b',True,self.peer)
        a=self.until(lambda:self.probe_events(app),lambda rows:any(r.get('event')=='key' and r.get('pressed') for r in rows))
        b=self.until(lambda:self.probe_events(other),lambda rows:any(r.get('event')=='key' and r.get('pressed') for r in rows))
        self.assertNotEqual(self.native_device(app),self.native_device(other))
        self.assertFalse(any(r.get('text_hex')=='62' for r in a))
        self.assertFalse(any(r.get('text_hex')=='61' for r in b))

    def test_session_close_releases_before_handoff(self):
        app,surface,observe,control=self.begin_control();self.send_key_event('Shift_L',True)
        self.assertTrue(self.viewer.call(close=session.WorldSessionClose()).HasField('closed'))
        self.assert_released(app,inspect=False)
        self.actor_aim(self.peer);self.actor_pointer(True,self.peer);self.actor_pointer(False,self.peer)
        self.send_key_event('a',True,self.peer)
        self.until(lambda:self.probe_events(app),lambda rows:any(r.get('text_hex')=='61' for r in rows))

    def test_controller_disconnect_releases_before_handoff(self):
        app,surface,observe,control=self.begin_control();self.send_key_event('a',True)
        self.viewer.close();self.assert_released(app,inspect=False)
        self.actor_aim(self.peer);self.actor_pointer(True,self.peer)
        self.assertTrue(self.actor_state(self.peer).held_buttons)

    def test_restored_grant_does_not_restore_held_input(self):
        app,surface,observe,control=self.begin_control();self.send_key_event('a',True)
        self.revoke_grant(self.peer,control);self.assert_released(app)
        self.grant_viewer([grant.APPLICATION_CONTROL],app)
        self.denied(self.viewer.act(key=core.ActorKeyRequest(keysym='b',pressed=True)),core.NO_FOCUS)
        self.actor_aim();self.actor_pointer(True);self.actor_pointer(False)
        self.send_key_event('b',True);self.send_key_event('b',False)
        self.until(lambda:self.probe_events(app),lambda rows:any(r.get('text_hex')=='62' for r in rows))

    def test_popup_uses_same_application_reservation(self):
        app,surface,observe,control=self.begin_control();self.actor_pointer(False)
        rows=self.probe_command(app,'popup',100,80)
        xid=next(r['xid'] for r in rows if r.get('event')=='popup')
        child=self.until(lambda:self.world_process.management('surface.children',id=surface['id']),
                         lambda result:any(r['xid']==xid for r in result['surfaces']))
        popup=next(r for r in child['surfaces'] if r['xid']==xid)
        point=popup['pose']['p']
        self.assertEqual(self.actor_aim(x=point[0],y=point[1],z=point[2]).hit.id,popup['id']);self.actor_pointer(True);self.actor_pointer(False);self.send_key_event('a',True)
        self.assertEqual(self.actor_state().focus,popup['id'])
        self.actor_aim(self.peer);self.denied(self.peer.act(pointer=core.ActorPointerRequest(button=1,pressed=True)),core.BUSY)
        self.until(lambda:self.probe_events(app),lambda rows:any(r.get('text_hex')=='61' and r.get('xid')==xid for r in rows))

    def test_resized_aim_rejects_stale_pixels(self):
        self.setup_world();app,surface=self.probe();self.grant_control(app);self.actor_aim()
        self.probe_command(app,'resize',640,480)
        self.until(lambda:self.world_process.management('surface.get',id=surface['id']),lambda r:r['pixels']==[640,480])
        self.denied(self.viewer.act(pointer=core.ActorPointerRequest(button=1,pressed=True)),core.STALE_TARGET)
        self.assertFalse(any(r.get('event')=='button' for r in self.probe_events(app)))

    def test_unmapped_capture_releases_reservation(self):
        app,surface,observe,control=self.begin_control();self.send_key_event('a',True)
        self.probe_command(app,'unmap');self.until(lambda:self.actor_state(),lambda s:not s.held_keys and not s.held_buttons and not s.HasField('focus'))
        self.probe_command(app,'map');self.actor_aim(self.peer);self.actor_pointer(True,self.peer)
        self.assertTrue(self.actor_state(self.peer).held_buttons)

    def test_m09_voluntary_release_balances_input_before_peer_event(self):
        self.m09_run_balanced_transfer('voluntary')

    def test_m09_disconnect_balances_input_before_peer_event(self):
        self.m09_run_balanced_transfer('disconnect')

    def test_m09_authority_revocation_balances_input_before_peer_event(self):
        self.m09_run_balanced_transfer('revoke')

    def test_m09_unmap_balances_input_before_peer_event(self):
        self.m09_run_balanced_transfer('unmap')

    def test_m09_destroy_releases_input_and_preserves_probe(self):
        self.m09_run_balanced_transfer('destroy')

    def revoke_viewer_identity(self,kind):
        target=self.second.actor_id if kind==issuer.ACTOR_TARGET else self.second.credential_id
        operation=issuer.IssuerOperation(identity_revoke=issuer.IdentityRevoke(
            target=issuer.IdentityReference(kind=kind,id=target),expected_security_revision=0,reason='native control regression'))
        client=self.connect()
        result=self.execute(client,self.challenge(client,operation))
        self.assertTrue(result.HasField('identity_status'),result)

    def test_actor_revocation_releases_native_input(self):
        app,surface,observe,control=self.begin_control();self.send_key_event('a',True)
        self.revoke_viewer_identity(issuer.ACTOR_TARGET);self.assert_released(app,inspect=False)
        self.denied(self.viewer.call(inspect=session.WorldSessionInspect()),core.UNAUTHORIZED)
        self.actor_aim(self.peer);self.actor_pointer(True,self.peer)

    def test_credential_revocation_releases_native_input(self):
        app,surface,observe,control=self.begin_control();self.send_key_event('a',True)
        self.revoke_viewer_identity(issuer.CREDENTIAL_TARGET);self.assert_released(app,inspect=False)
        self.denied(self.viewer.call(inspect=session.WorldSessionInspect()),core.UNAUTHORIZED)
        self.actor_aim(self.peer);self.actor_pointer(True,self.peer)

    def revocation_during_broker_wait(self,kind):
        app,surface,observe,control=self.begin_control();self.send_key_event('a',True)
        world=self.world_process;world.sequence+=1
        request=make_request('process.wait',dict(id=app.application.decode(),timeout_seconds=3));request.request_id=world.sequence
        world.proc.stdin.write(frame(request));world.proc.stdin.flush()
        self.assertFalse(select.select([world.proc.stdout],[],[],.1)[0])
        if kind is None:self.viewer.close()
        else:self.revoke_viewer_identity(kind)
        self.assert_released(app,inspect=False)
        # Cancellation must complete while the unrelated process wait is still pending.
        self.assertFalse(select.select([world.proc.stdout],[],[],0)[0])
        (self.data(app)/'release').touch()
        reply=receive(lambda n:os.read(world.proc.stdout.fileno(),n),world.proc.stdout,core.Reply)
        self.assertEqual(reply.request_id,request.request_id);self.assertTrue(reply.HasField('process_wait'),reply)
        self.assertEqual(reply.process_wait.exit_code,0)

    def test_actor_revocation_cancels_during_broker_wait(self):
        self.revocation_during_broker_wait(issuer.ACTOR_TARGET)

    def test_credential_revocation_cancels_during_broker_wait(self):
        self.revocation_during_broker_wait(issuer.CREDENTIAL_TARGET)

    def test_disconnect_cancels_during_broker_wait(self):
        self.revocation_during_broker_wait(None)

    def test_revocation_during_renderer_wait_does_not_replay_input(self):
        app,surface,observe,control=self.begin_control();self.send_key_event('a',True)
        children=access.Path(f'/proc/{self.world_process.proc.pid}/task/{self.world_process.proc.pid}/children').read_text().split()
        compositor=next(int(pid) for pid in children if access.Path(f'/proc/{pid}/comm').read_text().strip()=='spatialwm')
        os.kill(compositor,signal.SIGSTOP)
        try:
            self.viewer.sequence+=1;self.viewer.action_sequence+=1
            request=session.WorldSessionRequest(protocol_version=1,request_id=self.viewer.sequence,
                action=core.ActorActionRequest(seq=self.viewer.action_sequence,key=core.ActorKeyRequest(keysym='b',pressed=True)))
            self.viewer.stream.sendall(frame(request))
            self.assertFalse(select.select([self.viewer.stream],[],[],.1)[0])
            self.revoke_viewer_identity(issuer.CREDENTIAL_TARGET)
            time.sleep(.2)
        finally:os.kill(compositor,signal.SIGCONT)
        reply=receive(self.viewer.stream.recv,self.viewer.stream,session.WorldSessionReply)
        self.assertTrue(reply.HasField('error'),reply)
        self.assert_released(app,inspect=False)
        self.assertFalse(any(r.get('text_hex')=='62' for r in self.probe_events(app)))
        self.actor_aim(self.peer);self.actor_pointer(True,self.peer)


    def native_device_count(self):
        root=self.native.supervisor;name=b'XInputExtension'
        head,_=root.request(98,struct.pack('<HH',len(name),0)+name)
        self.assertTrue(head[8]);opcode=head[9]
        root.request(opcode,struct.pack('<HH',2,0),minor=47)
        head,_=root.request(opcode,struct.pack('<HH',0,0),minor=48)
        return struct.unpack_from('<H',head,8)[0]

    def test_revocation_during_native_cancel_closes_actor_devices(self):
        app,surface,observe,lease=self.begin_control();self.send_key_event('a',True)
        before=self.native_device_count()
        children=access.Path(f'/proc/{self.world_process.proc.pid}/task/{self.world_process.proc.pid}/children').read_text().split()
        compositor=next(int(pid) for pid in children if access.Path(f'/proc/{pid}/comm').read_text().strip()=='spatialwm')
        os.kill(compositor,signal.SIGSTOP)
        try:
            self.viewer.sequence+=1;self.viewer.action_sequence+=1
            request=session.WorldSessionRequest(protocol_version=1,request_id=self.viewer.sequence,
                action=core.ActorActionRequest(seq=self.viewer.action_sequence,unfocus=core.ActorStopRequest()))
            self.viewer.stream.sendall(frame(request))
            self.assertFalse(select.select([self.viewer.stream],[],[],.1)[0])
            self.revoke_viewer_identity(issuer.CREDENTIAL_TARGET)
            time.sleep(.2)
        finally:os.kill(compositor,signal.SIGCONT)
        receive(self.viewer.stream.recv,self.viewer.stream,session.WorldSessionReply)
        self.assert_released(app,inspect=False)
        self.until(self.native_device_count,lambda count:count==before-4)


def suite():
    return unittest.TestSuite(WorldApplicationControlTests(name) for name in vars(WorldApplicationControlTests) if name.startswith('test_'))


if __name__=='__main__':unittest.main(defaultTest='suite',verbosity=2)
