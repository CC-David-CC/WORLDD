"""Prepared actor frames use real spatialwm buffers and independent X readback."""
import unittest
import core_pb2 as core
import actor_local_view_pb2 as local
import presentation_pb2 as protocol
import test_presentation_server as baseline


class LocalFrameTests(baseline.PresentationServerTests):
    def ready(self):
        self.launch()
        self.binding, self.producer = self.attach(1)
        self.hello(self.producer)
        self.assertTrue(self.control(select=self.binding).state.selected)
        seed = self.draw(self.producer, 2, (17, 34, 51))
        self.assertTrue(seed.HasField('frame'), seed)
        self.xid = seed.frame.xid
        self.sequences = {self.producer: 2}
        self.target = self.target_for(self.binding)
        self.select_local(self.target)
        self.assertEqual(self.pixel(self.xid), 0)

    def target_for(self, binding, view=b'v' * 32, actor='human'):
        return protocol.LocalActorPresentationTarget(binding=binding,
            identity=local.ActorLocalViewIdentity(protocol_version=1, world_id=binding.world_id,
                actor_id=actor, view_id=view, authority_service_incarnation=binding.service_incarnation,
                authority_generation=binding.authority_generation))

    def select_local(self, target):
        reply = self.control(select_local_actor=target)
        self.assertTrue(reply.HasField('local_actor_state'), reply)
        self.assertTrue(reply.local_actor_state.selected)
        self.assertEqual(reply.local_actor_state.target, target)

    def command(self, producer=None, **fields):
        producer = producer or self.producer
        self.sequences[producer] += 1
        sequence = self.sequences[producer]
        return self.world_command(producer, sequence, **fields)

    def scene_value(self, color=(51, 34, 17)):
        result = core.RenderScene(world_revision=17)
        result.camera.position.SetInParent()
        result.camera.orientation.w = 1
        result.config.CopyFrom(core.RenderConfig(vertical_fov=1.57079632679,
            near_clip=.01, far_clip=1000,
            background=core.Color(red=color[0], green=color[1], blue=color[2]),
            barrier_color=core.Color(red=1, green=2, blue=3)))
        return result

    def prepare(self, target=None, scene=None, width=64, height=64, producer=None):
        target = target or self.target
        return self.command(producer, local_actor_view=target.identity,
            prepare_local_frame=True, scene=scene or self.scene_value(), width=width, height=height)

    def commit(self, frame_id, target=None, producer=None):
        target = target or self.target
        return self.command(producer, local_actor_view=target.identity,
            commit_local_frame=core.ActorLocalFrameCommit(frame_id=frame_id))

    def prepared(self, **kwargs):
        reply = self.prepare(**kwargs)
        self.assertTrue(reply.HasField('frame'), reply)
        self.assertFalse(reply.frame.path)
        self.assertEqual(reply.frame.world_revision, 17)
        self.assertGreater(reply.frame.frame_id, 0)
        return reply.frame

    def geometry(self):
        header, _ = self.supervisor.operation(14, self.supervisor.pack('I', self.xid), reply=True)
        import struct
        return struct.unpack_from(self.supervisor.endian + 'HH', header, 16)

    def test_prepare_does_not_paint_or_resize_before_exact_commit(self):
        self.ready()
        self.assertEqual(self.geometry(), (64, 64))
        pending = self.prepared(width=96, height=80)
        self.assertEqual(self.pixel(self.xid), 0)
        self.assertEqual(self.geometry(), (64, 64))
        committed = self.commit(pending.frame_id)
        self.assertTrue(committed.HasField('frame'), committed)
        self.assertEqual(committed.frame, pending)
        self.assertEqual(self.pixel(self.xid), 0x332211)
        self.assertEqual(self.geometry(), (96, 80))

    def test_observation_cannot_overwrite_prepared_or_visible_frame(self):
        self.ready()
        pending = self.prepared()
        observed = self.command(scene=self.scene_value((255, 0, 0)), width=64, height=64,
            observe=True, include_pixels=True)
        self.assertTrue(observed.HasField('visibility'), observed)
        self.assertGreater(observed.visibility.frame_id, pending.frame_id)
        self.assertEqual(observed.visibility.image.rgb8[:3], b'\xff\0\0')
        self.assertEqual(self.pixel(self.xid), 0)
        self.assertEqual(self.commit(pending.frame_id).frame, pending)
        self.assertEqual(self.pixel(self.xid), 0x332211)

    def test_wrong_actor_view_world_and_authority_cannot_paint(self):
        self.ready()
        pending = self.prepared()
        for field, value, code in (
            ('actor_id', 'other', core.FORBIDDEN), ('view_id', b'x' * 32, core.FORBIDDEN),
            ('world_id', 'foreign', core.INVALID_ARGUMENT),
            ('authority_service_incarnation', b'x' * 32, core.INVALID_ARGUMENT),
            ('authority_generation', 2, core.INVALID_ARGUMENT)):
            with self.subTest(field=field):
                wrong = protocol.LocalActorPresentationTarget()
                wrong.CopyFrom(self.target)
                setattr(wrong.identity, field, value)
                denied = self.prepare(target=wrong)
                self.assertEqual(denied.error.code, code, denied)
                self.assertEqual(self.pixel(self.xid), 0)
        self.assertEqual(self.commit(pending.frame_id).frame, pending)
        self.assertEqual(self.pixel(self.xid), 0x332211)

    def test_other_producer_cannot_prepare_or_commit_selected_view(self):
        self.ready()
        other, stream = self.attach(2)
        self.hello(stream)
        self.sequences[stream] = 1
        pending = self.prepared()
        self.assertEqual(self.prepare(producer=stream).error.code, core.INVALID_ARGUMENT)
        self.assertEqual(self.commit(pending.frame_id, producer=stream).error.code, core.INVALID_ARGUMENT)
        self.assertEqual(self.prepare(target=self.target_for(other), producer=stream).error.code, core.FORBIDDEN)
        self.assertEqual(self.pixel(self.xid), 0)
        self.assertEqual(self.commit(pending.frame_id).frame, pending)

    def test_duplicate_and_superseded_frames_cannot_commit(self):
        self.ready()
        old = self.prepared()
        current = self.prepared(scene=self.scene_value((17, 34, 51)))
        self.assertGreater(current.frame_id, old.frame_id)
        self.assertEqual(self.commit(old.frame_id).error.code, core.STALE_TARGET)
        self.assertEqual(self.pixel(self.xid), 0)
        self.assertEqual(self.commit(current.frame_id).frame, current)
        self.assertEqual(self.pixel(self.xid), 0x112233)
        self.assertEqual(self.commit(current.frame_id).error.code, core.STALE_TARGET)
        self.assertEqual(self.pixel(self.xid), 0x112233)

    def test_old_view_clear_and_retirement_cannot_blank_replacement(self):
        self.ready()
        old = self.prepared()
        replacement = self.target_for(self.binding, view=b'n' * 32)
        self.select_local(replacement)
        self.assertEqual(self.commit(old.frame_id).error.code, core.FORBIDDEN)
        self.assertEqual(self.commit(old.frame_id, target=replacement).error.code, core.STALE_TARGET)
        current = self.prepared(target=replacement)
        self.assertEqual(self.commit(current.frame_id, target=replacement).frame, current)
        self.assertEqual(self.pixel(self.xid), 0x332211)
        self.assertFalse(self.control(retire_local_actor=self.target).local_actor_state.selected)
        cleared = self.command(local_actor_view=self.target.identity, clear_local_view=core.ActorLocalViewRelease())
        self.assertTrue(cleared.local_view_cleared, cleared)
        self.assertEqual(self.pixel(self.xid), 0x332211)
        self.assertTrue(self.control(inspect=self.binding).state.selected)

    def test_clear_and_retire_discard_frames_without_stopping_producer(self):
        self.ready()
        for operation in ('clear', 'retire', 'clear-selection'):
            with self.subTest(operation=operation):
                self.select_local(self.target)
                shown = self.prepared()
                self.assertEqual(self.commit(shown.frame_id).frame, shown)
                pending = self.prepared(scene=self.scene_value((255, 0, 0)))
                if operation == 'clear':
                    self.assertTrue(self.command(local_actor_view=self.target.identity,
                        clear_local_view=core.ActorLocalViewRelease()).local_view_cleared)
                elif operation == 'retire':
                    self.assertFalse(self.control(retire_local_actor=self.target).local_actor_state.selected)
                else:
                    self.assertTrue(self.control(clear_selection=protocol.PresentationEmpty()).selection_cleared)
                self.assertEqual(self.pixel(self.xid), 0)
                self.assertEqual(self.commit(pending.frame_id).error.code, core.FORBIDDEN)
                self.select_local(self.target)
                self.assertEqual(self.commit(pending.frame_id).error.code, core.STALE_TARGET)
                self.assertTrue(self.command(sync=core.RendererSyncRequest()).HasField('snapshot'))
                self.assertIsNone(self.proc.poll())

    def test_failed_preparation_invalidates_previous_pending_frame(self):
        self.ready()
        old = self.prepared()
        denied = self.prepare(width=0)
        self.assertEqual(denied.error.code, core.INVALID_ARGUMENT, denied)
        self.assertEqual(self.commit(old.frame_id).error.code, core.STALE_TARGET)
        self.assertEqual(self.pixel(self.xid), 0)
        current = self.prepared()
        self.assertEqual(self.commit(current.frame_id).frame, current)

    def test_producer_disconnect_blanks_frame_and_preserves_other_world(self):
        self.ready()
        pending = self.prepared()
        self.assertEqual(self.commit(pending.frame_id).frame, pending)
        other, stream = self.attach(2)
        self.hello(stream)
        self.producer.close()
        self.assertFalse(self.retired(self.binding).selected)
        self.assertEqual(self.pixel(self.xid), 0)
        self.assertTrue(self.control(select=other).state.selected)
        self.assertTrue(self.draw(stream, 2, (17, 34, 51)).HasField('frame'))
        self.assertEqual(self.pixel(self.xid), 0x112233)

    def test_unbound_draw_cannot_bypass_selected_actor_view(self):
        self.ready()
        self.sequences[self.producer] += 1
        denied = self.draw(self.producer, self.sequences[self.producer], (255, 0, 0))
        self.assertEqual(denied.error.code, core.FORBIDDEN, denied)
        self.assertEqual(self.pixel(self.xid), 0)
        current = self.prepared()
        self.assertEqual(self.commit(current.frame_id).frame, current)
        self.assertTrue(self.control(select=self.binding).state.selected)
        self.assertEqual(self.pixel(self.xid), 0)
        self.sequences[self.producer] += 1
        self.assertTrue(self.draw(self.producer, self.sequences[self.producer], (17, 34, 51)).HasField('frame'))
        self.assertEqual(self.pixel(self.xid), 0x112233)

    def test_prepared_frame_uses_real_live_native_application_texture(self):
        self.ready()
        snapshot = self.command(sync=core.RendererSyncRequest())
        self.assertTrue(snapshot.HasField('snapshot'), snapshot)
        surfaces = [s for s in snapshot.snapshot.surfaces if s.xid == self.window and s.mapped]
        self.assertEqual(len(surfaces), 1)
        surface = surfaces[0]
        scene = self.scene_value()
        placed = scene.surfaces.add(handle=surface.handle, pixels=core.PixelExtent(width=32, height=32))
        placed.panel.id = surface.handle.id
        placed.panel.kind = core.PANEL
        placed.panel.color.SetInParent()
        placed.panel.pose.position.z = -1
        placed.panel.pose.orientation.w = 1
        placed.panel.size.CopyFrom(core.Vec2(x=2, y=2))
        first = self.prepared(scene=scene)
        self.assertEqual(self.commit(first.frame_id).frame, first)
        self.assertEqual(self.pixel(self.xid), 0x234567)
        self.assertIsNone(self.owner.operation(2, self.owner.pack('III', self.window, 2, 0x765432)))
        self.assertIsNone(self.owner.operation(61, self.owner.pack('IhhHH', self.window, 0, 0, 0, 0)))
        second = self.prepared(scene=scene)
        self.assertEqual(self.pixel(self.xid), 0x234567)
        self.assertEqual(self.commit(second.frame_id).frame, second)
        self.assertEqual(self.pixel(self.xid), 0x765432)

    def test_invalid_root_target_does_not_replace_current_view(self):
        self.ready()
        current = self.prepared()
        for field, value in (('world_id', 'wrong'), ('authority_service_incarnation', b'x' * 32),
                             ('authority_generation', 2), ('view_id', b'short')):
            with self.subTest(field=field):
                wrong = protocol.LocalActorPresentationTarget()
                wrong.CopyFrom(self.target)
                setattr(wrong.identity, field, value)
                self.assertEqual(self.control(select_local_actor=wrong).error.code, core.INVALID_ARGUMENT)
        self.assertEqual(self.commit(current.frame_id).frame, current)


if __name__ == '__main__':
    suite = unittest.TestSuite(LocalFrameTests(name) for name in LocalFrameTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
