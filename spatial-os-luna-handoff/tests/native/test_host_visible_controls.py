"""Human menu actions must change the actual production-host foreground."""
import unittest
import test_host_shared_controls as shared


class VisibleHostControlsTests(shared.SharedHostControlsTests):
    def test_f10_preferences_and_escape_change_real_pixels_before_reply(self):
        self.ready_host()
        self.connect_keyboard()
        self.assertEqual((self.status().viewport_width, self.status().viewport_height), (128, 128))
        before = self.foreground_pixels()
        pids = self.retained_pids()
        self.assertTrue(self.key('F10').menu_open)
        shown = self.foreground_pixels()
        self.assertNotEqual(shown, before)
        self.key('Right')
        self.assertTrue(self.preferences.is_file())
        self.key('Right', False)
        self.key('Escape')
        self.assertFalse(self.shared('controls_menu').menu.visible)
        self.assertEqual(self.foreground_pixels(), before)
        self.assertEqual(self.retained_pids(), pids)
        self.shutdown_checked()

    def test_menu_remains_available_without_world_observation_authority(self):
        self.ready_host(observe_denied=True)
        self.connect_keyboard()
        self.assert_foreground_blank()
        self.assertTrue(self.key('F10').menu_open)
        shown = self.foreground_pixels()
        self.assertTrue(any(shown[0::4]) or any(shown[1::4]) or any(shown[2::4]))
        self.key('Escape')
        self.assert_foreground_blank()
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
