"""Regression checks for provisioning ownership of the exact mounted root."""
import os
from pathlib import Path
import stat
import tempfile
import unittest

from tools.provision_storage import assign_volume_root_owner


class ProvisionStorageRootOwnerTests(unittest.TestCase):
    def test_assigns_only_root_uid_and_restrictive_mode_on_pinned_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'mounted-volume'
            root.mkdir(mode=0o755)
            root.chmod(0o755)
            before = root.stat()
            checks = []

            def verify_mount():
                checks.append(True)
                current = root.stat()
                self.assertEqual((current.st_dev, current.st_ino), (before.st_dev, before.st_ino))

            assign_volume_root_owner(root, os.getuid(), verify_mount)
            after = root.stat()
            self.assertEqual(after.st_uid, os.getuid())
            self.assertEqual(after.st_gid, before.st_gid)
            self.assertEqual(stat.S_IMODE(after.st_mode), 0o700)
            self.assertEqual((after.st_dev, after.st_ino), (before.st_dev, before.st_ino))
            self.assertEqual(len(checks), 2)

    def test_refuses_symlink_without_mount_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'real-volume'
            root.mkdir()
            alias = Path(temporary)/'volume-link'
            alias.symlink_to(root, target_is_directory=True)
            checked = []
            with self.assertRaises(OSError):
                assign_volume_root_owner(alias, os.getuid(), lambda: checked.append(True))
            self.assertEqual(checked, [])
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o755)

    def test_mount_identity_refusal_precedes_any_ownership_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'mounted-volume'
            root.mkdir(mode=0o755)
            root.chmod(0o755)
            before = root.stat()

            def reject_unexpected_mount():
                raise ValueError('Volume mount identity changed')

            with self.assertRaisesRegex(ValueError, 'mount identity changed'):
                assign_volume_root_owner(root, os.getuid(), reject_unexpected_mount)
            after = root.stat()
            self.assertEqual((after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)),
                             (before.st_uid, before.st_gid, 0o755))


if __name__ == '__main__':
    unittest.main()
