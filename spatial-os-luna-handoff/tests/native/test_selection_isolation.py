"""Independent X11 selection isolation checks after the version-2 migration."""
import struct
import unittest
import test_resource_isolation as resources
import security_pb2 as pb
import selection_scope_pb2 as scope_pb
import selection_scope_client as scope_wire


class SelectionIsolationTests(unittest.TestCase):
    setUp = resources.ResourceIsolationTests.setUp
    start = resources.ResourceIsolationTests.start
    connect = resources.ResourceIsolationTests.connect
    scene = resources.ResourceIsolationTests.scene
    denied = resources.ResourceIsolationTests.denied

    def claim(self):
        # PRIMARY remains the ordinary public atom in each private namespace.
        self.assertIsNone(self.owner.operation(22, self.owner.pack('III', self.window, 1, 0)))

    def selection_owner(self, client, selection=1):
        header, body = client.operation(23, client.pack('I', selection), reply=True)
        self.assertEqual(body, b'')
        return struct.unpack_from(client.endian+'I', header, 8)[0]

    def test_foreign_owner_query_returns_an_empty_private_namespace(self):
        self.scene(); self.claim()
        self.assertEqual(self.selection_owner(self.other), 0)

    def test_independent_claim_preserves_the_foreign_owner(self):
        self.scene(); self.claim()
        self.assertIsNone(self.other.operation(22, self.other.pack('III', self.other_window, 1, 0)))
        self.assertEqual(self.selection_owner(self.owner), self.window)
        self.assertEqual(self.selection_owner(self.other), self.other_window)

    def test_private_clear_preserves_the_foreign_owner(self):
        self.scene(); self.claim()
        self.assertIsNone(self.other.operation(22, self.other.pack('III', 0, 1, 0)))
        self.assertEqual(self.selection_owner(self.other), 0)
        self.assertEqual(self.selection_owner(self.owner), self.window)

    def test_private_empty_conversion_returns_selection_notify(self):
        self.scene(); self.claim()
        self.other.send(24, self.other.pack('IIIII', self.other_window, 1, 31, 39, 0))
        event = self.other.exact(32)
        self.assertEqual(event[0], 31)
        self.assertEqual(struct.unpack_from(self.other.endian+'IIII', event, 8),
                         (self.other_window, 1, 31, 0))
        self.assertEqual(self.selection_owner(self.owner), self.window)

    def test_workspace_membership_does_not_grant_selection_access(self):
        self.scene(same_workspace=True); self.claim()
        self.assertEqual(self.selection_owner(self.other), 0)

    def test_application_name_in_another_workspace_is_not_same_identity(self):
        self.scene(same_application=True); self.claim()
        self.assertEqual(self.selection_owner(self.other), 0)

    def test_same_application_connections_can_query_owner(self):
        self.scene(same_workspace=True, same_application=True); self.claim()
        self.assertEqual(self.selection_owner(self.other), self.window)

    def test_same_application_conversion_reaches_real_owner(self):
        self.scene(same_workspace=True, same_application=True); self.claim()
        self.assertIsNone(self.other.operation(24, self.other.pack('IIIII', self.other_window, 1, 31, 39, 0)))
        event = self.owner.exact(32)
        self.assertEqual(event[0], 30, 'Expected real SelectionRequest')
        self.assertEqual(struct.unpack_from(self.owner.endian+'IIIII', event, 8),
                         (self.window, self.other_window, 1, 31, 39))

    def test_supervisor_can_query_owner(self):
        supervisor = self.scene(); self.claim()
        self.select_scope(supervisor)
        self.assertEqual(self.selection_owner(supervisor), self.window)

    def test_compositor_can_query_owner(self):
        supervisor = self.scene(); self.claim()
        compositor = self.connect(supervisor.issue(pb.COMPOSITOR, b'', b'').cookie)
        self.select_scope(compositor)
        self.assertEqual(self.selection_owner(compositor), self.window)

    def test_broker_cannot_query_application_selection(self):
        supervisor = self.scene(); self.claim()
        broker = self.connect(supervisor.issue(pb.BROKER, b'', b'').cookie)
        self.denied(broker, 23, broker.pack('I', 1), reply=True)

    def test_swapped_client_has_independent_selection_ownership(self):
        self.scene(endian='>'); self.claim()
        self.assertEqual(self.selection_owner(self.other), 0)
        self.assertIsNone(self.other.operation(22, self.other.pack('III', self.other_window, 1, 0)))
        self.assertEqual(self.selection_owner(self.other), self.other_window)
        self.assertEqual(self.selection_owner(self.owner), self.window)

    def test_ownerless_selection_can_be_queried_and_claimed(self):
        self.scene()
        self.assertEqual(self.selection_owner(self.other), 0)
        self.assertIsNone(self.other.operation(22, self.other.pack('III', self.other_window, 1, 0)))
        self.assertEqual(self.selection_owner(self.other), self.other_window)

    def test_claiming_secondary_does_not_grant_primary_access(self):
        self.scene(); self.claim()
        self.assertIsNone(self.other.operation(22, self.other.pack('III', self.other_window, 2, 0)))
        self.assertEqual(self.selection_owner(self.other), 0)
        self.assertEqual(self.selection_owner(self.owner, 2), 0)
        self.assertEqual(self.selection_owner(self.other, 2), self.other_window)
        self.assertEqual(self.selection_owner(self.owner), self.window)

    def select_scope(self, client):
        reply = scope_wire.call(client, scope_pb.SelectionScope(workspace=b'one', application=b'editor'))
        self.assertEqual(reply.code, pb.OK)
        self.assertTrue(reply.scope_set)


if __name__ == '__main__':
    unittest.main(verbosity=2)
