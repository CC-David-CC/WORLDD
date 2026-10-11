"""Independent X11 selection operations must remain application-local."""
import struct
import unittest
import test_resource_isolation as resources
import security_pb2 as pb
import selection_scope_pb2 as scope_pb
import selection_scope_client as scope_wire


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

    def test_same_application_conversion_uses_public_selection_atom(self):
        self.scene(same_workspace=True, same_application=True)
        self.claim(self.owner, self.window)
        self.assertIsNone(self.other.operation(24, self.other.pack('IIIII', self.other_window, 1, 31, 39, 0)))
        event = self.owner.exact(32)
        self.assertEqual(event[0], 30)
        self.assertEqual(struct.unpack_from(self.owner.endian + 'IIIII', event, 8),
                         (self.window, self.other_window, 1, 31, 39))

    def test_empty_namespace_conversion_does_not_contact_foreign_owner(self):
        self.scene()
        self.claim(self.owner, self.window)
        self.other.send(24, self.other.pack('IIIII', self.other_window, 1, 31, 39, 0))
        event = self.other.exact(32)
        self.assertEqual(event[0], 31)
        self.assertEqual(struct.unpack_from(self.other.endian + 'IIII', event, 8),
                         (self.other_window, 1, 31, 0))
        # A pending foreign SelectionRequest would break this independent fence.
        self.assertEqual(self.selection_owner(self.owner), self.window)

    def test_same_application_transfer_emits_public_selection_clear(self):
        self.scene(same_workspace=True, same_application=True)
        self.claim(self.owner, self.window)
        self.claim(self.other, self.other_window)
        event = self.owner.exact(32)
        self.assertEqual(event[0], 29)
        self.assertEqual(struct.unpack_from(self.owner.endian + 'II', event, 8), (self.window, 1))
        self.assertEqual(self.selection_owner(self.owner), self.other_window)

    def scope(self, client, workspace, application, default=False):
        reply = scope_wire.call(client, scope_pb.SelectionScope(workspace=workspace,
                                                              application=application,
                                                              default_scope=default))
        self.assertEqual(reply.code, pb.OK)
        self.assertTrue(reply.scope_set)

    def test_supervisor_selects_each_namespace_without_changing_identity(self):
        supervisor = self.scene()
        self.claim(self.owner, self.window)
        self.claim(self.other, self.other_window)
        before = supervisor.inspect()
        self.assertEqual(self.selection_owner(supervisor), 0)
        self.scope(supervisor, b'one', b'editor')
        self.assertEqual(self.selection_owner(supervisor), self.window)
        self.scope(supervisor, b'two', b'terminal')
        self.assertEqual(self.selection_owner(supervisor), self.other_window)
        self.scope(supervisor, b'', b'', default=True)
        self.assertEqual(self.selection_owner(supervisor), 0)
        self.assertEqual(supervisor.inspect(), before)

    def test_application_cannot_change_selection_namespace(self):
        self.scene()
        self.claim(self.owner, self.window)
        reply = scope_wire.call(self.other, scope_pb.SelectionScope(workspace=b'one', application=b'editor'))
        self.assertEqual(reply, 10, 'The extension dispatch gate must deny application scope requests')
        self.assertEqual(self.selection_owner(self.other), 0)

    def test_broker_cannot_change_selection_namespace(self):
        supervisor = self.scene()
        broker = self.connect(supervisor.issue(pb.BROKER, b'', b'').cookie)
        reply = scope_wire.call(broker, scope_pb.SelectionScope(workspace=b'one', application=b'editor'))
        self.assertEqual(reply, 10, 'The extension dispatch gate must deny broker scope requests')

    def test_invalid_scope_preserves_the_trusted_selection_view(self):
        supervisor = self.scene()
        self.claim(self.owner, self.window)
        self.scope(supervisor, b'one', b'editor')
        for scope in [scope_pb.SelectionScope(), scope_pb.SelectionScope(workspace=b'one'),
                      scope_pb.SelectionScope(workspace=b'one', application=b'editor', default_scope=True),
                      scope_pb.SelectionScope(workspace=b'x' * 257, application=b'editor'),
                      scope_pb.SelectionScope(workspace=b'one\0bad', application=b'editor')]:
            self.assertEqual(scope_wire.call(supervisor, scope).code, pb.INVALID_ARGUMENT)
            self.assertEqual(self.selection_owner(supervisor), self.window)

    def test_destroyed_owner_does_not_clear_another_namespace(self):
        self.scene()
        self.claim(self.owner, self.window)
        self.claim(self.other, self.other_window)
        self.assertIsNone(self.owner.operation(4, self.owner.pack('I', self.window)))
        self.assertEqual(self.selection_owner(self.owner), 0)
        self.assertEqual(self.selection_owner(self.other), self.other_window)

    def test_private_namespace_does_not_authorize_a_foreign_owner_window(self):
        self.scene()
        self.claim(self.owner, self.window)
        self.assertEqual(self.other.operation(22, self.other.pack('III', self.window, 1, 0)), 10)
        self.assertEqual(self.selection_owner(self.other), 0)
        self.assertEqual(self.selection_owner(self.owner), self.window)

    def test_private_namespace_does_not_authorize_a_foreign_requestor(self):
        self.scene()
        self.claim(self.owner, self.window)
        self.assertEqual(self.other.operation(24, self.other.pack('IIIII', self.window, 1, 31, 39, 0)), 10)
        self.assertEqual(self.selection_owner(self.other), 0)

    def test_swapped_same_application_conversion_uses_public_selection_atom(self):
        self.scene(endian='>', same_workspace=True, same_application=True)
        self.claim(self.owner, self.window)
        self.assertIsNone(self.other.operation(24, self.other.pack('IIIII', self.other_window, 1, 31, 39, 0)))
        event = self.owner.exact(32)
        self.assertEqual(event[0], 30)
        self.assertEqual(struct.unpack_from(self.owner.endian + 'IIIII', event, 8),
                         (self.window, self.other_window, 1, 31, 39))

    def test_compositor_selects_namespace_and_observes_revocation_cleanup(self):
        supervisor = self.scene()
        self.claim(self.owner, self.window)
        identity = self.owner.inspect()
        compositor = self.connect(supervisor.issue(pb.COMPOSITOR, b'', b'').cookie)
        self.scope(compositor, identity.workspace, identity.application)
        self.assertEqual(self.selection_owner(compositor), self.window)
        reply = supervisor.call(revoke=pb.Revoke(credential_id=identity.credential_id))
        self.assertEqual(reply.code, pb.OK)
        self.assertTrue(reply.revoked)
        self.assertEqual(self.selection_owner(compositor), 0)

    def test_scope_operation_rejects_replay_and_preserves_view(self):
        supervisor = self.scene()
        self.claim(self.owner, self.window)
        self.scope(supervisor, b'one', b'editor')
        previous = supervisor.operation_sequence
        request = scope_pb.SelectionScopeRequest(protocol_version=1, request_id=previous,
                                                 scope=scope_pb.SelectionScope(default_scope=True))
        self.assertEqual(scope_wire.exchange(supervisor, request).code, pb.STALE_SEQUENCE)
        self.assertEqual(self.selection_owner(supervisor), self.window)

    def test_scope_operation_rejects_unknown_fields_and_missing_scope(self):
        supervisor = self.scene()
        self.claim(self.owner, self.window)
        self.scope(supervisor, b'one', b'editor')
        next_id = supervisor.operation_sequence + 1
        request = scope_pb.SelectionScopeRequest(protocol_version=1, request_id=next_id,
                                                 scope=scope_pb.SelectionScope(default_scope=True))
        reply = scope_wire.exchange(supervisor, payload=request.SerializeToString() + b'\x98\x06\x01')
        self.assertEqual(reply.code, pb.INVALID_ARGUMENT)
        request.ClearField('scope')
        self.assertEqual(scope_wire.exchange(supervisor, request).code, pb.INVALID_ARGUMENT)
        self.assertEqual(self.selection_owner(supervisor), self.window)

    def test_swapped_trusted_scope_operation_uses_the_separate_schema(self):
        root = self.scene()
        self.claim(self.owner, self.window)
        compositor = self.connect(root.issue(pb.COMPOSITOR, b'', b'').cookie, endian='>')
        self.scope(compositor, b'one', b'editor')
        self.assertEqual(self.selection_owner(compositor), self.window)



if __name__ == '__main__':
    unittest.main(verbosity=2)
