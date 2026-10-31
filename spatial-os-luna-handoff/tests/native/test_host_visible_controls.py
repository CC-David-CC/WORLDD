"""Human menu actions must reach the real production-host foreground."""
import time
import unittest
import test_host_continuous_capture as continuous


class VisibleHostControlsTests(continuous.ContinuousCaptureTests):
    def wait_pixels(self, predicate, description):
        deadline = time.monotonic() + 3
        pixels = None
        while time.monotonic() < deadline:
            pixels = self.foreground_pixels()
            if predicate(pixels):
                return pixels
            time.sleep(.02)
        self.fail('Native menu output did not %s' % description)

    def connect_keyboard(self, generation=0):
        self.human_capture = self.producer(generation)

    def key(self, keysym, pressed=True, generation=0):
        return self.wait_processed(self.human_capture.key(keysym, pressed), generation)

    def test_f10_preferences_and_escape_change_real_pixels_after_input_dispatch(self):
        self.ready_host()
        self.connect_keyboard()
        self.assertEqual((self.status().viewport_width, self.status().viewport_height), (128, 128))
        before = self.foreground_pixels()
        pids = self.retained_pids()
        self.assertTrue(self.key('F10').menu_open)
        self.wait_pixels(lambda pixels: pixels != before, 'show the F10 menu')
        self.key('Right')
        self.assertTrue(self.preferences.is_file())
        self.key('Right', False)
        self.key('Escape')
        self.assertFalse(self.shared('controls_menu').menu.visible)
        self.wait_pixels(lambda pixels: pixels == before, 'restore the foreground after Escape')
        self.assertEqual(self.retained_pids(), pids)
        self.shutdown_checked()

    def test_menu_remains_available_without_world_observation_authority(self):
        self.ready_host(observe_denied=True)
        self.connect_keyboard()
        self.assert_foreground_blank()
        self.assertTrue(self.key('F10').menu_open)
        self.wait_pixels(lambda pixels: any(pixels[0::4]) or any(pixels[1::4]) or any(pixels[2::4]),
            'show controls without world observation authority')
        self.key('Escape')
        self.wait_pixels(lambda pixels: not any(pixels[0::4]) and not any(pixels[1::4]) and
            not any(pixels[2::4]), 'restore the blank foreground after Escape')
        self.shutdown_checked()

    def test_f12_stops_held_navigation_and_requires_a_fresh_key_edge(self):
        self.ready_host()
        self.connect_keyboard()
        self.key('w')
        self.assertTrue(self.shared('controls_tick').accepted)
        self.assertEqual(list(self.state().axes), [0, 0, -1, 0, 0, 0])
        self.assertTrue(self.key('F12').navigating)
        self.assertEqual(list(self.state().axes), [0] * 6)
        self.key('w')
        self.assertTrue(self.shared('controls_tick').accepted)
        self.assertEqual(list(self.state().axes), [0] * 6)
        self.key('w', False)
        self.key('w')
        self.assertTrue(self.shared('controls_tick').accepted)
        self.assertEqual(list(self.state().axes), [0, 0, -1, 0, 0, 0])
        self.stop()
        self.shutdown_checked()


if __name__ == '__main__':
    names = [name for name in VisibleHostControlsTests.__dict__ if name.startswith('test_')]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(VisibleHostControlsTests(name) for name in names))
    raise SystemExit(not result.wasSuccessful())
