"""Real-client surface lifecycle counterexamples outside the locked acceptance suite."""
from concurrent.futures import ThreadPoolExecutor
import ctypes
import math
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from harness.oracles import PPM, pose, axis_angle, transform
from harness.probe import Probe
from harness.rpc import Client, RpcError

HOST, DAEMON, PROBE = sys.argv[1:4]
sys.argv[1:4] = []
os.environ['SPATIAL_XPROBE'] = PROBE
DISPLAY = os.environ['SPATIAL_TEST_DISPLAY']


def transient(child, parent):
    """Publish an actual X property, without involving a production test hook."""
    lib = ctypes.CDLL('libX11.so.6')
    lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    lib.XOpenDisplay.restype = ctypes.c_void_p
    lib.XSetTransientForHint.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
    lib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    display = lib.XOpenDisplay(DISPLAY.encode())
    if not display:
        raise RuntimeError('Cannot open the disposable test display')
    try:
        lib.XSetTransientForHint(display, child, parent)
        lib.XSync(display, 0)
    finally:
        lib.XCloseDisplay(display)


class SurfaceRuntimeTests(unittest.TestCase):
    def setUp(self):
        retained = os.environ.get('SPATIAL_RENDER_EVIDENCE')
        if retained:
            self.root = Path(retained) / self._testMethodName
            self.root.mkdir(parents=True, exist_ok=False)
        else:
            temp = tempfile.TemporaryDirectory()
            self.addCleanup(temp.cleanup)
            self.root = Path(temp.name)
        self.sequence = 0

    def start(self, direct=False):
        argv = [DAEMON, '--display', DISPLAY] if direct else [HOST]
        self.client = Client(argv, self.root/'state', self.root/'logs', timeout=8)
        client = self.client
        def stop():
            # Wait for the owned WM to release X ownership before the next test.
            # A killed WM may still intercept a later client's MapRequest briefly.
            try:
                if client.proc.poll() is None:
                    client.proc.stdin.close()
                    self.assertEqual(client.proc.wait(timeout=8), 0)
            finally:
                client.stop()
        self.addCleanup(stop)
        self.room = self.call('world.info')['entry_room']
        self.actor = self.call('actor.open', id='observer', pose=pose(), grants=['navigate'])

    def call(self, method, **params):
        return self.client.call(method, params)

    def native(self):
        probe = Probe(self.root, display=DISPLAY)
        self.addCleanup(probe.close)
        return probe

    def until(self, function, predicate):
        end = time.monotonic() + 5
        while True:
            value = function()
            if predicate(value):
                return value
            if time.monotonic() >= end:
                self.fail(f'Native condition did not complete: {value}')
            time.sleep(.005)

    def register(self, probe, position=(0, 0, -4), size=(2, 2)):
        result = self.until(lambda: self.call('surface.find', probe_id=probe.id),
                            lambda x: x['surface'] is not None)['surface']
        self.assertEqual(result['xid'], probe.xid)
        return self.place(result, pose(position), size)

    def place(self, surface, placement, size=(2, 2)):
        return self.call('surface.place', id=surface['id'], room=self.room, pose=placement, size=list(size))

    def capture(self):
        result = self.call('render.capture', token=self.actor['token'], width=128, height=128)
        return PPM(result['path'])

    def aim(self):
        self.sequence += 1
        return self.call('actor.act', token=self.actor['token'], seq=self.sequence, op='aim',
                         args={'direction': [0, 0, -1]})['hit']

    def stats(self, live):
        self.call('runtime.fence')
        resources = self.call('runtime.resources')
        self.assertEqual(resources['live_pixmaps'], live)
        self.assertEqual(resources['retired_pixmaps_pending'], 0)
        self.assertEqual(resources['invalid_texture_bindings'], 0)

    def error(self, code, method, **params):
        with self.assertRaises(RpcError) as caught:
            self.call(method, **params)
        self.assertEqual(caught.exception.code, code)

    def slot(self, ident='slot', position=(0, 0, -4), size=(2, 2)):
        return self.call('world.create', entity=dict(id=ident, kind='surface_slot', label=ident,
                         room=self.room, pose=pose(position), size=list(size)))

    def attach(self, surface, slot):
        return self.call('surface.attach_slot', id=surface['id'], slot=slot['id'])

    def test_slot_attachment_is_exclusive_idempotent_and_survives_resize_and_unmap(self):
        self.start(); slot = self.slot()
        first, second = self.native(), self.native()
        a, b = self.register(first), self.register(second, (4, 0, -4))
        before = self.call('world.snapshot')
        attached = self.attach(a, slot)
        self.assertEqual(attached['slot'], slot['id'])
        self.assertEqual(attached['pose'], slot['pose'])
        self.assertEqual(self.attach(a, slot), attached)
        self.error('BUSY', 'surface.attach_slot', id=b['id'], slot=slot['id'])
        self.assertEqual(self.call('surface.get', id=b['id']), b)
        first.command('resize', 413, 317); first.command('unmap')
        unmapped = self.call('surface.get', id=a['id'])
        self.assertFalse(unmapped['mapped']); self.assertEqual(unmapped['slot'], slot['id'])
        self.error('BUSY', 'surface.attach_slot', id=b['id'], slot=slot['id'])
        first.command('map')
        remapped = self.call('surface.get', id=a['id'])
        self.assertTrue(remapped['mapped']); self.assertGreater(remapped['generation'], attached['generation'])
        self.assertEqual(remapped['pose'], slot['pose']); self.assertEqual(remapped['size'], slot['size'])
        self.assertEqual(self.call('world.snapshot'), before)
        first.command('destroy'); self.call('runtime.fence')
        self.error('NOT_FOUND', 'surface.get', id=a['id'])
        self.assertEqual(self.attach(b, slot)['slot'], slot['id'])
        self.assertEqual(self.call('world.snapshot'), before)

    def test_slot_is_the_single_placement_for_surface_and_world_edits(self):
        self.start(); slot = self.slot()
        probe = self.native(); surface = self.register(probe, (5, 0, -4))
        geometry = lambda: {k: v for k, v in probe.geometry().items() if k not in ('seq', 'event')}
        native_before = geometry()
        probe.command('paint', 61, 139, 203)
        self.attach(surface, slot)
        self.assertEqual(self.capture().pixel(64, 64), (61, 139, 203))
        self.assertEqual(self.aim()['id'], surface['id'])
        moved = self.place(surface, pose([4, 2, -5]), (3, 1))
        stored = self.call('world.get', id=slot['id'])
        self.assertEqual(stored['revision'], slot['revision']+1)
        self.assertEqual(stored['world_revision'], slot['world_revision']+1)
        for field in ('room', 'pose', 'size'):
            self.assertEqual(stored[field], moved[field])
        self.assertEqual(self.capture().pixel(64, 64), (10, 12, 16))
        changed = self.call('world.update', id=slot['id'], expected_revision=stored['revision'],
                            patch=dict(pose=pose([0, 0, -6]), size=[4, 3]))
        current = self.call('surface.get', id=surface['id'])
        for field in ('room', 'pose', 'size'):
            self.assertEqual(current[field], changed[field])
        self.assertEqual(self.capture().pixel(64, 64), (61, 139, 203))
        self.assertEqual(self.aim()['id'], surface['id'])
        self.assertEqual(geometry(), native_before)
        self.assertEqual(current['generation'], surface['generation'])

    def test_rapid_native_lifecycle_retains_slot_and_identity_until_destroy(self):
        self.start(); slot = self.slot()
        probe = self.native(); surface = self.register(probe)
        self.attach(surface, slot)
        generation = surface['generation']
        for cycle in range(64):
            # Resize is redirected to the WM. Unmap can overtake its native inspections.
            probe.command('resize', 301+cycle%29, 211+cycle%23)
            probe.command('unmap')
            current = self.call('surface.get', id=surface['id'])
            self.assertFalse(current['mapped']); self.assertEqual(current['slot'], slot['id'])
            self.assertGreater(current['generation'], generation)
            probe.command('map')
            current = self.call('surface.get', id=surface['id'])
            self.assertTrue(current['mapped']); self.assertEqual(current['slot'], slot['id'])
            self.assertEqual(current['pose'], slot['pose'])
            generation = current['generation']
        probe.command('paint', 79, 151, 223)
        self.assertEqual(self.capture().pixel(64, 64), (79, 151, 223))
        self.assertEqual(self.call('world.get', id=slot['id']), slot)

    def test_slot_rebinding_and_invalid_attachments_do_not_change_durable_state(self):
        self.start(); a, b = self.slot('a'), self.slot('b', (3, 1, -5))
        first, second = self.native(), self.native()
        surface, other = self.register(first), self.register(second)
        attached = self.attach(surface, a); before = self.call('world.snapshot')
        for ident, target, code in [(surface['id'], self.room, 'INVALID_ARGUMENT'),
                                   (surface['id'], 'missing', 'NOT_FOUND'),
                                   ('missing', a['id'], 'NOT_FOUND'),
                                   ('', a['id'], 'INVALID_ARGUMENT'),
                                   (surface['id'], '', 'INVALID_ARGUMENT')]:
            self.error(code, 'surface.attach_slot', id=ident, slot=target)
            self.assertEqual(self.call('surface.get', id=surface['id']), attached)
            self.assertEqual(self.call('world.snapshot'), before)
        rebound = self.attach(surface, b)
        self.assertEqual(rebound['pose'], b['pose'])
        self.assertEqual(self.attach(other, a)['slot'], a['id'])
        self.assertEqual(self.call('world.snapshot'), before)

    def test_slot_placement_survives_restart_without_reusing_live_attachment(self):
        self.start(); slot = self.slot()
        probe = self.native(); surface = self.register(probe)
        self.attach(surface, slot)
        self.place(surface, pose([2, 1, -5]), (3, 2))
        durable = self.call('world.snapshot')
        stored = self.call('world.get', id=slot['id'])
        old = self.client
        old.proc.stdin.close(); self.assertEqual(old.proc.wait(timeout=8), 0); old.stop()
        self.start()
        current = self.until(lambda: self.call('surface.find', probe_id=probe.id),
                             lambda value: value['surface'] is not None)['surface']
        self.assertEqual(current['xid'], surface['xid'])
        self.assertNotEqual(current['id'], surface['id'])
        self.assertIsNone(current['slot']); self.assertEqual(current['room'], '')
        self.error('NOT_FOUND', 'surface.get', id=surface['id'])
        self.assertEqual(self.call('world.snapshot'), durable)
        rebound = self.attach(current, slot)
        for field in ('room', 'pose', 'size'):
            self.assertEqual(rebound[field], stored[field])
        self.assertEqual(self.call('world.snapshot'), durable)

    def test_slot_delete_removes_placement_without_destroying_native_application(self):
        self.start(); slot = self.slot()
        probe = self.native(); surface = self.register(probe)
        probe.command('paint', 91, 173, 207)
        self.attach(surface, slot)
        self.error('REVISION_CONFLICT', 'world.delete', id=slot['id'], expected_revision=slot['revision']+1)
        self.assertEqual(self.call('surface.get', id=surface['id'])['slot'], slot['id'])
        self.call('world.delete', id=slot['id'], expected_revision=slot['revision'])
        current = self.call('surface.get', id=surface['id'])
        self.assertIsNone(current['slot']); self.assertEqual(current['room'], '')
        self.assertTrue(current['mapped']); self.assertIsNone(probe.proc.poll())
        self.assertEqual(self.capture().pixel(64, 64), (10, 12, 16))
        replacement = self.slot()  # Equal spelling cannot restore the removed live association.
        self.assertIsNone(self.call('surface.get', id=surface['id'])['slot'])
        self.attach(current, replacement)
        self.assertEqual(self.capture().pixel(64, 64), (91, 173, 207))

    def test_failed_durable_surface_move_preserves_slot_pixels_and_live_placement(self):
        self.start(); slot = self.slot()
        probe = self.native(); surface = self.register(probe)
        probe.command('paint', 43, 103, 181)
        attached = self.attach(surface, slot); before = self.call('world.snapshot')
        self.call('fault.arm', point='storage.next_write', action='error', errno='ENOSPC')
        self.error('STORAGE_ERROR', 'surface.place', id=surface['id'], room=self.room, pose=pose([4, 0, -5]), size=[3, 2])
        self.assertEqual(self.call('world.snapshot'), before)
        self.assertEqual(self.call('surface.get', id=surface['id']), attached)
        self.assertEqual(self.capture().pixel(64, 64), (43, 103, 181))
        self.place(surface, pose([4, 0, -5]), (3, 2))
        self.assertEqual(self.call('world.get', id=slot['id'])['revision'], slot['revision']+1)
        self.assertEqual(self.capture().pixel(64, 64), (10, 12, 16))

    def test_concurrent_slot_attachment_has_one_winner(self):
        self.start(); slot = self.slot()
        first, second = self.native(), self.native()
        surfaces = [self.register(first), self.register(second)]
        def attach(surface):
            try: return self.attach(surface, slot)
            except RpcError as error: return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attach, surfaces))
        self.assertEqual(sum(result == 'BUSY' for result in results), 1)
        winner = next(result for result in results if isinstance(result, dict))
        self.assertEqual(winner['slot'], slot['id'])
        self.assertEqual(sum(self.call('surface.get', id=s['id'])['slot'] == slot['id'] for s in surfaces), 1)

    def test_transients_follow_slotted_parent_and_cannot_hold_an_independent_slot(self):
        self.start(); slot, spare = self.slot(), self.slot('spare')
        parent, second = self.native(), self.native()
        parent_surface, child = self.register(parent), self.register(second)
        self.attach(parent_surface, slot); self.attach(child, spare)
        transient(second.xid, parent.xid)
        current = self.call('surface.get', id=child['id'])
        self.assertEqual(current['parent_id'], parent_surface['id']); self.assertIsNone(current['slot'])
        self.error('INVALID_ARGUMENT', 'surface.attach_slot', id=child['id'], slot=spare['id'])
        third = self.native(); replacement = self.register(third, (6, 0, -4))
        self.assertEqual(self.attach(replacement, spare)['slot'], spare['id'])
        other_room = self.call('world.create', entity=dict(id='other-room', kind='room', label='other'))
        self.call('world.update', id=slot['id'], expected_revision=slot['revision'],
                  patch=dict(room=other_room['id'], pose=pose([2, 1, -5]), size=[4, 3]))
        current = self.call('surface.get', id=child['id'])
        self.assertEqual(current['room'], other_room['id'])
        self.assertEqual(current['size'], [4, 3])
        parent.command('destroy'); self.call('runtime.fence')
        self.error('INVALID_ARGUMENT', 'surface.attach_slot', id=child['id'], slot=slot['id'])
        self.error('INVALID_ARGUMENT', 'surface.place', id=child['id'], room=self.room, pose=pose(), size=[2, 2])
        self.assertFalse(self.call('surface.get', id=child['id'])['mapped'])

    def test_world_placement_preserves_overlapping_native_geometry(self):
        self.start()
        near, far = self.native(), self.native()
        near_surface = self.register(near, (0, 0, -3))
        far_surface = self.register(far, (0, 0, -6))
        geometry = lambda p: {k: v for k, v in p.geometry().items() if k not in ('seq', 'event')}
        initial = [geometry(p) for p in (near, far)]
        self.assertEqual(initial[0], initial[1])
        near.command('paint', 19, 137, 221)
        far.command('paint', 163, 71, 29)
        self.assertEqual(self.capture().pixel(64, 64), (19, 137, 221))
        hit = self.aim()
        self.assertEqual(hit['id'], near_surface['id'])
        self.assertEqual(hit['pixel'], [160, 120])
        self.place(near_surface, pose([4, 0, -3]))
        self.assertEqual(self.capture().pixel(64, 64), (163, 71, 29))
        self.assertEqual(self.aim()['id'], far_surface['id'])
        self.assertEqual([geometry(p) for p in (near, far)], initial)

    def test_transient_inherits_rotated_parent_scale_and_unmap(self):
        self.start()
        probe = self.native()
        parent = self.register(probe)
        probe.command('popup', 80, 60)
        child = self.until(lambda: self.call('surface.children', id=parent['id'])['surfaces'], bool)[0]
        destination = pose([3, -2, -5], axis_angle([0, 1, 0], math.pi/3))
        self.place(parent, destination, (4, 1))
        moved = self.call('surface.get', id=child['id'])
        # The C witness puts its 80x60 popup at root (30,40), parent at (0,0).
        expected = transform(destination, [(30+40-160)*4/320, -(40+30-120)/240, .001])
        for actual, wanted in zip(moved['pose']['p'], expected):
            self.assertAlmostEqual(actual, wanted)
        self.assertEqual(moved['pose']['q'], destination['q'])
        self.assertEqual(moved['size'], [1, .25])
        probe.command('unmap')
        self.assertFalse(self.call('surface.get', id=child['id'])['mapped'])
        self.assertEqual(self.capture().pixel(64, 64), (10, 12, 16))
        probe.command('map')
        self.assertTrue(self.call('surface.get', id=child['id'])['mapped'])
        probe.command('destroy')
        self.assertFalse(self.call('surface.get', id=child['id'])['mapped'])

    def test_existing_client_is_imported_on_compositor_start(self):
        probe = self.native()
        probe.wait(lambda event: event['event'] == 'map' and event['xid'] == probe.xid)
        probe.command('paint', 73, 119, 167)
        self.start()
        self.register(probe)
        self.assertEqual(self.capture().pixel(64, 64), (73, 119, 167))
        self.stats(1)

    def test_repeated_client_death_releases_resources_and_never_reuses_identity(self):
        self.start()
        seen = set()
        for index in range(32):
            probe = self.native()
            surface = self.register(probe)
            self.assertNotIn(surface['id'], seen)
            seen.add(surface['id'])
            probe.command('resize', 103+index, 79+index)
            self.call('runtime.fence')
            probe.command('paint', index+40, 81, 197)
            self.assertEqual(self.capture().pixel(64, 64), (index+40, 81, 197))
            probe.proc.kill()
            probe.proc.wait(timeout=3)
            self.stats(0)
            with self.assertRaises(RpcError) as caught:
                self.call('surface.get', id=surface['id'])
            self.assertEqual(caught.exception.code, 'NOT_FOUND')
            self.assertIsNone(self.aim())

    def test_concurrent_resize_and_damage_leave_latest_pixels_and_live_runtime(self):
        self.start()
        probe = self.native()
        surface = self.register(probe)
        def change():
            for i in range(150):
                probe.command('resize', 101+i%47, 77+i%31)
                probe.command('paint', i%255, (3*i)%255, (7*i)%255)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(change)
            for _ in range(75):
                self.capture()
            pending.result(timeout=20)
        probe.command('sync')
        probe.command('paint', 211, 131, 43)
        self.assertEqual(self.capture().pixel(64, 64), (211, 131, 43))
        self.assertEqual(self.call('surface.get', id=surface['id'])['pixels'], [101+149%47, 77+149%31])
        self.stats(1)

    def test_cyclic_transients_do_not_break_unrelated_rendering_and_can_recover(self):
        self.start()
        first, second, valid = self.native(), self.native(), self.native()
        a = self.register(first, (4, 0, -4))
        b = self.register(second, (-4, 0, -4))
        good = self.register(valid)
        valid.command('paint', 113, 157, 197)
        transient(first.xid, second.xid)
        transient(second.xid, first.xid)
        self.assertEqual(self.capture().pixel(64, 64), (113, 157, 197))
        self.assertEqual(self.aim()['id'], good['id'])
        self.assertFalse(self.call('surface.get', id=a['id'])['mapped'])
        self.assertFalse(self.call('surface.get', id=b['id'])['mapped'])
        transient(second.xid, valid.xid)
        self.assertTrue(self.call('surface.get', id=a['id'])['mapped'])
        self.assertEqual(self.call('surface.get', id=b['id'])['parent_id'], good['id'])

    def test_resize_away_and_back_replaces_backing_resource_at_the_same_final_size(self):
        self.start()
        probe = self.native()
        surface = self.register(probe)
        probe.command('paint', 73, 89, 149)
        self.assertEqual(self.capture().pixel(64, 64), (73, 89, 149))
        compositor = self.call('runtime.identities')['spatialwm_pid']
        os.kill(compositor, signal.SIGSTOP)
        try:
            probe.command('resize', 471, 359)
            probe.command('resize', 320, 240)
        finally:
            os.kill(compositor, signal.SIGCONT)
        updated = self.call('surface.get', id=surface['id'])
        self.assertEqual(updated['pixels'], surface['pixels'])
        self.assertGreater(updated['generation'], surface['generation'])
        probe.command('sync')
        probe.command('paint', 167, 191, 211)
        self.assertEqual(self.capture().pixel(64, 64), (167, 191, 211))
        self.stats(1)

    def test_invalid_placement_is_atomic(self):
        self.start()
        probe = self.native()
        surface = self.register(probe)
        for patch in ({'size': [0, 1]}, {'room': 'missing'}, {'pose': pose(q=(0, 0, 0, 0))}):
            params = dict(id=surface['id'], room=self.room, pose=pose([2, 3, -5]), size=[3, 4])
            params.update(patch)
            with self.assertRaises(RpcError):
                self.call('surface.place', **params)
            self.assertEqual(self.call('surface.get', id=surface['id']), surface)

    def test_graceful_shutdown_releases_live_imports_without_sanitizer_errors(self):
        self.start(direct=True)
        probe = self.native()
        self.register(probe)
        probe.command('popup', 80, 60)
        self.capture()
        self.stats(2)
        compositor = self.call('runtime.identities')['spatialwm_pid']
        self.client.proc.stdin.close()
        self.assertEqual(self.client.proc.wait(timeout=8), 0)
        self.assertFalse(Path(f'/proc/{compositor}').exists())
        stderr = ''.join(p.read_text() for p in (self.root/'logs').glob('stderr-*.log'))
        self.assertNotIn('Sanitizer', stderr)
        self.assertNotIn('runtime error:', stderr)
        probe.command('paint', 17, 53, 91)  # The independent application remains usable.


if __name__ == '__main__':
    unittest.main(verbosity=2)
