"""Real adapter proposals and real worldd must keep keyboard motion level."""
import io
import math
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

import control_adapter_pose_pb2 as fixture
import control_pb2 as controls
from harness.rpc import Client, RpcError
from harness.wire import pb, frame, read_message

DAEMON, PROBE = sys.argv[1:3]
sys.argv[1:3] = []


class ControlLevelMotionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.client = Client([DAEMON, '--fixture-clock'], root / 'state', root / 'logs')
        self.addCleanup(self.client.stop)
        self.token = self.client.call('actor.open', {'id': 'human', 'grants': ['navigate']})['token']
        self.sequence = 0

    def action(self, action):
        self.sequence += 1
        request = pb.Request(protocol_version=1)
        request.actor_act.CopyFrom(action)
        request.actor_act.token = self.token
        request.actor_act.seq = self.sequence
        return self.client.call_message(request)

    def tilt(self):
        action = pb.ActorActionRequest()
        action.relative_look.angular_delta.x = math.pi / 3
        self.action(action)

    def adapter(self, events=(), profiles=None):
        state = self.client.call('actor.inspect', {'token': self.token})
        request = fixture.AdapterPoseRequest(protocol_version=1, request_id=17)
        request.state.id = state['id']
        request.state.room = state['room']
        p, q = state['pose']['p'], state['pose']['q']
        request.state.pose.position.CopyFrom(pb.Vec3(x=p[0], y=p[1], z=p[2]))
        request.state.pose.orientation.CopyFrom(pb.Quaternion(x=q[0], y=q[1], z=q[2], w=q[3]))
        request.state.axes.extend(state['axes'])
        if profiles is not None:
            request.profiles.CopyFrom(profiles)
        request.events.extend(events)
        result = subprocess.run([PROBE], input=frame(request), capture_output=True, timeout=8, check=True)
        reply = read_message(io.BytesIO(result.stdout), fixture.AdapterPoseReply)
        self.assertEqual(reply.protocol_version, 1)
        self.assertEqual(reply.request_id, 17)
        return reply

    @staticmethod
    def connection(source, kind):
        return controls.DeviceEvent(protocol_version=1, source_id=source, source_generation=1,
                                    event_sequence=1, connected=controls.DeviceConnected(kind=kind))

    def apply_adapter_input(self, reply):
        inputs = [action for action in reply.actions if action.HasField('input')]
        self.assertEqual(len(inputs), 1, 'Expected one real DeviceAdapters motion proposal')
        self.assertFalse(inputs[0].token, 'Adapter fixture must not manufacture actor tokens')
        self.action(inputs[0])
        return inputs[0]

    def calibrated_profiles(self):
        profiles = self.adapter().profiles
        for profile in profiles.profiles:
            profile.spacemouse_calibrated = True
            for axis in range(6):
                profile.spacemouse_axes.add(raw_axis=axis + 1, full_scale=100)
        return profiles

    def test_pitched_keyboard_forward_stays_level_after_authoritative_deadzone(self):
        self.tilt()
        events = [self.connection('keyboard', controls.KEYBOARD),
                  controls.DeviceEvent(protocol_version=1, source_id='keyboard', source_generation=1,
                                       event_sequence=2, key=controls.DeviceKey(keysym='w', pressed=True))]
        motion = self.apply_adapter_input(self.adapter(events))
        self.assertEqual(motion.input.translation_frame, pb.LEVEL_TRANSLATION)
        self.client.call('clock.advance', {'seconds': .2})
        state = self.client.call('actor.inspect', {'token': self.token})
        self.assertAlmostEqual(state['pose']['p'][1], 0, places=12)
        self.assertLess(state['pose']['p'][2], -.1)

    def test_spacemouse_retains_body_frame_translation_in_level_keyboard_mode(self):
        self.tilt()
        profiles = self.calibrated_profiles()
        events = [self.connection('spacemouse', controls.SPACEMOUSE),
                  controls.DeviceEvent(protocol_version=1, source_id='spacemouse', source_generation=1,
                                       event_sequence=2, axes=controls.DeviceAxes(raw=[0, 0, -100, 0, 0, 0]))]
        motion = self.apply_adapter_input(self.adapter(events, profiles))
        self.assertEqual(motion.input.translation_frame, pb.BODY_TRANSLATION)
        self.client.call('clock.advance', {'seconds': .2})
        state = self.client.call('actor.inspect', {'token': self.token})
        self.assertAlmostEqual(state['pose']['p'][1], math.sqrt(3) * .1, places=12)
        self.assertAlmostEqual(state['pose']['p'][2], -.1, places=12)

    def test_level_diagonal_keeps_both_independent_axis_magnitudes(self):
        self.tilt()
        events = [self.connection('keyboard', controls.KEYBOARD),
                  controls.DeviceEvent(protocol_version=1, source_id='keyboard', source_generation=1,
                                       event_sequence=2, key=controls.DeviceKey(keysym='w', pressed=True)),
                  controls.DeviceEvent(protocol_version=1, source_id='keyboard', source_generation=1,
                                       event_sequence=3, key=controls.DeviceKey(keysym='d', pressed=True))]
        self.apply_adapter_input(self.adapter(events))
        self.client.call('clock.advance', {'seconds': .2})
        position = self.client.call('actor.inspect', {'token': self.token})['pose']['p']
        for actual, expected in zip(position, [.2, 0, -.2]):
            self.assertAlmostEqual(actual, expected, places=12)

    def test_vertical_view_has_finite_level_heading(self):
        action = pb.ActorActionRequest()
        action.relative_look.angular_delta.x = math.pi / 2
        self.action(action)
        events = [self.connection('keyboard', controls.KEYBOARD),
                  controls.DeviceEvent(protocol_version=1, source_id='keyboard', source_generation=1,
                                       event_sequence=2, key=controls.DeviceKey(keysym='w', pressed=True))]
        self.apply_adapter_input(self.adapter(events))
        self.client.call('clock.advance', {'seconds': .2})
        position = self.client.call('actor.inspect', {'token': self.token})['pose']['p']
        self.assertTrue(all(math.isfinite(value) for value in position))
        self.assertAlmostEqual(position[1], 0, places=12)
        self.assertLess(position[2], -.1)

    def test_keyboard_translation_stays_level_during_spacemouse_pitch(self):
        self.tilt()
        before = self.client.call('actor.inspect', {'token': self.token})['pose']['q']
        events = [self.connection('keyboard', controls.KEYBOARD), self.connection('spacemouse', controls.SPACEMOUSE),
                  controls.DeviceEvent(protocol_version=1, source_id='keyboard', source_generation=1,
                                       event_sequence=2, key=controls.DeviceKey(keysym='w', pressed=True)),
                  controls.DeviceEvent(protocol_version=1, source_id='spacemouse', source_generation=1,
                                       event_sequence=2, axes=controls.DeviceAxes(raw=[0, 0, 0, 100, 0, 0]))]
        motion = self.apply_adapter_input(self.adapter(events, self.calibrated_profiles()))
        self.assertEqual(motion.input.translation_frame, pb.LEVEL_TRANSLATION)
        self.client.call('clock.advance', {'seconds': .2})
        state = self.client.call('actor.inspect', {'token': self.token})
        self.assertAlmostEqual(state['pose']['p'][1], 0, places=12)
        self.assertLess(state['pose']['p'][2], -.1)
        self.assertNotEqual(state['pose']['q'], before)

    def test_invalid_frame_does_not_replace_input_or_refresh_its_expiry(self):
        valid = pb.ActorActionRequest(input=pb.ActorInputRequest(axes=[0, 1, 0, 0, 0, 0]))
        self.action(valid)
        self.client.call('clock.advance', {'seconds': .2})
        invalid = pb.ActorActionRequest(input=pb.ActorInputRequest(axes=[1, 0, 0, 0, 0, 0], translation_frame=99))
        with self.assertRaises(RpcError) as caught:
            self.action(invalid)
        self.assertEqual(caught.exception.code, 'INVALID_ARGUMENT')
        self.client.call('clock.advance', {'seconds': .1})
        state = self.client.call('actor.inspect', {'token': self.token})
        self.assertEqual(state['axes'], [0] * 6)
        self.assertAlmostEqual(state['pose']['p'][1], .25)


if __name__ == '__main__':
    unittest.main(verbosity=2)
