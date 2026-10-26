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
from test_world_identity import WorldIdentityFixture


class WorldIssuerProgressTests(IssuerLifetimeWitness, WorldIdentityFixture):
    def config_file(self, endpoint_port=None, **overrides):
        relay = getattr(self, "_info_relay", None)
        return super().config_file(endpoint_port=endpoint_port or (relay.port if relay else None), **overrides)

    def pending_info_with_native_input(self):
        self.assertIn(socket.gethostname().split(".")[0], ("sb16", "sb16-2"))
        self.assertTrue(os.environ.get("SPATIAL_TEST_DISPLAY"))
        self._info_relay = DelayedInfoIssuer(self)
        native_fd=self.native_compositor_authority()
        world, peer, probe = self.held(native_fd=native_fd)
        self._progress_observations = dict(stage="native_input_held", issuer_io_budget_seconds=5,
            broker_outer_wait="UNRUN in this native INFO case", operation_300_second_deadline="UNRUN")
        def retain_progress():
            self.write_observations(dict(retained_monotonic=time.monotonic(), world_exit_code=world.proc.poll(),
                info_delays=list(self._info_relay.delay_records)))
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
        native_fd=self.native_compositor_authority()
        self.retained_movement_override=True
        self.second_key,self.second_public=self.key('m10-slow-auth-actor')
        self.second_record=self.decide(self.owner_peer,self.enroll(self.owner_peer,
            self.intent(public=self.second_public,label='M10 slow authentication actor'),
            self.second_key,self.second_public)).enrollment
        self.second=self.claims(self.second_record,self.info)
        self._info_relay=DelayedInfoIssuer(self,delayed_operation='credential_check',actor_id=self.second.actor_id)
        world=self.world(grants=['navigate','interact','observe'],native=True,native_fd=native_fd,fixture_faults=True,
            additional_actor_profiles=[(self.second.actor_id,['navigate'])])
        self._progress_observations=dict(stage='other_actor_credential_check_pending',input_timeout_seconds=.25)
        peer,other_peer=world.peers
        opened=self.authenticate(peer)
        self.assertTrue(opened.HasField('opened'))
        self._native_actor_id=opened.opened.actor_id
        probe=self.native_probe(self.root,xi2=False)
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

    def test_other_actor_authentication_does_not_gate_motion_refresh(self):
        import concurrent.futures
        world,peer,other_peer,probe=self.pending_other_actor_auth_with_native_input()
        before=peer.call(inspect=session.WorldSessionInspect()).state
        self.assertEqual(before.axes_size(),6)
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

    def test_actor_revocation_releases_native_input_during_other_actor_authentication(self):
        import concurrent.futures
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
                peer.motion_refresh(movement);refreshes.append(time.monotonic())
                time.sleep(.08)
            moving_pose=world.management('world.get',id=actor_id)['pose']['p'][0]
            self.assertGreater(moving_pose-before_pose,.08,'Actor did not move before revocation')
            peer.motion_refresh(movement)
            last_refresh=time.monotonic()
            self.revoke(kind=issuer.ACTOR_TARGET)
            revoke_ack=time.monotonic()
            released=self.independent_release(probe,offset)
            still_pending=not self._info_relay.delay_finished.is_set()
            stopped_pose=world.management('world.get',id=actor_id)['pose']['p'][0]
            time.sleep(.12)
            final_pose=world.management('world.get',id=actor_id)['pose']['p'][0]
            self.assertTrue(still_pending,'Other actor authentication finished before revocation was observed')
            self.assertLess(revoke_ack-last_refresh,.25,'Revocation exceeded the native input expiry window')
            self.assertLess(released['observed_monotonic']-last_refresh,.25,
                'Native input release arrived only after ordinary movement expiry')
            self.assertEqual(stopped_pose,final_pose,'Revoked actor continued moving after native release')
            self.assertTrue(self._info_relay.delay_finished.wait(6),'Other actor authentication did not recover')
            authenticated=authentication.result(timeout=3)
        self.assertTrue(authenticated.HasField('opened'),authenticated)
        self.write_observations(dict(stage='actor_revocation_during_other_actor_authentication',
            actor_moved_before_revocation=moving_pose-before_pose,
            refresh_count=len(refreshes)+1,
            last_refresh_to_revoke_ack_seconds=revoke_ack-last_refresh,
            last_refresh_to_native_release_seconds=released['observed_monotonic']-last_refresh,
            native_release=released,other_actor_authentication_pending_at_release=still_pending,
            unrelated_actor_authenticated=authenticated.opened.actor_id,
            pose_stable_after_release=stopped_pose==final_pose))
        self.assertIsNone(world.proc.poll(),'World failed while processing actor revocation')
        self.assertIsNone(probe.proc.poll(),'Native input witness exited during revocation')
        world.close();self.assertEqual(world.proc.returncode,0)

    def test_motion_refresh_progresses_while_native_renderer_is_slow(self):
        self.retained_movement_override=True
        marker=self.root/'renderer-input-delay-entered'
        environment={
            'SPATIAL_TEST_RENDER_INPUT_DELAY_MS':'1000',
            'SPATIAL_TEST_RENDER_INPUT_DELAY_COMMAND':'3',
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
        self.assertTrue(self._info_relay.delay_finished.is_set())
        self.assertGreaterEqual(elapsed,4.0)
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

    def test_actual_issuer_exit_fails_closed_and_releases_input_during_pending_info(self):
        world, peer, probe, offset = self.pending_info_with_native_input()
        stopped = time.monotonic()
        self.write_observations(dict(issuer_stop_started_monotonic=stopped))
        self.stop_daemons()
        self.assertTrue(all(proc.poll()==0 for proc,_ in self.daemons), "Actual issuer shutdown was not confirmed")
        released = self.independent_release(probe, offset)
        self.assertFalse(self._info_relay.delay_finished.is_set(),
                         "Issuer-loss cancellation waited for a stale INFO reply")
        after = len(probe.events)
        outcome = None
        try:
            reply = peer.act(key=core.ActorKeyRequest(keysym="a", pressed=True))
            self.assertTrue(reply.HasField("error"), reply)
            self.assertEqual(reply.error.code, core.UNAVAILABLE, reply)
            outcome = "typed UNAVAILABLE"
        except EOFError:
            world.log.flush()
            diagnostic = Path(world.log.name).read_text()
            self.assertIn("issuer exchange failed", diagnostic)
            self.assertIn("identity service disabled", diagnostic)
            outcome = "actor EOF with exact private issuer failure diagnostic"
        events = probe.input_events(after=after)
        self.assertFalse(any(row["event"] in ("key","button") and row["pressed"] for row in events), events)
        self.capture_issuer_lifetime("issuer_exit_confirmed_no_reconnect_or_input")
        self.write_observations(dict(issuer_stopped_monotonic=stopped, release=released,
            denied_action=outcome, issuer_io_budget_seconds=5,
            broker_outer_wait="UNRUN in this native INFO case", info_delays=self._info_relay.delay_records))


if __name__ == "__main__":
    unittest.main(verbosity=2)
