"""Cancel production presentation stages against real paused world/compositor processes."""
from contextlib import contextmanager
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import threading
import time
import unittest

import launch_pb2 as launch
import session_probe_pb2 as probe
EXPECTED_SOURCE=Path(sys.argv[1]).resolve(strict=True)
EXPECTED_LAUNCH=Path(sys.argv[2]).resolve(strict=True)
COORDINATOR=Path(sys.argv.pop()).resolve(strict=True)
import test_local_foreground as foreground
PARSED=foreground.baseline.existing.base.baseline
if PARSED.SOURCE!=EXPECTED_SOURCE or PARSED.supervisor.owned.existing.PROBE!=EXPECTED_LAUNCH:
    raise RuntimeError('Foreground fixture shifted its source or launch executable arguments')


class LocalPresentationContinuationTests(foreground.LocalForegroundTests):
    coordinator_probe=COORDINATOR

    def begin(self):
        pin=os.pidfd_open(self.coordinator.pid)
        try:
            self.assertEqual(select.select([pin],[],[],0)[0],[])
            # Production owners are non-dumpable. Inspect their executable without changing that boundary.
            executable=subprocess.check_output(['sudo','-n','readlink','--',
                '/proc/'+str(self.coordinator.pid)+'/exe'],text=True,timeout=5).strip()
            self.assertEqual(Path(executable),COORDINATOR,'Fixture selected a different coordinator executable')
            self.assertEqual(select.select([pin],[],[],0)[0],[])
        finally:os.close(pin)
        reply=self.request(local_begin=probe.LocalForegroundPresent(
            identity=self.local_identity,width=256,height=256))
        self.assertTrue(reply.HasField('local_progress'),reply)
        self.assertFalse(reply.local_progress.submitted)
        return reply.local_progress

    def poll_frame(self,serial,*,steps=1,error=False):
        return self.request(expect_error=error,
            local_poll=probe.LocalForegroundPoll(serial=serial,maximum_steps=steps))

    def reach(self,serial,stage):
        def advance():
            reply=self.poll_frame(serial)
            self.assertTrue(reply.HasField('local_progress'),'Advanced beyond requested stage: '+str(reply))
            return reply.local_progress
        return self.until(advance,lambda value:value.stage==stage and not value.submitted)

    @contextmanager
    def paused(self,pid):
        pin=os.pidfd_open(pid)
        try:
            signal.pidfd_send_signal(pin,signal.SIGSTOP)
            def stopped():
                return next(line.split()[1] for line in Path('/proc/'+str(pid)+'/status').read_text().splitlines()
                    if line.startswith('State:'))
            self.until(stopped,lambda value:value in ('T','t'))
            self.assertEqual(select.select([pin],[],[],0)[0],[])
            yield pin
        finally:
            if not select.select([pin],[],[],0)[0]:signal.pidfd_send_signal(pin,signal.SIGCONT)
            os.close(pin)

    def cancel_with_late_reply(self,stage):
        first=self.show()
        self.assertTrue(self.request(local_retire=self.local_identity).local_retired)
        self.assertEqual(self.pixel(),0)
        self.native_command('paint',51,34,17)
        pending=self.begin();self.assertEqual(pending.stage,probe.LocalForegroundProgress.BIND)
        if stage!=pending.stage:pending=self.reach(pending.serial,stage)
        pid=self.world_ids.spatialwm_pid if stage==probe.LocalForegroundProgress.SELECT else self.world_ids.worldd_pid
        with self.paused(pid) as pin:
            started=time.monotonic();reply=self.poll_frame(pending.serial)
            self.assertLess(time.monotonic()-started,.25,'One nonblocking stage poll waited for its paused peer')
            self.assertEqual(reply.local_progress.stage,stage)
            self.assertTrue(reply.local_progress.submitted)
            self.assertFalse(reply.local_progress.cancelled)
            resumed=threading.Event();resume_lock=threading.Lock();errors=[]
            def resume():
                try:
                    time.sleep(.1)
                    with resume_lock:
                        signal.pidfd_send_signal(pin,signal.SIGCONT);resumed.set()
                except BaseException as error:errors.append(error)
            worker=threading.Thread(target=resume);worker.start()
            try:
                cancelled=self.request(local_cancel=pending.serial)
                with resume_lock:drained_after_resume=resumed.is_set()
            finally:
                worker.join(2)
            self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
            self.assertTrue(cancelled.local_cancelled)
            self.assertTrue(drained_after_resume,'Cancellation abandoned its submitted request instead of draining it')
        allowed=stage==probe.LocalForegroundProgress.PRESENT
        self.assertEqual(self.pixel(),0x332211 if allowed else 0)
        time.sleep(.05)
        self.assertEqual(self.pixel(),0x332211 if allowed else 0,'Cancellation started a later presentation stage')
        fresh=self.begin()
        self.assertGreater(fresh.serial,pending.serial)
        self.assertEqual(fresh.stage,probe.LocalForegroundProgress.BIND)
        stale=self.poll_frame(pending.serial,error=True)
        self.assertIn('Stale local presentation operation',stale.error.message)
        stale=self.request(expect_error=True,local_cancel=pending.serial)
        self.assertIn('Stale local presentation operation',stale.error.message)
        invalid=self.poll_frame(fresh.serial,steps=0,error=True)
        self.assertIn('Invalid local presentation poll budget',invalid.error.message)
        complete=self.until(lambda:self.poll_frame(fresh.serial),lambda value:value.HasField('local_frame')).local_frame
        self.assertEqual(complete.identity,self.local_identity)
        self.assertEqual(complete.frame_id,first.frame_id+(2 if allowed else 1),
            'Cancelled work rendered an unsubmitted stage or replayed a submitted frame')
        self.assertEqual(self.pixel(),0x332211)
        self.finish_foreground()

    def test_cancel_pending_bind_drains_without_selection_or_presentation(self):
        self.cancel_with_late_reply(probe.LocalForegroundProgress.BIND)

    def test_cancel_pending_selection_drains_without_presentation(self):
        self.cancel_with_late_reply(probe.LocalForegroundProgress.SELECT)

    def test_cancel_pending_presentation_discards_receipt_without_replay(self):
        self.cancel_with_late_reply(probe.LocalForegroundProgress.PRESENT)

    def test_cancel_unsubmitted_frame_has_no_effect_and_preserves_next_owner(self):
        first=self.show();self.native_command('paint',51,34,17)
        pending=self.begin()
        self.assertEqual(pending.stage,probe.LocalForegroundProgress.SELECT)
        self.assertTrue(self.request(local_cancel=pending.serial).local_cancelled)
        self.assertEqual(self.pixel(),0x112233)
        self.assertIn('Stale local presentation operation',self.poll_frame(pending.serial,error=True).error.message)
        completed=self.foreground().local_frame
        self.assertEqual(completed.frame_id,first.frame_id+1)
        self.assertEqual(self.pixel(),0x332211)
        self.finish_foreground()

    def stopped_world(self,stage):
        self.show()
        if stage==probe.LocalForegroundProgress.BIND:
            self.assertTrue(self.request(local_retire=self.local_identity).local_retired)
        watches=list(self.request(inspect=self.owner).owner.observation_watches)
        pending=self.begin()
        if stage==probe.LocalForegroundProgress.PRESENT:pending=self.reach(pending.serial,stage)
        self.assertEqual(pending.stage,stage)
        paused_pid=self.world_ids.spatialwm_pid if stage==probe.LocalForegroundProgress.SELECT else self.world_ids.worldd_pid
        with self.paused(paused_pid):
            self.assertTrue(self.poll_frame(pending.serial).local_progress.submitted)
            pin=os.pidfd_open(self.world_ids.worldd_pid)
            try:
                signal.pidfd_send_signal(pin,signal.SIGKILL)
                self.assertEqual(select.select([pin],[],[],6)[0],[pin])
            finally:os.close(pin)
        try:
            stopped=self.request(stop_world=self.owner).cleanup
            self.assertEqual(stopped.stage,launch.OWNED_SCOPES_STOPPED)
            self.assertEqual(stopped.process_stopped.identity,self.world_record.process)
            self.assertEqual(stopped.native_stopped.identity,self.world_record.native)
            self.assertEqual(self.pixel(),0)
            self.assertEqual(list(self.request(inspect=self.owner).owner.observation_watches),watches)
            recovered=self.request(recover_world=self.owner).owner
            self.assertTrue(recovered.channel_fenced)
            self.assertFalse(recovered.observation_watches)
            self.assertFalse(recovered.reservations)
            self.assertEqual(list(recovered.recovery.observation_released),watches)
            self.assertIn('Stale local presentation operation',self.poll_frame(pending.serial,error=True).error.message)
        finally:
            self.finish_session()

    def test_checked_world_stop_drains_pending_selection_before_presentation_recovery(self):
        self.stopped_world(probe.LocalForegroundProgress.SELECT)

    def test_checked_world_stop_retires_pending_bind_without_starting_later_stages(self):
        self.stopped_world(probe.LocalForegroundProgress.BIND)

    def test_checked_world_stop_retires_pending_presentation_and_invalidates_its_ticket(self):
        self.stopped_world(probe.LocalForegroundProgress.PRESENT)

    def test_lost_compositor_keeps_pending_selection_and_observation_obligations_fenced(self):
        self.show();watches=list(self.request(inspect=self.owner).owner.observation_watches)
        self.assertEqual(len(watches),1)
        pending=self.begin();self.assertEqual(pending.stage,probe.LocalForegroundProgress.SELECT)
        with self.paused(self.world_ids.spatialwm_pid) as pin:
            self.assertTrue(self.poll_frame(pending.serial).local_progress.submitted)
            signal.pidfd_send_signal(pin,signal.SIGKILL)
            self.assertEqual(select.select([pin],[],[],6)[0],[pin])
        rejected=self.request(expect_error=True,local_cancel=pending.serial)
        self.assertEqual(rejected.error.message,'Host presentation ownership unavailable')
        rejected=self.poll_frame(pending.serial,error=True)
        self.assertEqual(rejected.error.message,'Local presentation operation was cancelled')
        rejected=self.request(expect_error=True,stop_world=self.owner)
        self.assertEqual(rejected.error.message,'Host presentation ownership unavailable')
        rejected=self.request(expect_error=True,recover_world=self.owner)
        self.assertEqual(rejected.error.message,'Recover broker observations only after checked presentation retirement')
        state=self.request(inspect=self.owner).owner
        self.assertTrue(state.channel_fenced)
        self.assertEqual(list(state.observation_watches),watches)
        self.finish_session()


if __name__=='__main__':
    names=[name for name in LocalPresentationContinuationTests.__dict__ if name.startswith('test_')]
    suite=unittest.TestSuite(LocalPresentationContinuationTests(name) for name in names)
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
