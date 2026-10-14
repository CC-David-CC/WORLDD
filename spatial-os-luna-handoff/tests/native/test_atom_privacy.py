"""Independent core-X atom metadata attacks under immutable application labels."""
import struct
import time
import unittest
import uuid
import test_resource_isolation as resources


class AtomPrivacyTests(unittest.TestCase):
    setUp = resources.ResourceIsolationTests.setUp
    start = resources.ResourceIsolationTests.start
    connect = resources.ResourceIsolationTests.connect
    scene = resources.ResourceIsolationTests.scene

    def intern(self, client, name, only_existing=False):
        result = client.operation(16, client.pack('H2x', len(name)) + name,
                                  minor=int(only_existing), reply=True)
        self.assertIsInstance(result, tuple)
        header, body = result
        self.assertEqual(body, b'')
        return struct.unpack_from(client.endian + 'I', header, 8)[0]

    def name(self, client, atom):
        result = client.operation(17, client.pack('I', atom), reply=True)
        if isinstance(result, int):
            return result
        header, body = result
        size = struct.unpack_from(client.endian + 'H', header, 8)[0]
        self.assertEqual(len(body), (size + 3) // 4 * 4)
        return body[:size]

    def secret(self):
        return ('private-project-' + uuid.uuid4().hex).encode()

    def test_foreign_atom_name_is_not_disclosed(self):
        self.scene()
        atom = self.intern(self.owner, self.secret())
        self.assertEqual(self.name(self.other, atom), 5, 'Expected native BadAtom')

    def test_foreign_atom_existence_is_not_disclosed(self):
        self.scene()
        name = self.secret()
        self.intern(self.owner, name)
        self.assertEqual(self.intern(self.other, name, only_existing=True), 0)

    def test_same_workspace_does_not_disclose_atom_name(self):
        self.scene(same_workspace=True)
        atom = self.intern(self.owner, self.secret())
        self.assertEqual(self.name(self.other, atom), 5)

    def test_matching_application_name_in_other_workspace_is_private(self):
        self.scene(same_application=True)
        atom = self.intern(self.owner, self.secret())
        self.assertEqual(self.name(self.other, atom), 5)

    def test_swapped_client_cannot_disclose_atom_name(self):
        self.scene(endian='>')
        atom = self.intern(self.owner, self.secret())
        self.assertEqual(self.name(self.other, atom), 5)

    def test_same_application_connections_share_atom_names(self):
        self.scene(same_workspace=True, same_application=True)
        name = self.secret()
        atom = self.intern(self.owner, name)
        self.assertEqual(self.name(self.other, atom), name)
        self.assertEqual(self.intern(self.other, name, only_existing=True), atom)

    def test_predefined_atoms_remain_public(self):
        self.scene()
        self.assertEqual(self.name(self.other, 1), b'PRIMARY')
        self.assertEqual(self.intern(self.other, b'PRIMARY', only_existing=True), 1)

    def test_explicitly_interned_name_remains_usable(self):
        self.scene()
        name = self.secret()
        self.intern(self.owner, name)
        atom = self.intern(self.other, name)
        self.assertEqual(self.name(self.other, atom), name)

    def test_declarations_survive_creator_disconnect_while_peer_is_live(self):
        self.scene(same_workspace=True, same_application=True)
        name = self.secret()
        atom = self.intern(self.owner, name)
        # The peer has never requested an atom. The creator's close must retain
        # declarations for that already-authorized application connection.
        self.owner.close()
        self.assertEqual(self.name(self.other, atom), name)

    def test_repeated_declaration_does_not_exhaust_the_budget(self):
        self.scene()
        name = self.secret()
        atom = self.intern(self.owner, name)
        for _ in range(100):
            self.assertEqual(self.intern(self.owner, name), atom)
        self.assertEqual(self.name(self.owner, atom), name)

    def test_broker_does_not_disclose_application_atom(self):
        import security_pb2 as pb
        supervisor = self.scene()
        atom = self.intern(self.owner, self.secret())
        broker = self.connect(supervisor.issue(pb.BROKER, b'', b'').cookie)
        self.assertEqual(self.name(broker, atom), 5)

    def test_privileged_compositor_can_inspect_application_atom(self):
        import security_pb2 as pb
        supervisor = self.scene()
        name = self.secret()
        atom = self.intern(self.owner, name)
        compositor = self.connect(supervisor.issue(pb.COMPOSITOR, b'', b'').cookie)
        self.assertEqual(self.name(compositor, atom), name)

    def test_declarations_insert_older_global_ids_in_sorted_order(self):
        self.scene()
        old_name, new_name = self.secret(), self.secret()
        old_atom = self.intern(self.owner, old_name)
        new_atom = self.intern(self.other, new_name)
        self.assertGreater(new_atom, old_atom)
        self.assertEqual(self.intern(self.other, old_name), old_atom)
        self.assertEqual(self.name(self.other, old_atom), old_name)
        self.assertEqual(self.name(self.other, new_atom), new_name)

    def test_final_disconnect_does_not_retain_application_declarations(self):
        supervisor = self.scene()
        name = self.secret()
        atom = self.intern(self.owner, name)
        identity = self.owner.inspect()
        self.owner.close()
        # A privileged QueryTree fences destruction of the old connection.
        # A new connection alone must declare dynamic names again.
        deadline = time.monotonic() + 3
        while self.window in supervisor.tree(supervisor.root):
            self.assertLess(time.monotonic(), deadline)
        replacement = self.connect(supervisor.issue(workspace=identity.workspace,
                                                    application=identity.application).cookie)
        self.assertEqual(self.name(replacement, atom), 5)
        self.assertEqual(self.intern(replacement, name, only_existing=True), 0)

    def test_application_declaration_budget_is_enforced_and_isolated(self):
        self.scene()
        prefix = self.secret()
        first_name = prefix + b'-0'
        first = self.intern(self.owner, first_name)
        for index in range(1, 65536):
            self.intern(self.owner, prefix + b'-' + str(index).encode())
        name = prefix + b'-overflow'
        result = self.owner.operation(16, self.owner.pack('H2x', len(name)) + name,
                                      reply=True)
        self.assertEqual(result, 11, 'Expected native BadAlloc at declaration limit')
        self.assertEqual(self.intern(self.owner, first_name), first)
        self.assertEqual(self.name(self.owner, first), first_name)
        self.assertNotEqual(self.intern(self.other, name), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
