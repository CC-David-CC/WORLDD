"""Trusted reparenting must not disclose a foreign application's native XIDs."""
import struct
import unittest
import test_resource_isolation as resources


class ParentMetadataTests(unittest.TestCase):
    setUp = resources.ResourceIsolationTests.setUp
    start = resources.ResourceIsolationTests.start
    connect = resources.ResourceIsolationTests.connect
    scene = resources.ResourceIsolationTests.scene

    def reparented_scene(self, endian='<', same_application=False):
        supervisor = self.scene(endian=endian, same_workspace=same_application,
                                same_application=same_application)
        self.assertIsNone(supervisor.operation(7, supervisor.pack(
            'IIhh', self.other_window, self.window, 0, 0)))
        return supervisor

    def parent(self, client, window):
        reply = client.operation(15, client.pack('I', window), reply=True)
        self.assertIsInstance(reply, tuple)
        header, _ = reply
        return struct.unpack_from(client.endian + 'I', header, 12)[0]

    def reparent_event(self, client, window, parent, supervisor):
        self.assertIsNone(client.operation(2, client.pack('III', window, 1 << 11, 1 << 17)))
        self.assertIsNone(supervisor.operation(7, supervisor.pack('IIhh', window, parent, 0, 0)))
        event = client.exact(32)
        self.assertEqual(event[0], 21)
        self.assertEqual(struct.unpack_from(client.endian + 'II', event, 4), (window, window))
        return struct.unpack_from(client.endian + 'I', event, 12)[0]

    def test_query_tree_hides_foreign_parent(self):
        self.reparented_scene()
        self.assertEqual(self.parent(self.other, self.other_window), 0)

    def test_swapped_query_tree_hides_foreign_parent(self):
        self.reparented_scene(endian='>')
        self.assertEqual(self.parent(self.other, self.other_window), 0)

    def test_query_tree_preserves_same_application_parent(self):
        self.reparented_scene(same_application=True)
        self.assertEqual(self.parent(self.other, self.other_window), self.window)

    def test_query_tree_preserves_privileged_view(self):
        supervisor = self.reparented_scene()
        self.assertEqual(self.parent(supervisor, self.other_window), self.window)

    def test_query_tree_preserves_public_root_parent(self):
        self.scene()
        self.assertEqual(self.parent(self.other, self.other_window), self.other.root)

    def test_reparent_notify_hides_foreign_parent(self):
        supervisor = self.scene()
        self.assertEqual(self.reparent_event(self.other, self.other_window, self.window, supervisor), 0)

    def test_swapped_reparent_notify_hides_foreign_parent(self):
        supervisor = self.scene(endian='>')
        self.assertEqual(self.reparent_event(self.other, self.other_window, self.window, supervisor), 0)

    def test_reparent_notify_preserves_same_application_parent(self):
        supervisor = self.scene(same_workspace=True, same_application=True)
        self.assertEqual(self.reparent_event(self.other, self.other_window, self.window, supervisor), self.window)

    def test_reparent_notify_preserves_privileged_view(self):
        supervisor = self.scene()
        self.assertEqual(self.reparent_event(supervisor, self.other_window, self.window, supervisor), self.window)

    def test_reparent_notify_preserves_public_root_parent(self):
        supervisor = self.reparented_scene()
        self.assertEqual(self.reparent_event(self.other, self.other_window, self.other.root, supervisor), self.other.root)


if __name__ == '__main__':
    unittest.main(verbosity=2)
