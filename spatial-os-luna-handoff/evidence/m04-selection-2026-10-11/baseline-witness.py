"""Independent X11 selection operations must remain application-local."""
import struct
import unittest
import test_resource_isolation as resources


class SelectionNamespaceTests(unittest.TestCase):
    setUp = resources.ResourceIsolationTests.setUp
    start = resources.ResourceIsolationTests.start
    connect = resources.ResourceIsolationTests.connect
    scene = resources.ResourceIsolationTests.scene

    def claim(self, client, window, atom=1):
        self.assertIsNone(client.operation(22, client.pack('III', window, atom, 0)))

    def selection_owner(self, client, atom=1):
        result = client.operation(23, client.pack('I', atom), reply=True)
        self.assertIsInstance(result, tuple, 'A private namespace must return its own owner')
        header, body = result
        self.assertEqual(body, b'')
        return struct.unpack_from(client.endian + 'I', header, 8)[0]

    def test_foreign_primary_ownership_does_not_change_empty_namespace(self):
        self.scene()
        self.assertEqual(self.selection_owner(self.other), 0)
        self.claim(self.owner, self.window)
        self.assertEqual(self.selection_owner(self.other), 0)

    def test_two_applications_can_own_primary_at_the_same_time(self):
        self.scene()
        self.claim(self.owner, self.window)
        self.claim(self.other, self.other_window)
        self.assertEqual(self.selection_owner(self.owner), self.window)
        self.assertEqual(self.selection_owner(self.other), self.other_window)

    def test_same_workspace_applications_have_independent_primary(self):
        self.scene(same_workspace=True)
        self.claim(self.owner, self.window)
        self.claim(self.other, self.other_window)
        self.assertEqual(self.selection_owner(self.owner), self.window)
        self.assertEqual(self.selection_owner(self.other), self.other_window)

    def test_matching_application_names_in_different_workspaces_are_independent(self):
        self.scene(same_application=True)
        self.claim(self.owner, self.window)
        self.claim(self.other, self.other_window)
        self.assertEqual(self.selection_owner(self.owner), self.window)
        self.assertEqual(self.selection_owner(self.other), self.other_window)

    def test_clearing_one_namespace_preserves_another_owner(self):
        self.scene()
        self.claim(self.owner, self.window)
        self.claim(self.other, 0)
        self.assertEqual(self.selection_owner(self.owner), self.window)
        self.assertEqual(self.selection_owner(self.other), 0)

    def test_same_application_connections_share_primary(self):
        self.scene(same_workspace=True, same_application=True)
        self.claim(self.owner, self.window)
        self.assertEqual(self.selection_owner(self.other), self.window)

    def test_secondary_remains_independent_from_primary(self):
        self.scene()
        self.claim(self.owner, self.window, 1)
        self.claim(self.owner, self.window, 2)
        self.assertEqual(self.selection_owner(self.owner, 1), self.window)
        self.assertEqual(self.selection_owner(self.owner, 2), self.window)
        self.assertEqual(self.selection_owner(self.other, 1), 0)
        self.assertEqual(self.selection_owner(self.other, 2), 0)

    def test_swapped_client_has_an_independent_namespace(self):
        self.scene(endian='>')
        self.claim(self.owner, self.window)
        self.claim(self.other, self.other_window)
        self.assertEqual(self.selection_owner(self.owner), self.window)
        self.assertEqual(self.selection_owner(self.other), self.other_window)



if __name__ == '__main__':
    unittest.main(verbosity=2)
