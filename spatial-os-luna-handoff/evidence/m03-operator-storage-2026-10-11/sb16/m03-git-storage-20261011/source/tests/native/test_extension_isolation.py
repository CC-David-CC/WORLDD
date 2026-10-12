"""Independent native extension discovery, dispatch, and resource attacks."""
import struct
import time
import unittest
import test_resource_isolation as resources
import security_pb2 as pb


class Client(resources.Client):
    def extension(self, name):
        name = name.encode()
        head, body = self.operation(98, self.pack('HH', len(name), 0)+resources.native.padded(name), reply=True)
        return head[9] if head[8] else None

    def extensions(self):
        head, body = self.operation(99, reply=True)
        names = []; offset = 0
        for _ in range(head[1]):
            size = body[offset]; offset += 1
            names.append(body[offset:offset+size].decode()); offset += size
        if len(names) != len(set(names)): raise AssertionError('Duplicate extension names')
        return names

    def formats(self):
        opcode = self.extension('RENDER')
        if opcode is None: raise AssertionError('RENDER missing')
        result = self.operation(opcode, self.pack('II', 0, 11), 0, True)
        if not isinstance(result, tuple): raise AssertionError(('RENDER version', result))
        head, body = self.operation(opcode, minor=1, reply=True)
        count = struct.unpack_from(self.endian+'I', head, 8)[0]
        if len(body) < count*28: raise AssertionError('Truncated public formats')
        formats = [struct.unpack_from(self.endian+'IBBH8HI', body, i*28) for i in range(count)]
        return opcode, formats

    def picture(self, drawable, depth=24):
        opcode, formats = self.formats()
        candidates = [f for f in formats if f[1] == 1 and f[2] == depth and
                      (depth != 24 or f[4:12] == (16, 255, 8, 255, 0, 255, 0, 0))]
        if not candidates: raise AssertionError('Required public format absent')
        picture = self.xid()
        result = self.operation(opcode, self.pack('IIII', picture, drawable, candidates[0][0], 0), 4)
        if result is not None: raise AssertionError(('CreatePicture denied', result))
        return opcode, picture

    def fill(self, opcode, picture, rgb=(0xffff, 0, 0)):
        return self.operation(opcode, self.pack('B3xIHHHHhhHH', 1, picture, *rgb, 0xffff, 0, 0, 16, 16), 26)

    def composite(self, opcode, source, destination, mask=0):
        return self.operation(opcode, self.pack('B3xIIIhhhhhhHH', 1, source, mask, destination,
                                              0, 0, 0, 0, 0, 0, 1, 1), 8)


