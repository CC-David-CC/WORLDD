"""Independent asynchronous X11 events must not enumerate foreign windows."""
import struct
import unittest
import test_resource_isolation as resources


class EventMetadataTests(unittest.TestCase):
    setUp = resources.ResourceIsolationTests.setUp
    start = resources.ResourceIsolationTests.start
    connect = resources.ResourceIsolationTests.connect
    scene = resources.ResourceIsolationTests.scene

    def configure_event(self, client, window):
        # StructureNotifyMask on an owned window. No root subscription.
        self.assertIsNone(client.operation(2, client.pack('III', window, 1 << 11, 1 << 17)))
        client.send(12, client.pack('IH2xI', window, 1 << 2, 40))
        event = client.exact(32)
        self.assertEqual(event[0], 22, 'Expected real ConfigureNotify')
        self.assertEqual(struct.unpack_from(client.endian + 'II', event, 4), (window, window))
        return struct.unpack_from(client.endian + 'I', event, 12)[0]

    def test_configure_notify_does_not_disclose_foreign_sibling(self):
        self.scene()
        window = self.owner.create_window()
        self.assertEqual(self.configure_event(self.owner, window), self.window,
                         'ConfigureNotify must not enumerate a foreign sibling')

    def test_swapped_configure_notify_does_not_disclose_foreign_sibling(self):
        self.scene(endian='>')
        self.owner.create_window()
        window = self.other.create_window()
        self.assertEqual(self.configure_event(self.other, window), self.other_window)

    def test_configure_notify_preserves_authorized_same_application_sibling(self):
        self.scene(same_workspace=True, same_application=True)
        window = self.owner.create_window()
        self.assertEqual(self.configure_event(self.owner, window), self.other_window)

    def test_filter_does_not_change_the_privileged_recipients_event(self):
        supervisor = self.scene()
        window = self.owner.create_window()
        self.assertIsNone(supervisor.operation(2, supervisor.pack('III', window, 1 << 11, 1 << 17)))
        self.assertEqual(self.configure_event(self.owner, window), self.window)
        event = supervisor.exact(32)
        self.assertEqual(event[0], 22)
        self.assertEqual(struct.unpack_from(supervisor.endian + 'I', event, 12)[0], self.other_window)

    def redirected_configure(self, endian='<', same_application=False, privileged=False):
        supervisor = self.scene(endian=endian, same_workspace=same_application,
                                same_application=same_application)
        child = self.other.create_window(self.other_window)
        self.assertIsNone(supervisor.operation(7, supervisor.pack(
            'IIhh', self.window, self.other_window, 0, 0)))
        observer = supervisor if privileged else self.other
        requester = self.connect(supervisor.issue(role=resources.pb.COMPOSITOR,
                                                  workspace=b'', application=b'').cookie)
        self.assertIsNone(observer.operation(2, observer.pack(
            'III', self.other_window, 1 << 11, 1 << 20)))
        self.assertIsNone(requester.operation(12, requester.pack(
            'IH2xII', child, (1 << 5) | (1 << 6), self.window, 0)))
        event = observer.exact(32)
        self.assertEqual(event[0], 23, 'Expected real ConfigureRequest')
        self.assertEqual(struct.unpack_from(observer.endian + 'II', event, 4),
                         (self.other_window, child))
        sibling = struct.unpack_from(observer.endian + 'I', event, 12)[0]
        mask = struct.unpack_from(observer.endian + 'H', event, 26)[0]
        return sibling, mask

    def test_configure_request_does_not_disclose_foreign_sibling(self):
        sibling, mask = self.redirected_configure()
        self.assertEqual(sibling, 0)
        self.assertEqual(mask, 1 << 6)

    def test_swapped_configure_request_does_not_disclose_foreign_sibling(self):
        sibling, mask = self.redirected_configure(endian='>')
        self.assertEqual(sibling, 0)
        self.assertEqual(mask, 1 << 6)

    def test_configure_request_preserves_same_application_sibling(self):
        sibling, mask = self.redirected_configure(same_application=True)
        self.assertEqual(sibling, self.window)
        self.assertEqual(mask, (1 << 5) | (1 << 6))

    def test_configure_request_preserves_privileged_sibling(self):
        sibling, mask = self.redirected_configure(privileged=True)
        self.assertEqual(sibling, self.window)
        self.assertEqual(mask, (1 << 5) | (1 << 6))


if __name__ == '__main__':
    unittest.main(verbosity=2)
