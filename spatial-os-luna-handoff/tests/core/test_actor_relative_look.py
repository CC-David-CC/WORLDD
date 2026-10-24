"""Relative mouse look through the actual worldd actor-action boundary.

The hand encoder also runs against the M09 schema to reproduce missing support.
It does not import or implement the production rotation function.
"""
from pathlib import Path
import math
import os
import select
import struct
import subprocess
import sys
import tempfile
import time
import unittest

from harness.rpc import Client, RpcError
from harness.wire import pb

DAEMON = sys.argv.pop(1)


def varint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def look_request(token, seq, angles=None):
    action = pb.ActorActionRequest(token=token, seq=seq)
    # Field 31 is ActorRelativeLookRequest; its field 1 is the body angular delta.
    # Presence matters, including a valid all-zero Vec3.
    rotation = b'' if angles is None else b''.join(
        bytes([(index + 1) * 8 + 1]) + struct.pack('<d', value)
        for index, value in enumerate(angles))
    payload = b'' if angles is None else b'\x0a' + varint(len(rotation)) + rotation
    action.ParseFromString(action.SerializeToString() + varint(31 * 8 + 2)
                           + varint(len(payload)) + payload)
    request = pb.Request(protocol_version=1)
    request.actor_act.CopyFrom(action)
    return request


class ActorRelativeLookTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.root = root
        self.client = Client([DAEMON, '--fixture-clock'], root / 'state', root / 'logs')
        self.addCleanup(self.client.stop)
        self.client.call('system.hello')

    def actor(self, name='actor', grants=('navigate',)):
        return self.client.call('actor.open', {'id': name, 'grants': list(grants)})['token']

    def state(self, token):
        return self.client.call('actor.inspect', {'token': token})

    def look(self, token, sequence, angles):
        return self.client.call_message(look_request(token, sequence, angles))

    def rejects(self, code, callback):
        with self.assertRaises(RpcError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def test_body_rotation_changes_only_the_requesting_actor(self):
        token = self.actor()
        other = self.actor('other')
        before = self.state(other)
        self.assertTrue(self.look(token, 1, (0, -math.pi / 2, 0))['accepted'])
        pose = self.state(token)['pose']
        self.assertEqual(pose['p'], [0, 0, 0])
        for actual, expected in zip(pose['q'], [0, -math.sqrt(.5), 0, math.sqrt(.5)]):
            self.assertAlmostEqual(actual, expected, places=12)
        self.assertEqual(self.state(other), before)

    def test_navigation_denial_consumes_sequence_and_preserves_pose(self):
        token = self.actor(grants=())
        before = self.state(token)
        self.rejects('FORBIDDEN', lambda: self.look(token, 1, (.1, 0, 0)))
        self.rejects('STALE_SEQUENCE', lambda: self.look(token, 1, (.1, 0, 0)))
        self.assertEqual(self.state(token), before)

    def test_missing_and_excessive_rotation_have_no_effect(self):
        token = self.actor()
        before = self.state(token)
        for sequence, angles in enumerate((None, (math.pi, 0, 0),
                                           (1.2, 1.2, 0)), start=1):
            self.rejects('INVALID_ARGUMENT', lambda: self.look(token, sequence, angles))
            self.assertEqual(self.state(token), before)
        self.assertTrue(self.look(token, 4, (0, 0, 0))['accepted'])

    def test_nonfinite_bytes_reach_worldd_and_are_rejected_without_rotation(self):
        # Client.call_message deliberately refuses NaN/Inf before transport.
        # This independent raw channel must reach the production decoder instead.
        process = subprocess.Popen([DAEMON, '--fixture-clock', '--state-dir', str(self.root / 'raw')],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   bufsize=0)
        self.addCleanup(process.stderr.close)
        self.addCleanup(process.stdout.close)
        self.addCleanup(process.stdin.close)

        def stop():
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=4)
        self.addCleanup(stop)

        def exact(count, deadline):
            data = bytearray()
            while len(data) < count:
                remaining = deadline - time.monotonic()
                self.assertGreater(remaining, 0, 'Raw worldd reply exceeded absolute deadline')
                self.assertTrue(select.select([process.stdout], [], [], remaining)[0])
                chunk = os.read(process.stdout.fileno(), count - len(data))
                self.assertTrue(chunk, 'Worldd closed the raw channel')
                data.extend(chunk)
            return bytes(data)

        def exchange(request, request_id):
            request.request_id = request_id
            payload = request.SerializeToString()
            process.stdin.write(struct.pack('!I', len(payload)) + payload)
            process.stdin.flush()
            deadline = time.monotonic() + 4
            size, = struct.unpack('!I', exact(4, deadline))
            self.assertGreater(size, 0)
            self.assertLessEqual(size, 1024 * 1024)
            reply = pb.Reply.FromString(exact(size, deadline))
            self.assertEqual(reply.protocol_version, 1)
            self.assertEqual(reply.request_id, request_id)
            return reply

        opened = pb.Request(protocol_version=1)
        opened.actor_open.id = 'raw-actor'
        opened.actor_open.grants.append('navigate')
        reply = exchange(opened, 1)
        self.assertTrue(reply.HasField('actor_opened'), reply)
        token = reply.actor_opened.token
        for request_id, value in enumerate((float('nan'), float('inf'), -float('inf')), start=2):
            rejected = exchange(look_request(token, request_id, (value, 0, 0)), request_id)
            self.assertTrue(rejected.HasField('error'), rejected)
            self.assertEqual(rejected.error.code, pb.INVALID_ARGUMENT)
        inspect = pb.Request(protocol_version=1)
        inspect.actor_inspect.token = token
        inspected = exchange(inspect, 5)
        self.assertTrue(inspected.HasField('actor_state'), inspected)
        state = inspected.actor_state
        self.assertEqual(state.pose.position, pb.Vec3())
        self.assertEqual(state.pose.orientation.w, 1)
        self.assertEqual([state.pose.orientation.x, state.pose.orientation.y, state.pose.orientation.z], [0, 0, 0])

    def test_same_counts_give_same_rotation_at_different_event_rates(self):
        for count in (30, 60, 144):
            token = self.actor(str(count))
            for sequence in range(1, count + 1):
                self.look(token, sequence, (0, -.6 / count, 0))
            rotation = self.state(token)['pose']['q']
            for actual, expected in zip(rotation, [0, -math.sin(.3), 0, math.cos(.3)]):
                self.assertAlmostEqual(actual, expected, places=11)

    def test_relative_look_does_not_refresh_continuous_motion_expiry(self):
        token = self.actor()
        self.client.call('actor.act', {'token': token, 'seq': 1, 'op': 'input',
                                     'args': {'axes': [0, 1, 0, 0, 0, 0]}})
        self.client.call('clock.advance', {'seconds': .2})
        self.look(token, 2, (0, .1, 0))
        self.client.call('clock.advance', {'seconds': .1})
        state = self.state(token)
        self.assertEqual(state['axes'], [0] * 6)
        self.assertAlmostEqual(state['pose']['p'][1], .25)


if __name__ == '__main__':
    unittest.main(verbosity=2)
