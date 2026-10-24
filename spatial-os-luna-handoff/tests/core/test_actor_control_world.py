"""Checked cancellation through real issuer enrollment and authenticated worldd.

These cases prove motion cancellation and authority. Native held-input witnesses
remain separate requirements for the combined M10 gate.
"""
import time
import unittest
import core_pb2 as core
import grants_pb2 as grant
import world_session_pb2 as session
from test_world_identity import WorldIdentityFixture
import test_world_authority as authority


class ActorControlWorldTests(WorldIdentityFixture):
    principal = authority.WorldAuthorityTests.principal
    terms = authority.WorldAuthorityTests.terms
    scoped_world = authority.WorldAuthorityTests.scoped_world

    def opened_world(self, rights=(grant.WORLD_ENTER, grant.WORLD_NAVIGATE)):
        world = self.scoped_world(roots=lambda: [self.terms(rights=rights)])
        return world, world.peers[0]

    def control(self, peer, **action):
        peer.action_sequence += 1
        return peer.call(control_action=core.ActorActionRequest(seq=peer.action_sequence, **action))

    def accepted(self, peer, reply):
        self.assertTrue(reply.HasField('control_action'), reply)
        self.assertEqual(reply.control_action.sequence, peer.action_sequence)
        self.assertTrue(reply.control_action.HasField('accepted'), reply)
        self.assertTrue(reply.control_action.accepted.accepted)

    def cancelled(self, peer, reply, code):
        self.assertTrue(reply.HasField('control_action'), reply)
        self.assertEqual(reply.control_action.sequence, peer.action_sequence)
        self.assertTrue(reply.control_action.HasField('rejected'), reply)
        rejected = reply.control_action.rejected
        self.assertEqual(rejected.error.code, code)
        self.assertTrue(rejected.cancellation_confirmed)

    def test_denied_observation_cancels_live_motion_before_fresh_input(self):
        world, peer = self.opened_world()
        self.accepted(peer, self.control(peer, input=core.ActorInputRequest(axes=[1, 0, 0, 0, 0, 0])))
        active = peer.call(inspect=session.WorldSessionInspect()).state
        self.assertEqual(list(active.axes), [1, 0, 0, 0, 0, 0], 'Motion must still be live before rejection')
        self.cancelled(peer, self.control(peer, observe=core.ActorObserveRequest()), core.FORBIDDEN)
        stopped = peer.call(inspect=session.WorldSessionInspect()).state
        self.assertEqual(list(stopped.axes), [0] * 6)
        time.sleep(.06)
        later = peer.call(inspect=session.WorldSessionInspect()).state
        self.assertEqual(later.pose, stopped.pose)
        self.assertEqual(later.id, active.id)
        self.accepted(peer, self.control(peer, input=core.ActorInputRequest(axes=[0, 1, 0, 0, 0, 0])))
        resumed = peer.call(inspect=session.WorldSessionInspect()).state
        self.assertEqual(list(resumed.axes), [0, 1, 0, 0, 0, 0])
        self.assertEqual(resumed.id, active.id)
        self.accepted(peer, self.control(peer, stop=core.ActorStopRequest()))
        self.assertEqual(world.management('world.info')['world_id'], self.world_info['world_id'])

    def test_navigation_denial_uses_the_same_authority_and_preserves_session(self):
        _, peer = self.opened_world(rights=(grant.WORLD_ENTER,))
        before = peer.call(inspect=session.WorldSessionInspect()).state
        self.cancelled(peer, self.control(peer, input=core.ActorInputRequest(axes=[1, 0, 0, 0, 0, 0])), core.FORBIDDEN)
        self.assertEqual(peer.call(inspect=session.WorldSessionInspect()).state, before)
        self.accepted(peer, self.control(peer, stop=core.ActorStopRequest()))

    def test_persistent_operations_cannot_use_input_cancellation_wrapper(self):
        world, peer = self.opened_world()
        before = world.management('world.snapshot')
        for action in [core.ActorActionRequest(process_launch=core.ProcessLaunchRequest(argv=['/bin/true'], operation_id='forbidden-wrapper')),
                       core.ActorActionRequest(workspace_select=core.WorkspaceIdRequest(id='workspace')),
                       core.ActorActionRequest(object_grab=core.ObjectGrabRequest(id='object'))]:
            peer.action_sequence += 1
            action.seq = peer.action_sequence
            reply = peer.call(control_action=action)
            self.assertTrue(reply.HasField('error'), reply)
            self.assertEqual(reply.error.code, core.INVALID_ARGUMENT)
            self.assertFalse(reply.HasField('control_action'))
        self.assertEqual(world.management('world.snapshot'), before)

    def test_control_action_rejects_caller_tokens_and_stale_sequences(self):
        _, peer = self.opened_world()
        forged = peer.call(control_action=core.ActorActionRequest(token='other-actor-token', seq=1, stop=core.ActorStopRequest()))
        self.assertEqual(forged.error.code, core.INVALID_ARGUMENT)
        self.accepted(peer, self.control(peer, stop=core.ActorStopRequest()))
        replay = peer.call(control_action=core.ActorActionRequest(seq=peer.action_sequence, stop=core.ActorStopRequest()))
        self.assertTrue(replay.HasField('error'), replay)
        self.assertEqual(replay.error.code, core.STALE_SEQUENCE)
        self.assertFalse(replay.HasField('control_action'))

    def test_anonymous_control_channel_cannot_obtain_cancellation_receipt(self):
        world = self.scoped_world(authenticate=False)
        reply = world.peers[0].call(control_action=core.ActorActionRequest(seq=1, stop=core.ActorStopRequest()))
        self.assertTrue(reply.HasField('error'), reply)
        self.assertEqual(reply.error.code, core.UNAUTHORIZED)
        self.assertFalse(reply.HasField('control_action'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
