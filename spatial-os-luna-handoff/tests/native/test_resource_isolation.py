"""Independent native-wire attacks against the production X resource boundary."""
import struct
import time
import unittest
import test_security_identity as native
import security_pb2 as pb


class Client(native.NativeClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.base, self.mask = struct.unpack_from(self.endian+'II', self.setup, 4)
        vendor = struct.unpack_from(self.endian+'H', self.setup, 16)[0]
        screen = 32 + (vendor+3)//4*4 + self.setup[21]*8
        self.root = struct.unpack_from(self.endian+'I', self.setup, screen)[0]
        self.white = struct.unpack_from(self.endian+'I', self.setup, screen+8)[0]
        self.black = struct.unpack_from(self.endian+'I', self.setup, screen+12)[0]
        self.depth = self.setup[screen+38]
        self.next_id = 1

    def pack(self, fmt, *values): return struct.pack(self.endian+fmt, *values)

    def xid(self):
        value = self.base | self.next_id
        self.next_id += 1
        if value & self.mask == 0: raise AssertionError('Resource ID exhausted')
        return value

    def operation(self, opcode, payload=b'', minor=0, reply=False):
        """Fence with an independent identity request and retain native errors."""
        self.send(opcode, payload, minor)
        target = self.sequence % 65536
        self.operation_sequence = getattr(self, 'operation_sequence', 0)+1
        query = pb.Request(protocol_version=1, request_id=self.operation_sequence, inspect=pb.Inspect())
        data = query.SerializeToString()
        self.send(self.opcode, self.pack('I', len(data))+data)
        fence = self.sequence % 65536
        result = None
        while True:
            header = self.exact(32)
            sequence = struct.unpack_from(self.endian+'H', header, 2)[0]
            if header[0] == 1:
                size = struct.unpack_from(self.endian+'I', header, 4)[0]*4
                if size > 1048576: raise AssertionError('Excessive reply')
                body = self.exact(size)
                if sequence == fence:
                    size = struct.unpack_from(self.endian+'I', header, 8)[0]
                    if pb.Reply.FromString(body[:size]).code != pb.OK: raise AssertionError('Fence failed')
                    if reply and result is None: raise AssertionError('Missing native reply')
                    return result
                if sequence != target or result is not None: raise AssertionError('Unexpected reply sequence')
                result = (header, body)
            elif header[0] == 0:
                if sequence != target or result is not None: raise AssertionError('Unexpected native error')
                result = header[1]
            else:
                raise AssertionError('Unexpected event in resource witness: '+str(header[0]))

    def create_window(self, parent=None):
        window = self.xid()
        result = self.operation(1, self.pack('IIhhHHHHII', window, parent or self.root,
                                0, 0, 32, 32, 0, 1, 0, 2)+self.pack('I', 0x234567), self.depth)
        if result is not None: raise AssertionError(('CreateWindow failed', result))
        return window

    def pixmap(self, drawable=None):
        pixmap = self.xid()
        result = self.operation(53, self.pack('IIHH', pixmap, drawable or self.root, 16, 16), self.depth)
        if result is not None: raise AssertionError(('CreatePixmap failed', result))
        return pixmap

    def gc(self, drawable):
        gc = self.xid()
        result = self.operation(55, self.pack('III', gc, drawable, 4)+self.pack('I', 0x789abc))
        if result is not None: raise AssertionError(('CreateGC failed', result))
        return gc

    def tree(self, window):
        result = self.operation(15, self.pack('I', window), reply=True)
        if isinstance(result, int): return result
        head, body = result
        count = struct.unpack_from(self.endian+'H', head, 16)[0]
        if len(body) != count*4: raise AssertionError('Invalid tree length')
        return list(struct.unpack(self.endian+'I'*count, body))


class ResourceIsolationTests(unittest.TestCase):
    # Share only disposable-server plumbing. All attacks use independent X11 wire messages.
    setUp = native.SecurityIdentityTests.setUp
    start = native.SecurityIdentityTests.start
    assert_closed = native.SecurityIdentityTests.assert_closed

    def connect(self, cookie, **options):
        client = Client(self.display, cookie, **options)
        self.clients.append(client)
        return client

    def scene(self, endian='<', same_workspace=False, same_application=False):
        root = self.start()
        self.owner = self.connect(root.issue(workspace=b'one', application=b'editor').cookie)
        self.other = self.connect(root.issue(workspace=b'one' if same_workspace else b'two',
                                            application=b'editor' if same_application else b'terminal').cookie,
                                  endian=endian)
        self.window = self.owner.create_window()
        self.other_window = self.other.create_window()
        self.pixmap = self.owner.pixmap()
        self.gc = self.owner.gc(self.pixmap)
        self.assertIsNone(self.owner.operation(70, self.owner.pack('IIhhHH', self.pixmap, self.gc, 0, 0, 16, 16)))
        return root

    def denied(self, client, opcode, payload=b'', minor=0, reply=False):
        self.assertEqual(client.operation(opcode, payload, minor, reply), 10, 'Expected native BadAccess')

    def test_root_tree_filters_other_workspaces(self):
        root = self.scene()
        self.assertIn(self.window, root.tree(root.root))
        self.assertEqual(self.other.tree(self.other.root), [self.other_window])

    def test_same_workspace_does_not_grant_other_application_access(self):
        self.scene(same_workspace=True)
        self.denied(self.other, 14, self.other.pack('I', self.window), reply=True)
        self.assertEqual(self.other.tree(self.other.root), [self.other_window])

    def test_same_application_name_in_other_workspace_is_not_same_identity(self):
        self.scene(same_application=True)
        self.denied(self.other, 14, self.other.pack('I', self.window), reply=True)

    def test_separate_connections_of_same_application_share_resources(self):
        self.scene(same_workspace=True, same_application=True)
        self.assertIsInstance(self.other.operation(14, self.other.pack('I', self.window), reply=True), tuple)
        self.assertEqual(set(self.other.tree(self.other.root)), {self.window, self.other_window})
        self.assertIsNone(self.other.operation(70, self.other.pack('IIhhHH', self.pixmap, self.gc, 0, 0, 1, 1)))

    def test_foreign_window_tree_and_attributes_are_denied(self):
        self.scene()
        for opcode in (3, 14, 15, 21):
            with self.subTest(opcode=opcode): self.denied(self.other, opcode, self.other.pack('I', self.window), reply=True)

    def test_foreign_window_changes_are_denied(self):
        root = self.scene()
        for opcode, payload in ((2, self.other.pack('III', self.window, 2, 0)),
                                (8, self.other.pack('I', self.window)), (10, self.other.pack('I', self.window)),
                                (12, self.other.pack('IHHI', self.window, 1, 0, 30)),
                                (4, self.other.pack('I', self.window))):
            with self.subTest(opcode=opcode): self.denied(self.other, opcode, payload)
        self.assertIn(self.window, root.tree(root.root))

    def test_foreign_parent_cannot_receive_new_children(self):
        self.scene()
        self.denied(self.other, 1, self.other.pack('IIhhHHHHII', self.other.xid(), self.window,
                                               0, 0, 2, 2, 0, 1, 0, 0), self.other.depth)

    def test_reparent_cannot_cross_application_boundary(self):
        self.scene()
        self.denied(self.other, 7, self.other.pack('IIhh', self.other_window, self.window, 0, 0))
        self.assertEqual(self.owner.tree(self.window), [])

    def test_foreign_pixels_cannot_be_read(self):
        self.scene()
        self.denied(self.other, 73, self.other.pack('IhhHHI', self.pixmap, 0, 0, 1, 1, 0xffffffff), 2, True)
        head, body = self.owner.operation(73, self.owner.pack('IhhHHI', self.pixmap, 0, 0, 1, 1, 0xffffffff), 2, True)
        self.assertEqual(len(body), 4)
        self.assertEqual(int.from_bytes(body, 'little') & 0xffffff, 0x789abc)

    def test_root_pixels_cannot_be_read(self):
        self.scene()
        self.denied(self.other, 73, self.other.pack('IhhHHI', self.other.root, 0, 0, 1, 1, 0xffffffff), 2, True)

    def test_copy_area_cannot_read_foreign_pixels(self):
        self.scene(); target = self.other.pixmap(); gc = self.other.gc(target)
        self.denied(self.other, 62, self.other.pack('IIIhhhhHH', self.pixmap, target, gc, 0, 0, 0, 0, 1, 1))

    def test_copy_area_cannot_write_foreign_pixels(self):
        self.scene(); source = self.other.pixmap(); gc = self.other.gc(source)
        self.denied(self.other, 62, self.other.pack('IIIhhhhHH', source, self.pixmap, gc, 0, 0, 0, 0, 1, 1))

    def test_foreign_graphics_context_cannot_be_used_or_changed(self):
        self.scene(); pixmap = self.other.pixmap()
        self.denied(self.other, 70, self.other.pack('IIhhHH', pixmap, self.gc, 0, 0, 1, 1))
        self.denied(self.other, 56, self.other.pack('III', self.gc, 4, 0xffffff))

    def test_foreign_resource_cannot_be_freed(self):
        self.scene()
        self.denied(self.other, 54, self.other.pack('I', self.pixmap))
        self.denied(self.other, 60, self.other.pack('I', self.gc))
        self.assertIsInstance(self.owner.operation(14, self.owner.pack('I', self.pixmap), reply=True), tuple)

    def test_foreign_property_read_and_write_are_denied(self):
        self.scene()
        self.assertIsNone(self.owner.operation(18, self.owner.pack('IIIB3xI', self.window, 39, 31, 8, 6)+b'secret'))
        self.denied(self.other, 20, self.other.pack('IIIII', self.window, 39, 0, 0, 16), reply=True)
        self.denied(self.other, 18, self.other.pack('IIIB3xI', self.window, 39, 31, 8, 1)+b'x')
        self.denied(self.other, 19, self.other.pack('II', self.window, 39))
        head, body = self.owner.operation(20, self.owner.pack('IIIII', self.window, 39, 0, 0, 16), reply=True)
        self.assertEqual(body[:6], b'secret')

    def test_root_properties_are_private_to_supervisor_and_compositor(self):
        root = self.scene()
        self.assertIsNone(root.operation(18, root.pack('IIIB3xI', root.root, 39, 31, 8, 6)+b'secret'))
        self.denied(self.other, 20, self.other.pack('IIIII', self.other.root, 39, 0, 0, 16), reply=True)
        self.denied(self.other, 18, self.other.pack('IIIB3xI', self.other.root, 39, 31, 8, 1)+b'x')

    def test_root_subwindow_operations_cannot_change_other_applications(self):
        self.scene()
        for opcode in (5, 9, 11):
            with self.subTest(opcode=opcode): self.denied(self.other, opcode, self.other.pack('I', self.other.root))

    def test_compositor_can_observe_application_resources(self):
        root = self.scene(); compositor = self.connect(root.issue(pb.COMPOSITOR, b'', b'').cookie)
        self.assertEqual(set(compositor.tree(compositor.root)), {self.window, self.other_window})
        self.assertIsInstance(compositor.operation(73, compositor.pack('IhhHHI', self.pixmap, 0, 0, 1, 1, 0xffffffff), 2, True), tuple)

    def test_broker_role_does_not_grant_application_pixels(self):
        root = self.scene(); broker = self.connect(root.issue(pb.BROKER, b'', b'').cookie)
        self.denied(broker, 73, broker.pack('IhhHHI', self.pixmap, 0, 0, 1, 1, 0xffffffff), 2, True)

    def test_swapped_client_cannot_bypass_resource_checks(self):
        self.scene(endian='>')
        self.denied(self.other, 14, self.other.pack('I', self.window), reply=True)
        self.assertEqual(self.other.tree(self.other.root), [self.other_window])

    def test_application_resources_cannot_be_retained_after_disconnect(self):
        self.scene()
        for mode in (1, 2):
            with self.subTest(mode=mode): self.denied(self.owner, 112, minor=mode)
        self.assertIsNone(self.owner.operation(112, minor=0))
        self.owner.close()
        deadline = time.monotonic()+3
        while self.window in self.supervisor.tree(self.supervisor.root) and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.supervisor.tree(self.supervisor.root), [self.other_window])

    def test_revocation_destroys_resources_before_acknowledgement(self):
        root = self.scene(); grant = root.issue(workspace=b'three', application=b'editor')
        victim = self.connect(grant.cookie); window = victim.create_window()
        self.assertIn(window, root.tree(root.root))
        self.assertEqual(root.call(revoke=pb.Revoke(credential_id=grant.id)).code, pb.OK)
        self.assert_closed(victim)
        self.assertNotIn(window, root.tree(root.root))
        self.assertIn(self.other_window, self.other.tree(self.other.root))

    def test_foreign_send_event_cannot_inject_application_messages(self):
        self.scene()
        event = self.other.pack('BBHII20s', 33, 8, 0, self.window, 39, b'attack')
        self.denied(self.other, 25, self.other.pack('II', self.window, 0)+event)
        self.assertEqual(self.owner.inspect().application, b'editor')

    def test_input_focus_alias_cannot_bypass_send_event_authorization(self):
        root = self.scene()
        self.assertIsNone(self.owner.operation(8, self.owner.pack('I', self.window)))
        self.assertIsNone(root.operation(42, root.pack('II', self.window, 0)))
        event = self.other.pack('BBHII20s', 33, 8, 0, self.window, 39, b'attack')
        self.denied(self.other, 25, self.other.pack('II', 1, 0)+event)
        self.assertEqual(self.owner.inspect().application, b'editor')

    def test_root_event_selection_cannot_observe_other_applications(self):
        self.scene()
        self.denied(self.other, 2, self.other.pack('III', self.other.root, 1<<11, 1<<19))

    def test_owned_window_can_be_destroyed_without_root_mutation_authority(self):
        root = self.scene()
        self.assertIsNone(self.owner.operation(4, self.owner.pack('I', self.window)))
        self.assertEqual(root.tree(root.root), [self.other_window])

    def test_owned_child_can_be_reparented_to_root(self):
        self.scene(); child = self.owner.create_window(self.window)
        self.assertEqual(self.owner.tree(self.window), [child])
        self.assertIsNone(self.owner.operation(7, self.owner.pack('IIhh', child, self.owner.root, 0, 0)))
        self.assertEqual(set(self.owner.tree(self.owner.root)), {self.window, child})

    def test_translate_coordinates_does_not_disclose_foreign_child(self):
        self.scene()
        self.assertIsNone(self.owner.operation(8, self.owner.pack('I', self.window)))
        head, body = self.other.operation(40, self.other.pack('IIhh', self.other_window, self.other.root, 1, 1), reply=True)
        self.assertEqual(struct.unpack_from(self.other.endian+'I', head, 8)[0], 0)

    def test_wrong_application_cannot_kill_resource_owner(self):
        self.scene()
        self.denied(self.other, 113, self.other.pack('I', self.window))
        self.assertEqual(self.owner.inspect().application, b'editor')

    def test_reused_client_slot_does_not_retain_old_application_authority(self):
        root = self.scene(); prior = self.window
        self.owner.close(); deadline = time.monotonic()+3
        while prior in root.tree(root.root) and time.monotonic() < deadline: time.sleep(.01)
        self.assertNotIn(prior, root.tree(root.root))
        replacement = self.connect(root.issue(workspace=b'replacement', application=b'replacement').cookie)
        new_window = replacement.create_window()
        self.assertEqual(new_window, prior, 'Witness must exercise actual client-slot and XID reuse')
        old_identity = self.connect(root.issue(workspace=b'one', application=b'editor').cookie)
        self.denied(old_identity, 14, old_identity.pack('I', new_window), reply=True)
        self.assertIsInstance(replacement.operation(14, replacement.pack('I', new_window), reply=True), tuple)

    def test_none_background_cannot_read_pixels_from_window_underneath(self):
        self.scene()
        self.assertIsNone(self.owner.operation(8, self.owner.pack('I', self.window)))
        self.assertIsNone(self.other.operation(2, self.other.pack('III', self.other_window, 1, 0)))
        self.assertIsNone(self.other.operation(8, self.other.pack('I', self.other_window)))
        head, body = self.other.operation(73, self.other.pack('IhhHHI', self.other_window, 0, 0, 1, 1, 0xffffffff), 2, True)
        self.assertEqual(int.from_bytes(body, 'little') & 0xffffff, self.other.white & 0xffffff)

    def test_parent_relative_cannot_copy_private_root_background(self):
        root = self.scene()
        self.assertIsNone(root.operation(2, root.pack('III', root.root, 2, 0x997733)))
        self.denied(self.other, 2, self.other.pack('III', self.other_window, 1, 1))

    def test_owned_parent_relative_background_remains_available(self):
        self.scene(); child = self.owner.create_window(self.window)
        self.assertIsNone(self.owner.operation(2, self.owner.pack('III', child, 1, 1)))
        self.assertIsNone(self.owner.operation(8, self.owner.pack('I', self.window)))
        self.assertIsNone(self.owner.operation(8, self.owner.pack('I', child)))
        head, body = self.owner.operation(73, self.owner.pack('IhhHHI', child, 0, 0, 1, 1, 0xffffffff), 2, True)
        self.assertEqual(int.from_bytes(body, 'little') & 0xffffff, 0x234567)

    def test_reparent_to_root_cannot_turn_owned_background_into_private_pixels(self):
        self.scene(); child = self.owner.create_window(self.window)
        self.assertIsNone(self.owner.operation(2, self.owner.pack('III', child, 1, 1)))
        self.denied(self.owner, 7, self.owner.pack('IIhh', child, self.owner.root, 0, 0))

    def test_explicit_border_copy_cannot_read_private_root_color(self):
        root = self.scene()
        self.assertIsNone(root.operation(2, root.pack('III', root.root, 8, 0x994433)))
        self.denied(self.other, 2, self.other.pack('III', self.other_window, 4, 0))

    def test_implicit_border_copy_uses_public_color_for_foreign_parent(self):
        root = self.scene()
        self.assertIsNone(root.operation(2, root.pack('III', root.root, 8, 0x994433)))
        window = self.other.xid()
        self.assertIsNone(self.other.operation(1, self.other.pack('IIhhHHHHII', window, self.other.root,
                                            10, 10, 16, 16, 3, 1, 0, 2)+self.other.pack('I', 0), self.other.depth))
        self.assertIsNone(self.other.operation(8, self.other.pack('I', window)))
        head, body = self.other.operation(73, self.other.pack('IhhHHI', window, -1, -1, 1, 1, 0xffffffff), 2, True)
        self.assertEqual(int.from_bytes(body, 'little') & 0xffffff, self.other.black & 0xffffff)


if __name__ == '__main__': unittest.main(verbosity=2)
