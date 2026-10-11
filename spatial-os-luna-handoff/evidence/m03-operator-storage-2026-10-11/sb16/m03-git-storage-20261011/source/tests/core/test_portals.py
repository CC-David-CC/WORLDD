"""Independent portal path, recovery, rendering, and native input witnesses."""
import math
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from harness.oracles import PPM, pose, axis_angle
from harness.rpc import Client, RpcError
from harness.probe import Probe

HOST, DAEMON, PROBE = sys.argv[1:4]
sys.argv[1:4] = []
os.environ['SPATIAL_XPROBE'] = PROBE
DISPLAY = os.environ['SPATIAL_TEST_DISPLAY']


class PortalTests(unittest.TestCase):
    def setUp(self):
        retained = os.environ.get('SPATIAL_RENDER_EVIDENCE')
        if retained:
            self.root = Path(retained)/self._testMethodName
            self.root.mkdir(parents=True, exist_ok=False)
        else:
            temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
            self.root = Path(temp.name)
        config = self.root/'portals.textproto'
        config.write_text('linear_gain: 10 angular_gain: 10 actor_radius: 0.05 '
                          'deadzone: 0.05 input_timeout: 0.25 '
                          'render { vertical_fov: 1.5707963267948966 near_clip: 0.01 far_clip: 1000 '
                          'background { red: 10 green: 12 blue: 16 } }')
        self.argv = [DAEMON, '--fixture-clock', '--display', DISPLAY, '--config', str(config)]
        self.start(); self.seq = {}
        self.room = self.call('world.info')['entry_room']
        self.create('room-b', 'room')

    def start(self):
        self.client = Client(self.argv, self.root/'state', self.root/f'logs-{time.monotonic_ns()}', timeout=8)
        self.addCleanup(self.client.stop)

    def call(self, method, **params):
        return self.client.call(method, params)

    def create(self, ident, kind, **fields):
        if kind in ('room', 'panel'): fields.setdefault('label', ident)
        return self.call('world.create', entity=dict(id=ident, kind=kind, **fields))

    def portal(self, ident='door', room=None, entry=None, destination='room-b', exit=None, size=(2, 2)):
        return self.create(ident, 'portal', room=room or self.room, pose=entry or pose([0, 0, -.5]),
                           size=list(size), destination=dict(room=destination, pose=exit or pose([4, 0, 2])))

    def actor(self, ident='a', placement=None, grants=None):
        actor = self.call('actor.open', id=ident, room=self.room, pose=placement or pose(),
                          grants=['navigate', 'interact'] if grants is None else grants)
        self.seq[actor['token']] = 0
        return actor

    def act(self, actor, op, **args):
        self.seq[actor['token']] += 1
        return self.call('actor.act', token=actor['token'], seq=self.seq[actor['token']], op=op, args=args)

    def state(self, actor):
        return self.call('actor.inspect', token=actor['token'])

    def move(self, actor, seconds=.25, axes=(0, 0, -1, 0, 0, 0)):
        self.act(actor, 'input', axes=list(axes)); self.call('clock.advance', seconds=seconds)
        return self.state(actor)

    def vec(self, actual, expected, tolerance=1e-8):
        self.assertEqual(len(actual), len(expected))
        for a, b in zip(actual, expected): self.assertAlmostEqual(a, b, delta=tolerance)

    def error(self, code, method, **params):
        with self.assertRaises(RpcError) as caught: self.call(method, **params)
        self.assertEqual(caught.exception.code, code)

    def wall(self, low, high, room=None):
        return self.create('wall', 'barrier', room=room or self.room, pose=pose(), bounds=dict(min=low, max=high))

    def test_rotated_entry_maps_remaining_motion_and_body_orientation(self):
        entry_q = axis_angle([0, 1, 0], math.pi/2)
        exit_q = axis_angle([0, 0, 1], math.pi/2)
        self.portal(entry=pose([-.5, 0, 0], entry_q), exit=pose([4, 2, 3], exit_q))
        actor = self.actor(placement=pose([0, 0, -.1], entry_q))
        state = self.move(actor)
        self.assertEqual(state['room'], 'room-b'); self.vec(state['pose']['p'], [4, 2.1, 1])
        self.assertAlmostEqual(abs(sum(a*b for a,b in zip(state['pose']['q'], exit_q))), 1)
        state = self.move(actor, .1, (1, 0, 0, 0, 0, 0)); self.vec(state['pose']['p'], [4, 3.1, 1])

    def test_exact_plane_endpoint_preserves_partitioned_crossing(self):
        self.portal(); actor = self.actor()
        state = self.move(actor, .05)
        self.assertEqual(state['room'], self.room); self.vec(state['pose']['p'], [0, 0, -.5])
        state = self.move(actor, .01)
        self.assertEqual(state['room'], 'room-b'); self.vec(state['pose']['p'], [4, 0, 1.9])

    def test_aperture_edges_are_half_open(self):
        self.portal()
        for i, (x, y, expected) in enumerate([(-1, 0, True), (1, 0, False), (0, 1, True), (0, -1, False)]):
            actor = self.actor(str(i), pose([x, y, 0])); state = self.move(actor)
            self.assertEqual(state['room'], 'room-b' if expected else self.room)

    def test_nearest_portal_wins_independent_of_id_order(self):
        self.create('room-c', 'room')
        self.portal('a-far', entry=pose([0, 0, -1]), destination='room-c')
        self.portal('z-near'); actor = self.actor()
        self.assertEqual(self.move(actor)['room'], 'room-b')

    def test_multiple_portals_consume_one_displacement(self):
        self.create('room-c', 'room'); self.portal()
        self.portal('second', 'room-b', pose([4, 0, 1]), 'room-c', pose([0, 0, 8]))
        state = self.move(self.actor())
        self.assertEqual(state['room'], 'room-c'); self.vec(state['pose']['p'], [0, 0, 7])

    def test_source_wall_prevents_crossing(self):
        self.portal(); self.wall([-3, -3, -.31], [3, 3, -.3]); state = self.move(self.actor())
        self.assertEqual(state['room'], self.room); self.vec(state['pose']['p'], [0, 0, -.25])

    def test_sliding_path_can_cross_a_portal(self):
        self.portal(entry=pose([.25, 0, -.5]))
        self.wall([.3, -3, -3], [.31, 3, 3])
        state = self.move(self.actor(), axes=(1, 0, -1, 0, 0, 0))
        self.assertEqual(state['room'], 'room-b'); self.vec(state['pose']['p'], [4, 0, 0])

    def test_destination_wall_constrains_remaining_displacement(self):
        self.portal(); self.wall([1, -3, 1], [7, 3, 1.01], 'room-b')
        state = self.move(self.actor()); self.assertEqual(state['room'], 'room-b')
        self.vec(state['pose']['p'], [4, 0, 1.06])

    def test_obstructed_landing_stops_before_entry(self):
        self.portal(); self.wall([3, -1, 1.9], [5, 1, 2.1], 'room-b')
        state = self.move(self.actor()); self.assertEqual(state['room'], self.room)
        self.assertGreater(state['pose']['p'][2], -.5); self.assertLess(state['pose']['p'][2], -.499999)
        self.assertEqual(state['axes'], [0]*6)

    def test_zero_distance_cycle_is_bounded(self):
        self.portal(destination=self.room, exit=pose([0, 0, -.5]))
        state = self.move(self.actor()); self.assertEqual(state['room'], self.room)
        self.vec(state['pose']['p'], [0, 0, -.5]); self.assertEqual(state['axes'], [0]*6)

    def test_committed_portal_update_invalidates_cached_mapping(self):
        door = self.portal(); actor = self.actor(); self.move(actor, .01)
        self.call('world.update', id=door['id'], expected_revision=door['revision'],
                  patch=dict(destination=dict(room='room-b', pose=pose([8, 0, 9]))))
        state = self.move(actor, .05); self.assertEqual(state['room'], 'room-b')
        self.vec(state['pose']['p'], [8, 0, 8.9])

    def test_live_actor_prevents_room_deletion_before_checkpoint(self):
        door = self.portal(); self.move(self.actor())
        self.call('world.delete', id=door['id'], expected_revision=door['revision'])
        room = self.call('world.get', id='room-b'); before = self.call('world.snapshot')
        self.error('REVISION_CONFLICT', 'world.delete', id='room-b', expected_revision=room['revision']+1)
        self.error('IN_USE', 'world.delete', id='room-b', expected_revision=room['revision'])
        self.assertEqual(self.call('world.snapshot'), before)

    def test_checkpoint_restores_destination_without_old_session(self):
        self.portal(); actor = self.actor(); state = self.move(actor)
        self.call('world.checkpoint'); self.client.stop(crash=True); self.start()
        self.error('UNAUTHORIZED', 'actor.inspect', token=actor['token'])
        restored = self.actor(); current = self.state(restored)
        self.assertEqual(current['room'], state['room']); self.assertEqual(current['pose'], state['pose'])
        self.assertEqual(current['axes'], [0]*6); self.assertIsNone(current['focus'])

    def test_navigation_grant_is_required_and_crossing_does_not_grant_interaction(self):
        self.portal(); actor = self.actor(grants=[])
        self.error('FORBIDDEN', 'actor.act', token=actor['token'], seq=1, op='input', args=dict(axes=[0,0,-1,0,0,0]))
        self.call('clock.advance', seconds=.25); self.assertEqual(self.state(actor)['room'], self.room)
        walker = self.actor('walker', grants=['navigate']); self.move(walker)
        self.seq[walker['token']] += 1
        self.error('FORBIDDEN', 'actor.act', token=walker['token'], seq=self.seq[walker['token']], op='pointer',
                   args=dict(button=1, pressed=True))

    def test_frame_is_visible_aperture_is_open_and_other_room_is_hidden(self):
        self.portal(entry=pose([0, 0, -2]))
        self.create('foreign', 'panel', room='room-b', pose=pose([4, 0, 0]), size=[1,1], color=[255,0,0])
        actor = self.actor(); before = self.call('runtime.identities')
        visible = self.act(actor, 'observe')['visible']
        self.assertEqual([item['id'] for item in visible], ['door']); self.assertEqual(visible[0]['kind'], 'portal')
        self.assertEqual(visible[0]['size'], [2,2])
        frame = self.call('render.capture', token=actor['token'], width=128, height=128)
        image = PPM(frame['path']); self.assertEqual(image.pixel(64,64), (10,12,16))
        self.assertIn(bytes([80,180,220]), image.data)
        self.assertIsNone(self.act(actor, 'aim', direction=[0,0,-1])['hit'])
        self.assertEqual(self.act(actor, 'aim', direction=[1.04,0,-2])['hit']['id'], 'door')
        self.move(actor); visible = self.act(actor, 'observe')['visible']
        self.assertEqual([item['id'] for item in visible], ['foreign'])
        self.assertEqual(self.call('runtime.identities'), before)

    def test_curved_path_crosses_when_whole_interval_chord_misses(self):
        self.portal(entry=pose([-.16, 0, -.55]), size=(.25, 1))
        state = self.move(self.actor(), .2, (0,0,-1,0,1,0))
        # At z=-.55 the exact radius-one arc has x=sqrt(1-.55^2)-1=-.165.
        # The whole-interval chord has x=-.856 there, outside the aperture.
        self.assertEqual(state['room'], 'room-b')
        self.vec(state['pose']['p'], [4.16 + math.cos(2)-1, 0, 2.55-math.sin(2)], 1e-7)

    def test_portal_marker_is_visible_from_back_without_reverse_activation(self):
        self.portal(entry=pose([0,0,-2]))
        actor = self.actor(placement=pose([0,0,-4], axis_angle([0,1,0], math.pi)))
        visible = self.act(actor, 'observe')['visible']
        self.assertEqual([item['id'] for item in visible], ['door'])
        frame = self.call('render.capture', token=actor['token'], width=128, height=128)
        image = PPM(frame['path']); self.assertEqual(image.pixel(64,64), (10,12,16))
        self.assertIn(bytes([80,180,220]), image.data)
        self.assertEqual(self.move(actor)['room'], self.room)

    def native_probe(self, x):
        probe = Probe(self.root, xi2=True, display=DISPLAY); self.addCleanup(probe.close)
        deadline = time.monotonic()+4
        while True:
            surface = self.call('surface.find', probe_id=probe.id)['surface']
            if surface: break
            if time.monotonic() >= deadline: self.fail('Native surface did not appear')
            time.sleep(.005)
        self.call('surface.place', id=surface['id'], room=self.room, pose=pose([x,0,-4]), size=[2,2])
        return probe, surface

    def test_same_room_portal_still_cancels_previous_interaction(self):
        self.portal(destination=self.room); actor = self.actor(); probe, _ = self.native_probe(0)
        self.act(actor, 'aim', direction=[0,0,-1]); self.act(actor, 'pointer', button=1, pressed=True)
        self.act(actor, 'key', keysym='Control_L', pressed=True)
        state = self.move(actor); self.assertEqual(state['room'], self.room)
        self.vec(state['pose']['p'], [4,0,0]); self.assertIsNone(state['focus'])
        self.assertEqual(state['held_keys'], []); self.assertEqual(state['held_buttons'], [])
        self.call('runtime.fence')
        for event in ['button', 'key']:
            self.assertEqual([e['pressed'] for e in probe.input_events() if e['event']==event], [True,False])

    def test_crossing_releases_native_input_without_disturbing_another_actor(self):
        self.portal(); a, b = self.actor(), self.actor('b')
        probes = [self.native_probe(x) for x in [-2,2]]
        for actor, x in [(a,-2), (b,2)]:
            self.act(actor, 'aim', direction=[x,0,-4]); self.act(actor, 'pointer', button=1, pressed=True)
            self.act(actor, 'key', keysym='Shift_L', pressed=True)
        self.move(a); self.call('runtime.fence')
        state = self.state(a); self.assertIsNone(state['focus']); self.assertEqual(state['held_keys'], [])
        self.assertEqual(state['held_buttons'], []); self.assertEqual(self.state(b)['held_keys'], ['Shift_L'])
        self.assertEqual(self.state(b)['held_buttons'], [1])
        for event in ['button', 'key']:
            self.assertEqual([e['pressed'] for e in probes[0][0].input_events() if e['event']==event], [True,False])
            self.assertEqual([e['pressed'] for e in probes[1][0].input_events() if e['event']==event], [True])
        self.seq[a['token']] += 1
        self.error('NO_TARGET', 'actor.act', token=a['token'], seq=self.seq[a['token']], op='pointer', args=dict(button=1,pressed=True))
        self.act(b, 'key', keysym='b', pressed=True); self.act(b, 'key', keysym='b', pressed=False)
        self.call('runtime.fence')
        self.assertEqual([e['text_hex'] for e in probes[1][0].input_events() if e['event']=='key' and e['text_hex']], ['42','42'])
        # The left application remains alive and another actor can acquire its released lease.
        c = self.actor('c'); self.act(c, 'aim', direction=[-2,0,-4]); self.act(c, 'pointer', button=1, pressed=True)
        self.assertEqual(self.state(c)['focus']['id'], probes[0][1]['id'])


if __name__ == '__main__': unittest.main(verbosity=2)
