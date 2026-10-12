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

    def foreign_substructure(self, action, endian='<', authorized=False):
        supervisor = self.scene(endian=endian, same_workspace=authorized, same_application=authorized)
        creator = self.owner if authorized else supervisor
        observer = self.other
        parent = self.other_window
        child = None
        if action != 'create':
            child = creator.create_window(parent)
            if action == 'unmap':
                self.assertIsNone(creator.operation(8, creator.pack('I', child)))
            if action in ('circulate', 'circulate_request'):
                sibling = creator.create_window(parent)
                for window in (child, sibling):
                    self.assertIsNone(creator.operation(8, creator.pack('I', window)))
            if action == 'gravity':
                self.assertIsNone(creator.operation(2, creator.pack('III', child, 1 << 5, 9)))
        event_mask = 1 << (20 if action.endswith('_request') else 19)
        self.assertIsNone(observer.operation(2, observer.pack(
            'III', parent, 1 << 11, event_mask)))
        if action == 'create':
            child = creator.create_window(parent)
        elif action in ('map', 'map_request', 'unmap', 'destroy'):
            opcode = {'map': 8, 'map_request': 8, 'unmap': 10, 'destroy': 4}[action]
            self.assertIsNone(creator.operation(opcode, creator.pack('I', child)))
        elif action in ('configure', 'configure_request'):
            self.assertIsNone(creator.operation(12, creator.pack('IH2xI', child, 1 << 2, 40)))
        elif action in ('circulate', 'circulate_request'):
            self.assertIsNone(creator.operation(13, creator.pack('I', parent), 0))
        elif action == 'gravity':
            self.assertIsNone(creator.operation(12, creator.pack('IH2xII', parent, 12, 40, 40)))
        elif action == 'reparent':
            self.assertIsNone(creator.operation(7, creator.pack('IIhh', child, creator.root, 0, 0)))
        else:
            self.fail('Unknown event probe')
        if authorized:
            event = observer.exact(32)
            expected = {'create': 16, 'destroy': 17, 'unmap': 18, 'map': 19,
                        'reparent': 21, 'configure': 22, 'map_request': 20,
                        'configure_request': 23, 'gravity': 24,
                        'circulate': 26, 'circulate_request': 27}[action]
            self.assertEqual(event[0], expected)
            self.assertEqual(struct.unpack_from(observer.endian + 'II', event, 4), (parent, child))
        # A native reply fence detects any queued event. Do not discard events.
        self.assertEqual(observer.inspect().role, resources.pb.APPLICATION)

    def test_foreign_map_request_is_not_delivered(self):
        self.foreign_substructure('map_request')

    def test_swapped_foreign_map_request_is_not_delivered(self):
        self.foreign_substructure('map_request', endian='>')

    def test_same_application_map_request_is_delivered(self):
        self.foreign_substructure('map_request', authorized=True)

    def test_foreign_configure_request_is_not_delivered(self):
        self.foreign_substructure('configure_request')

    def test_swapped_foreign_configure_request_is_not_delivered(self):
        self.foreign_substructure('configure_request', endian='>')

    def test_same_application_configure_request_is_delivered(self):
        self.foreign_substructure('configure_request', authorized=True)

    def test_foreign_gravity_event_is_not_delivered(self):
        self.foreign_substructure('gravity')

    def test_swapped_foreign_gravity_event_is_not_delivered(self):
        self.foreign_substructure('gravity', endian='>')

    def test_same_application_gravity_event_is_delivered(self):
        self.foreign_substructure('gravity', authorized=True)

    def test_foreign_circulate_event_is_not_delivered(self):
        self.foreign_substructure('circulate')

    def test_swapped_foreign_circulate_event_is_not_delivered(self):
        self.foreign_substructure('circulate', endian='>')

    def test_same_application_circulate_event_is_delivered(self):
        self.foreign_substructure('circulate', authorized=True)

    def test_foreign_circulate_request_is_not_delivered(self):
        self.foreign_substructure('circulate_request')

    def test_swapped_foreign_circulate_request_is_not_delivered(self):
        self.foreign_substructure('circulate_request', endian='>')

    def test_same_application_circulate_request_is_delivered(self):
        self.foreign_substructure('circulate_request', authorized=True)

    def resize_redirect(self, endian='<', authorized=False):
        self.scene(endian=endian, same_workspace=authorized, same_application=authorized)
        result = self.other.operation(2, self.other.pack('III', self.window, 1 << 11, 1 << 18))
        if not authorized:
            self.assertEqual(result, 10, 'Foreign ResizeRedirect subscription must return BadAccess')
            return
        self.assertIsNone(result)
        self.assertIsNone(self.owner.operation(12, self.owner.pack('IH2xII', self.window, 12, 40, 41)))
        event = self.other.exact(32)
        self.assertEqual(event[0], 25)
        self.assertEqual(struct.unpack_from(self.other.endian + 'IHH', event, 4),
                         (self.window, 40, 41))
        self.assertEqual(self.other.inspect().role, resources.pb.APPLICATION)

    def test_foreign_resize_redirect_is_denied(self):
        self.resize_redirect()

    def test_swapped_foreign_resize_redirect_is_denied(self):
        self.resize_redirect(endian='>')

    def test_same_application_resize_request_is_delivered(self):
        self.resize_redirect(authorized=True)

    def test_swapped_same_application_resize_request_is_delivered(self):
        self.resize_redirect(endian='>', authorized=True)

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

    def test_swapped_foreign_child_create_event_is_not_delivered(self):
        self.foreign_substructure('create', endian='>')

    def test_swapped_foreign_child_map_event_is_not_delivered(self):
        self.foreign_substructure('map', endian='>')

    def test_swapped_foreign_child_unmap_event_is_not_delivered(self):
        self.foreign_substructure('unmap', endian='>')

    def test_swapped_foreign_child_destroy_event_is_not_delivered(self):
        self.foreign_substructure('destroy', endian='>')

    def test_swapped_foreign_child_configure_event_is_not_delivered(self):
        self.foreign_substructure('configure', endian='>')

    def test_swapped_foreign_child_reparent_event_is_not_delivered(self):
        self.foreign_substructure('reparent', endian='>')

    def test_same_application_map_event_is_delivered(self):
        self.foreign_substructure('map', authorized=True)

    def test_same_application_unmap_event_is_delivered(self):
        self.foreign_substructure('unmap', authorized=True)

    def test_same_application_destroy_event_is_delivered(self):
        self.foreign_substructure('destroy', authorized=True)

    def test_same_application_configure_event_is_delivered(self):
        self.foreign_substructure('configure', authorized=True)

    def test_same_application_reparent_event_is_delivered(self):
        self.foreign_substructure('reparent', authorized=True)

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
