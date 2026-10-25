"""Actual native Stop completes while an independent issuer reply remains held."""
import time
import unittest
import core_pb2 as core
import test_world_issuer_progress as existing


class ActorStopProgressTests(existing.WorldIssuerProgressTests):
    def checked_native_stop(self,controlled):
        self.retained_movement_override=False
        world,peer,probe,offset=self.pending_info_with_native_input()
        peer.action_sequence+=1
        action=core.ActorActionRequest(seq=peer.action_sequence,stop=core.ActorStopRequest())
        started=time.monotonic();reply=peer.call(**{'control_action' if controlled else 'action':action})
        elapsed=time.monotonic()-started
        if controlled:self.assertTrue(reply.control_action.accepted.accepted,reply)
        else:self.assertTrue(reply.action.accepted,reply)
        self.assertLess(elapsed,.35,'Native Stop waited for the unrelated issuer reply')
        self.assertFalse(self._info_relay.delay_finished.is_set())
        released=self.independent_release(probe,offset)
        self.assertFalse(self._info_relay.delay_finished.is_set(),'Actual native release waited for issuer completion')
        self.write_observations(dict(stop_elapsed_seconds=elapsed,controlled=controlled,
            issuer_still_pending=True,independent_native_release=released))
        self.assertTrue(self._info_relay.delay_finished.wait(5))
        world.close();self.assertEqual(world.proc.returncode,0)

    def test_native_stop_releases_input_during_issuer_wait(self):self.checked_native_stop(False)
    def test_native_controlled_stop_releases_input_during_issuer_wait(self):self.checked_native_stop(True)

    def test_stop_retires_queued_capture_motion_and_world_exits_cleanly(self):
        self.retained_movement_override=False
        world,peer,probe,offset=self.pending_info_with_native_input()
        moved=peer.act(input=core.ActorInputRequest(axes=[1,0,0,0,0,0]))
        self.assertTrue(moved.HasField('action') and moved.action.accepted,moved)
        self.assertTrue(self._info_relay.delay_finished.is_set())
        stopped=peer.act(stop=core.ActorStopRequest())
        self.assertTrue(stopped.HasField('action') and stopped.action.accepted,stopped)
        released=self.independent_release(probe,offset)
        state=peer.call(inspect=existing.session.WorldSessionInspect()).state
        self.assertEqual(list(state.axes),[0]*6)
        self.assertFalse(state.held_keys);self.assertFalse(state.held_buttons)
        self.write_observations(dict(queued_capture_motion_retired=True,independent_native_release=released))
        world.close();self.assertEqual(world.proc.returncode,0)


if __name__=='__main__':
    names=[name for name in ActorStopProgressTests.__dict__ if name.startswith('test_')]
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(
        unittest.TestSuite(ActorStopProgressTests(name) for name in names)).wasSuccessful())
