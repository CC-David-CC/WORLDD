"""Real native lifecycle changes must invalidate prepared application frames."""
import unittest
import core_pb2 as core
from test_local_frame_presentation import LocalFrameTests


class LocalFrameLifetimeTests(LocalFrameTests):
    def application_scene(self):
        snapshot = self.command(sync=core.RendererSyncRequest())
        self.assertTrue(snapshot.HasField('snapshot'), snapshot)
        matches = [s for s in snapshot.snapshot.surfaces if s.xid == self.window and s.mapped]
        self.assertEqual(len(matches), 1)
        surface = matches[0]
        scene = self.scene_value()
        placed = scene.surfaces.add(handle=surface.handle, pixels=surface.pixels)
        placed.panel.id = surface.handle.id
        placed.panel.kind = core.PANEL
        placed.panel.color.SetInParent()
        placed.panel.pose.position.z = -1
        placed.panel.pose.orientation.w = 1
        placed.panel.size.CopyFrom(core.Vec2(x=2, y=2))
        return scene, surface

    def changed_before_commit(self, operation):
        self.ready()
        scene, before = self.application_scene()
        pending = self.prepared(scene=scene)
        self.assertEqual(self.pixel(self.xid), 0)
        if operation == 'resize':
            self.assertIsNone(self.owner.operation(12, self.owner.pack('IHHII', self.window, 12, 0, 48, 40)))
        elif operation == 'destroy':
            self.assertIsNone(self.owner.operation(4, self.owner.pack('I', self.window)))
        else:
            self.assertIsNone(self.owner.operation(10, self.owner.pack('I', self.window)))
            if operation == 'remap':
                self.assertIsNone(self.owner.operation(8, self.owner.pack('I', self.window)))
        self.owner.operation(43, reply=True)
        # No intervening compositor sync: commitment must process native changes itself.
        denied = self.commit(pending.frame_id)
        self.assertEqual(denied.error.code, core.STALE_TARGET, denied)
        self.assertEqual(self.pixel(self.xid), 0)
        self.assertEqual(self.commit(pending.frame_id).error.code, core.STALE_TARGET)
        if operation in ('resize', 'remap'):
            current, after = self.application_scene()
            self.assertEqual(before.handle.id, after.handle.id)
            self.assertNotEqual(before.handle.generation, after.handle.generation)
            if operation == 'resize':
                self.assertEqual((after.pixels.width, after.pixels.height), (48, 40))
            fresh = self.prepared(scene=current)
        else:
            fresh = self.prepared()
        self.assertEqual(self.commit(fresh.frame_id).frame, fresh)
        self.assertIsNone(self.proc.poll())

    def test_resize_between_prepare_and_commit_rejects_old_frame(self):
        self.changed_before_commit('resize')

    def test_unmap_between_prepare_and_commit_rejects_old_frame(self):
        self.changed_before_commit('unmap')

    def test_destroy_between_prepare_and_commit_rejects_old_frame(self):
        self.changed_before_commit('destroy')

    def test_unmap_remap_same_geometry_rejects_old_frame(self):
        self.changed_before_commit('remap')

    def test_prepare_rejects_stale_application_handle(self):
        self.ready()
        scene, before = self.application_scene()
        self.assertIsNone(self.owner.operation(4, self.owner.pack('I', self.window)))
        self.owner.operation(43, reply=True)
        denied = self.prepare(scene=scene)
        self.assertEqual(denied.error.code, core.STALE_TARGET, denied)
        self.assertEqual(self.pixel(self.xid), 0)
        fresh = self.prepared()
        self.assertEqual(self.commit(fresh.frame_id).frame, fresh)

    def test_local_receipt_uses_actual_retained_producer_generation(self):
        self.ready(index=37)
        first = self.prepared()
        self.assertEqual(first.presentation_generation, 37)
        self.assertNotEqual(first.presentation_generation, self.target.identity.authority_generation)
        self.assertEqual(self.commit(first.frame_id).frame, first)
        binding, stream = self.attach(73)
        self.hello(stream)
        self.sequences[stream] = 1
        target = self.target_for(binding)
        self.select_local(target)
        second = self.prepared(target=target, producer=stream)
        self.assertEqual(second.presentation_generation, 73)
        self.assertEqual(self.commit(second.frame_id, target=target, producer=stream).frame, second)

    def test_legacy_render_receipt_keeps_zero_generation(self):
        self.ready(index=37)
        self.assertTrue(self.control(select=self.binding).state.selected)
        self.sequences[self.producer] += 1
        reply = self.draw(self.producer, self.sequences[self.producer], (17, 34, 51))
        self.assertTrue(reply.HasField('frame'), reply)
        self.assertEqual(reply.frame.presentation_generation, 0)
        self.assertEqual(self.pixel(self.xid), 0x112233)


if __name__ == '__main__':
    suite = unittest.TestSuite(LocalFrameLifetimeTests(name) for name in LocalFrameLifetimeTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
