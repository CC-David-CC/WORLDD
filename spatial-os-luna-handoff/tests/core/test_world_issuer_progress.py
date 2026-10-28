"""Real native input cancellation remains live during a pending issuer INFO."""
import json
import os
from pathlib import Path
import socket
import threading
import time
from unittest.mock import patch
import unittest
from google.protobuf import text_format
import core_pb2 as core
import issuer_pb2 as issuer
import world_session_pb2 as session
from harness.issuer_progress import DelayedInfoIssuer, IssuerLifetimeWitness
from harness.oracles import pose
from test_world_identity import WorldIdentityFixture, receive
from harness.wire import frame


class WorldIssuerProgressTests(IssuerLifetimeWitness, WorldIdentityFixture):
    def test_zz_renderer_fault_environment_does_not_leak(self):
        for name in ('SPATIAL_TEST_RENDER_INPUT_DELAY_MS','SPATIAL_TEST_RENDER_INPUT_DELAY_COMMAND',
                     'SPATIAL_TEST_RENDER_INPUT_DELAY_MARKER'):
            self.assertNotIn(name,os.environ,'A prior native fixture leaked its renderer fault')

    def config_file(self, endpoint_port=None, **overrides):
        relay = getattr(self, "_info_relay", None)
        return super().config_file(endpoint_port=endpoint_port or (relay.port if relay else None), **overrides)

    def pending_info_with_native_input(self):
        self.assertIn(socket.gethostname().split(".")[0], ("sb16", "sb16-2"))
        self.assertTrue(os.environ.get("SPATIAL_TEST_DISPLAY"))
        native_fd=self.setup_native_input_workspace()
        # Keep the workspace authority on the real issuer. The delay belongs only
        # to worldd's test connection, or the broker participant times out too.
        self._info_relay = DelayedInfoIssuer(self,delayed_connection=getattr(self,'delayed_info_connection',None))
        world, peer, probe = self.held(native_fd=native_fd,
            fixture_faults=getattr(self,'actor_test_send_buffer',None) is not None)
        self._progress_observations = dict(stage="native_input_held", issuer_io_budget_seconds=5,
            broker_outer_wait="UNRUN in this native INFO case", operation_300_second_deadline="UNRUN")
        def retain_progress():
            self.write_observations(dict(retained_monotonic=time.monotonic(), world_exit_code=world.proc.poll(),
                info_delays=list(self._info_relay.delay_records),
                relay_connections=list(self._info_relay.connection_events)))
        self.addCleanup(retain_progress)
        pressed_key = probe.wait(lambda row:row.get("event")=="key" and row["pressed"], timeout=2)
        pressed_button = probe.wait(lambda row:row.get("event")=="button" and row["pressed"], timeout=2)
        self._progress_observations["independent_pressed_control"] = dict(key=pressed_key, button=pressed_button)
        with probe.condition:
            offset = len(probe.events)
        before = self.capture_issuer_lifetime("before_info_reply_delay")
        self.assertEqual(before["policy"]["idle_timeout_ms"], 3000)
        self.assertEqual(before["policy"]["io_timeout_ms"], 5000)
        self.assertTrue(all(item["exit_code"] is None for item in before["processes"]))
        if getattr(self,'capture_position_before_info',False):
            self._before_info_pose=peer.call(inspect=session.WorldSessionInspect()).state.pose.position.x
        self._info_relay.delay_armed.set()
        self.assertTrue(self._info_relay.delay_entered.wait(3), "Real world did not issue idle INFO")
        self.assertFalse(self._info_relay.delay_finished.is_set())
        self.assertIsNone(world.proc.poll(), "World exited while issuer INFO was pending")
        self.assertIsNone(probe.proc.poll(), "Native target exited while issuer INFO was pending")
        def retain_probe():
            target = Path(os.environ["SPATIAL_SECURITY_EVIDENCE"])
            probe.log.flush()
            for path in (probe.path, Path(probe.stderr.name)):
                (target/(self._testMethodName+"-"+path.name)).write_bytes(path.read_bytes())
        self.addCleanup(retain_probe)
        return world, peer, probe, offset

    def pending_other_actor_auth_with_native_input(self):
        self.assertIn(socket.gethostname().split('.')[0],('sb16','sb16-2'))
        self.assertTrue(os.environ.get('SPATIAL_TEST_DISPLAY'))
        native_fd=self.setup_native_input_workspace()
        self.retained_movement_override=True
        self.second_key,self.second_public=self.key('m10-slow-auth-actor')
        issuer_peer=self.connect()
        try:
            self.second_record=self.decide(issuer_peer,self.enroll(issuer_peer,
                self.intent(public=self.second_public,label='M10 slow authentication actor'),
                self.second_key,self.second_public)).enrollment
        finally:
            issuer_peer.close()
        self.second=self.claims(self.second_record,self.info)
        self._info_relay=DelayedInfoIssuer(self,delayed_operation='credential_check',actor_id=self.second.actor_id)
        owner=getattr(self,'_probe_owner',None)
        profiles=[(self.second.actor_id,['navigate'])]
        profiles.extend(getattr(self,'additional_auth_profiles',[]))
        if owner:profiles.append((owner[3].actor_id,['navigate','interact','observe','process.launch']))
        world=self.world(grants=['navigate','interact','observe','process.launch'],native=True,native_fd=native_fd,fixture_faults=True,
            additional_actor_profiles=profiles,broker=self._native_input_relay,
            count=max(3 if owner or getattr(self,'auth_test_extra_peer',False) else 2,
                      getattr(self,'auth_test_peer_count',2)))
        self._progress_observations=dict(stage='other_actor_credential_check_pending',input_timeout_seconds=.25)
        peer,other_peer=world.peers[:2]
        opened=self.authenticate(peer)
        self.assertTrue(opened.HasField('opened'))
        self._native_actor_id=opened.opened.actor_id
        world.management('workspace.available',id='one',available=True)
        selected=peer.act(workspace_select=core.WorkspaceIdRequest(id='one'))
        self.assertTrue(selected.HasField('action'),selected)
        self.assertTrue(selected.action.accepted,selected)
        launcher=peer
        if owner:
            launcher=world.peers[2]
            self.assertTrue(self.authenticate(launcher,owner[0],owner[1],owner[2]).HasField('opened'))
            selected=launcher.act(workspace_select=core.WorkspaceIdRequest(id='one'))
            self.assertTrue(selected.HasField('action') and selected.action.accepted,selected)
        probe=self.brokered_native_probe(world,launcher)
        if owner:launcher.close()
        found=self.until(lambda:world.management('surface.find',probe_id=probe.id),lambda result:result['surface'] is not None)['surface']
        room=world.management('world.info')['entry_room']
        world.management('surface.place',id=found['id'],room=room,pose=pose((0,0,-2)),size=[2,2])
        self.assertTrue(peer.act(aim=core.ActorAimRequest(direction=core.Vec3(z=-1))).action.accepted)
        self.assertTrue(peer.act(pointer=core.ActorPointerRequest(button=1,pressed=True)).action.accepted)
        self.assertTrue(peer.act(key=core.ActorKeyRequest(keysym='Shift_L',pressed=True)).action.accepted)
        state=peer.call(inspect=session.WorldSessionInspect()).state
        self.assertTrue(state.held_keys);self.assertTrue(state.held_buttons)
        probe.wait(lambda row:row.get('event')=='key' and row['pressed'],timeout=2)
        probe.wait(lambda row:row.get('event')=='button' and row['pressed'],timeout=2)
        return world,peer,other_peer,probe

    def independent_release(self, probe, offset):
        # This is a separately compiled X client, with no world RPC or SUT event queue.
        key = probe.wait(lambda row:row.get("event")=="key" and not row["pressed"], after=offset, timeout=1.5)
        button = probe.wait(lambda row:row.get("event")=="button" and not row["pressed"], after=offset, timeout=1.5)
        self.assertIsNone(probe.proc.poll(), "Input target process died")
        return dict(key=key, button=button, observed_monotonic=time.monotonic())

    def write_observations(self, value):
        path = Path(os.environ["SPATIAL_SECURITY_EVIDENCE"])/(self._testMethodName+"-progress.json")
        self._progress_observations.update(value)
        path.write_text(json.dumps(self._progress_observations, sort_keys=True, indent=2)+"\n")
        path.chmod(0o600)

    def test_shutdown_cancels_pending_auxiliary_info(self):
        self.independent_probe_owner=True
        self.delayed_info_connection=2
        world,peer,probe,offset=self.pending_info_with_native_input()
        self.assertEqual(self._info_relay.delay_records[0]['connection'],2)
        started=time.monotonic()
        world.close()
        elapsed=time.monotonic()-started
        release=self.independent_release(probe,offset)
        pending=not self._info_relay.delay_finished.is_set()
        self.write_observations(dict(stage='shutdown_with_auxiliary_info_pending',
            shutdown_seconds=elapsed,info_pending_at_shutdown=pending,native_release=release,
            world_exit_code=world.proc.returncode))
        self.assertEqual(world.proc.returncode,0)
        self.assertTrue(pending,'Shutdown waited for the unrelated INFO response')
        self.assertLess(elapsed,2,'Shutdown did not cancel the pending auxiliary INFO')

    def test_auxiliary_info_connection_loss_releases_native_input(self):
        self.independent_probe_owner=True
        self.delayed_info_connection=2
        world,peer,probe,offset=self.pending_info_with_native_input()
        self.assertEqual(self._info_relay.delay_records[0]['connection'],2)
        started=time.monotonic()
        self._info_relay.abort_connection(2)
        release=self.independent_release(probe,offset)
        elapsed=time.monotonic()-started
        try:
            reply=peer.act(key=core.ActorKeyRequest(keysym='a',pressed=True))
            self.assertTrue(reply.HasField('error'),reply)
            self.assertEqual(reply.error.code,core.UNAVAILABLE,reply)
            outcome='UNAVAILABLE'
        except (EOFError,BrokenPipeError,ConnectionResetError):
            outcome='actor channel closed'
        with probe.condition:
            forbidden=[row for row in probe.events[offset:] if row.get('event')=='key' and
                row.get('keysym')==ord('a') and row.get('pressed')]
        self.write_observations(dict(stage='auxiliary_info_connection_loss',
            release_seconds=elapsed,native_release=release,post_loss_action=outcome,
            forbidden_key_events=forbidden))
        self.assertEqual(forbidden,[],'Input reached the app after issuer connection loss')
        self.assertLess(elapsed,1.5,'Auxiliary issuer failure did not cancel input promptly')
        self.assertIsNone(probe.proc.poll(),'Independent application exited after world issuer loss')

    def test_motion_receipt_and_displacement_during_pending_info_without_inspect(self):
        self.retained_movement_override=True
        self.capture_position_before_info=True
        self.setup_native_input_workspace()
        with patch.dict(os.environ,SPATIAL_TRACE_STOP='1'):
            world,peer,probe,_=self.pending_info_with_native_input()
        started=time.monotonic()
        request=peer.motion_refresh(core.ActorInputRequest(axes=[1,0,0,0,0,0]))
        marker=f'role=world_motion_refresh id={request} '
        deadline=started+.22
        while marker not in Path(world.log.name).read_text() and time.monotonic()<deadline:
            time.sleep(.005)
        received=marker in Path(world.log.name).read_text()
        observed=time.monotonic();pending=not self._info_relay.delay_finished.is_set()
        self.write_observations(dict(stage='direct_motion_during_pending_info',request_id=request,
            checked_refresh_received=received,receipt_elapsed_seconds=observed-started,
            info_pending_at_receipt=pending))
        self.assertTrue(self._info_relay.delay_finished.wait(6),'Delayed INFO did not recover')
        state=peer.call(inspect=session.WorldSessionInspect()).state
        displacement=state.pose.position.x-self._before_info_pose
        self.write_observations(dict(displacement_x=displacement))
        self.assertTrue(received,'World did not apply the checked refresh within 220 ms')
        self.assertTrue(pending,'INFO completed before the checked refresh')
        self.assertGreater(displacement,.02,'Actor did not move after the checked refresh')
        self.assertTrue(peer.act(stop=core.ActorStopRequest()).action.accepted)
        self.released(world,probe)
        world.close();self.assertEqual(world.proc.returncode,0)

    def test_authenticated_motion_refresh_completes_while_info_reply_is_pending(self):
        self.retained_movement_override=True
        world, peer, probe, _ = self.pending_info_with_native_input()
        identity=text_format.Parse(world.config_path.read_text(),session.WorldIdentityConfig())
        self.assertTrue(identity.enable_retained_movement)
        config_path = Path(world.proc.args[0]).with_name('runtime.textproto')
        config = text_format.Parse(config_path.read_text(), core.RuntimeConfig())
        self.assertEqual(config.input_timeout, .25)
        before = peer.call(inspect=session.WorldSessionInspect()).state.pose.position.x
        started = time.monotonic()
        request_id=peer.motion_refresh(core.ActorInputRequest(axes=[1, 0, 0, 0, 0, 0]))
        moved=None;deadline=started+.22
        while time.monotonic()<deadline:
            state=peer.call(inspect=session.WorldSessionInspect()).state
            if state.pose.position.x>before+.02:
                moved=state;break
            time.sleep(.01)
        elapsed=time.monotonic()-started
        pending = not self._info_relay.delay_finished.is_set()
        self.write_observations(dict(motion_started_monotonic=started,
            motion_request_id=request_id,motion_observed_seconds=elapsed,
            info_still_pending_at_motion_receipt=pending,
            moved_pose=[moved.pose.position.x,moved.pose.position.y,moved.pose.position.z] if moved else None))
        self.assertIsNotNone(moved,'One-way authenticated movement did not reach the world within 220 ms')
        self.assertTrue(pending, 'Movement refresh waited for the unrelated issuer INFO reply')
        self.assertLess(elapsed, .25, 'Movement refresh exceeded the unchanged input expiry')
        self.assertIsNone(world.proc.poll())
        self.assertIsNone(probe.proc.poll())
        # Measure movement before allowing the delayed exchange to settle.
        # Then prove the same world remains usable and releases its participation.
        self.assertTrue(self._info_relay.delay_finished.wait(5))
        stopped = peer.act(stop=core.ActorStopRequest())
        self.assertTrue(stopped.HasField('action') and stopped.action.accepted, stopped)
        world.close()
        self.assertEqual(world.proc.returncode, 0)

    def test_pending_primary_info_does_not_gate_actor_inspection_or_movement(self):
        self.delayed_info_connection=1
        self.test_authenticated_motion_refresh_completes_while_info_reply_is_pending()

    def test_pending_auxiliary_info_does_not_gate_actor_inspection_or_movement(self):
        self.delayed_info_connection=2
        self.test_authenticated_motion_refresh_completes_while_info_reply_is_pending()

    def test_revocation_discards_unsent_cooperative_inspection(self):
        import re
        self.independent_probe_owner=True
        self.delayed_info_connection=1
        self.actor_test_send_buffer=1024
        world,peer,probe,offset=self.pending_info_with_native_input()
        self.assertEqual(self._info_relay.delay_records[0]['connection'],1)
        first=peer.sequence+1
        for _ in range(50):
            peer.sequence+=1
            peer.stream.sendall(frame(session.WorldSessionRequest(protocol_version=1,
                request_id=peer.sequence,inspect=session.WorldSessionInspect())))
        log=Path(world.log.name)
        deadline=time.monotonic()+2
        last=0;stable_since=time.monotonic()
        while time.monotonic()<deadline:
            ids=[int(v) for v in re.findall(r'reason=reply_queued .*output_request_id=(\d+)',log.read_text())]
            current=max((v for v in ids if v>=first),default=0)
            if current!=last:last=current;stable_since=time.monotonic()
            if current>=first and time.monotonic()-stable_since>.25:break
            time.sleep(.01)
        self.assertGreaterEqual(last,first,'No inspect reply was queued')
        self.assertLess(last,peer.sequence,'Actor socket did not reach backpressure')
        self.assertFalse(self._info_relay.delay_finished.is_set(),'Primary INFO delay ended before revocation')
        self.revoke(kind=issuer.ACTOR_TARGET)
        release=self.independent_release(probe,offset)
        deadline=time.monotonic()+2
        while 'reason=disconnect_drop_output' not in log.read_text() and time.monotonic()<deadline:time.sleep(.01)
        dropped=re.findall(r'reason=disconnect_drop_output .*output_request_id=(\d+)',log.read_text())
        self.assertTrue(dropped,'Revocation did not discard queued output')
        discarded=int(dropped[-1])
        received=[]
        try:
            while True:
                reply=receive(peer.stream.recv,peer.stream,session.WorldSessionReply,deadline=time.monotonic()+2)
                received.append(reply.request_id)
        except (EOFError,ConnectionResetError):pass
        self.write_observations(dict(stage='revoke_unsent_cooperative_inspection',
            first_request=first,last_queued_before_revoke=last,discarded_request=discarded,received_request_ids=received,
            native_release=release,info_pending_at_revoke=not self._info_relay.delay_finished.is_set()))
        self.assertNotIn(discarded,received,'Unsent inspect reply was delivered after revocation')
        self.assertIsNone(probe.proc.poll(),'Independent app exited during revocation')
        self.assertTrue(self._info_relay.delay_finished.wait(6),'Delayed INFO did not recover')
        world.close();self.assertEqual(world.proc.returncode,0)

    def test_other_actor_authentication_does_not_gate_motion_refresh(self):
        import concurrent.futures
        world,peer,other_peer,probe=self.pending_other_actor_auth_with_native_input()
        before=peer.call(inspect=session.WorldSessionInspect()).state
        self.assertEqual(len(before.axes),6)
        self.assertTrue(all(axis==0 for axis in before.axes))
        config_path=Path(world.proc.args[0]).with_name('runtime.textproto')
        config=text_format.Parse(config_path.read_text(),core.RuntimeConfig())
        self.assertEqual(config.input_timeout,.25)
        self._info_relay.delay_armed.set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            authentication=pool.submit(self.authenticate,other_peer,self.second_record,self.second_key,self.second_public)
            self.assertTrue(self._info_relay.delay_entered.wait(3),'Second actor did not enter its checked credential exchange')
            started=time.monotonic();refreshes=[]
            movement=core.ActorInputRequest(axes=[1,0,0,0,0,0])
            while time.monotonic()-started<1.2:
                peer.motion_refresh(movement);refreshes.append(time.monotonic())
                time.sleep(.08)
            peer.motion_refresh(core.ActorInputRequest(axes=[0,0,0,0,0,0]))
            pending=not self._info_relay.delay_finished.is_set()
            self.assertTrue(pending,'Other actor authentication finished before the movement window ended')
            self.assertGreaterEqual(len(refreshes),12)
            self.assertTrue(all(later-earlier<.25 for earlier,later in zip(refreshes,refreshes[1:])),refreshes)
            self.assertTrue(self._info_relay.delay_finished.wait(6),'Slow second actor authentication did not recover')
            authenticated=authentication.result(timeout=3)
        self.assertTrue(authenticated.HasField('opened'),authenticated)
        after=peer.call(inspect=session.WorldSessionInspect()).state
        displacement=after.pose.position.x-before.pose.position.x
        self.write_observations(dict(other_actor_auth_pending_seconds=self._info_relay.delay_records[0].get('elapsed_seconds',4.1),
            movement_window_seconds=1.2,movement_refresh_count=len(refreshes),
            maximum_refresh_gap_seconds=max(later-earlier for earlier,later in zip(refreshes,refreshes[1:])),
            displacement_x=displacement,other_actor_authenticated=authenticated.opened.actor_id))
        self.assertGreater(displacement,.25,'Authenticated movement did not advance during the other actor credential check')
        stopped=peer.act(stop=core.ActorStopRequest())
        self.assertTrue(stopped.HasField('action') and stopped.action.accepted,stopped)
        self.released(world,probe)
        world.close();self.assertEqual(world.proc.returncode,0)

    def test_four_authentications_wait_without_blocking_authenticated_actor(self):
        import concurrent.futures
        import re
        extras=[]
        for index in range(3):
            key,public=self.key('m10-queued-auth-'+str(index))
            record=self.decide(self.owner_peer,self.enroll(self.owner_peer,
                self.intent(public=public,label='M10 queued actor '+str(index)),key,public)).enrollment
            extras.append((record,key,public,self.claims(record,self.info)))
        self.additional_auth_profiles=[(item[3].actor_id,['navigate']) for item in extras]
        self.auth_test_peer_count=5
        with patch.dict(os.environ,SPATIAL_TRACE_STOP='1'):
            world,peer,other_peer,probe=self.pending_other_actor_auth_with_native_input()
        before=peer.call(inspect=session.WorldSessionInspect()).state.pose.position.x
        with probe.condition:offset=len(probe.events)
        proof_lock=threading.Lock()
        def authenticate_isolated(channel,record,key,public):
            challenge=channel.call(begin=session.WorldAuthenticationBegin(credential=record.credential))
            self.assertTrue(challenge.HasField('challenge'),challenge)
            # The inherited proof helper writes fixed paths. Serialize only
            # signing; the authentication requests remain concurrent.
            with proof_lock:proof=self.proof(challenge.challenge,key,public)
            return channel.call(complete=issuer.AuthenticationComplete(nonce=challenge.challenge.nonce,proof=proof))
        self._info_relay.delay_armed.set()
        with probe.condition:offset=len(probe.events)
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            first=pool.submit(authenticate_isolated,other_peer,self.second_record,self.second_key,self.second_public)
            self.assertTrue(self._info_relay.delay_entered.wait(3),'Slow credential check did not start')
            waiting=[pool.submit(authenticate_isolated,world.peers[index+2],record,key,public)
                for index,(record,key,public,_) in enumerate(extras)]
            key_started=time.monotonic()
            key_action=pool.submit(peer.act,key_tap_batch=core.ActorKeyTapBatchRequest(
                chords=[core.ActorKeyChord(keysyms=['F1'])]))
            movement=core.ActorInputRequest(axes=[1,0,0,0,0,0]);refreshes=[];started=time.monotonic()
            while time.monotonic()-started<1.0:
                refreshes.append((peer.motion_refresh(movement),time.monotonic()));time.sleep(.08)
            zero_refresh=peer.motion_refresh(core.ActorInputRequest(axes=[0,0,0,0,0,0]))
            receipts={}
            receipt_pattern=re.compile(r'role=world_motion_refresh id=(\d+) .* phase=refresh_sequence mono_ns=(\d+)')
            receipt_deadline=time.monotonic()+.2
            while time.monotonic()<receipt_deadline and not self._info_relay.delay_finished.is_set():
                output=Path(world.log.name).read_text()
                receipts={int(match.group(1)):int(match.group(2))/1e9 for match in receipt_pattern.finditer(output)}
                if all(request in receipts for request,_ in refreshes) and zero_refresh in receipts:break
                time.sleep(.005)
            refresh_receipts=[receipts[request] for request,_ in refreshes if request in receipts]
            refresh_gaps=[later-earlier for earlier,later in zip(refresh_receipts,refresh_receipts[1:])]
            pending_check_time=time.monotonic()
            pending=not self._info_relay.delay_finished.is_set()
            key_still_queued=not key_action.done()
            with probe.condition:
                early_key_events=[row for row in probe.events[offset:] if row.get('event')=='key' and row.get('keysym')==65470]
            self.assertTrue(self._info_relay.delay_finished.wait(6),'Slow credential check did not finish')
            opened=[first.result(timeout=8)]+[future.result(timeout=8) for future in waiting]
            reply=key_action.result(timeout=8)
            key_elapsed=time.monotonic()-key_started
            probe.wait(lambda row:row.get('event')=='key' and row.get('keysym')==65470 and row.get('pressed'),after=offset,timeout=1)
            probe.wait(lambda row:row.get('event')=='key' and row.get('keysym')==65470 and not row.get('pressed'),after=offset,timeout=1)
            with probe.condition:
                events=[row for row in probe.events[offset:] if row.get('event')=='key' and row.get('keysym')==65470]
        self.assertTrue(all(result.HasField('opened') for result in opened),opened)
        ids=[result.opened.actor_id for result in opened]
        self.assertEqual(len(set(ids)),4,'Queued authentications did not produce distinct actors')
        after=peer.call(inspect=session.WorldSessionInspect()).state.pose.position.x
        refresh_send_times=[sent for _,sent in refreshes]
        self.write_observations(dict(stage='four_queued_authentications',key_elapsed_seconds=key_elapsed,
            key_deferred_without_guardian=key_still_queued,early_key_events=early_key_events,
            info_pending_during_actor_work=pending,movement_refresh_count=len(refreshes),
            movement_refresh_receipts=len(refresh_receipts),
            maximum_refresh_send_gap_seconds=max((b-a for a,b in zip(refresh_send_times,refresh_send_times[1:])),default=None),
            maximum_processed_refresh_gap_seconds=max(refresh_gaps,default=None),
            zero_refresh_processed_before_pending_check=receipts.get(zero_refresh,0)<=pending_check_time,
            displacement_x=after-before,distinct_authenticated_actors=len(set(ids)),key_events=events))
        self.assertTrue(reply.HasField('action') and reply.action.accepted,reply)
        self.assertTrue(key_still_queued,'Unguarded key bypassed issuer serialization')
        self.assertEqual(early_key_events,[],'Unguarded key reached the application before authorization completed')
        self.assertEqual([row['pressed'] for row in events],[True,False],events)
        self.assertTrue(pending,'Slow authentication ended before the movement window')
        self.assertGreaterEqual(len(refreshes),10)
        self.assertEqual(len(refresh_receipts),len(refreshes),'World did not process every movement refresh during authentication')
        self.assertIn(zero_refresh,receipts,'World did not process the zero-intent refresh during authentication')
        self.assertLessEqual(receipts[zero_refresh],pending_check_time,
            'World processed zero intent only after authentication ended')
        self.assertTrue(all(gap<.25 for gap in refresh_gaps),refresh_gaps)
        self.assertGreater(after-before,.25,'Authenticated actor did not move during queued authentications')
        self.assertTrue(peer.act(stop=core.ActorStopRequest()).action.accepted)
        self.released(world,probe)
        world.close();self.assertEqual(world.proc.returncode,0)

    def test_revoked_actor_cannot_finish_delayed_credential_check(self):
        import concurrent.futures
        world,peer,other_peer,probe=self.pending_other_actor_auth_with_native_input()
        self._info_relay.delay_armed.set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            authentication=pool.submit(other_peer.call,begin=session.WorldAuthenticationBegin(
                credential=self.second_record.credential))
            self.assertTrue(self._info_relay.delay_entered.wait(3),'Second actor credential check did not start')
            target=issuer.IdentityReference(kind=issuer.ACTOR_TARGET,id=self.second.actor_id)
            operation=issuer.IssuerOperation(identity_revoke=issuer.IdentityRevoke(
                target=target,expected_security_revision=0,reason='delayed authentication regression'))
            issuer_peer=self.connect()
            try:result=self.execute(issuer_peer,self.challenge(issuer_peer,operation))
            finally:issuer_peer.close()
            self.assertTrue(result.HasField('identity_status'),result)
            self.assertFalse(self._info_relay.delay_finished.is_set(),'Credential delay ended before revocation')
            deadline=time.monotonic()+2
            log=Path(world.log.name)
            revision=result.identity_status.security_revision
            self.assertGreater(revision, 0)
            def consumed_revocation():
                return any('role=world' in line and 'stage=revocation_received' in line and
                    f'target_revision={revision}' in line.split() for line in log.read_text().splitlines())
            while not consumed_revocation() and time.monotonic()<deadline:time.sleep(.005)
            self.assertTrue(consumed_revocation(),'World did not consume the issuer revocation revision')
            self.assertFalse(self._info_relay.delay_finished.is_set(),
                'Credential check finished before world consumed revocation')
            self.assertTrue(self._info_relay.delay_finished.wait(6),'Credential check did not finish')
            try:
                reply=authentication.result(timeout=3)
                self.assertTrue(reply.HasField('error'),reply)
                self.assertIn(reply.error.code,(core.UNAUTHORIZED,core.UNAVAILABLE),reply)
                outcome='error '+str(reply.error.code)
            except (EOFError,BrokenPipeError,ConnectionResetError):outcome='actor channel closed'
        self.write_observations(dict(stage='revoked_actor_delayed_credential',outcome=outcome,
            credential_reply_pending_at_revocation=True))
        self.assertTrue(peer.act(stop=core.ActorStopRequest()).action.accepted)
        self.released(world,probe)
        world.close();self.assertEqual(world.proc.returncode,0)

    def test_closed_actor_channel_discards_delayed_credential_check(self):
        import concurrent.futures
        self.auth_test_extra_peer=True
        world,peer,other_peer,probe=self.pending_other_actor_auth_with_native_input()
        replacement=world.peers[2]
        self._info_relay.delay_armed.set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            authentication=pool.submit(other_peer.call,begin=session.WorldAuthenticationBegin(
                credential=self.second_record.credential))
            self.assertTrue(self._info_relay.delay_entered.wait(3),'Second actor credential check did not start')
            other_peer.stream.shutdown(socket.SHUT_RDWR)
            other_peer.close()
            self.assertFalse(self._info_relay.delay_finished.is_set(),'Credential delay ended before channel closure')
            try:authentication.result(timeout=3)
            except (EOFError,OSError,ValueError):pass
            deadline=time.monotonic()+2;log=Path(world.log.name)
            while ('channel_id=2 reason=poll_transport_lost' not in log.read_text() and
                   'channel_id=2 reason=poll_error_or_hangup' not in log.read_text() and time.monotonic()<deadline):
                time.sleep(.005)
            self.assertTrue('channel_id=2 reason=poll_transport_lost' in log.read_text() or
                'channel_id=2 reason=poll_error_or_hangup' in log.read_text(),
                'World did not observe the old actor channel closing')
            self.assertFalse(self._info_relay.delay_finished.is_set(),'Channel closure was not consumed during the delay')
            self.assertTrue(self._info_relay.delay_finished.wait(6),'Credential check did not finish')
        opened=self.authenticate(replacement,self.second_record,self.second_key,self.second_public)
        self.assertTrue(opened.HasField('opened'),opened)
        self.write_observations(dict(stage='closed_actor_delayed_credential',
            original_channel_closed_while_pending=True,replacement_actor_authenticated=True))
        self.assertTrue(peer.act(stop=core.ActorStopRequest()).action.accepted)
        self.released(world,probe)
        world.close();self.assertEqual(world.proc.returncode,0)

    def test_direct_world_without_guardian_defers_key_tap_during_authentication(self):
        import concurrent.futures
        world,peer,other_peer,probe=self.pending_other_actor_auth_with_native_input()
        offset=len(probe.events)
        self._info_relay.delay_armed.set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            authentication=pool.submit(self.authenticate,other_peer,self.second_record,self.second_key,self.second_public)
            self.assertTrue(self._info_relay.delay_entered.wait(3),'Second actor did not enter its checked credential exchange')
            started=time.monotonic()
            def send_tap():
                result=peer.act(key_tap_batch=core.ActorKeyTapBatchRequest(
                    chords=[core.ActorKeyChord(keysyms=['F1'])]))
                return result,time.monotonic()
            tap=pool.submit(send_tap)
            try:
                reply,completed=tap.result(timeout=.25)
                completed_while_pending=True
            except concurrent.futures.TimeoutError:
                completed_while_pending=False;completed=None;reply=None
            deadline=started+.25
            while time.monotonic()<deadline:
                witnessed=[row for row in probe.events[offset:] if row.get('event')=='key' and row.get('keysym')==65470]
                if [row.get('pressed') for row in witnessed]==[True,False]:break
                time.sleep(.005)
            pending=not self._info_relay.delay_finished.is_set()
            observed=[row for row in probe.events[offset:] if row.get('event')=='key' and row.get('keysym')==65470]
            self.assertTrue(self._info_relay.delay_finished.wait(6),'Slow second actor authentication did not recover')
            authenticated=authentication.result(timeout=3)
            if reply is None:reply,completed=tap.result(timeout=3)
        delivered_after=[row for row in probe.events[offset:] if row.get('event')=='key' and row.get('keysym')==65470]
        self.write_observations(dict(stage='unguarded_direct_world_fails_closed_for_key_during_authentication',
            elapsed_seconds=completed-started,
            completed_while_auth_pending=completed_while_pending,other_actor_auth_pending_at_deadline=pending,
            independent_key_events_at_deadline=observed,other_actor_authenticated=authenticated.HasField('opened'),
            tap_accepted=reply.HasField('action') and reply.action.accepted,
            independent_key_events_after_authentication=delivered_after))
        self.assertTrue(authenticated.HasField('opened'),authenticated)
        self.assertTrue(pending,'Other actor authentication finished before the liveness deadline')
        self.assertFalse(completed_while_pending,'An unguarded direct world must not bypass issuer serialization')
        self.assertEqual(observed,[],'Unguarded world delivered input while issuer authorization was pending')
        self.assertTrue(reply.HasField('action') and reply.action.accepted,reply)
        self.assertEqual([row['pressed'] for row in delivered_after],[True,False],delivered_after)
        self.assertTrue(peer.act(stop=core.ActorStopRequest()).action.accepted)
        self.released(world,probe)
        world.close();self.assertEqual(world.proc.returncode,0)

    def test_motion_refresh_progresses_behind_focused_key_during_other_actor_authentication(self):
        import concurrent.futures
        import re

        self.setup_native_input_workspace()
        with patch.dict(os.environ,SPATIAL_TRACE_STOP='1'):
            world,peer,other_peer,probe=self.pending_other_actor_auth_with_native_input()
        before=peer.call(inspect=session.WorldSessionInspect()).state.pose.position.x
        self._info_relay.delay_armed.set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            authentication=pool.submit(other_peer.call,begin=session.WorldAuthenticationBegin(
                credential=self.second_record.credential))
            self.assertTrue(self._info_relay.delay_entered.wait(3),'Second actor did not enter its credential check')
            request_id=peer.sequence+1
            key=pool.submit(peer.act,key_tap_batch=core.ActorKeyTapBatchRequest(
                chords=[core.ActorKeyChord(keysyms=['F1'])]))
            marker=f'role=world_key_ingress id={request_id} '
            deadline=time.monotonic()+1.0
            while marker not in Path(world.log.name).read_text() and time.monotonic()<deadline:
                time.sleep(.005)
            self.assertIn(marker,Path(world.log.name).read_text(),'World did not read the focused key request')
            started=time.monotonic();request_ids=[]
            movement=core.ActorInputRequest(axes=[1,0,0,0,0,0])
            while time.monotonic()-started<1.2:
                request_ids.append(peer.motion_refresh(movement))
                time.sleep(.08)
            stopped_at=time.monotonic()
            peer.motion_refresh(core.ActorInputRequest(axes=[0,0,0,0,0,0]))
            pending=not self._info_relay.delay_finished.is_set()
            output=Path(world.log.name).read_text()
            receipts={int(m.group(1)):int(m.group(2))/1e9 for m in re.finditer(
                r'role=world_motion_refresh id=(\d+) .* phase=refresh_sequence mono_ns=(\d+)',output)}
            timely=[receipts[request] for request in request_ids if request in receipts and receipts[request]<=stopped_at]
            self.write_observations(dict(stage='motion_behind_queued_key_during_authentication',
                motion_requests=request_ids,processed_before_window_end=len(timely),
                movement_window_seconds=stopped_at-started,
                other_actor_authentication_pending_at_window_end=pending,
                maximum_processed_gap_seconds=max((b-a for a,b in zip(timely,timely[1:])),default=None)))
            self.assertTrue(self._info_relay.delay_finished.wait(6),'Delayed credential check did not recover')
            initial=authentication.result(timeout=3)
            reply=key.result(timeout=3)
        self.assertTrue(initial.HasField('challenge'),initial)
        self.assertTrue(reply.HasField('action') and reply.action.accepted,reply)
        self.assertTrue(pending,'Other actor authentication finished before the movement window ended')
        self.assertEqual(len(timely),len(request_ids),'Some movement refreshes waited behind the focused key')
        self.assertTrue(all(b-a<.25 for a,b in zip(timely,timely[1:])),timely)
        after=peer.call(inspect=session.WorldSessionInspect()).state.pose.position.x
        self.assertGreater(after-before,.25,'World did not advance while movement refreshes were sent')
        self.assertTrue(peer.act(stop=core.ActorStopRequest()).action.accepted)
        self.released(world,probe)
        world.close();self.assertEqual(world.proc.returncode,0)

    def test_queued_key_has_no_effect_after_issuer_revocation_ack(self):
        import concurrent.futures
        import signal
        self.independent_probe_owner=True
        self.setup_native_input_workspace()
        with patch.dict(os.environ,SPATIAL_TRACE_STOP='1'):
            world,peer,other_peer,probe=self.pending_other_actor_auth_with_native_input()
        children=Path(f'/proc/{world.proc.pid}/task/{world.proc.pid}/children').read_text().split()
        compositors=[int(pid) for pid in children if Path(f'/proc/{pid}/comm').read_text().strip()=='spatialwm']
        self.assertEqual(len(compositors),1)
        compositor=compositors[0]
        with probe.condition:offset=len(probe.events)
        self._info_relay.delay_armed.set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            authentication=pool.submit(other_peer.call,begin=session.WorldAuthenticationBegin(
                credential=self.second_record.credential))
            self.assertTrue(self._info_relay.delay_entered.wait(3))
            os.kill(compositor,signal.SIGSTOP)
            try:
                deadline=time.monotonic()+1
                while time.monotonic()<deadline:
                    status=Path(f'/proc/{compositor}/status').read_text()
                    if any(line.startswith('State:') and 'T' in line.split()[1] for line in status.splitlines()):break
                    time.sleep(.005)
                self.assertTrue(any(line.startswith('State:') and 'T' in line.split()[1] for line in status.splitlines()))
                request_id=peer.sequence+1
                key=pool.submit(peer.act,key_tap_batch=core.ActorKeyTapBatchRequest(
                    chords=[core.ActorKeyChord(keysyms=['F1'])]))
                marker=f'role=world_key_ingress id={request_id} '
                deadline=time.monotonic()+1.5;log=Path(world.log.name)
                while marker not in log.read_text() and time.monotonic()<deadline:time.sleep(.005)
                self.assertIn(marker,log.read_text())
                self.revoke(kind=issuer.ACTOR_TARGET);ack=time.monotonic()
                with probe.condition:
                    before=[row for row in probe.events[offset:] if row.get('event')=='key' and row.get('keysym')==65470]
                self.assertEqual(before,[],'F1 was delivered before the stopped compositor resumed')
                self.assertFalse(self._info_relay.delay_finished.is_set())
            finally:os.kill(compositor,signal.SIGCONT)
            released_key=probe.wait(lambda row:row.get('event')=='key' and row.get('keysym')==65505 and not row['pressed'],after=offset,timeout=1.5)
            released_button=probe.wait(lambda row:row.get('event')=='button' and not row['pressed'],after=offset,timeout=1.5)
            self.assertIsNone(probe.proc.poll(), 'Input target process died')
            released=dict(key=released_key,button=released_button,observed_monotonic=time.monotonic())
            self.assertTrue(self._info_relay.delay_finished.wait(6))
            initial=authentication.result(timeout=3)
            try:reply=key.result(timeout=3);outcome=reply.WhichOneof('outcome')
            except (EOFError,BrokenPipeError,ConnectionResetError):outcome='actor channel closed'
        with probe.condition:
            events=[row for row in probe.events[offset:] if row.get('event')=='key' and row.get('keysym')==65470]
        self.write_observations(dict(stage='key_after_issuer_revocation_ack',revocation_ack_monotonic=ack,
            forbidden_key_events=events,native_release=released,key_outcome=outcome,
            other_actor_initial_result=initial.WhichOneof('outcome')))
        self.assertEqual(events,[],'Key reached the application after issuer revocation was acknowledged')
        world.close();self.assertEqual(world.proc.returncode,0)

    def test_revocation_discards_queued_key_during_other_actor_authentication(self):
        import concurrent.futures
        import signal

        self.independent_probe_owner=True
        self.setup_native_input_workspace()
        with patch.dict(os.environ,SPATIAL_TRACE_STOP='1'):
            world,peer,other_peer,probe=self.pending_other_actor_auth_with_native_input()
        children=Path(f'/proc/{world.proc.pid}/task/{world.proc.pid}/children').read_text().split()
        compositors=[int(pid) for pid in children if Path(f'/proc/{pid}/comm').read_text().strip()=='spatialwm']
        self.assertEqual(len(compositors),1,'Expected the production compositor child')
        compositor=compositors[0]
        with probe.condition:offset=len(probe.events)
        self._info_relay.delay_armed.set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            authentication=pool.submit(other_peer.call,begin=session.WorldAuthenticationBegin(
                credential=self.second_record.credential))
            self.assertTrue(self._info_relay.delay_entered.wait(3),'Second actor did not enter its credential check')
            os.kill(compositor,signal.SIGSTOP)
            try:
                deadline=time.monotonic()+1
                while time.monotonic()<deadline:
                    status=Path(f'/proc/{compositor}/status').read_text()
                    if any(line.startswith('State:') and 'T' in line.split()[1] for line in status.splitlines()):break
                    time.sleep(.005)
                self.assertTrue(any(line.startswith('State:') and 'T' in line.split()[1] for line in status.splitlines()),
                    'Compositor did not enter stopped state')
                request_id=peer.sequence+1
                key=pool.submit(peer.act,key_tap_batch=core.ActorKeyTapBatchRequest(
                    chords=[core.ActorKeyChord(keysyms=['F1'])]))
                marker=f'role=world_key_ingress id={request_id} '
                deadline=time.monotonic()+1.5
                while marker not in Path(world.log.name).read_text() and time.monotonic()<deadline:
                    time.sleep(.005)
                self.assertIn(marker,Path(world.log.name).read_text(),
                    'World did not read the key frame before revocation')
                self.assertFalse(self._info_relay.delay_finished.is_set(),
                    'Other actor authentication finished before revocation')
                self.revoke(kind=issuer.ACTOR_TARGET)
                revoke_ack=time.monotonic()
                deadline=time.monotonic()+1.5
                while 'channel_id=1 reason=credential_revocation' not in Path(world.log.name).read_text() and time.monotonic()<deadline:
                    time.sleep(.005)
                self.assertIn('channel_id=1 reason=credential_revocation',Path(world.log.name).read_text(),
                    'World did not consume the actor revocation before the compositor resumed')
                released_key=probe.wait(lambda row:row.get('event')=='key' and row.get('keysym')==65505 and not row['pressed'],after=offset,timeout=1)
                released_button=probe.wait(lambda row:row.get('event')=='button' and not row['pressed'],after=offset,timeout=1)
                self.assertFalse(self._info_relay.delay_finished.is_set(),
                    'Other actor authentication finished before revocation was consumed')
            finally:
                os.kill(compositor,signal.SIGCONT)
            released=dict(key=released_key,button=released_button,observed_monotonic=time.monotonic())
            self.assertTrue(self._info_relay.delay_finished.wait(6),'Delayed credential check did not recover')
            initial=authentication.result(timeout=3)
            try:
                reply=key.result(timeout=3)
                outcome='reply: '+str(reply)
                self.assertTrue(reply.HasField('error'),reply)
            except (EOFError,BrokenPipeError,ConnectionResetError):
                outcome='actor channel closed'
        with probe.condition:
            events=[row for row in probe.events[offset:] if row.get('event')=='key' and row.get('keysym')==65470]
        self.write_observations(dict(stage='queued_key_revocation_during_other_actor_authentication',
            key_request_id=request_id,key_outcome=outcome,forbidden_key_events=events,
            native_release=released,revocation_ack_monotonic=revoke_ack,
            other_actor_initial_result=initial.WhichOneof('outcome')))
        self.assertEqual(events,[],'Revoked key reached the independent application witness')
        self.assertIsNone(probe.proc.poll(),'Independent application exited during controller revocation')
        world.close();self.assertEqual(world.proc.returncode,0)

    def test_nested_close_wait_keeps_other_actor_motion_and_revocation_live(self):
        import concurrent.futures
        from contextlib import contextmanager
        import signal

        self.independent_probe_owner=True
        self.auth_test_peer_count=4
        third_key,third_public=self.key('m10-nested-refresh-actor')
        issuer_peer=self.connect()
        try:
            third_record=self.decide(issuer_peer,self.enroll(issuer_peer,
                self.intent(public=third_public,label='M10 nested refresh actor'),third_key,third_public)).enrollment
        finally:issuer_peer.close()
        third_claims=self.claims(third_record,self.info)
        self.additional_auth_profiles=[(third_claims.actor_id,['navigate'])]
        with patch.dict(os.environ,SPATIAL_TRACE_STOP='1'):
            world,peer,other_peer,probe=self.pending_other_actor_auth_with_native_input()
        third_peer=world.peers[3]
        opened=self.authenticate(third_peer,third_record,third_key,third_public)
        self.assertTrue(opened.HasField('opened'),opened)
        actor_id=opened.opened.actor_id
        before=third_peer.call(inspect=session.WorldSessionInspect()).state.pose.position.x
        third_peer.sequence=1000000
        children=Path(f'/proc/{world.proc.pid}/task/{world.proc.pid}/children').read_text().split()
        compositors=[int(pid) for pid in children if Path(f'/proc/{pid}/comm').read_text().strip()=='spatialwm']
        self.assertEqual(len(compositors),1)
        compositor=compositors[0]
        @contextmanager
        def resume_stopped_compositor():
            nonlocal compositor
            try:yield
            finally:
                if compositor is not None:
                    os.kill(compositor,signal.SIGCONT)
                    compositor=None
        with probe.condition:offset=len(probe.events)
        os.kill(compositor,signal.SIGSTOP)
        try:
            deadline=time.monotonic()+1
            while time.monotonic()<deadline:
                status=Path(f'/proc/{compositor}/status').read_text()
                if any(line.startswith('State:') and 'T' in line.split()[1] for line in status.splitlines()):break
                time.sleep(.005)
            self.assertTrue(any(line.startswith('State:') and 'T' in line.split()[1] for line in status.splitlines()))
            self._info_relay.delay_armed.set()
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool, resume_stopped_compositor():
                authentication=pool.submit(other_peer.call,begin=session.WorldAuthenticationBegin(
                    credential=self.second_record.credential))
                self.assertTrue(self._info_relay.delay_entered.wait(3))
                closing=pool.submit(peer.call,close=session.WorldSessionClose())
                probe.wait(lambda row:row.get('event')=='key' and row.get('keysym')==65505 and not row['pressed'],after=offset,timeout=1)
                probe.wait(lambda row:row.get('event')=='button' and not row['pressed'],after=offset,timeout=1)
                log=Path(world.log.name)
                self.assertFalse(closing.done(),'Close completed while the compositor was stopped')
                movement=core.ActorInputRequest(axes=[1,0,0,0,0,0]);sent=[]
                for _ in range(8):
                    third_peer.motion_refresh(movement);sent.append(time.monotonic());time.sleep(.08)
                deadline=time.monotonic()+1
                while 'role=world_motion_refresh id=1000008 ' not in log.read_text() and time.monotonic()<deadline:time.sleep(.005)
                self.assertIn('role=world_motion_refresh id=1000008 ',log.read_text(),
                    'Other actor movement did not reach the nested pump')
                operation=issuer.IssuerOperation(identity_revoke=issuer.IdentityRevoke(
                    target=issuer.IdentityReference(kind=issuer.ACTOR_TARGET,id=third_claims.actor_id),
                    expected_security_revision=0,reason='nested pump regression'))
                revoker=self.connect()
                try:revoked=self.execute(revoker,self.challenge(revoker,operation))
                finally:revoker.close()
                self.assertTrue(revoked.HasField('identity_status'),revoked)
                deadline=time.monotonic()+1
                while 'channel_id=4 reason=credential_revocation' not in log.read_text() and time.monotonic()<deadline:time.sleep(.005)
                self.assertIn('channel_id=4 reason=credential_revocation',log.read_text(),
                    'Nested wait did not consume the other actor revocation')
                self.assertIn('role=world_nested_refresh',log.read_text(),'Nested refresh pump was not entered')
                self.assertFalse(self._info_relay.delay_finished.is_set(), 'Authentication finished before the nested checks')
                self.assertFalse(closing.done(), 'Close completed before compositor resumption')
                os.kill(compositor,signal.SIGCONT);compositor=None
                self.assertTrue(self._info_relay.delay_finished.wait(6))
                initial=authentication.result(timeout=3)
                try:close_result=closing.result(timeout=3);close_outcome=close_result.WhichOneof('outcome')
                except (EOFError,BrokenPipeError,ConnectionResetError):close_outcome='actor channel closed'
            if initial.HasField('challenge'):
                proof=self.proof(initial.challenge,self.second_key,self.second_public)
                authenticated=other_peer.call(complete=issuer.AuthenticationComplete(nonce=initial.challenge.nonce,proof=proof))
            else:
                self.assertTrue(initial.HasField('error'),initial)
                self.assertEqual(initial.error.code,core.UNAVAILABLE,initial)
                authenticated=self.authenticate(other_peer,self.second_record,self.second_key,self.second_public)
            self.assertTrue(authenticated.HasField('opened'),authenticated)
            after=world.management('world.get',id=actor_id)['pose']['p'][0]
            time.sleep(.12)
            final=world.management('world.get',id=actor_id)['pose']['p'][0]
            self.write_observations(dict(stage='nested_close_wait',movement_refreshes=len(sent),
                maximum_sender_gap_seconds=max(b-a for a,b in zip(sent,sent[1:])),
                movement_displacement=after-before,pose_stable_after_revocation=after==final,
                close_outcome=close_outcome,other_actor_authenticated=True))
            self.assertGreater(after-before,.15,'Third actor did not move during the nested renderer wait')
            self.assertEqual(after,final,'Revoked actor kept moving after the nested wait')
            self.assertIsNone(probe.proc.poll())
            world.close();self.assertEqual(world.proc.returncode,0)
        finally:
            if compositor is not None:os.kill(compositor,signal.SIGCONT)

    def test_actor_revocation_releases_native_input_during_other_actor_authentication(self):
        import concurrent.futures
        import re
        self.independent_probe_owner=True
        with patch.dict(os.environ,SPATIAL_TRACE_STOP='1'):
            world,peer,other_peer,probe=self.pending_other_actor_auth_with_native_input()
        actor_id=self._native_actor_id
        with probe.condition:offset=len(probe.events)
        before_pose=world.management('world.get',id=actor_id)['pose']['p'][0]
        movement=core.ActorInputRequest(axes=[1,0,0,0,0,0])
        self.assertTrue(peer.act(input=movement).action.accepted)
        self._info_relay.delay_armed.set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            authentication=pool.submit(self.authenticate,other_peer,self.second_record,self.second_key,self.second_public)
            self.assertTrue(self._info_relay.delay_entered.wait(3),'Other actor did not enter the delayed credential check')
            started=time.monotonic();refreshes=[]
            while time.monotonic()-started<.4:
                refreshes.append(peer.motion_refresh(movement));time.sleep(.08)
            moving_state=peer.call(inspect=session.WorldSessionInspect()).state
            moving_pose=moving_state.pose.position.x
            pending_after_inspect=not self._info_relay.delay_finished.is_set()
            self.assertTrue(pending_after_inspect,
                'Live actor inspection waited until other actor authentication finished')
            self.assertGreater(moving_pose-before_pose,.08,'Actor did not move before revocation')
            last_refresh=time.monotonic()
            # Use the live actor channel: the management pipe is serviced by
            # worldd's outer loop and cannot observe this issuer wait.
            self.revoke(kind=issuer.ACTOR_TARGET)
            revoke_ack=time.monotonic()
            released=self.independent_release(probe,offset)
            still_pending=not self._info_relay.delay_finished.is_set()
            self.assertTrue(still_pending,'Other actor authentication finished before native release')
            self.assertLess(revoke_ack-last_refresh,.25,'Revocation exceeded the native input expiry window')
            self.assertLess(released['observed_monotonic']-last_refresh,.25,
                'Native input release arrived only after ordinary movement expiry')
            self.assertTrue(self._info_relay.delay_finished.wait(6),'Other actor authentication did not recover')
            authenticated=authentication.result(timeout=3)
        self.assertTrue(authenticated.HasField('opened'),authenticated)
        output=Path(world.log.name).read_text()
        receipts={int(match.group(1)):int(match.group(2))/1e9 for match in re.finditer(
            r'role=world_motion_refresh id=(\d+) .* phase=refresh_sequence mono_ns=(\d+)',output)}
        processed=[receipts[request] for request in refreshes if request in receipts and
            receipts[request]<=released['observed_monotonic']]
        processed_gaps=[later-earlier for earlier,later in zip(processed,processed[1:])]
        self.assertGreaterEqual(len(processed),4,
            'World did not process sustained movement refreshes during the pending authentication')
        self.assertTrue(all(gap<.25 for gap in processed_gaps),processed_gaps)
        stopped_pose=world.management('world.get',id=actor_id)['pose']['p'][0]
        time.sleep(.12)
        final_pose=world.management('world.get',id=actor_id)['pose']['p'][0]
        self.assertEqual(stopped_pose,final_pose,'Revoked actor continued moving after native release')
        self.write_observations(dict(stage='actor_revocation_during_other_actor_authentication',
            actor_moved_before_revocation=moving_pose-before_pose,
            refresh_count=len(refreshes),processed_refreshes_before_native_release=len(processed),
            maximum_processed_refresh_gap_seconds=max(processed_gaps,default=None),
            inspection_completed_during_authentication=pending_after_inspect,
            last_refresh_to_revoke_ack_seconds=revoke_ack-last_refresh,
            last_refresh_to_native_release_seconds=released['observed_monotonic']-last_refresh,
            native_release=released,other_actor_authentication_pending_at_release=still_pending,
            unrelated_actor_authenticated=authenticated.opened.actor_id,
            pose_stable_after_release=stopped_pose==final_pose))
        self.assertIsNone(world.proc.poll(),'World failed while processing actor revocation')
        self.assertIsNone(probe.proc.poll(),'Native input witness exited during revocation')
        world.close();self.assertEqual(world.proc.returncode,0)

    def test_actor_revocation_releases_input_without_waiting_for_other_actor_authentication(self):
        import concurrent.futures
        self.independent_probe_owner=True
        world,peer,other_peer,probe=self.pending_other_actor_auth_with_native_input()
        actor_id=self._native_actor_id
        offset=len(probe.events)
        before=world.management('world.get',id=actor_id)['pose']['p'][0]
        movement=core.ActorInputRequest(axes=[1,0,0,0,0,0])
        self.assertTrue(peer.act(input=movement).action.accepted)
        time.sleep(.12)
        moved=world.management('world.get',id=actor_id)['pose']['p'][0]
        self.assertGreater(moved-before,.08,'Actor did not move before the delayed exchange')
        self._info_relay.delay_armed.set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            authentication=pool.submit(other_peer.call,begin=session.WorldAuthenticationBegin(
                credential=self.second_record.credential))
            self.assertTrue(self._info_relay.delay_entered.wait(3),'Second actor did not enter the delayed credential check')
            started=time.monotonic();refreshes=[]
            while time.monotonic()-started<.4:
                peer.motion_refresh(movement);refreshes.append(time.monotonic())
                time.sleep(.08)
            peer.motion_refresh(movement);last_refresh=time.monotonic()
            self.revoke(kind=issuer.ACTOR_TARGET);revoke_ack=time.monotonic()
            released=self.independent_release(probe,offset)
            still_pending=not self._info_relay.delay_finished.is_set()
            self.write_observations(dict(stage='native_revocation_during_other_actor_authentication',
                actor_moved_before_delayed_exchange=moved-before,refresh_count=len(refreshes)+1,
                last_refresh_to_revoke_ack_seconds=revoke_ack-last_refresh,
                last_refresh_to_native_release_seconds=released['observed_monotonic']-last_refresh,
                native_release=released,other_actor_authentication_pending_at_release=still_pending))
            self.assertTrue(self._info_relay.delay_finished.wait(6),'Other actor authentication did not recover')
            initial=authentication.result(timeout=3)
        if initial.HasField('challenge'):
            proof=self.proof(initial.challenge,self.second_key,self.second_public)
            authenticated=other_peer.call(complete=issuer.AuthenticationComplete(nonce=initial.challenge.nonce,proof=proof))
            first_outcome='challenge'
        else:
            self.assertTrue(initial.HasField('error'),initial)
            self.assertEqual(initial.error.code,core.UNAVAILABLE,initial)
            self.assertIn('superseded',initial.error.message)
            first_outcome='superseded; fresh authentication required'
            authenticated=self.authenticate(other_peer,self.second_record,self.second_key,self.second_public)
        self.assertTrue(authenticated.HasField('opened'),authenticated)
        self.write_observations(dict(unrelated_actor_initial_result=first_outcome,
            unrelated_actor_authenticated_after_fresh_check=True))
        self.assertTrue(still_pending,'Other actor authentication finished before native release')
        self.assertLess(revoke_ack-last_refresh,.25,'Revocation exceeded the native input expiry window')
        self.assertLess(released['observed_monotonic']-last_refresh,.25,
            'Native input release arrived only after ordinary movement expiry')
        stopped=world.management('world.get',id=actor_id)['pose']['p'][0]
        time.sleep(.12)
        self.assertEqual(stopped,world.management('world.get',id=actor_id)['pose']['p'][0])
        self.assertIsNone(probe.proc.poll(),'Workspace-owned native witness exited during revocation')
        world.close();self.assertEqual(world.proc.returncode,0)

    def test_motion_refresh_progresses_while_native_renderer_is_slow(self):
        self.retained_movement_override=True
        # Initialize the fixture's longer-lived environment patch before the
        # temporary renderer fault environment, so cleanup cannot restore it.
        self.setup_native_input_workspace()
        marker=self.root/'renderer-input-delay-entered'
        environment={
            'SPATIAL_TEST_RENDER_INPUT_DELAY_MS':'1000',
            # The empty-seat preparation is the first renderer input command.
            'SPATIAL_TEST_RENDER_INPUT_DELAY_COMMAND':'4',
            'SPATIAL_TEST_RENDER_INPUT_DELAY_MARKER':str(marker),
        }
        with patch.dict(os.environ,environment):
            world,peer,probe=self.held(fixture_faults=True)
        runtime_path=Path(world.proc.args[0]).with_name('runtime.textproto')
        runtime=text_format.Parse(runtime_path.read_text(),core.RuntimeConfig())
        self.assertEqual(runtime.input_timeout,.25)
        self._progress_observations=dict(stage='native_renderer_input_delay',input_timeout_seconds=.25)
        before=peer.call(inspect=session.WorldSessionInspect()).state
        outcome=[];failures=[]
        request=core.ActorKeyTapBatchRequest(chords=[core.ActorKeyChord(keysyms=['a'])])
        def delayed_native_input():
            try:outcome.append(peer.act(key_tap_batch=request))
            except BaseException as error:failures.append(error)
        action_started=time.monotonic()
        action=threading.Thread(target=delayed_native_input,name='m10-slow-renderer-action')
        action.start()
        deadline=time.monotonic()+3
        while not marker.exists() and time.monotonic()<deadline:time.sleep(.005)
        self.assertTrue(marker.exists(),'Production spatialwm did not enter its configured input delay')
        self.assertTrue(action.is_alive(),'Native input returned before the compositor delay started')

        started=time.monotonic();refreshes=[]
        moving=core.ActorInputRequest(axes=[1,0,0,0,0,0])
        while time.monotonic()-started<.78:
            peer.motion_refresh(moving);refreshes.append(time.monotonic())
            time.sleep(.08)
        peer.motion_refresh(core.ActorInputRequest(axes=[0,0,0,0,0,0]))
        action.join(timeout=4)
        self.assertFalse(action.is_alive(),'Actor action remained blocked after the renderer delay ended')
        if failures:raise failures[0]
        self.assertEqual(len(outcome),1)
        self.assertTrue(outcome[0].HasField('action'),outcome[0])
        self.assertTrue(outcome[0].action.accepted,outcome[0])
        after=peer.call(inspect=session.WorldSessionInspect()).state
        displacement=after.pose.position.x-before.pose.position.x
        gaps=[later-earlier for earlier,later in zip(refreshes,refreshes[1:])]
        observations=dict(renderer_delay_ms=1000,renderer_delay_command=3,
            renderer_marker=str(marker),movement_window_seconds=time.monotonic()-started,
            movement_refresh_count=len(refreshes),maximum_refresh_gap_seconds=max(gaps),
            displacement_x=displacement,action_elapsed_seconds=time.monotonic()-action_started)
        self.write_observations(observations)
        self.assertGreaterEqual(len(refreshes),8,refreshes)
        self.assertTrue(all(gap<.25 for gap in gaps),gaps)
        self.assertGreater(displacement,.3,'World motion did not advance during the delayed native renderer call')
        self.assertIsNone(world.proc.poll())
        self.assertIsNone(probe.proc.poll())
        world.close();self.assertEqual(world.proc.returncode,0)

    def test_default_checked_movement_stays_live_and_revocable_during_auxiliary_info(self):
        self.independent_probe_owner=True
        self.retained_movement_override=False
        self.delayed_info_connection=2
        world,peer,probe,offset=self.pending_info_with_native_input()
        identity=text_format.Parse(world.config_path.read_text(),session.WorldIdentityConfig())
        self.assertFalse(identity.enable_retained_movement)
        started=time.monotonic()
        reply=peer.act(input=core.ActorInputRequest(axes=[1,0,0,0,0,0]))
        elapsed=time.monotonic()-started
        pending=not self._info_relay.delay_finished.is_set()
        self.assertTrue(reply.HasField('action') and reply.action.accepted,reply)
        self.assertTrue(pending,'Checked movement waited for auxiliary INFO')
        self.assertLess(elapsed,.25,'Checked movement exceeded the unchanged input expiry')
        self.revoke(kind=issuer.ACTOR_TARGET)
        released=self.independent_release(probe,offset)
        try:
            denied=peer.act(input=core.ActorInputRequest(axes=[1,0,0,0,0,0]))
            self.assertTrue(denied.HasField('error'),denied)
            self.assertEqual(denied.error.code,core.UNAUTHORIZED,denied)
            outcome='UNAUTHORIZED'
        except (EOFError,BrokenPipeError,ConnectionResetError):
            outcome='actor channel closed'
        self.write_observations(dict(stage='default_checked_movement_during_auxiliary_info',
            movement_seconds=elapsed,info_pending_at_movement=pending,
            post_revocation_action=outcome,native_release=released))
        self.assertIsNone(probe.proc.poll(),'Independent application exited during actor revocation')
        self.assertTrue(self._info_relay.delay_finished.wait(6),'Auxiliary INFO did not recover')
        world.close();self.assertEqual(world.proc.returncode,0)

    def test_default_disabled_retained_refresh_has_no_motion_effect(self):
        import select
        self.independent_probe_owner=True
        self.retained_movement_override=False
        self._progress_observations=dict(stage='default_disabled_retained_refresh')
        world,peer,probe=self.held()
        identity=text_format.Parse(world.config_path.read_text(),session.WorldIdentityConfig())
        self.assertFalse(identity.enable_retained_movement)
        actor=peer.call(inspect=session.WorldSessionInspect()).state.id
        initial=world.management('world.get',id=actor)['pose']
        with probe.condition:offset=len(probe.events)
        peer.motion_refresh(core.ActorInputRequest(axes=[1,0,0,0,0,0]))
        released=self.independent_release(probe,offset)
        self.assertTrue(select.select([peer.stream],[],[],2)[0],'Invalid retained refresh left the actor channel open')
        self.assertEqual(peer.stream.recv(1),b'','Invalid retained refresh did not close the channel')
        stopped=world.management('world.get',id=actor)['pose']
        time.sleep(.15)
        final=world.management('world.get',id=actor)['pose']
        self.write_observations(dict(stage='default_disabled_retained_refresh',initial_pose=initial,
            stopped_pose=stopped,final_pose=final,native_release=released))
        self.assertEqual(initial,stopped,'Disabled retained refresh moved the actor')
        self.assertEqual(stopped,final,'Actor continued moving after rejected refresh')
        self.assertIsNone(probe.proc.poll(),'Independent application exited after actor rejection')
        world.close();self.assertEqual(world.proc.returncode,0)

    def test_default_configuration_keeps_retained_movement_disabled(self):
        self.retained_movement_override=False
        world, peer, probe, _ = self.pending_info_with_native_input()
        identity=text_format.Parse(world.config_path.read_text(),session.WorldIdentityConfig())
        self.assertFalse(identity.enable_retained_movement)
        self.assertNotIn('enable_retained_movement',world.config_path.read_text())
        started=time.monotonic()
        reply=peer.act(input=core.ActorInputRequest(axes=[1,0,0,0,0,0]))
        elapsed=time.monotonic()-started
        self.write_observations(dict(retained_movement_enabled=False,motion_roundtrip_seconds=elapsed,
            info_still_pending_at_motion_receipt=not self._info_relay.delay_finished.is_set(),
            motion_reply=reply.WhichOneof('outcome')))
        self.assertTrue(reply.HasField('action') and reply.action.accepted,reply)
        self.assertFalse(self._info_relay.delay_finished.is_set(),
            'Checked movement waited for unrelated issuer INFO')
        self.assertLess(elapsed,.25,'Checked movement exceeded input expiry')
        self.assertIsNone(probe.proc.poll())
        self.assertTrue(peer.act(stop=core.ActorStopRequest()).action.accepted)
        world.close();self.assertEqual(world.proc.returncode,0)

    def test_controller_loss_releases_native_input_while_info_reply_is_pending(self):
        world, peer, probe, offset = self.pending_info_with_native_input()
        closed = time.monotonic()
        self.write_observations(dict(controller_closed_monotonic=closed))
        peer.close()
        released = self.independent_release(probe, offset)
        self.assertFalse(self._info_relay.delay_finished.is_set(),
                         "Controller cancellation waited for the delayed INFO reply")
        self.assertLess(released["observed_monotonic"]-closed, 1.5)
        self.assertTrue(self._info_relay.delay_finished.wait(5))
        self.assertEqual(len(self._info_relay.delay_records), 1)
        delayed = self._info_relay.delay_records[0]
        self.assertGreaterEqual(delayed["elapsed_seconds"], 4.1)
        self.assertLess(delayed["elapsed_seconds"], 5)
        started = time.monotonic()
        self.assertTrue(self.authenticate(world.peers[1]).HasField("opened"))
        authentication_elapsed = time.monotonic()-started
        self.assertLess(authentication_elapsed, 5)
        after = self.capture_issuer_lifetime("controller_cancelled_and_same_issuer_connection_usable")
        self.assertTrue(all(item["exit_code"] is None for item in after["processes"]))
        self.write_observations(dict(controller_closed_monotonic=closed, release=released,
            info_delay=delayed, authentication_elapsed_seconds=authentication_elapsed,
            issuer_io_budget_seconds=5, broker_outer_wait="UNRUN in this native INFO case",
            absolute_operation_deadline="UNRUN; engine owner-delay case separately records actual broker RPC elapsed"))

    def configure_outage_broker(self):
        import test_application_access as access

        class OutageBroker(access.Broker):
            def close(broker):
                if broker.stream.fileno()<0:return
                if broker.proc.poll() is None:broker.stream.shutdown(socket.SHUT_WR)
                if broker.resources:broker.resources.close()
                else:broker.proc.wait(timeout=8)
                broker.stream.close();broker.log.close()
                if broker.owner_log:broker.owner_log.close()
                diagnostic=broker.path.read_text()
                self.assertEqual(broker.proc.returncode,2,diagnostic)
                self.assertIn('Workspace participant release remains unresolved',diagnostic)
                for marker in ('AddressSanitizer','LeakSanitizer','runtime error:'):
                    self.assertNotIn(marker,diagnostic)

        self.native_broker_type=OutageBroker

    def test_actual_issuer_exit_fails_closed_and_releases_input_during_pending_info(self):
        import security_pb2 as security
        from harness.native_application_input import NativeRawKeyObserver

        self.configure_outage_broker()
        world, peer, probe, offset = self.pending_info_with_native_input()
        from test_security_identity import NativeClient
        key = probe.wait(lambda row: row.get('event')=='key' and row.get('pressed'), timeout=2)
        button = probe.wait(lambda row: row.get('event')=='button' and row.get('pressed'), timeout=2)
        client = NativeClient(self.native_fixture.display, self.native_fixture.cookie)
        try:
            identity=client.inspect()
            self.assertEqual(identity.role,security.SUPERVISOR)
            self.assertEqual(identity.server_id,self.native_server_id)
            observer=NativeRawKeyObserver(client,probe.xid)
        except BaseException:
            client.close();raise
        self.addCleanup(observer.close)
        binding=observer.controlled_devices()
        self.assertTrue(observer.pointer_button_state(binding['pointer'],button['button']))
        stopped = time.monotonic()
        self.write_observations(dict(issuer_stop_started_monotonic=stopped))
        self.stop_daemons()
        self.assertTrue(all(proc.poll()==0 for proc,_ in self.daemons), 'Actual issuer shutdown was not confirmed')
        events=observer.read_until(lambda rows:any(row['kind']=='key' and row['detail']==key['keycode']
            and not row['pressed'] for row in rows),timeout=1.5)
        released=[row for row in events if row['kind']=='key' and row['detail']==key['keycode'] and not row['pressed']]
        self.assertEqual(len(released),1,released)
        self.assertEqual(released[0]['device'],binding['keyboard'])
        self.assertEqual(binding['devices'].get(released[0]['source']),(4,binding['keyboard']))
        pointer_state=observer.pointer_button_state(binding['pointer'],button['button'])
        deadline=time.monotonic()+1.5
        while pointer_state and time.monotonic()<deadline:
            time.sleep(.01)
            pointer_state=observer.pointer_button_state(binding['pointer'],button['button'])
        self.assertFalse(pointer_state,'Native pointer still reports the button held')
        self.assertFalse(self._info_relay.delay_finished.is_set(),
                         'Issuer-loss cancellation waited for a stale INFO reply')
        before=len(observer.events)
        outcome = None
        try:
            reply = peer.act(key=core.ActorKeyRequest(keysym='a', pressed=True))
            self.assertTrue(reply.HasField('error'), reply)
            self.assertEqual(reply.error.code, core.UNAVAILABLE, reply)
            outcome = 'typed UNAVAILABLE'
        except EOFError:
            world.log.flush()
            diagnostic = Path(world.log.name).read_text()
            self.assertIn('issuer exchange failed', diagnostic)
            self.assertIn('identity service disabled', diagnostic)
            outcome = 'actor EOF with exact private issuer failure diagnostic'
        observer.pointer_button_state(binding['pointer'],button['button'])
        observer.drain()
        self.assertFalse(any(row['kind']=='key' and row['pressed'] for row in observer.events[before:]),observer.events)
        self.capture_issuer_lifetime('issuer_exit_confirmed_no_reconnect_or_input')
        self.write_observations(dict(issuer_stopped_monotonic=stopped, native_release=released,
            pointer_exists=pointer_state is not None,button_held=pointer_state,
            application_exit_code=probe.proc.poll(),denied_action=outcome, issuer_io_budget_seconds=5,
            broker_outer_wait='UNRUN in this native INFO case', info_delays=self._info_relay.delay_records))

    def test_world_issuer_connection_loss_releases_input_and_keeps_workspace_application(self):
        world,peer,probe,offset=self.pending_info_with_native_input()
        before=self.capture_issuer_lifetime('before_world_only_issuer_connection_loss')
        self.assertTrue(all(item['exit_code'] is None for item in before['processes']))
        started=time.monotonic()
        self._info_relay.close()
        released=self.independent_release(probe,offset)
        issuer_peer=self.connect()
        try:
            info=issuer_peer.call(info=issuer.IssuerInfoRequest())
            self.assertTrue(info.HasField('info'),info)
            self.assertEqual(info.info.issuer_id,self.info.issuer_id)
        finally:issuer_peer.close()
        self.assertIsNone(probe.proc.poll(),'Workspace-owned application exited after world-only issuer loss')
        after=self.capture_issuer_lifetime('world_connection_lost_workspace_issuer_alive')
        self.assertTrue(all(item['exit_code'] is None for item in after['processes']))
        outcome='unobserved'
        try:
            reply=peer.act(key=core.ActorKeyRequest(keysym='a',pressed=True))
            self.assertTrue(reply.HasField('error'),reply)
            self.assertEqual(reply.error.code,core.UNAVAILABLE,reply)
            outcome='typed UNAVAILABLE'
        except (EOFError,BrokenPipeError,ConnectionResetError):
            outcome='actor channel closed'
        rows=probe.events[offset:]
        self.assertFalse(any(row.get('event')=='key' and row.get('keysym')==ord('a') and row.get('pressed') for row in rows),rows)
        self.write_observations(dict(stage='world_only_issuer_connection_loss',
            release=released,release_elapsed_seconds=released['observed_monotonic']-started,
            real_issuer_alive=True,application_alive=True,subsequent_action=outcome))

    def test_global_issuer_loss_releases_key_and_clears_pointer_button_state(self):
        import security_pb2 as security
        from harness.native_application_input import NativeRawKeyObserver

        self.configure_outage_broker()
        world,peer,probe=self.held()
        from test_security_identity import NativeClient
        key=probe.wait(lambda row:row.get('event')=='key' and row.get('pressed'),timeout=2)
        button=probe.wait(lambda row:row.get('event')=='button' and row.get('pressed'),timeout=2)
        client=NativeClient(self.native_fixture.display,self.native_fixture.cookie)
        try:
            identity=client.inspect()
            self.assertEqual(identity.role,security.SUPERVISOR)
            self.assertEqual(identity.server_id,self.native_server_id)
            observer=NativeRawKeyObserver(client,probe.xid)
        except BaseException:
            client.close();raise
        self.addCleanup(observer.close)
        binding=observer.controlled_devices()
        self.assertTrue(observer.pointer_button_state(binding['pointer'],button['button']),
            'Independent device query did not confirm the held button before issuer loss')
        self._progress_observations=dict(stage='global_issuer_loss_native_witness',
            keycode=key['keycode'],button=button['button'])
        def retain_observer():
            self.write_observations(dict(native_events=observer.events,
                application_exit_code=probe.proc.poll(),world_exit_code=world.proc.poll()))
        self.addCleanup(retain_observer)
        started=time.monotonic();self.stop_daemons()
        witness_failure=None
        try:
            events=observer.read_until(lambda rows:any(row['kind']=='key' and
                row['detail']==key['keycode'] and not row['pressed'] for row in rows),timeout=1.5)
        except AssertionError as error:
            witness_failure=error;events=list(observer.events)
        pointer_state=observer.pointer_button_state(binding['pointer'],button['button'])
        deadline=time.monotonic()+1.5
        while pointer_state and time.monotonic()<deadline:
            time.sleep(.01)
            pointer_state=observer.pointer_button_state(binding['pointer'],button['button'])
        self.write_observations(dict(native_events=observer.events,application_exit_code=probe.proc.poll(),
            pointer_exists=pointer_state is not None,button_held=pointer_state))
        if witness_failure:raise witness_failure
        self.assertFalse(pointer_state,'Native pointer still reports the button held')
        released=[row for row in events if not row['pressed'] and row['kind']=='key' and row['detail']==key['keycode']]
        self.assertEqual(len(released),1,released)
        for row in released:
            expected=binding['keyboard'];source_use=4
            self.assertEqual(row['device'],expected,row)
            self.assertEqual(binding['devices'].get(row['source']),(source_use,expected),row)
        self.write_observations(dict(native_release_seconds=time.monotonic()-started,
            native_events=events,application_exit_code=probe.proc.poll()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
