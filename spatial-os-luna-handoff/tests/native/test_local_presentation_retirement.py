"""Frame retirement preserves authenticated binding; context retirement removes it."""
import copy
import select
import signal
import time
import unittest
import test_local_presentation_continuation as existing
import core_pb2 as core
import grants_pb2 as grants
import session_probe_pb2 as probe
import world_session_pb2 as session
from harness.wire import frame


class LocalPresentationRetirementTests(existing.LocalPresentationContinuationTests):
    def request(self,expect_error=False,**operation):
        if expect_error is not None:return super().request(expect_error=expect_error,**operation)
        # One async poll can produce either its pending receipt or exact denial.
        self.coordinator_sequence+=1
        request=probe.SessionProbeRequest(protocol_version=1,request_id=self.coordinator_sequence,**operation)
        self.coordinator.stdin.write(frame(request));self.coordinator.stdin.flush()
        reply=self.message(self.coordinator.stdout,probe.SessionProbeReply)
        self.assertEqual((reply.protocol_version,reply.request_id),(1,request.request_id))
        self.assertIn(reply.WhichOneof('outcome'),['local_progress','error'],reply)
        return reply

    def defer(self,*,context=False,identity=None):
        reply=self.request(local_defer_retire=probe.LocalForegroundRetirement(
            identity=identity or self.local_identity,presentation_only=not context))
        self.assertTrue(reply.HasField('local_cleanup'),reply)
        return reply.local_cleanup

    def cleanup(self,*,identity=None,steps=1):
        started=time.monotonic()
        reply=self.request(local_cleanup_poll=probe.LocalForegroundCleanupPoll(
            identity=identity or self.local_identity,maximum_steps=steps))
        self.assertLess(time.monotonic()-started,.25,'A cleanup poll waited for a paused peer')
        self.assertTrue(reply.HasField('local_cleanup'),reply)
        return reply.local_cleanup

    def settled(self,identity=None):
        return self.until(lambda:self.cleanup(identity=identity),lambda value:not value.pending)

    def assert_context_released(self):
        state=self.settled();self.assertFalse(state.attested)
        self.assertEqual(self.pixel(),0)
        self.assertFalse(self.request(inspect=self.owner).owner.observation_watches)
        self.finish_foreground(retired=True)

    def test_presentation_retirement_keeps_binding_watch_and_fresh_presentation(self):
        first=self.show();watch=list(self.request(inspect=self.owner).owner.observation_watches)
        pending=self.defer();self.assertTrue(pending.pending);self.assertTrue(pending.attested)
        self.assertFalse(pending.releases_binding)
        denied=self.request(expect_error=True,local_begin=probe.LocalForegroundPresent(identity=self.local_identity))
        self.assertIn('Local actor retirement remains pending',denied.error.message)
        complete=self.settled();self.assertTrue(complete.attested)
        self.assertEqual(self.pixel(),0)
        self.assertEqual(list(self.request(inspect=self.owner).owner.observation_watches),watch)
        fresh=self.begin();self.assertEqual(fresh.stage,probe.LocalForegroundProgress.SELECT)
        frame=self.until(lambda:self.poll_frame(fresh.serial),lambda value:value.HasField('local_frame')).local_frame
        self.assertTrue(frame.bound);self.assertGreater(frame.frame_id,first.frame_id)
        self.assertEqual(self.pixel(),0x112233)
        self.finish_foreground()

    def test_context_loss_after_frame_retirement_still_releases_retained_binding(self):
        self.show();first=self.defer();self.assertTrue(self.settled().attested)
        context=self.defer(context=True)
        self.assertGreater(context.serial,first.serial);self.assertTrue(context.releases_binding)
        self.assertFalse(context.attested);self.assert_context_released()

    def test_context_upgrade_before_selection_is_monotonic(self):
        self.show();first=self.defer()
        context=self.defer(context=True);self.assertEqual(context.serial,first.serial)
        self.assertTrue(context.releases_binding);self.assertFalse(context.attested)
        lower=self.defer();self.assertTrue(lower.releases_binding);self.assertFalse(lower.attested)
        self.assert_context_released()

    def test_context_upgrade_preserves_pending_native_retirement(self):
        self.show();first=self.defer();self.assertEqual(self.cleanup().stage,probe.LocalForegroundCleanup.NATIVE)
        with self.paused(self.world_ids.spatialwm_pid):
            native=self.cleanup();self.assertEqual(native.stage,probe.LocalForegroundCleanup.NATIVE)
            self.assertTrue(native.pending);self.assertFalse(native.native_retired)
            started=time.monotonic();context=self.defer(context=True)
            self.assertLess(time.monotonic()-started,.25)
            self.assertEqual(context.serial,first.serial);self.assertTrue(context.releases_binding)
        self.assert_context_released()

    def test_context_upgrade_drains_original_world_reply_before_release(self):
        self.show();frame=self.begin();frame=self.reach(frame.serial,probe.LocalForegroundProgress.PRESENT)
        with self.paused(self.world_ids.worldd_pid):
            self.assertTrue(self.poll_frame(frame.serial).local_progress.submitted)
            first=self.defer()
            cleared=self.until(self.cleanup,lambda value:value.stage==probe.LocalForegroundCleanup.WORLD_DRAIN)
            self.assertTrue(cleared.pending);self.assertTrue(cleared.native_retired);self.assertTrue(cleared.attested)
            self.assertEqual(self.pixel(),0)
            context=self.defer(context=True);self.assertEqual(context.serial,first.serial)
            self.assertTrue(context.releases_binding);self.assertFalse(context.attested)
            self.assertTrue(self.cleanup().pending)
        self.assert_context_released()

    def test_failed_bind_stays_unattested_and_cannot_clear_another_view(self):
        self.show();foreign=copy.deepcopy(self.local_identity);foreign.actor_id='unrelated-actor'
        reply=self.request(local_begin=probe.LocalForegroundPresent(identity=foreign))
        serial=reply.local_progress.serial
        denied=self.until(lambda:self.request(expect_error=None,
            local_poll=probe.LocalForegroundPoll(serial=serial,maximum_steps=1)),lambda value:value.HasField('error'))
        self.assertEqual(denied.error.code,core.FORBIDDEN)
        pending=self.defer(identity=foreign);self.assertFalse(pending.attested)
        self.assertFalse(self.settled(foreign).attested)
        self.assertEqual(self.pixel(),0x112233)
        fresh=self.request(local_begin=probe.LocalForegroundPresent(identity=foreign)).local_progress
        self.assertEqual(fresh.stage,probe.LocalForegroundProgress.BIND)
        self.assertTrue(self.request(local_cancel=fresh.serial).local_cancelled)
        self.finish_foreground()

    def test_retained_binding_cannot_restore_revoked_observation(self):
        self.show();self.defer();self.assertTrue(self.settled().attested)
        self.policy_revoke(grants.WORLD_OBSERVE,workspace=False)
        self.assertEqual(self.pixel(),0)
        self.assertFalse(self.request(inspect=self.owner).owner.observation_watches)
        denied=self.foreground(error=True);self.assertEqual(denied.error.code,core.FORBIDDEN)
        self.assertEqual(self.pixel(),0)
        self.assertTrue(self.actor_call(self.stream,inspect=session.WorldSessionInspect()).HasField('state'))
        self.act(input=core.ActorInputRequest(axes=[.2,0,0,0,0,0]));self.act(stop=core.ActorStopRequest())
        self.released();self.finish_foreground()

    def test_lost_compositor_does_not_complete_presentation_cleanup_or_reuse_binding(self):
        self.show();watches=list(self.request(inspect=self.owner).owner.observation_watches)
        self.defer();self.cleanup()
        with self.paused(self.world_ids.spatialwm_pid) as pin:
            self.assertEqual(self.cleanup().stage,probe.LocalForegroundCleanup.NATIVE)
            signal.pidfd_send_signal(pin,signal.SIGKILL);self.assertEqual(select.select([pin],[],[],6)[0],[pin])
        state=self.cleanup();self.assertTrue(state.pending);self.assertFalse(state.native_retired)
        retry=self.defer();self.assertTrue(retry.pending);self.assertFalse(retry.native_retired)
        denied=self.request(expect_error=True,local_begin=probe.LocalForegroundPresent(identity=self.local_identity))
        self.assertIn('Local actor retirement remains pending',denied.error.message)
        self.assertEqual(list(self.request(inspect=self.owner).owner.observation_watches),watches)
        stopped=self.request(expect_error=True,stop_world=self.owner)
        self.assertIn('unconfirmed',stopped.error.message)
        self.finish_session()


if __name__=='__main__':
    names=[name for name in LocalPresentationRetirementTests.__dict__ if name.startswith('test_')]
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(
        unittest.TestSuite(LocalPresentationRetirementTests(name) for name in names)).wasSuccessful())
