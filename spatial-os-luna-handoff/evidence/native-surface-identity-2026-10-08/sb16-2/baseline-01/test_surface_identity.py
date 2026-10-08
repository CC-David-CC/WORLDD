"""Independent native witnesses for atomic surface ownership and generation."""
import struct
import unittest
import input_pb2 as inp
import security_pb2 as sec
import test_actor_binding as binding


class SurfaceIdentityTests(unittest.TestCase):
    setUp = binding.ActorBindingTests.setUp
    start = binding.ActorBindingTests.start
    connect = binding.ActorBindingTests.connect
    setup_scene = binding.ActorBindingTests.setup_scene
    call = binding.ActorBindingTests.call

    def attribution(self, window=None):
        reply = self.call(inspect=inp.Inspect(xid=window or self.window))
        self.assertTrue(reply.HasField('security_enabled'), 'Missing explicit security mode')
        self.assertTrue(reply.security_enabled)
        self.assertTrue(reply.HasField('application_identity'), 'Missing server-owned application identity')
        value = reply.application_identity
        self.assertEqual(len(value.server_id), 32)
        self.assertGreater(value.connection_id, 0)
        self.assertGreater(reply.target.generation, 0)
        return reply

    def map(self, client, window):
        client = self.compositor  # Only the root redirect owner can complete top-level mapping.
        self.assertIsNone(client.operation(8, client.pack('I', window)))

    def test_owner_labels_share_the_geometry_and_generation_reply(self):
        self.setup_scene(); reply = self.attribution()
        self.assertEqual((reply.application_identity.workspace, reply.application_identity.application), (b'one', b'editor'))
        self.assertEqual((reply.target.xid, reply.width, reply.height), (self.window, 32, 32))

    def test_same_application_name_in_another_workspace_is_distinct(self):
        self.setup_scene(); a = self.attribution(); b = self.attribution(self.other_window)
        self.assertEqual(a.application_identity.application, b.application_identity.application)
        self.assertNotEqual(a.application_identity.workspace, b.application_identity.workspace)
        self.assertEqual(a.application_identity.server_id, b.application_identity.server_id)

    def test_same_workspace_different_application_is_distinct(self):
        self.setup_scene()
        peer = self.connect(self.supervisor.issue(workspace=b'one', application=b'terminal').cookie)
        window = peer.create_window(); self.map(peer, window)
        self.assertEqual(self.attribution(window).application_identity.application, b'terminal')

    def test_same_group_connections_keep_separate_connection_ids(self):
        self.setup_scene(); window = self.peer.create_window(); self.map(self.peer, window)
        a = self.attribution().application_identity; b = self.attribution(window).application_identity
        self.assertEqual((a.workspace, a.application, a.server_id), (b.workspace, b.application, b.server_id))
        self.assertNotEqual(a.connection_id, b.connection_id)

    def forge(self, client, window):
        for name in (b'WM_CLASS', b'_SPATIAL_WORKSPACE', b'_SPATIAL_APPLICATION'):
            head, _ = self.supervisor.operation(16, self.supervisor.pack('HH', len(name), 0)+name, reply=True)
            atom = struct.unpack_from('<I', head, 8)[0]
            self.assertIsNone(client.operation(18, client.pack('IIIB3xI', window, atom, 31, 8, 6)+b'forged'))

    def test_window_properties_cannot_spoof_authority(self):
        self.setup_scene(); before = self.attribution().application_identity
        self.forge(self.owner, self.window)
        self.assertEqual(self.attribution().application_identity, before)

    def test_remap_changes_generation_without_changing_owner(self):
        self.setup_scene(); before = self.attribution()
        self.assertIsNone(self.owner.operation(10, self.owner.pack('I', self.window)))
        stale = self.call(inp.STALE_TARGET, inspect=inp.Inspect(xid=self.window))
        self.assertFalse(stale.HasField('application_identity'))
        self.map(self.owner, self.window); after = self.attribution()
        self.assertNotEqual(before.target.generation, after.target.generation)
        self.assertEqual(before.application_identity, after.application_identity)

    def test_resize_returns_new_geometry_with_same_owner(self):
        self.setup_scene(); before = self.attribution()
        # The root redirect owner performs the resize, as a real compositor does.
        self.assertIsNone(self.compositor.operation(12, self.compositor.pack('IHHII', self.window, 12, 0, 48, 40)))
        after = self.attribution()
        self.assertEqual((after.width, after.height), (48, 40))
        self.assertNotEqual(before.target.generation, after.target.generation)
        self.assertEqual(before.application_identity, after.application_identity)

    def test_reused_xid_does_not_reuse_window_generation(self):
        self.setup_scene(); before = self.attribution()
        self.assertIsNone(self.owner.operation(4, self.owner.pack('I', self.window)))
        self.call(inp.STALE_TARGET, inspect=inp.Inspect(xid=self.window))
        self.owner.next_id = self.window & self.owner.mask
        self.assertEqual(self.owner.create_window(), self.window)
        # A mapped request is redirected to the compositor.
        self.map(self.compositor, self.window)
        after = self.attribution()
        self.assertNotEqual(before.target.generation, after.target.generation)
        self.assertEqual(before.application_identity, after.application_identity)

    def test_non_application_window_has_no_application_identity(self):
        self.setup_scene(); window = self.compositor.create_window(); self.map(self.compositor, window)
        reply = self.call(inspect=inp.Inspect(xid=window))
        self.assertTrue(reply.HasField('security_enabled')); self.assertTrue(reply.security_enabled)
        self.assertFalse(reply.HasField('application_identity'))

    def test_application_cannot_query_foreign_attribution(self):
        self.setup_scene()
        data = inp.Request(protocol_version=1, request_id=1, inspect=inp.Inspect(xid=self.other_window)).SerializeToString()
        self.assertEqual(self.owner.operation(self.input_opcode, self.owner.pack('I', len(data))+data, reply=True), 10)

    def test_non_controller_compositor_cannot_query_attribution(self):
        self.setup_scene(); peer = self.connect(self.supervisor.issue(sec.COMPOSITOR, b'', b'').cookie)
        data = inp.Request(protocol_version=1, request_id=1, inspect=inp.Inspect(xid=self.window)).SerializeToString()
        head, body = peer.operation(self.input_opcode, peer.pack('I', len(data))+data, reply=True)
        reply = inp.Reply.FromString(body[:struct.unpack_from('<I', head, 8)[0]])
        self.assertEqual(reply.code, inp.FORBIDDEN); self.assertFalse(reply.HasField('application_identity'))

    def test_owner_disconnect_removes_attribution(self):
        self.setup_scene(); self.attribution(); self.owner.close()
        # A server round trip on the peer fences connection teardown.
        self.peer.operation(43, reply=True)
        reply = self.call(inp.STALE_TARGET, inspect=inp.Inspect(xid=self.window))
        self.assertFalse(reply.HasField('application_identity'))

    def test_revocation_removes_only_the_revoked_application(self):
        self.setup_scene(); other = self.attribution(self.other_window)
        self.assertEqual(self.supervisor.call(revoke=sec.Revoke(credential_id=self.owner_grant.id)).code, sec.OK)
        reply = self.call(inp.STALE_TARGET, inspect=inp.Inspect(xid=self.window))
        self.assertFalse(reply.HasField('application_identity'))
        self.assertEqual(self.attribution(self.other_window).application_identity, other.application_identity)

    def test_child_window_uses_its_creator_connection(self):
        self.setup_scene(); child = self.peer.create_window(parent=self.window); self.map(self.peer, child)
        a = self.attribution().application_identity; b = self.attribution(child).application_identity
        self.assertEqual((a.workspace, a.application), (b.workspace, b.application))
        self.assertNotEqual(a.connection_id, b.connection_id)

    def test_swapped_application_keeps_exact_binary_labels(self):
        self.setup_scene(endian='>'); value = self.attribution().application_identity
        self.assertEqual((value.workspace, value.application), (b'one', b'editor'))


if __name__ == '__main__': unittest.main(verbosity=2)
