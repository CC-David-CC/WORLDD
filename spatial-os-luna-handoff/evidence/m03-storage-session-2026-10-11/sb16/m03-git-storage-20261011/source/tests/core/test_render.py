"""Independent rendering, configuration, authorization, and process counterexamples."""
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import random
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from harness.oracles import PPM, pose, axis_angle, transform
from harness.rpc import Client, RpcError, TransportError

HOST, DAEMON, PROBE = sys.argv[1:4]
sys.argv[1:4] = []
DISPLAY = os.environ['SPATIAL_TEST_DISPLAY']


class RenderRuntimeTests(unittest.TestCase):
    def setUp(self):
        retained = os.environ.get('SPATIAL_RENDER_EVIDENCE')
        if retained:
            self.root = Path(retained) / self._testMethodName
            self.root.mkdir(parents=True, exist_ok=False)
        else:
            temp = tempfile.TemporaryDirectory()
            self.addCleanup(temp.cleanup)
            self.root = Path(temp.name)
        self.state = self.root / 'state'

    def start(self, argv=None, extra_env=None, state=None):
        client = Client(argv or [HOST], state or self.state, self.root / 'logs', timeout=6, extra_env=extra_env)
        self.addCleanup(client.stop)
        client.call('system.hello')
        return client

    def actor(self, client, ident='a', grants=None, pose_=None):
        return client.call('actor.open', {'id': ident, 'grants': ['navigate'] if grants is None else grants,
                                         'pose': pose_ or pose()})

    def panel(self, client, ident='panel', color=(123, 49, 211), pose_=None, size=(2, 2)):
        return client.call('world.create', {'entity': {'id': ident, 'kind': 'panel',
            'label': ident,
            'room': client.call('world.info')['entry_room'], 'pose': pose_ or pose([0, 0, -4]),
            'size': list(size), 'color': list(color)}})

    def capture(self, client, actor, width=128, height=128):
        result = client.call('render.capture', {'token': actor['token'], 'width': width, 'height': height})
        path = Path(result['path']).resolve()
        self.assertTrue(path.is_relative_to(self.state.resolve()))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        return PPM(path), result

    def aim(self, client, actor, seq=1, direction=(0, 0, -1)):
        return client.call('actor.act', {'token': actor['token'], 'seq': seq, 'op': 'aim',
                                         'args': {'direction': list(direction)}})['hit']

    def error(self, code, function, *args, **kwargs):
        with self.assertRaises(RpcError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_visible_drawable_matches_randomized_capture_and_resize(self):
        client = self.start()
        actor = self.actor(client)
        panel = self.panel(client)
        rng = random.Random(593102)
        for width, height in [(127, 95), (256, 128), (81, 137)]:
            color = [rng.randrange(1, 255) for _ in range(3)]
            panel = client.call('world.update', {'id': panel['id'], 'expected_revision': panel['revision'],
                                                  'patch': {'color': color}})
            capture, meta = self.capture(client, actor, width, height)
            self.assertEqual(capture.pixel(width//2, height//2), tuple(color))
            present = client.call('render.present', {'token': actor['token'], 'width': width, 'height': height})
            output = self.root / f'witness-{width}-{height}.ppm'
            subprocess.run([PROBE, '--capture-window', str(present['xid']),
                            '--output', str(output)], env={**os.environ, 'DISPLAY': DISPLAY}, check=True, timeout=5)
            witness = PPM(output)
            self.assertEqual((witness.width, witness.height), (width, height))
            self.assertEqual(witness.data, capture.data)
            self.assertGreater(present['frame_id'], meta['frame_id'])
            self.assertEqual(present['world_revision'], panel['world_revision'])

    def test_rotated_translated_camera_matches_aim(self):
        client = self.start()
        camera = pose([3, -2, 5], axis_angle([0, 1, 0], math.pi/2))
        actor = self.actor(client, pose_=camera)
        self.panel(client, pose_=pose(transform(camera, [.5, .25, -3]), camera['q']), size=(1, 1))
        image, _ = self.capture(client, actor)
        self.assertEqual(image.pixel(75, 59), (123, 49, 211))
        self.assertEqual(image.pixel(40, 59), (10, 12, 16))
        hit = self.aim(client, actor, direction=(.5, .25, -3))
        self.assertEqual(hit['id'], 'panel')
        self.assertAlmostEqual(hit['distance'], math.sqrt(9.3125))
        for value in hit['uv']:
            self.assertAlmostEqual(value, .5)

    def test_coplanar_tie_order_agrees_with_pick(self):
        client = self.start()
        actor = self.actor(client)
        self.panel(client, ident='z-last', color=(1, 2, 3))
        self.panel(client, ident='a-first', color=(4, 5, 6))
        image, _ = self.capture(client, actor)
        self.assertEqual(image.pixel(64, 64), (4, 5, 6))
        self.assertEqual(self.aim(client, actor)['id'], 'a-first')

    def test_half_open_panel_edges_match_pixel_center_rays(self):
        client = self.start()
        actor = self.actor(client)
        self.panel(client, pose_=pose([1/64, -1/64, -2]), size=(1, 1))
        image, _ = self.capture(client, actor)
        for seq, (x, y, inside) in enumerate([(48, 64, True), (80, 64, False),
                                             (64, 48, True), (64, 80, False)], 1):
            hit = self.aim(client, actor, seq, ((x+.5-64)/64, -(y+.5-64)/64, -1))
            self.assertEqual(hit is not None, inside, (x, y, hit))
            self.assertEqual(image.pixel(x, y), (123, 49, 211) if inside else (10, 12, 16), (x, y))

    def test_configuration_controls_fov_background_and_clipping(self):
        config = self.root / 'custom.textproto'
        config.write_text('linear_gain: 1 angular_gain: 1 deadzone: 0.05 input_timeout: 0.25\n'
                          'render { vertical_fov: 1.0471975511965976 near_clip: 1 far_clip: 10 '
                          'background { red: 31 green: 47 blue: 61 } }\n')
        client = self.start([DAEMON, '--display', DISPLAY, '--config', str(config), '--fixture-clock'])
        actor = self.actor(client)
        self.panel(client)
        self.panel(client, ident='too-close', pose_=pose([0, 0, -.5]), color=(5, 6, 7))
        self.panel(client, ident='too-far', pose_=pose([0, 0, -11]), color=(8, 9, 10))
        image, _ = self.capture(client, actor)
        self.assertEqual(image.pixel(40, 64), (123, 49, 211))  # 60 degree FOV, outside a 90 degree panel.
        self.assertEqual(image.pixel(2, 2), (31, 47, 61))
        self.assertEqual(self.aim(client, actor)['id'], 'panel')

    def test_inclusive_clip_planes_and_quad_crossing_camera_plane(self):
        config = self.root / 'clip.textproto'
        config.write_text('linear_gain: 1 angular_gain: 1 deadzone: 0.05 input_timeout: 0.25\n'
                          'render { vertical_fov: 1.5707963267948966 near_clip: 1 far_clip: 10 '
                          'background { red: 10 green: 12 blue: 16 } }\n')
        client = self.start([DAEMON, '--display', DISPLAY, '--config', str(config), '--fixture-clock'])
        actor = self.actor(client)
        panel = self.panel(client, pose_=pose([0, 0, -10]))
        image, _ = self.capture(client, actor)
        self.assertEqual(image.pixel(64, 64), (123, 49, 211))
        self.assertAlmostEqual(self.aim(client, actor)['distance'], 10)
        panel = client.call('world.update', {'id': panel['id'], 'expected_revision': panel['revision'],
                                             'patch': {'pose': pose([0, 0, -1])}})
        image, _ = self.capture(client, actor)
        self.assertEqual(image.pixel(64, 64), (123, 49, 211))
        self.assertAlmostEqual(self.aim(client, actor, 2)['distance'], 1)
        client.call('world.update', {'id': panel['id'], 'expected_revision': panel['revision'],
                    'patch': {'pose': pose([0, 0, -.75], axis_angle([0, 1, 0], math.pi/3)), 'size': [4, 2]}})
        image, _ = self.capture(client, actor)
        self.assertEqual(image.pixel(81, 64), (123, 49, 211))
        self.assertEqual(image.pixel(40, 64), (10, 12, 16))

    def test_graceful_shutdown_releases_graphics_without_sanitizer_errors(self):
        client = self.start([DAEMON, '--display', DISPLAY])
        self.capture(client, self.actor(client))
        pid = client.call('runtime.identities')['spatialwm_pid']
        client.proc.stdin.close()
        self.assertEqual(client.proc.wait(timeout=6), 0)
        self.assertFalse(Path(f'/proc/{pid}').exists())
        stderr = ''.join(p.read_text() for p in (self.root/'logs').glob('stderr-*.log'))
        self.assertNotIn('Sanitizer', stderr)
        self.assertNotIn('runtime error:', stderr)

    def test_invalid_dimensions_tokens_and_aim_do_not_mutate_world(self):
        client = self.start()
        actor = self.actor(client)
        denied = self.actor(client, 'denied', [])
        self.panel(client)
        before = client.call('world.snapshot')
        self.error('UNAUTHORIZED', client.call, 'render.capture', {'token': 'wrong', 'width': 128, 'height': 128})
        for width, height in [(0, 128), (128, 0), (4097, 1), (4096, 4096)]:
            self.error('INVALID_ARGUMENT', client.call, 'render.capture',
                       {'token': actor['token'], 'width': width, 'height': height})
        self.error('FORBIDDEN', self.aim, client, denied)
        self.error('STALE_SEQUENCE', self.aim, client, denied)
        self.error('INVALID_ARGUMENT', self.aim, client, actor, 1, (0, 0, 0))
        self.error('STALE_SEQUENCE', self.aim, client, actor, 1)
        self.assertEqual(self.aim(client, actor, 2)['id'], 'panel')
        self.assertEqual(client.call('world.snapshot'), before)
        interact = self.actor(client, 'interact', ['interact'])
        self.assertEqual(self.aim(client, interact)['id'], 'panel')

    def test_compositor_is_owned_separate_process_and_death_closes_session(self):
        client = self.start()
        ids = client.call('runtime.identities')
        pid = ids['spatialwm_pid']
        self.assertNotEqual(pid, ids['worldd_pid'])
        self.assertEqual(Path(f'/proc/{pid}/exe').resolve(), Path(HOST).parent/'spatialwm')
        self.assertIn(f'PPid:\t{ids["worldd_pid"]}\n', Path(f'/proc/{pid}/status').read_text())
        self.assertEqual(os.getpgid(pid), os.getpgid(client.proc.pid))
        self.assertEqual(ids['display'], DISPLAY)
        os.kill(pid, signal.SIGKILL)
        self.assertNotEqual(client.proc.wait(timeout=5), 0)
        with self.assertRaises(TransportError):
            client.call('system.hello')

    def test_parent_death_reaps_graphics_and_releases_display(self):
        client = self.start()
        pid = client.call('runtime.identities')['spatialwm_pid']
        fd = os.pidfd_open(pid)
        try:
            os.kill(client.proc.pid, signal.SIGKILL)
            client.proc.wait(timeout=5)
            poller = select.poll()
            poller.register(fd, select.POLLIN)
            self.assertTrue(poller.poll(5000), 'compositor outlived parent chain')
        finally:
            os.close(fd)
        replacement = self.start()
        self.assertNotEqual(replacement.call('runtime.identities')['spatialwm_pid'], pid)

    def test_existing_wm_is_not_replaced(self):
        first = self.start()
        ids = first.call('runtime.identities')
        second = Client([HOST], self.root/'other-state', self.root/'other-logs', timeout=5)
        self.addCleanup(second.stop)
        with self.assertRaises(TransportError):
            second.call('system.hello')
        self.assertNotEqual(second.proc.wait(timeout=5), 0)
        self.assertEqual(first.call('runtime.identities'), ids)
        self.capture(first, self.actor(first))

    def test_no_explicit_display_does_not_adopt_environment_display(self):
        client = self.start(extra_env={'SPATIAL_TEST_DISPLAY': '', 'DISPLAY': DISPLAY})
        self.assertNotIn('spatialwm_pid', client.call('runtime.identities'))
        actor = self.actor(client)
        self.error('UNIMPLEMENTED', client.call, 'render.capture',
                   {'token': actor['token'], 'width': 128, 'height': 128})

    def test_real_clock_expires_while_renderer_is_stopped(self):
        client = self.start([DAEMON, '--display', DISPLAY])
        actor = self.actor(client)
        pid = client.call('runtime.identities')['spatialwm_pid']
        client.call('actor.act', {'token': actor['token'], 'seq': 1, 'op': 'input',
                                 'args': {'axes': [1, 0, 0, 0, 0, 0]}})
        os.kill(pid, signal.SIGSTOP)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(self.capture, client, actor)
                time.sleep(.4)
                os.kill(pid, signal.SIGCONT)
                pending.result(timeout=5)
        finally:
            try:
                os.kill(pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
        state = client.call('actor.inspect', {'token': actor['token']})
        self.assertEqual(state['axes'], [0]*6)
        self.assertAlmostEqual(state['pose']['p'][0], .25, places=5)


if __name__ == '__main__':
    unittest.main(verbosity=2)
