"""Native parent pixel operations must not expose a foreign child window."""
import unittest
import test_resource_isolation as resources


class TopologyPixelTests(unittest.TestCase):
    setUp = resources.ResourceIsolationTests.setUp
    start = resources.ResourceIsolationTests.start
    connect = resources.ResourceIsolationTests.connect
    scene = resources.ResourceIsolationTests.scene

    def mapped_topology(self, endian='<', same_application=False):
        supervisor = self.scene(endian=endian, same_workspace=same_application,
                                same_application=same_application)
        self.secret = 0xc3a57b
        self.assertIsNone(self.owner.operation(2, self.owner.pack('III', self.window, 2, self.secret)))
        self.assertIsNone(supervisor.operation(12, supervisor.pack('IH2xII', self.window, 12, 8, 8)))
        self.assertIsNone(supervisor.operation(7, supervisor.pack('IIhh', self.window, self.other_window, 8, 8)))
        self.assertIsNone(supervisor.operation(8, supervisor.pack('I', self.other_window)))
        self.assertIsNone(supervisor.operation(8, supervisor.pack('I', self.window)))
        return supervisor

    def image(self, client, window, x, y):
        result = client.operation(73, client.pack('IhhHHI', window, x, y, 1, 1, 0xffffffff), 2, True)
        self.assertIsInstance(result, tuple)
        _, pixels = result
        self.assertEqual(len(pixels), 4)
        # Native image bytes use the server's advertised image order, not request order.
        order = 'little' if client.setup[22] == 0 else 'big'
        return int.from_bytes(pixels, order) & 0xffffff

    def test_parent_get_image_does_not_read_foreign_child_pixels(self):
        self.mapped_topology()
        self.assertNotEqual(self.image(self.other, self.other_window, 9, 9), self.secret)

    def test_swapped_parent_get_image_does_not_read_foreign_child_pixels(self):
        self.mapped_topology(endian='>')
        self.assertNotEqual(self.image(self.other, self.other_window, 9, 9), self.secret)

    def test_parent_get_image_preserves_owned_pixels_outside_foreign_child(self):
        self.mapped_topology()
        self.assertEqual(self.image(self.other, self.other_window, 1, 1), 0x234567)

    def test_same_application_parent_get_image_reads_its_child(self):
        self.mapped_topology(same_application=True)
        self.assertEqual(self.image(self.other, self.other_window, 9, 9), self.secret)

    def test_privileged_parent_get_image_reads_actual_child(self):
        supervisor = self.mapped_topology()
        self.assertEqual(self.image(supervisor, self.other_window, 9, 9), self.secret)

    def inferiors_gc(self, client, drawable):
        gc = client.gc(drawable)
        self.assertIsNone(client.operation(56, client.pack('IIII', gc, (1 << 15) | (1 << 16), 1, 0)))
        return gc

    def test_include_inferiors_copy_does_not_read_foreign_child(self):
        self.mapped_topology()
        destination = self.other.pixmap()
        gc = self.inferiors_gc(self.other, destination)
        result = self.other.operation(62, self.other.pack('IIIhhhhHH',
            self.other_window, destination, gc, 9, 9, 0, 0, 1, 1))
        self.assertEqual(result, 10, 'Cross-application inferiors require native BadAccess')

    def test_include_inferiors_fill_does_not_write_foreign_child(self):
        self.mapped_topology()
        gc = self.inferiors_gc(self.other, self.other_window)
        result = self.other.operation(70, self.other.pack('IIhhHH', self.other_window, gc, 9, 9, 1, 1))
        self.assertEqual(result, 10, 'Cross-application inferiors require native BadAccess')
        self.assertEqual(self.image(self.owner, self.window, 1, 1), self.secret)

    def test_same_application_include_inferiors_copy_remains_usable(self):
        self.mapped_topology(same_application=True)
        destination = self.other.pixmap()
        gc = self.inferiors_gc(self.other, destination)
        self.assertIsNone(self.other.operation(62, self.other.pack('IIIhhhhHH',
            self.other_window, destination, gc, 9, 9, 0, 0, 1, 1)))
        self.assertEqual(self.image(self.other, destination, 0, 0), self.secret)

    def test_same_application_include_inferiors_fill_remains_usable(self):
        self.mapped_topology(same_application=True)
        gc = self.inferiors_gc(self.other, self.other_window)
        self.assertIsNone(self.other.operation(70, self.other.pack('IIhhHH', self.other_window, gc, 9, 9, 1, 1)))
        self.assertEqual(self.image(self.owner, self.window, 1, 1), 0x789abc)


if __name__ == '__main__':
    unittest.main(verbosity=2)
