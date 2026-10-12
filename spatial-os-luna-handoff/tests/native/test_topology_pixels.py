"""Native parent pixel operations must not expose a foreign child window."""
import unittest
import test_resource_isolation as resources
import struct


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

    def nested_foreign_child(self, endian='<'):
        supervisor = self.mapped_topology(endian=endian)
        middle = self.other.create_window(self.other_window)
        self.assertIsNone(supervisor.operation(7, supervisor.pack('IIhh', self.window, middle, 8, 8)))
        self.assertIsNone(self.other.operation(8, self.other.pack('I', middle)))
        self.assertEqual(self.image(supervisor, self.other_window, 9, 9), self.secret)
        return supervisor

    def test_owned_child_cannot_restore_foreign_grandchild_pixels(self):
        self.nested_foreign_child()
        self.assertNotEqual(self.image(self.other, self.other_window, 9, 9), self.secret)

    def test_swapped_owned_child_cannot_restore_foreign_grandchild_pixels(self):
        self.nested_foreign_child(endian='>')
        self.assertNotEqual(self.image(self.other, self.other_window, 9, 9), self.secret)

    def test_owned_child_pixels_outside_foreign_grandchild_remain_readable(self):
        self.nested_foreign_child()
        self.assertEqual(self.image(self.other, self.other_window, 1, 1), 0x234567)

    def test_owned_grandchild_inside_foreign_child_remains_readable(self):
        supervisor = self.mapped_topology()
        own = self.other.create_window()
        self.assertIsNone(supervisor.operation(12, supervisor.pack('IH2xII', own, 12, 2, 2)))
        self.assertIsNone(supervisor.operation(7, supervisor.pack('IIhh', own, self.window, 2, 2)))
        self.assertIsNone(self.other.operation(8, self.other.pack('I', own)))
        self.assertEqual(self.image(supervisor, self.other_window, 10, 10), 0x234567)
        self.assertEqual(self.image(self.other, self.other_window, 10, 10), 0x234567)
        self.assertNotEqual(self.image(self.other, self.other_window, 9, 9), self.secret)

    def inferiors_gc(self, client, drawable):
        gc = client.gc(drawable)
        self.assertIsNone(client.operation(56, client.pack('IIII', gc, (1 << 15) | (1 << 16), 1, 0)))
        return gc

    def render_include_inferiors_picture(self, client, drawable):
        name = b'RENDER'
        extension, _ = client.operation(98, client.pack('HH', len(name), 0) + name, reply=True)
        opcode = extension[9]
        self.assertTrue(extension[8], 'RENDER extension is required')
        self.assertIsInstance(client.operation(opcode, client.pack('II', 0, 11), 0, True), tuple)
        reply, body = client.operation(opcode, minor=1, reply=True)
        count = struct.unpack_from(client.endian + 'I', reply, 8)[0]
        formats = [struct.unpack_from(client.endian + 'IBBH8HI', body, i * 28)
                   for i in range(count)]
        fmt = next(item[0] for item in formats if item[1] == 1 and item[2] == 24 and
                   item[4:12] == (16, 255, 8, 255, 0, 255, 0, 0))
        picture = client.xid()
        self.assertIsNone(client.operation(opcode,
            client.pack('IIII', picture, drawable, fmt, 1 << 8) + client.pack('I', 1), 4))
        return opcode, picture

    def render_fill(self, client, opcode, picture):
        return client.operation(opcode,
            client.pack('B3xIHHHHhhHH', 1, picture, 0xffff, 0, 0, 0xffff, 0, 0, 16, 16), 26)

    def test_include_inferiors_copy_does_not_read_foreign_child(self):
        self.mapped_topology()
        destination = self.other.pixmap()
        gc = self.inferiors_gc(self.other, destination)
        # Use a known destination value. A successful request must not copy
        # the foreign child's secret into this pixmap.
        self.assertIsNone(self.other.operation(70, self.other.pack('IIhhHH', destination, gc, 0, 0, 1, 1)))
        result = self.other.operation(62, self.other.pack('IIIhhhhHH',
            self.other_window, destination, gc, 9, 9, 0, 0, 1, 1))
        self.assertIn(result, (None, 10), 'CopyArea must complete or return native BadAccess')
        self.assertNotEqual(self.image(self.other, destination, 0, 0), self.secret,
                            'CopyArea copied pixels from a foreign child')

    def test_include_inferiors_copy_plane_does_not_read_foreign_child(self):
        self.mapped_topology()
        destination = self.other.pixmap()
        gc = self.inferiors_gc(self.other, destination)
        self.assertIsNone(self.other.operation(70, self.other.pack('IIhhHH', destination, gc, 0, 0, 1, 1)))
        result = self.other.operation(63, self.other.pack('IIIhhhhHHI',
            self.other_window, destination, gc, 9, 9, 0, 0, 1, 1, 1))
        self.assertEqual(result, 10, 'CopyPlane must deny a foreign IncludeInferiors source')
        self.assertNotEqual(self.image(self.other, destination, 0, 0), self.secret)

    def test_include_inferiors_fill_does_not_write_foreign_child(self):
        self.mapped_topology()
        gc = self.inferiors_gc(self.other, self.other_window)
        result = self.other.operation(70, self.other.pack('IIhhHH', self.other_window, gc, 9, 9, 1, 1))
        self.assertIn(result, (None, 10), 'PolyFillRectangle must complete or return native BadAccess')
        self.assertEqual(self.image(self.owner, self.window, 1, 1), self.secret,
                         'PolyFillRectangle changed a foreign child pixel')

    def test_same_application_include_inferiors_copy_remains_usable(self):
        self.mapped_topology(same_application=True)
        destination = self.other.pixmap()
        gc = self.inferiors_gc(self.other, destination)
        self.assertIsNone(self.other.operation(62, self.other.pack('IIIhhhhHH',
            self.other_window, destination, gc, 9, 9, 0, 0, 1, 1)))
        self.assertEqual(self.image(self.other, destination, 0, 0), self.secret)

    def test_same_application_include_inferiors_copy_plane_remains_usable(self):
        self.mapped_topology(same_application=True)
        destination = self.other.pixmap()
        gc = self.inferiors_gc(self.other, destination)
        self.assertIsNone(self.other.operation(63, self.other.pack('IIIhhhhHHI',
            self.other_window, destination, gc, 9, 9, 0, 0, 1, 1, 1)))
        self.assertEqual(self.image(self.other, destination, 0, 0), 0x789abc)

    def test_same_application_include_inferiors_fill_remains_usable(self):
        self.mapped_topology(same_application=True)
        gc = self.inferiors_gc(self.other, self.other_window)
        self.assertIsNone(self.other.operation(70, self.other.pack('IIhhHH', self.other_window, gc, 9, 9, 1, 1)))
        self.assertEqual(self.image(self.owner, self.window, 1, 1), 0x789abc)

    def test_render_include_inferiors_fill_cannot_write_foreign_child(self):
        self.mapped_topology()
        opcode, picture = self.render_include_inferiors_picture(self.other, self.other_window)
        result = self.render_fill(self.other, opcode, picture)
        self.assertEqual(result, 10, 'Render IncludeInferiors must deny a foreign child')
        self.assertEqual(self.image(self.owner, self.window, 1, 1), self.secret,
                         'Render changed a foreign child pixel')

    def test_render_include_inferiors_keeps_same_application_behavior(self):
        self.mapped_topology(same_application=True)
        opcode, picture = self.render_include_inferiors_picture(self.other, self.other_window)
        self.assertIsNone(self.render_fill(self.other, opcode, picture))
        self.assertEqual(self.image(self.owner, self.window, 1, 1), 0xff0000)

    def test_privileged_render_include_inferiors_keeps_access(self):
        supervisor = self.mapped_topology()
        opcode, picture = self.render_include_inferiors_picture(supervisor, self.other_window)
        self.assertIsNone(self.render_fill(supervisor, opcode, picture))
        self.assertEqual(self.image(self.owner, self.window, 1, 1), 0xff0000)


if __name__ == '__main__':
    unittest.main(verbosity=2)