class ExtensionIsolationTests(unittest.TestCase):
    setUp = resources.ResourceIsolationTests.setUp
    start = resources.ResourceIsolationTests.start
    scene = resources.ResourceIsolationTests.scene
    denied = resources.ResourceIsolationTests.denied

    def connect(self, cookie, **options):
        client = Client(self.display, cookie, **options); self.clients.append(client); return client

    def extension_required(self, client, name):
        opcode = client.extension(name); self.assertIsNotNone(opcode, name); return opcode

    def test_application_discovery_exposes_only_approved_extensions(self):
        self.scene()
        self.assertEqual(set(self.other.extensions()), {'BIG-REQUESTS', 'XC-MISC', 'RENDER', 'SHAPE', 'SPATIAL-SECURITY'})

    def test_query_hides_global_and_unreviewed_extensions(self):
        root = self.scene()
        for name in ('XTEST', 'X-Resource', 'SPATIAL-INPUT', 'Composite', 'MIT-SHM', 'XFIXES', 'DAMAGE'):
            with self.subTest(extension=name):
                self.extension_required(root, name)
                self.assertIsNone(self.other.extension(name))

    def test_direct_xtest_version_cannot_bypass_hidden_extension(self):
        root = self.scene(); opcode = self.extension_required(root, 'XTEST')
        payload = self.other.pack('BBH', 2, 0, 2)
        self.assertIsInstance(root.operation(opcode, payload, 0, True), tuple)
        self.denied(self.other, opcode, payload, 0, True)

    def test_direct_xtest_input_is_rejected_before_dispatch(self):
        root = self.scene(); opcode = self.extension_required(root, 'XTEST')
        # Valid native XTEST motion. A trusted service may use it on this disposable display.
        payload = self.other.pack('BBHIIIIhhIHBB', 6, 0, 0, 0, self.other.root, 0, 0, 8, 8, 0, 0, 0, 0)
        self.assertIsNone(root.operation(opcode, payload, 2))
        self.denied(self.other, opcode, payload, 2)

    def test_direct_resource_enumeration_is_rejected(self):
        root = self.scene(); opcode = self.extension_required(root, 'X-Resource')
        head, body = root.operation(opcode, minor=1, reply=True)
        self.assertGreaterEqual(struct.unpack_from(root.endian+'I', head, 8)[0], 3)
        self.denied(self.other, opcode, minor=1, reply=True)

    def test_direct_spatial_input_is_rejected(self):
        root = self.scene(); opcode = self.extension_required(root, 'SPATIAL-INPUT')
        self.denied(self.other, opcode, self.other.pack('I', 0))

    def test_unreviewed_dispatch_families_fail_closed(self):
        root = self.scene()
        for name in ('Composite', 'MIT-SHM', 'XFIXES', 'DAMAGE', 'SYNC', 'XInputExtension', 'XKEYBOARD'):
            with self.subTest(extension=name):
                self.denied(self.other, self.extension_required(root, name), minor=255)

    def test_malformed_forbidden_requests_do_not_enter_extension_parser(self):
        root = self.scene(); opcode = self.extension_required(root, 'XTEST')
        self.denied(self.other, opcode, minor=2)
        self.assertEqual(self.other.inspect().application, b'terminal')

    def test_broker_has_only_credential_and_transport_extensions(self):
        root = self.scene(); broker = self.connect(root.issue(pb.BROKER, b'', b'').cookie)
        self.assertEqual(set(broker.extensions()), {'BIG-REQUESTS', 'XC-MISC', 'SPATIAL-SECURITY'})
        self.denied(broker, self.extension_required(root, 'RENDER'), broker.pack('II', 0, 11), 0, True)
        self.assertEqual(broker.issue().role, pb.APPLICATION)

    def test_compositor_retains_service_extension_access(self):
        root = self.scene(); compositor = self.connect(root.issue(pb.COMPOSITOR, b'', b'').cookie)
        opcode = self.extension_required(compositor, 'XTEST')
        self.assertIsInstance(compositor.operation(opcode, compositor.pack('BBH', 2, 0, 2), 0, True), tuple)
        self.extension_required(compositor, 'SPATIAL-INPUT')
        self.assertEqual(set(compositor.extensions()), set(root.extensions()))

    def test_swapped_client_cannot_bypass_extension_policy(self):
        root = self.scene(endian='>'); opcode = self.extension_required(root, 'XTEST')
        self.assertIsNone(self.other.extension('XTEST'))
        self.denied(self.other, opcode, self.other.pack('BBH', 2, 0, 2), 0, True)

    def test_big_requests_and_client_local_ids_remain_usable(self):
        self.scene()
        opcode = self.extension_required(self.other, 'BIG-REQUESTS')
        self.assertIsInstance(self.other.operation(opcode, reply=True), tuple)
        opcode = self.extension_required(self.other, 'XC-MISC')
        head, body = self.other.operation(opcode, self.other.pack('I', 4), 2, True)
        self.assertEqual(struct.unpack_from(self.other.endian+'I', head, 8)[0], 4)
        ids = struct.unpack(self.other.endian+'IIII', body)
        self.assertTrue(all((x & ~self.other.mask) == self.other.base for x in ids))

    def test_unapproved_minor_opcode_is_not_implicitly_authorized(self):
        self.scene()
        for name in ('BIG-REQUESTS', 'XC-MISC', 'RENDER', 'SHAPE'):
            with self.subTest(extension=name): self.denied(self.other, self.extension_required(self.other, name), minor=255)

    def test_public_render_formats_allow_real_owned_pixel_output(self):
        self.scene(); opcode, picture = self.other.picture(self.other.pixmap())
        pixmap = self.other.pixmap(); opcode, destination = self.other.picture(pixmap)
        self.assertIsNone(self.other.fill(opcode, picture))
        self.assertIsNone(self.other.composite(opcode, picture, destination))
        head, body = self.other.operation(73, self.other.pack('IhhHHI', pixmap, 0, 0, 1, 1, 0xffffffff), 2, True)
        self.assertEqual(int.from_bytes(body, 'little') & 0xffffff, 0xff0000)

    def test_render_cannot_wrap_foreign_or_root_drawable(self):
        self.scene(); opcode, formats = self.other.formats(); form = next(f[0] for f in formats if f[1] == 1 and f[2] == 24)
        for drawable in (self.window, self.pixmap, self.other.root):
            with self.subTest(drawable=drawable):
                self.denied(self.other, opcode, self.other.pack('IIII', self.other.xid(), drawable, form, 0), 4)

    def render_scene(self):
        self.scene(); opcode, source = self.owner.picture(self.pixmap)
        destination_pixmap = self.other.pixmap(); _, destination = self.other.picture(destination_pixmap)
        self.assertIsNone(self.owner.fill(opcode, source))
        self.assertIsNone(self.other.fill(opcode, destination, (0, 0xffff, 0)))
        return opcode, source, destination, destination_pixmap

    def test_render_foreign_source_is_denied(self):
        opcode, source, destination, pixmap = self.render_scene()
        self.assertEqual(self.other.composite(opcode, source, destination), 10)
        head, body = self.other.operation(73, self.other.pack('IhhHHI', pixmap, 0, 0, 1, 1, 0xffffffff), 2, True)
        self.assertEqual(int.from_bytes(body, 'little') & 0xffffff, 0x00ff00)

    def test_render_foreign_destination_is_denied(self):
        opcode, source, destination, _ = self.render_scene()
        self.assertEqual(self.other.composite(opcode, destination, source), 10)

    def test_render_foreign_mask_is_denied(self):
        opcode, source, destination, _ = self.render_scene()
        self.assertEqual(self.other.composite(opcode, destination, destination, source), 10)

    def test_render_foreign_picture_mutation_and_release_are_denied(self):
        opcode, source, destination, _ = self.render_scene()
        self.denied(self.other, opcode, self.other.pack('III', source, 1, 1), 5)
        self.denied(self.other, opcode, self.other.pack('I', source), 7)
        self.assertIsNone(self.owner.fill(opcode, source))

    def test_render_foreign_alpha_map_and_clip_mask_are_denied(self):
        opcode, source, destination, _ = self.render_scene()
        self.denied(self.other, opcode, self.other.pack('III', destination, 2, source), 5)
        self.denied(self.other, opcode, self.other.pack('III', destination, 64, self.pixmap), 5)

    def test_glyph_set_reference_cannot_copy_foreign_authority(self):
        self.scene(); opcode, formats = self.owner.formats()
        form = next(f[0] for f in formats if f[1] == 1 and f[2] == 8)
        glyphs = self.owner.xid()
        self.assertIsNone(self.owner.operation(opcode, self.owner.pack('II', glyphs, form), 17))
        alias = self.owner.xid()
        self.assertIsNone(self.owner.operation(opcode, self.owner.pack('II', alias, glyphs), 18))
        self.denied(self.other, opcode, self.other.pack('II', self.other.xid(), glyphs), 18)
        self.denied(self.other, opcode, self.other.pack('I', glyphs), 19)
        self.assertIsNone(self.owner.operation(opcode, self.owner.pack('I', glyphs), 19))
        self.assertIsNone(self.owner.operation(opcode, self.owner.pack('I', alias), 19))

    def test_shape_changes_owned_window_only(self):
        self.scene(); opcode = self.extension_required(self.other, 'SHAPE')
        payload = self.other.pack('BBBBIhhhhHH', 0, 0, 0, 0, self.other_window, 0, 0, 1, 2, 8, 8)
        self.assertIsNone(self.other.operation(opcode, payload, 1))
        head, body = self.other.operation(opcode, self.other.pack('I', self.other_window), 5, True)
        self.assertEqual(head[8], 1)
        payload = self.other.pack('BBBBIhhhhHH', 0, 0, 0, 0, self.window, 0, 0, 1, 2, 8, 8)
        self.denied(self.other, opcode, payload, 1)

    def test_shape_foreign_queries_and_subscriptions_are_denied(self):
        self.scene(); opcode = self.extension_required(self.other, 'SHAPE')
        self.denied(self.other, opcode, self.other.pack('I', self.window), 5, True)
        self.denied(self.other, opcode, self.other.pack('IB3x', self.window, 1), 6)
        self.denied(self.other, opcode, self.other.pack('IB3x', self.window, 0), 8, True)

    def test_shape_combine_cannot_read_foreign_shape(self):
        self.scene(); opcode = self.extension_required(self.other, 'SHAPE')
        self.denied(self.other, opcode, self.other.pack('BBBBIhhI', 0, 0, 0, 0, self.other_window, 0, 0, self.window), 3)

    def test_shape_hole_does_not_reveal_underlying_foreign_pixels(self):
        self.scene(); opcode = self.extension_required(self.other, 'SHAPE')
        self.assertIsNone(self.owner.operation(8, self.owner.pack('I', self.window)))
        payload = self.other.pack('BBBBIhhhhHH', 0, 0, 0, 0, self.other_window, 0, 0, 8, 8, 8, 8)
        self.assertIsNone(self.other.operation(opcode, payload, 1))
        self.assertIsNone(self.other.operation(8, self.other.pack('I', self.other_window)))
        head, body = self.other.operation(73, self.other.pack('IhhHHI', self.other_window, 0, 0, 1, 1, 0xffffffff), 2, True)
        self.assertEqual(int.from_bytes(body, 'little') & 0xffffff, 0)

    def test_malformed_allowed_request_preserves_connection(self):
        self.scene(); opcode = self.extension_required(self.other, 'RENDER')
        self.assertEqual(self.other.operation(opcode, minor=4), 16)
        self.assertEqual(self.other.inspect().application, b'terminal')

    def test_render_cursor_and_animation_preserve_resource_authority(self):
        self.scene(); opcode, picture = self.owner.picture(self.pixmap)
        self.assertIsNone(self.owner.fill(opcode, picture))
        cursor = self.owner.xid()
        self.assertIsNone(self.owner.operation(opcode, self.owner.pack('IIHH', cursor, picture, 0, 0), 27))
        self.denied(self.other, opcode, self.other.pack('IIHH', self.other.xid(), picture, 0, 0), 27)
        self.denied(self.other, opcode, self.other.pack('III', self.other.xid(), cursor, 100), 31)
        self.assertIsNone(self.owner.operation(opcode, self.owner.pack('III', self.owner.xid(), cursor, 100), 31))

    def test_render_shape_hole_is_not_a_foreign_pixel_source(self):
        self.scene(); shape = self.extension_required(self.other, 'SHAPE')
        self.assertIsNone(self.owner.operation(8, self.owner.pack('I', self.window)))
        self.assertIsNone(self.other.operation(shape, self.other.pack('BBBBIhhhhHH',
            0, 0, 0, 0, self.other_window, 0, 0, 8, 8, 8, 8), 1))
        self.assertIsNone(self.other.operation(8, self.other.pack('I', self.other_window)))
        opcode, source = self.other.picture(self.other_window)
        pixmap = self.other.pixmap(); _, destination = self.other.picture(pixmap)
        self.assertIsNone(self.other.fill(opcode, destination, (0, 0xffff, 0)))
        self.assertEqual(self.other.composite(opcode, source, destination), 10)
        head, body = self.other.operation(73, self.other.pack('IhhHHI', pixmap, 0, 0, 1, 1, 0xffffffff), 2, True)
        self.assertEqual(int.from_bytes(body, 'little') & 0xffffff, 0x00ff00)

    def test_render_window_cursor_does_not_copy_pixels_outside_window_shape(self):
        root = self.scene(); client = self.other
        # Select an actual ARGB visual from the independent X setup response.
        vendor = struct.unpack_from(client.endian+'H', client.setup, 16)[0]
        screen = 32+(vendor+3)//4*4+client.setup[21]*8
        offset = screen+40; visual = None
        for _ in range(client.setup[screen+39]):
            depth = client.setup[offset]
            count = struct.unpack_from(client.endian+'H', client.setup, offset+2)[0]
            if depth == 32 and count:
                visual = struct.unpack_from(client.endian+'I', client.setup, offset+8)[0]
            offset += 8+count*24
        self.assertIsNotNone(visual, 'ARGB visual required for this native cursor attack')
        colormap = client.xid()
        self.assertIsNone(client.operation(78, client.pack('III', colormap, self.other_window, visual)))
        window = client.xid()
        self.assertIsNone(client.operation(1, client.pack('IIhhHHHHIIIII', window, client.root,
            0, 0, 32, 32, 0, 1, visual, 2|8|8192, 0xffffffff, 0, colormap), 32))
        shape = self.extension_required(client, 'SHAPE')
        self.assertIsNone(self.owner.operation(8, self.owner.pack('I', self.window)))
        self.assertIsNone(client.operation(shape, client.pack('BBBBIhhhhHH', 0, 0, 0, 0, window, 0, 0, 8, 8, 8, 8), 1))
        self.assertIsNone(client.operation(8, client.pack('I', window)))
        opcode, picture = client.picture(window, 32); cursor = client.xid()
        self.denied(client, opcode, client.pack('IIHH', cursor, picture, 0, 0), 27)

    def test_window_picture_is_a_real_drawing_target_but_not_a_read_alias(self):
        self.scene(); client = self.other
        self.assertIsNone(client.operation(8, client.pack('I', self.other_window)))
        opcode, picture = client.picture(self.other_window)
        self.assertIsNone(client.fill(opcode, picture))
        head, body = client.operation(73, client.pack('IhhHHI', self.other_window, 0, 0, 1, 1, 0xffffffff), 2, True)
        self.assertEqual(int.from_bytes(body, 'little') & 0xffffff, 0xff0000)
        _, destination = client.picture(client.pixmap())
        self.assertEqual(client.composite(opcode, picture, destination), 10)
        self.denied(client, opcode, client.pack('III', destination, 2, picture), 5)

    def test_procedural_source_is_usable_and_cursor_scratch_id_is_not_addressable(self):
        self.scene(); client = self.other; opcode, formats = client.formats()
        source = client.xid()
        self.assertIsNone(client.operation(opcode, client.pack('IHHHH', source, 0, 0, 0xffff, 0xffff), 33))
        pixmap = client.pixmap(); _, destination = client.picture(pixmap)
        self.assertIsNone(client.composite(opcode, source, destination))
        head, body = client.operation(73, client.pack('IhhHHI', pixmap, 0, 0, 1, 1, 0xffffffff), 2, True)
        self.assertEqual(int.from_bytes(body, 'little') & 0xffffff, 0x0000ff)
        form = next(f[0] for f in formats if f[1] == 1 and f[2] == 24)
        self.assertEqual(client.operation(opcode, client.pack('IIII', 0, pixmap, form, 0), 4), 14)

    def test_animated_cursor_timer_survives_use_then_releases_with_cursor(self):
        root = self.scene(); client = self.other
        opcode, picture = client.picture(client.pixmap())
        self.assertIsNone(client.fill(opcode, picture))
        cursor = client.xid()
        self.assertIsNone(client.operation(opcode, client.pack('IIHH', cursor, picture, 0, 0), 27))
        self.assertIsNone(client.operation(8, client.pack('I', self.other_window)))
        xtest = self.extension_required(root, 'XTEST')
        self.assertIsNone(root.operation(xtest, root.pack('BBHIIIIhhIHBB',
            6, 0, 0, 0, root.root, 0, 0, 9, 9, 0, 0, 0, 0), 2))
        for _ in range(8):
            animated = client.xid()
            self.assertIsNone(client.operation(opcode, client.pack('III', animated, cursor, 1), 31))
            self.assertIsNone(client.operation(2, client.pack('III', self.other_window, 16384, animated)))
            # Drop the XID while the window and pointer still retain the cursor.
            self.assertIsNone(client.operation(95, client.pack('I', animated)))
            time.sleep(.02)
            self.assertIsNone(client.operation(2, client.pack('III', self.other_window, 16384, cursor)))
        self.assertIsNone(client.operation(2, client.pack('III', self.other_window, 16384, 0)))
        self.assertIsNone(client.operation(95, client.pack('I', cursor)))


if __name__ == '__main__': unittest.main(verbosity=2)
