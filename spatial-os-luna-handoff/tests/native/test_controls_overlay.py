"""Production controls pixels, private context fences, and scene separation."""
import os
import struct
from pathlib import Path
import unittest
import core_pb2 as core
import control_pb2 as controls
import presentation_pb2 as protocol
import test_geometry_presentation as geometry
from geometry_pixel_witness import GeometryPixelWitness


class ControlsOverlayTests(geometry.GeometryPresentationTests):
    def witness(self):
        if not hasattr(self, 'pixel_witness'):
            self.pixel_witness = GeometryPixelWitness(self.display, self.cookie)
            self.addCleanup(self.pixel_witness.close)
        return self.pixel_witness.pixels()

    def ready_menu(self):
        self.ready()
        target = self.target()
        self.selected(target)
        self.publish(target, self.view(target))
        self.before = self.witness()
        self.assertTrue(self.before)
        self.menu_target = target

    def menu(self, selected=0, visible=True):
        view = controls.ControlMenuView(protocol_version=1, visible=visible,
            prompt='Up/Down: select. Enter: edit. Esc: back.')
        if visible:
            for i in range(18):
                view.entries.add(label='Option ' + str(i), value='Value ' + str(i), selected=i == selected)
        return view

    def show(self, view=None, generation=1, sequence=1, descriptors=(), raw=False):
        value = protocol.HostControlsPresentation(capture_generation=generation,
            menu_sequence=sequence, view=view if view is not None else self.menu())
        if raw:
            self.assertFalse(descriptors)
            self.sequence += 1
            request = protocol.PresentationControlRequest(protocol_version=1,
                request_id=self.sequence, controls=value)
            # Hostile bytes must reach production. The ordinary fixture encoder
            # correctly refuses nonfinite numbers before transmission.
            data = request.SerializeToString()
            self.stream.sendall(struct.pack('!I', len(data)) + data)
            reply = self.receive(self.stream, protocol.PresentationControlReply)
            self.assertEqual(reply.request_id, self.sequence)
        else:
            reply = self.control(descriptors, controls=value)
        if reply.HasField('controls'):
            self.assertEqual(reply.controls, value)
        return reply

    def assert_text_pixels(self, pixels):
        self.assertGreater(sum(pixels[i] > 200 and pixels[i+1] > 200 and pixels[i+2] > 200
                               for i in range(0, len(pixels), 4)), 20)

    def test_native_menu_receipt_confirms_pixels_and_hide_restores_scene(self):
        self.ready_menu()
        reply = self.show()
        self.assertTrue(reply.HasField('controls'), reply)
        shown = self.witness()
        self.assertNotEqual(shown, self.before)
        self.assert_text_pixels(shown)
        self.assertTrue(self.show(self.menu(visible=False), sequence=2).HasField('controls'))
        self.assertEqual(self.witness(), self.before)
        self.assertIsNone(self.proc.poll())

    def test_host_controls_work_without_any_world_presentation(self):
        self.launch()
        self.assertTrue(self.show().HasField('controls'))
        self.assert_text_pixels(self.witness())
        self.assertTrue(self.show(self.menu(visible=False), sequence=2).HasField('controls'))
        pixels = self.witness()
        for offset in (0, 1, 2):
            self.assertFalse(any(pixels[offset::4]))

    def test_stale_context_and_replayed_sequence_cannot_replace_native_menu(self):
        self.ready_menu()
        self.assertTrue(self.show(generation=2, sequence=8).HasField('controls'))
        shown = self.witness()
        for generation, sequence in ((1, 99), (2, 7), (2, 8)):
            reply = self.show(self.menu(visible=False), generation, sequence)
            self.assertEqual(reply.error.code, core.STALE_TARGET, reply)
            self.assertEqual(self.witness(), shown)
        self.assertTrue(self.show(self.menu(visible=False), 3, 1).HasField('controls'))
        self.assertEqual(self.witness(), self.before)

    def test_invalid_views_and_descriptors_reject_before_pixels_or_sequence(self):
        self.ready_menu()
        self.assertTrue(self.show().HasField('controls'))
        shown = self.witness()
        invalid = []
        view = self.menu(); view.protocol_version = 2; invalid.append(view)
        view = self.menu(); view.entries[0].label = 'x' * 257; invalid.append(view)
        view = self.menu(); view.prompt = 'x' * 513; invalid.append(view)
        view = self.menu(); view.entries[1].selected = True; invalid.append(view)
        view = self.menu(); view.raw_axes.append(float('nan')); invalid.append(view)
        view = self.menu(); view.ParseFromString(view.SerializeToString() + b'\xa0\x06\x01'); invalid.append(view)
        for view in invalid:
            reply = self.show(view, sequence=2, raw=True)
            self.assertEqual(reply.error.code, core.INVALID_ARGUMENT, reply)
            self.assertEqual(self.witness(), shown)
        fd = os.memfd_create('unexpected-controls-descriptor', os.MFD_CLOEXEC)
        try:
            reply = self.show(self.menu(visible=False), sequence=2, descriptors=(fd,))
        finally:
            os.close(fd)
        self.assertEqual(reply.error.code, core.INVALID_ARGUMENT, reply)
        self.assertIn('descriptor count', reply.error.message)
        self.assertEqual(self.witness(), shown)
        self.assertTrue(self.show(self.menu(visible=False), sequence=2).HasField('controls'))
        self.assertEqual(self.witness(), self.before)

    def test_menu_scrolls_to_selected_row_on_small_native_output(self):
        self.ready_menu()
        self.assertTrue(self.show().HasField('controls'))
        first = self.witness()
        self.assertTrue(self.show(self.menu(selected=17), sequence=2).HasField('controls'))
        last = self.witness()
        self.assertNotEqual(first, last)
        self.assert_text_pixels(last)
        # Both end selections retain the same visible highlight area.
        highlight = lambda p: sum(p[i:i+3] == bytes((128, 90, 49)) for i in range(0, len(p), 4))
        self.assertGreater(highlight(first), 10)
        self.assertGreater(highlight(last), 10)
        if os.environ.get('SPATIAL_SECURITY_EVIDENCE'):
            root = Path(os.environ['SPATIAL_SECURITY_EVIDENCE']); root.mkdir(parents=True, exist_ok=True)
            rgb = b''.join(last[i:i+3][::-1] for i in range(0, len(last), 4))
            (root / 'controls-overlay-small-native.ppm').write_bytes(b'P6\n128 128\n255\n' + rgb)

    def test_overlay_does_not_enter_actor_observation_or_capture(self):
        self.ready_menu()
        self.assertTrue(self.show().HasField('controls'))
        shown = self.witness()
        # An ordinary producer can observe its scene while another target presents.
        scene = core.RenderScene(world_revision=23)
        scene.camera.position.SetInParent(); scene.camera.orientation.w = 1
        scene.config.CopyFrom(self.view(self.menu_target).config)
        scene.config.background.CopyFrom(core.Color(red=17, green=34, blue=51))
        observed = self.world_command(self.local_stream, 3, scene=scene, width=64, height=64,
            observe=True, include_pixels=True)
        self.assertTrue(observed.HasField('visibility'), observed)
        self.assertEqual(observed.visibility.image.rgb8, bytes((17, 34, 51)) * (64 * 64))
        self.assertEqual(self.witness(), shown)
        self.control(select=self.local_binding)
        path = self.root / 'private-capture.ppm'
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        try:
            result = self.draw(self.local_stream, 4, (17, 34, 51), capture_fd=fd)
            self.assertTrue(result.HasField('frame'), result)
        finally:
            os.close(fd)
        self.assertEqual(path.read_bytes(), b'P6\n64 64\n255\n' + bytes((17, 34, 51)) * (64 * 64))
        self.assert_text_pixels(self.witness())
        self.assertTrue(self.show(self.menu(visible=False), sequence=2).HasField('controls'))
        pixels = self.witness()
        self.assertEqual(pixels[:3], bytes((51, 34, 17)))

    def test_world_clear_blanks_scene_but_host_preferences_remain_visible(self):
        self.ready_menu()
        self.show()
        self.assertTrue(self.control(clear_selection=protocol.PresentationEmpty()).selection_cleared)
        self.assert_text_pixels(self.witness())
        self.show(self.menu(visible=False), sequence=2)
        pixels = self.witness()
        for offset in (0, 1, 2):
            self.assertFalse(any(pixels[offset::4]))


if __name__ == '__main__':
    names = [name for name in ControlsOverlayTests.__dict__ if name.startswith('test_')]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(ControlsOverlayTests(name) for name in names))
    raise SystemExit(not result.wasSuccessful())
