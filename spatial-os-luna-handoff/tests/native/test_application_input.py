"""Real application connections must not share the fallback core devices."""
import struct
import time
import unittest
import test_resource_isolation as resources


class Client(resources.Client):
    def exact(self, size):
        data = super().exact(size)
        # XKB can send MappingNotify when a real device changes its keymap.
        # This fixture selects no input events. Keep all other events errors.
        while size == 32 and data[0] == 34:
            data = super().exact(size)
        return data


class ApplicationInputTests(unittest.TestCase):
    setUp = resources.ResourceIsolationTests.setUp
    start = resources.ResourceIsolationTests.start
    scene = resources.ResourceIsolationTests.scene

    def connect(self, cookie, **options):
        client = Client(self.display, cookie, **options)
        self.clients.append(client)
        return client

    def mapped_scene(self, **options):
        supervisor = self.scene(**options)
        for client, window in ((self.owner, self.window), (self.other, self.other_window)):
            self.assertIsNone(client.operation(8, client.pack('I', window)))
        return supervisor

    def focus(self, client):
        head, _ = client.operation(43, reply=True)
        return struct.unpack_from(client.endian+'I', head, 8)[0]

    def set_focus(self, client, window):
        self.assertIsNone(client.operation(42, client.pack('II', window, 0)))

    def pointer(self, client, window):
        head, _ = client.operation(38, client.pack('I', window), reply=True)
        return struct.unpack_from(client.endian+'hhhhH', head, 16)

    def warp(self, client, window, x, y):
        self.assertIsNone(client.operation(41, client.pack('IIhhHHhh', 0, window, 0, 0, 0, 0, x, y)))

    def test_foreign_application_cannot_read_focus(self):
        self.mapped_scene()
        self.set_focus(self.owner, self.window)
        self.assertEqual(self.focus(self.owner), self.window)
        self.assertEqual(self.focus(self.other), 0)

    def test_foreign_application_cannot_change_focus(self):
        self.mapped_scene()
        self.set_focus(self.owner, self.window)
        self.set_focus(self.other, self.other_window)
        self.assertEqual(self.focus(self.owner), self.window)
        self.assertEqual(self.focus(self.other), self.other_window)

    def test_workspace_membership_does_not_share_keyboard(self):
        self.mapped_scene(same_workspace=True)
        self.set_focus(self.owner, self.window)
        self.assertEqual(self.focus(self.other), 0)

    def test_same_application_name_in_other_workspace_is_isolated(self):
        self.mapped_scene(same_application=True)
        self.set_focus(self.owner, self.window)
        self.assertEqual(self.focus(self.other), 0)

    def test_swapped_application_has_private_keyboard(self):
        self.mapped_scene(endian='>')
        self.set_focus(self.owner, self.window)
        self.assertEqual(self.focus(self.other), 0)
        self.set_focus(self.other, self.other_window)
        self.assertEqual(self.focus(self.owner), self.window)

    def test_same_application_connections_share_real_focus(self):
        self.mapped_scene(same_workspace=True, same_application=True)
        self.set_focus(self.owner, self.window)
        self.assertEqual(self.focus(self.other), self.window)
        self.set_focus(self.other, self.other_window)
        self.assertEqual(self.focus(self.owner), self.other_window)

    def test_shared_pointer_motion_is_not_observable(self):
        supervisor = self.mapped_scene()
        before = self.pointer(self.other, self.other_window)
        self.warp(supervisor, supervisor.root, 77, 83)
        self.assertEqual(self.pointer(supervisor, supervisor.root)[:2], (77, 83))
        self.assertEqual(self.pointer(self.other, self.other_window), before)

    def test_application_warp_cannot_move_another_pointer(self):
        supervisor = self.mapped_scene()
        before = self.pointer(supervisor, supervisor.root)
        foreign = self.pointer(self.owner, self.window)
        self.warp(self.other, self.other_window, 13, 17)
        self.assertEqual(self.pointer(self.other, self.other_window)[2:4], (13, 17))
        self.assertEqual(self.pointer(self.owner, self.window), foreign)
        self.assertEqual(self.pointer(supervisor, supervisor.root), before)

    def grab(self, client, window, keyboard):
        payload = client.pack('IIBB2x', window, 0, 1, 1) if keyboard else client.pack('IHBBIII', window, 0, 1, 1, 0, 0, 0)
        head, _ = client.operation(31 if keyboard else 26, payload, reply=True)
        return head[1]

    def test_application_keyboard_grabs_do_not_compete(self):
        self.mapped_scene()
        self.assertEqual(self.grab(self.other, self.other_window, True), 0)
        self.assertEqual(self.grab(self.owner, self.window, True), 0)

    def test_application_pointer_grabs_do_not_compete(self):
        self.mapped_scene()
        self.assertEqual(self.grab(self.other, self.other_window, False), 0)
        self.assertEqual(self.grab(self.owner, self.window, False), 0)

    def test_same_application_grabs_still_compete(self):
        self.mapped_scene(same_workspace=True, same_application=True)
        self.assertEqual(self.grab(self.other, self.other_window, True), 0)
        self.assertEqual(self.grab(self.owner, self.window, True), 1)

    def test_shared_held_key_is_not_observable(self):
        supervisor = self.mapped_scene()
        name = b'XTEST'
        header, _ = supervisor.operation(98, supervisor.pack('HH', len(name), 0)+name, reply=True)
        self.assertTrue(header[8])
        def held(client):
            head, body = client.operation(44, reply=True)
            bitmap = head[8:]+body
            self.assertEqual(len(bitmap), 32)
            return bool(bitmap[38//8] & (1 << (38 % 8)))
        def inject(kind):
            payload = supervisor.pack('BBHIIIIhhIHBB', kind, 38, 0, 0, supervisor.root, 0, 0, 0, 0, 0, 0, 0, 0)
            self.assertIsNone(supervisor.operation(header[9], payload, minor=2))
        self.assertFalse(held(self.other))
        inject(2)
        try:
            deadline = time.monotonic()+1
            while not held(supervisor) and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(held(supervisor))
            self.assertFalse(held(self.other))
        finally:
            inject(3)

    def test_late_connection_joins_existing_application_input(self):
        supervisor = self.mapped_scene()
        self.set_focus(self.owner, self.window)
        another = self.connect(supervisor.issue(workspace=b'one', application=b'editor').cookie)
        self.assertEqual(self.focus(another), self.window)
        self.owner.close()
        # A window owned by the surviving connection remains a valid focus.
        window = another.create_window()
        self.assertIsNone(another.operation(8, another.pack('I', window)))
        self.set_focus(another, window)
        self.assertEqual(self.focus(another), window)

    def test_last_disconnect_reclaims_device_pairs(self):
        supervisor = self.start()
        for i in range(140):
            cookie = supervisor.issue(workspace=b'cycles', application=str(i).encode()).cookie
            client = self.connect(cookie)
            self.assertEqual(self.focus(client), 0)
            window = client.create_window()
            client.close()
            deadline = time.monotonic()+2
            while window in supervisor.tree(supervisor.root) and time.monotonic() < deadline:
                time.sleep(.002)
            self.assertNotIn(window, supervisor.tree(supervisor.root))

    def test_device_exhaustion_denies_connection_and_recovers(self):
        supervisor = self.start()
        opened = []
        denied_cookie = None
        for i in range(140):
            cookie = supervisor.issue(workspace=b'capacity', application=str(i).encode()).cookie
            try:
                client = self.connect(cookie)
            except resources.native.Denied:
                denied_cookie = cookie
                break
            opened.append((client, client.create_window()))
        self.assertIsNotNone(denied_cookie, 'Device allocation must remain bounded')
        self.assertTrue(opened)
        self.assertEqual(self.focus(opened[0][0]), 0)
        for client, _ in opened:
            client.close()
        deadline = time.monotonic()+3
        while supervisor.tree(supervisor.root) and time.monotonic() < deadline:
            time.sleep(.002)
        self.assertEqual(supervisor.tree(supervisor.root), [])
        recovered = self.connect(denied_cookie)
        self.assertEqual(self.focus(recovered), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
