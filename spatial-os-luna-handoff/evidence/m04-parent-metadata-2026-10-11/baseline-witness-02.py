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
        payload = supervisor.pack('IIhh', window, parent, 0, 0)
        if client is supervisor:
            supervisor.send(7, payload)
        else:
            self.assertIsNone(supervisor.operation(7, payload))
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

    def foreign_substructure(self, action):
        supervisor = self.scene()
        child = None
        if action != 'create':
            child = supervisor.create_window(self.window)
            if action == 'unmap':
                self.assertIsNone(supervisor.operation(8, supervisor.pack('I', child)))
        self.assertIsNone(self.owner.operation(2, self.owner.pack(
            'III', self.window, 1 << 11, 1 << 19)))
        if action == 'create':
            supervisor.create_window(self.window)
        elif action in ('map', 'unmap', 'destroy'):
            opcode = {'map': 8, 'unmap': 10, 'destroy': 4}[action]
            self.assertIsNone(supervisor.operation(opcode, supervisor.pack('I', child)))
        elif action == 'configure':
            self.assertIsNone(supervisor.operation(12, supervisor.pack('IH2xI', child, 1 << 2, 40)))
        elif action == 'reparent':
            self.assertIsNone(supervisor.operation(7, supervisor.pack('IIhh', child, supervisor.root, 0, 0)))
        else:
            self.fail('Unknown event probe')
        # A native reply fence detects any queued event. Do not discard events.
        self.assertEqual(self.owner.inspect().role, resources.pb.APPLICATION)

    def test_foreign_child_create_event_is_not_delivered(self):
        self.foreign_substructure('create')

    def test_foreign_child_map_event_is_not_delivered(self):
        self.foreign_substructure('map')

    def test_foreign_child_unmap_event_is_not_delivered(self):
        self.foreign_substructure('unmap')

    def test_foreign_child_destroy_event_is_not_delivered(self):
        self.foreign_substructure('destroy')

    def test_foreign_child_configure_event_is_not_delivered(self):
        self.foreign_substructure('configure')

    def test_foreign_child_reparent_event_is_not_delivered(self):
        self.foreign_substructure('reparent')

    def test_same_application_child_create_event_is_delivered(self):
        self.scene(same_workspace=True, same_application=True)
        self.assertIsNone(self.owner.operation(2, self.owner.pack(
            'III', self.window, 1 << 11, 1 << 19)))
        child = self.other.create_window(self.window)
        event = self.owner.exact(32)
        self.assertEqual(event[0], 16)
        self.assertEqual(struct.unpack_from(self.owner.endian + 'II', event, 4), (self.window, child))


if __name__ == '__main__':
    unittest.main(verbosity=2)
