"""Actor input counterexamples using production worldd and independent X clients."""
import ctypes
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from harness.oracles import pose, axis_angle
from harness.probe import Probe
from harness.rpc import Client, RpcError

HOST, DAEMON, PROBE = sys.argv[1:4]
sys.argv[1:4] = []
os.environ['SPATIAL_XPROBE'] = PROBE
DISPLAY = os.environ['SPATIAL_TEST_DISPLAY']


class ActorInputTests(unittest.TestCase):
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
        self.addCleanup(self.stop)
        self.seq = {}
        self.room = self.call('world.info')['entry_room']
        self.x = ctypes.CDLL('libX11.so.6')
        self.x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.x.XOpenDisplay.restype = ctypes.c_void_p
        self.x.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self.x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.x.XUnmapWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.x.XSetTransientForHint.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
        self.display = self.x.XOpenDisplay(DISPLAY.encode())
        self.assertTrue(self.display)
        self.addCleanup(self.x.XCloseDisplay, self.display)
        self.xi = ctypes.CDLL('libXi.so.6')
        self.xi.XIQueryDevice.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        self.xi.XIQueryDevice.restype = ctypes.c_void_p
        self.xi.XIFreeDeviceInfo.argtypes = [ctypes.c_void_p]

    def stop(self):
        try:
            if self.client.proc.poll() is None:
                self.client.proc.stdin.close()
                self.assertEqual(self.client.proc.wait(timeout=8), 0)
        finally:
            self.client.stop()

    def call(self, method, **args):
        return self.client.call(method, args)

    def actor(self, name='a', grants=None):
        actor = self.call('actor.open', id=name, grants=['interact'] if grants is None else grants)
        self.seq[actor['token']] = 0
        return actor

    def act(self, actor, op, **args):
        self.seq[actor['token']] += 1
        return self.call('actor.act', token=actor['token'], seq=self.seq[actor['token']], op=op, args=args)

    def error(self, code, fn, *args, **kwargs):
        with self.assertRaises(RpcError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def until(self, fn, check):
        end = time.monotonic() + 4
        while True:
            value = fn()
            if check(value):
                return value
            if time.monotonic() > end:
                self.fail(f'Native condition timed out: {value}')
            time.sleep(.005)

    def probe(self, position=(0, 0, -4)):
        probe = Probe(self.root, xi2=True, display=DISPLAY)
        self.addCleanup(probe.close)
        surface = self.until(lambda: self.call('surface.find', probe_id=probe.id),
                             lambda result: result['surface'] is not None)['surface']
        self.call('surface.place', id=surface['id'], room=self.room, pose=pose(position), size=[2, 2])
        return probe, surface

    def aim(self, actor, direction=(0, 0, -1)):
        return self.act(actor, 'aim', direction=list(direction))['hit']

    def click(self, actor):
        self.act(actor, 'pointer', button=1, pressed=True)
        self.act(actor, 'pointer', button=1, pressed=False)

    def state(self, actor):
        return self.call('actor.inspect', token=actor['token'])

    def events(self, probe):
        self.call('runtime.fence')
        return probe.input_events()

    def device_count(self):
        count = ctypes.c_int()
        devices = self.xi.XIQueryDevice(self.display, 0, ctypes.byref(count))
        self.assertTrue(devices)
        self.xi.XIFreeDeviceInfo(devices)
        return count.value

    def test_denied_input_precedes_target_and_consumes_sequence(self):
        actor = self.actor(grants=[])
        for op, args in [('pointer', {'button': 1, 'pressed': True}),
                         ('key', {'keysym': 'a', 'pressed': True}), ('scroll', {'steps_y': 1})]:
            self.error('FORBIDDEN', self.act, actor, op, **args)
            self.error('STALE_SEQUENCE', self.call, 'actor.act', token=actor['token'],
                       seq=self.seq[actor['token']], op='stop', args={})
        self.act(actor, 'unfocus')
        self.act(actor, 'stop')
        self.assertIsNone(self.state(actor)['focus'])

    def test_missing_fields_and_unknown_keys_leave_native_state_unchanged(self):
        actor = self.actor()
        probe, _ = self.probe()
        self.aim(actor)
        for args in ({'button': 1}, {'pressed': True}, {'button': 0, 'pressed': True}):
            self.error('INVALID_ARGUMENT', self.act, actor, 'pointer', **args)
        self.error('INVALID_ARGUMENT', self.act, actor, 'scroll', steps_y=65)
        self.act(actor, 'scroll', steps_y=0)
        self.assertEqual(self.events(probe), [])
        self.click(actor)
        before = self.state(actor)
        for args in ({'keysym': 'a'}, {'pressed': True}, {'keysym': 'NoSuchSpatialKey', 'pressed': True}):
            self.error('INVALID_ARGUMENT', self.act, actor, 'key', **args)
        self.assertEqual(self.state(actor), before)
        self.assertEqual([e for e in self.events(probe) if e['event'] == 'key'], [])

    def test_changed_placement_or_occluder_invalidates_saved_aim(self):
        actor = self.actor()
        probe, surface = self.probe()
        self.aim(actor)
        self.call('surface.place', id=surface['id'], room=self.room, pose=pose((3, 0, -4)), size=[2, 2])
        self.error('STALE_TARGET', self.act, actor, 'pointer', button=1, pressed=True)
        self.call('surface.place', id=surface['id'], room=self.room, pose=pose((0, 0, -4)), size=[2, 2])
        self.aim(actor)
        other, _ = self.probe((0, 0, -3))
        self.error('STALE_TARGET', self.act, actor, 'pointer', button=1, pressed=True)
        for target in (probe, other):
            self.assertEqual([e for e in self.events(target) if e['event'] == 'button'], [])

    def test_focus_transfer_releases_old_modifier_before_new_client_input(self):
        actor = self.actor()
        left, _ = self.probe((-2, 0, -4))
        right, rs = self.probe((2, 0, -4))
        self.aim(actor, (-2, 0, -4)); self.click(actor)
        self.act(actor, 'key', keysym='Control_L', pressed=True)
        self.aim(actor, (2, 0, -4)); self.click(actor)
        self.act(actor, 'key', keysym='b', pressed=True)
        self.act(actor, 'key', keysym='b', pressed=False)
        self.assertEqual([e['pressed'] for e in self.events(left) if e['event'] == 'key'], [True, False])
        self.assertEqual([e['text_hex'] for e in self.events(right) if e['event'] == 'key' and e['pressed']], ['62'])
        self.assertEqual(self.state(actor)['focus']['id'], rs['id'])
        self.assertEqual(self.state(actor)['held_keys'], [])

    def test_unfocus_does_not_release_other_actors_modifiers_or_lease(self):
        a, b = self.actor('a'), self.actor('b')
        left, _ = self.probe((-2, 0, -4))
        right, _ = self.probe((2, 0, -4))
        for actor, direction in ((a, (-2, 0, -4)), (b, (2, 0, -4))):
            self.aim(actor, direction); self.click(actor)
            self.act(actor, 'key', keysym='Shift_L', pressed=True)
        self.act(a, 'pointer', button=2, pressed=True)
        self.act(a, 'unfocus'); self.act(a, 'unfocus')
        self.assertEqual(self.state(a)['held_buttons'], [])
        self.assertIsNone(self.state(a)['focus'])
        self.assertEqual(self.state(b)['held_keys'], ['Shift_L'])
        self.act(b, 'key', keysym='b', pressed=True); self.act(b, 'key', keysym='b', pressed=False)
        self.aim(a, (2, 0, -4))
        self.error('BUSY', self.act, a, 'pointer', button=1, pressed=True)
        self.assertEqual([e['text_hex'] for e in self.events(right) if e['event'] == 'key' and e['text_hex']], ['42', '42'])
        self.assertEqual([e['pressed'] for e in self.events(left) if e['event'] == 'key'], [True, False])

    def test_transient_and_parent_share_interim_control_lease(self):
        a, b = self.actor('a'), self.actor('b')
        parent, surface = self.probe()
        self.aim(a); self.click(a)
        child, child_surface = self.probe((3, 0, -4))
        self.x.XSetTransientForHint(self.display, child.xid, parent.xid)
        self.x.XSync(self.display, 0)
        self.until(lambda: self.call('surface.get', id=child_surface['id']),
                   lambda result: result.get('parent_id') == surface['id'])
        self.assertEqual(self.aim(b)['id'], child_surface['id'])
        self.error('BUSY', self.act, b, 'pointer', button=1, pressed=True)
        self.act(a, 'unfocus'); self.click(b)
        self.assertEqual(self.state(b)['focus']['id'], child_surface['id'])
        self.assertEqual([e['pressed'] for e in self.events(child) if e['event'] == 'button'], [True, False])

    def test_unmap_cancels_native_held_state_and_invalidates_aim(self):
        actor = self.actor()
        probe, _ = self.probe()
        self.aim(actor); self.act(actor, 'pointer', button=1, pressed=True)
        self.act(actor, 'key', keysym='Shift_L', pressed=True)
        self.x.XUnmapWindow(self.display, probe.xid); self.x.XSync(self.display, 0)
        self.call('runtime.fence')
        state = self.state(actor)
        self.assertEqual(state['held_keys'], []); self.assertEqual(state['held_buttons'], [])
        self.assertIsNone(state['focus'])
        self.error('STALE_TARGET', self.act, actor, 'pointer', button=1, pressed=True)

    def test_scroll_cannot_retarget_a_held_drag(self):
        actor = self.actor()
        left, _ = self.probe((-2, 0, -4))
        right, _ = self.probe((2, 0, -4))
        self.aim(actor, (-2, 0, -4)); self.act(actor, 'pointer', button=1, pressed=True)
        self.aim(actor, (2, 0, -4))
        self.error('BUSY', self.act, actor, 'scroll', steps_y=1)
        self.act(actor, 'pointer', button=1, pressed=False)
        self.assertEqual([e['button'] for e in self.events(left) if e['event'] == 'button'], [1, 1])
        self.assertEqual([e for e in self.events(right) if e['event'] == 'button'], [])

    def test_actor_close_reclaims_native_devices_and_revokes_old_tokens(self):
        probe, _ = self.probe()
        baseline = self.device_count()
        for index in range(40):
            actor = self.actor('reconnect')
            self.aim(actor); self.click(actor)
            self.assertGreater(self.device_count(), baseline)
            self.act(actor, 'key', keysym='Shift_L', pressed=True)
            self.call('actor.close', token=actor['token'])
            self.assertEqual(self.device_count(), baseline, index)
            self.error('UNAUTHORIZED', self.act, actor, 'key', keysym='a', pressed=True)
        keys = [e['pressed'] for e in self.events(probe) if e['event'] == 'key']
        self.assertEqual(keys, [True, False] * 40)

    def test_drag_motion_keeps_signed_pixels_on_original_surface(self):
        actor = self.actor()
        probe, surface = self.probe()
        other, _ = self.probe((2, -2, -4))
        self.aim(actor, (-.5, .5, -4))
        self.act(actor, 'pointer', button=1, pressed=True)
        self.events(probe)
        mark = len(probe.events)
        for direction, expected in [((2, -2, -4), (480, 360)),
                                    ((-2, 2, -4), (-160, -120)),
                                    ((.25, -.5, -4), (200, 180))]:
            self.aim(actor, direction)
            self.call('runtime.fence')
            motion = [event for event in probe.input_events(mark) if event['event'] == 'motion']
            self.assertTrue(motion, 'Captured aim must produce a real native motion event')
            self.assertEqual((motion[-1]['x'], motion[-1]['y']), expected)
            self.assertEqual(motion[-1]['xid'], probe.xid)
            mark = len(probe.events)
        self.act(actor, 'pointer', button=1, pressed=False)
        buttons = [event for event in self.events(probe) if event['event'] == 'button']
        self.assertEqual([event['pressed'] for event in buttons], [True, False])
        self.assertEqual((buttons[-1]['x'], buttons[-1]['y']), (200, 180))
        self.assertEqual(self.state(actor)['focus']['id'], surface['id'])
        self.assertEqual([event for event in self.events(other) if event['event'] in ('motion', 'button')], [])

    def test_capture_projection_uses_camera_and_surface_rotations(self):
        import math
        actor = self.call('actor.open', id='rotated', grants=['interact'],
                          pose=pose(q=axis_angle((0, 0, 1), -math.pi/2)))
        self.seq[actor['token']] = 0
        probe, surface = self.probe()
        self.call('surface.place', id=surface['id'], room=self.room,
                  pose=pose((0, 0, -4), axis_angle((0, 0, 1), math.pi/2)), size=[4, 2])
        self.aim(actor); self.act(actor, 'pointer', button=1, pressed=True)
        self.aim(actor, (3, 2, -4))
        events = [event for event in self.events(probe) if event['event'] == 'motion']
        self.assertTrue(events)
        self.assertAlmostEqual(events[-1]['x'], -80, delta=1)
        self.assertAlmostEqual(events[-1]['y'], 360, delta=1)

    def test_additional_buttons_and_unprojectable_aim_preserve_capture_position(self):
        actor = self.actor()
        probe, _ = self.probe()
        self.aim(actor); self.act(actor, 'pointer', button=1, pressed=True)
        self.aim(actor, (2, -2, -4))
        self.act(actor, 'pointer', button=2, pressed=True)
        self.act(actor, 'pointer', button=1, pressed=False)
        for direction in ((1, 0, 0), (0, 0, 1)):
            self.aim(actor, direction)
        self.error('INVALID_ARGUMENT', self.aim, actor, (1, 0, -1e-9))
        self.assertEqual(self.state(actor)['held_buttons'], [2])
        self.act(actor, 'stop')
        events = [event for event in self.events(probe) if event['event'] == 'button']
        self.assertEqual([(event['button'], event['pressed']) for event in events],
                         [(1, True), (2, True), (1, False), (2, False)])
        self.assertEqual([(event['x'], event['y']) for event in events[1:]], [(480, 360)] * 3)

    def test_body_motion_updates_the_captured_ray_before_clock_reply(self):
        actor = self.actor(grants=['navigate', 'interact'])
        probe, _ = self.probe()
        self.aim(actor); self.act(actor, 'pointer', button=1, pressed=True)
        self.act(actor, 'input', axes=[1, 0, 0, 0, 0, 0])
        self.call('clock.advance', seconds=.1)
        events = [event for event in probe.input_events() if event['event'] == 'motion']
        self.assertTrue(events, 'Changing camera pose must update the captured application pointer')
        self.assertAlmostEqual(events[-1]['x'], 176, delta=1)
        self.assertAlmostEqual(events[-1]['y'], 120, delta=1)
        self.act(actor, 'stop')

    def test_realtime_capture_moves_without_queries_and_stops_at_deadline(self):
        self.stop()
        self.client = Client([DAEMON, '--display', DISPLAY], self.root/'realtime-state',
                             self.root/'realtime-logs', timeout=8)
        self.room = self.call('world.info')['entry_room']
        actor = self.actor(grants=['navigate', 'interact'])
        probe, _ = self.probe()
        self.aim(actor); self.act(actor, 'pointer', button=1, pressed=True)
        self.events(probe); mark = len(probe.events)
        self.act(actor, 'input', axes=[1, 0, 0, 0, 0, 0])
        event = probe.wait(lambda value: value['event'] == 'motion' and value['x'] > 160, after=mark)
        self.assertEqual(event['xid'], probe.xid)
        time.sleep(.3)
        self.assertEqual(self.state(actor)['axes'], [0] * 6)
        events = [value for value in self.events(probe) if value['event'] == 'motion']
        self.assertAlmostEqual(events[-1]['x'], 200, delta=1)
        self.act(actor, 'stop')


if __name__ == '__main__':
    unittest.main(verbosity=2)
