"""Independent core-X atom metadata attacks under immutable application labels."""
import struct
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


if __name__ == '__main__':
    unittest.main(verbosity=2)
