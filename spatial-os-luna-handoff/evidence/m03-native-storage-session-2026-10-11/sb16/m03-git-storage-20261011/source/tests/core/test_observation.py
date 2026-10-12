"""Observation counterexamples against real rendering and independent native clients."""
from collections import Counter
import ctypes
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from harness.oracles import PPM, pose, axis_angle, transform
from harness.probe import Probe
from harness.rpc import Client, RpcError
from harness.wire import pb

HOST, DAEMON, PROBE = sys.argv[1:4]
sys.argv[1:4] = []
os.environ['SPATIAL_XPROBE'] = PROBE
DISPLAY = os.environ['SPATIAL_TEST_DISPLAY']


class ObservationTests(unittest.TestCase):
    def setUp(self):
        retained = os.environ.get('SPATIAL_RENDER_EVIDENCE')
        if retained:
            self.root = Path(retained) / self._testMethodName
            self.root.mkdir(parents=True, exist_ok=False)
        else:
            temp = tempfile.TemporaryDirectory()
            self.addCleanup(temp.cleanup)
            self.root = Path(temp.name)
        self.client = Client([HOST], self.root/'state', self.root/'logs', timeout=8)
        self.addCleanup(self.client.stop)
        self.room = self.call('world.info')['entry_room']
        self.actor = self.call('actor.open', id='observer', pose=pose(), grants=['navigate', 'interact'])
        self.sequence = 0

    def call(self, method, **params):
        return self.client.call(method, params)

    def act(self, operation, **args):
        self.sequence += 1
        return self.call('actor.act', token=self.actor['token'], seq=self.sequence, op=operation, args=args)

    def observe(self, width=127, height=95):
        return self.act('observe', width=width, height=height)

    def panel(self, ident, position=(0, 0, -3), size=(2, 2), color=(101, 53, 217), room=None, placement=None):
        return self.call('world.create', entity=dict(id=ident, kind='panel', room=room or self.room,
                         pose=placement or pose(position), size=list(size), color=list(color), label='private fixture label'))

    def capture(self, actor=None, width=127, height=95):
        frame = self.call('render.capture', token=(actor or self.actor)['token'], width=width, height=height)
        return PPM(frame['path']), frame

    def verify_pixels(self, observation, colors):
        image, frame = self.capture(width=observation['viewport'][0], height=observation['viewport'][1])
        counts = Counter(tuple(image.data[i:i+3]) for i in range(0, len(image.data), 3))
        expected = {ident for ident, color in colors.items() if counts[color]}
        self.assertEqual({item['id'] for item in observation['visible']}, expected)
        self.assertEqual(observation['world_revision'], frame['world_revision'])
        for item in observation['visible']:
            self.assertEqual(item['visible_pixels'], counts[colors[item['id']]])
            self.assertEqual(image.pixel(*item['sample']), colors[item['id']])
            self.assertEqual(self.act('aim', direction=item['direction'])['hit']['id'], item['id'])

    def error(self, code, function, *args, **kwargs):
        with self.assertRaises(RpcError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def until(self, function, predicate):
        end = time.monotonic() + 5
        while True:
            value = function()
            if predicate(value):
                return value
            if time.monotonic() >= end:
                self.fail(f'Condition did not complete: {value}')
            time.sleep(.005)

    def native(self):
        probe = Probe(self.root, display=DISPLAY)
        self.addCleanup(probe.close)
        surface = self.until(lambda: self.call('surface.find', probe_id=probe.id), lambda x: x['surface'])['surface']
        surface = self.call('surface.place', id=surface['id'], room=self.room, pose=pose([0, 0, -3]), size=[2, 2])
        return probe, surface

    def test_room_frustum_facing_and_clip_filter_match_pixels(self):
        colors = {'front': (11, 53, 107), 'outside': (71, 101, 149), 'behind': (83, 191, 97),
                  'backface': (193, 31, 109), 'near': (97, 29, 239), 'far': (17, 179, 241)}
        self.panel('front', color=colors['front'])
        self.panel('outside', position=(30, 0, -3), color=colors['outside'])
        self.panel('behind', position=(0, 0, 3), color=colors['behind'])
        self.panel('backface', placement=pose([0, 0, -2], axis_angle([0, 1, 0], math.pi)), color=colors['backface'])
        self.panel('near', position=(0, 0, -.005), color=colors['near'])
        self.panel('far', position=(0, 0, -1001), size=(2000, 2000), color=colors['far'])
        other = self.call('world.create', entity=dict(id='other-room', kind='room', label='other'))
        self.panel('foreign', room=other['id'], position=(0, 0, -1))
        observation = self.observe()
        self.assertEqual([v['id'] for v in observation['visible']], ['front'])
        self.verify_pixels(observation, colors)

    def test_partial_and_union_occlusion_use_all_visible_pixels(self):
        colors = {'rear': (13, 89, 151), 'left': (37, 191, 71), 'right': (227, 59, 113)}
        self.panel('rear', position=(0, 0, -4), size=(4, 4), color=colors['rear'])
        self.panel('left', position=(-.5, 0, -2), size=(1, 2), color=colors['left'])
        first = self.observe()
        self.assertEqual({v['id'] for v in first['visible']}, {'rear', 'left'})
        self.verify_pixels(first, colors)
        self.panel('right', position=(.5, 0, -2), size=(1, 2), color=colors['right'])
        second = self.observe()
        self.assertEqual({v['id'] for v in second['visible']}, {'left', 'right'})
        self.verify_pixels(second, colors)

    def test_coplanar_tie_uses_render_order(self):
        colors = {'z-later': (11, 47, 197), 'a-first': (151, 17, 109)}
        for ident, color in colors.items():
            self.panel(ident, color=color)
        observation = self.observe()
        self.assertEqual([v['id'] for v in observation['visible']], ['a-first'])
        self.verify_pixels(observation, colors)

    def test_rotated_camera_non_square_views_and_thin_sliver(self):
        self.call('actor.close', token=self.actor['token'])
        camera = pose([3, -2, 5], axis_angle([0, 1, 0], math.pi/2))
        self.actor = self.call('actor.open', id='rotated', pose=camera, grants=['navigate'])
        colors = {'side': (67, 131, 229), 'sliver': (19, 113, 59)}
        self.panel('side', placement=pose(transform(camera, [3, 0, -3]), camera['q']), size=(1, 1), color=colors['side'])
        self.panel('sliver', placement=pose(transform(camera, [.3, .2, -2]), camera['q']), size=(.05, .6), color=colors['sliver'])
        for width, height in [(256, 96), (96, 256), (127, 95)]:
            observation = self.observe(width, height)
            self.assertEqual(observation['pose'], camera)
            self.verify_pixels(observation, colors)

    def test_session_sequence_and_viewport_validation_preserve_state(self):
        self.panel('front')
        before = self.call('world.snapshot')
        self.error('UNAUTHORIZED', self.call, 'actor.act', token='unknown', seq=1, op='observe', args={})
        for args in [dict(width=1), dict(width=0, height=5), dict(width=4097, height=1), dict(width=4096, height=4096)]:
            self.error('INVALID_ARGUMENT', self.act, 'observe', **args)
        with self.assertRaises(ValueError):
            self.act('observe', actor='different')
        self.act('observe')
        self.error('STALE_SEQUENCE', self.call, 'actor.act', token=self.actor['token'], seq=self.sequence, op='observe', args={})
        request = pb.Request(protocol_version=1)
        request.actor_act.token = self.actor['token']
        request.actor_act.seq = self.sequence + 1
        request.actor_act.observe.ParseFromString(b'\x18\x01')
        self.error('INVALID_ARGUMENT', self.client.call_message, request)
        self.assertEqual(self.call('world.snapshot'), before)
        self.call('actor.close', token=self.actor['token'])
        self.error('UNAUTHORIZED', self.act, 'observe')

    def test_observation_does_not_grant_control_or_refresh_motion(self):
        self.panel('front')
        stranger = self.call('actor.open', id='observer-only', grants=[], pose=pose())
        observed = self.call('actor.act', token=stranger['token'], seq=1, op='observe', args={})
        self.assertEqual(observed['viewport'], [512, 512])
        self.assertEqual([x['id'] for x in observed['visible']], ['front'])
        self.assertNotIn('label', observed['visible'][0])
        self.assertNotIn('xid', observed['visible'][0])
        self.error('FORBIDDEN', self.call, 'actor.act', token=stranger['token'], seq=2, op='pointer', args=dict(button=1, pressed=True))
        self.act('input', axes=[1, 0, 0, 0, 0, 0])
        for _ in range(5):
            self.call('clock.advance', seconds=.1)
            self.observe()
        info = self.call('actor.inspect', token=self.actor['token'])
        self.assertAlmostEqual(info['pose']['p'][0], .25)
        self.assertEqual(info['axes'], [0]*6)
        self.assertIsNone(info['focus'])

    def test_native_surface_visibility_generation_and_action_agree(self):
        probe, surface = self.native()
        observation = self.observe()
        item, = observation['visible']
        self.assertEqual((item['id'], item['generation'], item['pixels']), (surface['id'], surface['generation'], surface['pixels']))
        self.assertEqual(item['kind'], 'surface')
        hit = self.act('aim', direction=item['direction'])['hit']
        mark = len(probe.events)
        self.act('pointer', button=1, pressed=True)
        self.act('pointer', button=1, pressed=False)
        event = probe.wait(lambda e: e['event']=='button' and e['pressed'], after=mark)
        self.assertEqual([event['x'], event['y']], hit['pixel'])
        self.assertEqual(self.call('actor.inspect', token=self.actor['token'])['focus']['id'], surface['id'])
        probe.command('resize', 211, 137)
        self.until(lambda: self.call('surface.get', id=surface['id']), lambda x: x['pixels']==[211, 137])
        changed = self.observe()['visible'][0]
        self.assertGreater(changed['generation'], item['generation'])
        self.assertEqual(changed['pixels'], [211, 137])
        probe.command('unmap')
        self.until(lambda: self.call('surface.get', id=surface['id']), lambda x: not x['mapped'])
        self.assertEqual(self.observe()['visible'], [])

    def test_native_surface_is_hidden_by_world_geometry(self):
        probe, surface = self.native()
        cover = self.panel('cover', position=(0, 0, -2), size=(2, 2))
        self.assertEqual([v['id'] for v in self.observe()['visible']], ['cover'])
        self.call('world.delete', id=cover['id'], expected_revision=cover['revision'])
        self.assertEqual([v['id'] for v in self.observe()['visible']], [surface['id']])

    def test_observation_keeps_other_actor_presentation_even_after_expose(self):
        self.panel('front', color=(199, 71, 31))
        human = self.call('actor.open', id='human-view', pose=pose([20, 0, 0]), grants=[])
        expected, _ = self.capture(human, 81, 137)
        frame = self.call('render.present', token=human['token'], width=81, height=137)
        observation = self.observe(233, 107)
        self.assertEqual([v['id'] for v in observation['visible']], ['front'])
        lib = ctypes.CDLL('libX11.so.6')
        lib.XOpenDisplay.argtypes = [ctypes.c_char_p]; lib.XOpenDisplay.restype = ctypes.c_void_p
        lib.XClearArea.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_int]
        lib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        display = lib.XOpenDisplay(DISPLAY.encode())
        self.assertTrue(display)
        try:
            lib.XClearArea(display, frame['xid'], 0, 0, 0, 0, 1)
            lib.XSync(display, 0)
        finally:
            lib.XCloseDisplay(display)
        self.call('runtime.fence')
        output = self.root/'presentation.ppm'
        subprocess.run([PROBE, '--capture-window', str(frame['xid']), '--output', str(output)], env={**os.environ, 'DISPLAY': DISPLAY}, check=True, timeout=5)
        witness = PPM(output)
        self.assertEqual((witness.width, witness.height), (81, 137))
        self.assertEqual(witness.data, expected.data)


if __name__ == '__main__':
    unittest.main(verbosity=2)
