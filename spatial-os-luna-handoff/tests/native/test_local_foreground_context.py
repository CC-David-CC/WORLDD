"""Native room, scoped-authority, and uncertain producer retirement witnesses."""
import copy
import os
import select
import signal
import sqlite3
import struct
import unittest

import core_pb2 as core
import grants_pb2 as grants
import launch_pb2 as launch
import world_session_pb2 as session
import test_local_foreground as foreground
from harness.oracles import pose


class LocalForegroundContextTests(foreground.LocalForegroundTests):
    def workspace_grant_roots(self):
        roots = super().workspace_grant_roots()
        roots[0].rights.append(grants.WORKSPACE_PRESENTATION_ATTACH)
        return roots

    def world_grant_roots(self):
        roots = super().world_grant_roots()
        roots[0].rights.append(grants.WORLD_PLACEMENT_CREATE)
        # The inherited fixture creates the real world before installing grants.
        # Read its identity for setup; actors receive no database capability.
        with sqlite3.connect('file:' + str(self.root / 'world/world.db') + '?mode=ro', uri=True) as db:
            world, room = db.execute('SELECT world_id,entry_room FROM metadata WHERE singleton=1').fetchone()
        self.assertEqual(world, self.world_domain)
        roots.append(grants.GrantTerms(subject=self.principal,
            scope=grants.Scope(domain=grants.Domain(kind=grants.WORLD, id=world), components=[room.encode()]),
            rights=[grants.REPRESENTATION_VIEW]))
        return roots

    def pixel_at(self, x, y):
        peer = self.display_peer()
        for drawable in [peer.root, self.output_window]:
            head, _ = peer.operation(14, peer.pack('I', drawable), reply=True)
            width, height = struct.unpack_from(peer.endian + 'HH', head, 16)
            self.assertTrue(0 <= x < width and 0 <= y < height, (x, y, width, height))
        result = peer.operation(73, peer.pack('IhhHHI', self.output_window, x, y, 1, 1, 0xffffffff), 2, True)
        self.assertIsInstance(result, tuple, ('Native foreground readback failed', result))
        return int.from_bytes(result[1], 'little' if peer.endian == '<' else 'big') & 0xffffff

    @staticmethod
    def image_pixel(image, x, y):
        start = 3 * (y * image.width + x)
        return int.from_bytes(image.rgb8[start:start + 3], 'big')

    def test_portal_room_change_clears_old_pixels_and_preserves_workspace(self):
        first = self.show()
        old_watch = copy.deepcopy(self.request(inspect=self.owner).owner.observation_watches[0])
        self.act(stop=core.ActorStopRequest())
        self.released()
        self.management('world.create', entity={'id': 'foreground-destination', 'kind': 'room', 'label': 'Destination'})
        self.management('world.create', entity={'id': 'foreground-door', 'kind': 'portal', 'room': first.room,
            'pose': pose((0, 0, -.5)), 'size': [2, 2],
            'destination': {'room': 'foreground-destination', 'pose': pose((0, 0, 0))}})
        self.act(input=core.ActorInputRequest(axes=[0, 0, -1, 0, 0, 0]))
        state = self.until(lambda: self.actor_call(self.stream, inspect=session.WorldSessionInspect()).state,
            lambda value: value.room == 'foreground-destination')
        self.assertEqual(state.workspace, foreground.baseline.WORKSPACE)
        self.assertEqual(self.pixel(), 0, 'New-room acknowledgement retained old-room pixels')
        self.assertFalse(self.request(inspect=self.owner).owner.observation_watches)
        self.act(stop=core.ActorStopRequest())
        replacement = self.foreground().local_frame
        self.assertEqual(replacement.room, state.room)
        self.assertGreater(replacement.frame_id, first.frame_id)
        self.assertNotEqual(self.pixel(), 0x112233)
        self.assertNotEqual(self.request(inspect=self.owner).owner.observation_watches[0].id, old_watch.id)
        self.native_command('paint', 51, 34, 17)
        self.assertNotEqual(self.pixel(), 0x332211, 'Source-room application leaked into the destination')
        self.finish_foreground()

    def test_scoped_representation_revocation_clears_before_receipt_and_preserves_application(self):
        self.show()
        info = self.management('world.info')
        created = self.act(presentation_create=core.PresentationCreateRequest(protocol_version=1,
            operation_id=os.urandom(16), workspace_id=foreground.baseline.WORKSPACE,
            room_id=info['entry_room'], expected_world_revision=info['world_revision'],
            transform=core.Pose(position=core.Vec3(x=.8, z=-2), orientation=core.Quaternion(w=1)))).presentation
        self.assertEqual(created.room_id, info['entry_room'])
        self.assertTrue(self.foreground().local_frame.presented)
        before = self.act(observe=core.ActorObserveRequest(width=256, height=256, include_pixels=True)).observation
        geometry = 'workspace-presentation.v1/' + created.representation_id.hex()
        visible = [v for v in before.visible if v.id == geometry]
        self.assertEqual(len(visible), 1, before)
        sample = visible[0].sample
        representation_pixel = self.image_pixel(before.image, sample.x, sample.y)
        self.assertNotEqual(representation_pixel, 0)
        self.assertEqual(self.pixel_at(sample.x, sample.y), representation_pixel)
        reservation = copy.deepcopy(self.request(inspect=self.owner).owner.reservations[0])
        record = self.policy_revoke(grants.REPRESENTATION_VIEW, workspace=False)
        self.assertEqual(list(record.terms.scope.components), [info['entry_room'].encode()])
        self.assertEqual(self.pixel_at(sample.x, sample.y), 0, 'Scoped revocation receipt preceded native clearing')
        state = self.request(inspect=self.owner).owner
        self.assertFalse(state.observation_watches)
        self.assertEqual(list(state.reservations), [reservation])
        after = self.act(observe=core.ActorObserveRequest(width=256, height=256, include_pixels=True)).observation
        self.assertFalse(after.presentation_views)
        self.assertFalse(any(v.id == geometry for v in after.visible))
        self.assertTrue(self.foreground().local_frame.presented)
        self.assertEqual(self.pixel(), 0x112233)
        self.assertEqual(self.pixel_at(sample.x, sample.y), self.image_pixel(after.image, sample.x, sample.y))
        self.assertNotEqual(self.pixel_at(sample.x, sample.y), representation_pixel)
        self.native_command('paint', 51, 34, 17)
        self.assertTrue(self.foreground().local_frame.presented)
        self.assertEqual(self.pixel(), 0x332211)
        self.finish_foreground()

    def kill_pinned_process(self, pid):
        pin = os.pidfd_open(pid)
        try:
            signal.pidfd_send_signal(pin, signal.SIGKILL)
            self.assertEqual(select.select([pin], [], [], 6)[0], [pin])
        finally:
            os.close(pin)

    def test_world_crash_retains_watch_until_checked_presentation_recovery(self):
        first = self.show()
        watch = copy.deepcopy(self.request(inspect=self.owner).owner.observation_watches[0])
        rejected = self.request(expect_error=True, recover_world=self.owner)
        self.assertIn('requires checked world cleanup', rejected.error.message)
        self.assertEqual(self.pixel(), 0x112233)
        self.kill_pinned_process(self.world_ids.worldd_pid)
        self.until(self.pixel, lambda value: value == 0)
        self.released()
        state = self.until(lambda: self.request(inspect=self.owner).owner, lambda value: value.channel_fenced)
        self.assertEqual(list(state.observation_watches), [watch])
        self.assertEqual(list(state.reservations), [self.lease])
        cleanup = self.request(stop_world=self.owner).cleanup
        self.assertEqual(cleanup.stage, launch.OWNED_SCOPES_STOPPED)
        self.assertEqual(self.pixel(), 0)
        self.assertEqual(list(self.request(inspect=self.owner).owner.observation_watches), [watch])
        recovered = self.request(recover_world=self.owner).owner
        self.assertFalse(recovered.observation_watches)
        self.assertFalse(recovered.reservations)
        self.assertEqual(list(recovered.recovery.observation_released), [watch])
        self.assertEqual(recovered.recovery.cleanup.retired_presentation.world_id, first.identity.world_id)
        self.assertEqual(recovered.recovery.cleanup.retired_presentation.presentation_generation,
            first.presentation_generation)
        self.assertEqual(self.request(recover_world=self.owner).owner, recovered)
        self.native_command('paint', 51, 34, 17)
        self.finish_session()

    def test_missing_compositor_receipt_cannot_release_observation_obligation(self):
        self.show()
        watch = copy.deepcopy(self.request(inspect=self.owner).owner.observation_watches[0])
        self.kill_pinned_process(self.world_ids.spatialwm_pid)
        refused = self.request(expect_error=True, local_retire=self.local_identity)
        self.assertEqual(refused.error.message, 'Host presentation ownership unavailable')
        self.assertEqual(list(self.request(inspect=self.owner).owner.observation_watches), [watch])
        stopped = self.request(expect_error=True, stop_world=self.owner)
        self.assertEqual(stopped.error.message, 'Host presentation ownership unavailable')
        recovered = self.request(expect_error=True, recover_world=self.owner)
        self.assertEqual(recovered.error.message,
            'Recover broker observations only after checked presentation retirement')
        state = self.request(inspect=self.owner).owner
        self.assertTrue(state.channel_fenced)
        self.assertEqual(list(state.observation_watches), [watch])
        self.native_command('paint', 51, 34, 17)
        self.finish_session()


if __name__ == '__main__':
    names = [name for name in LocalForegroundContextTests.__dict__ if name.startswith('test_')]
    suite = unittest.TestSuite(LocalForegroundContextTests(name) for name in names)
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
