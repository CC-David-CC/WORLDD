"""Provisioning contract for host-local broker identity records."""
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import bootstrap_execution


class BrokerInstanceRegistryProvisioningTests(unittest.TestCase):
    def test_provisions_private_per_user_directory_and_refuses_unsafe_entries(self):
        uid, gid = os.getuid(), os.getgid()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = root / 'broker-registry'
            self.assertTrue(bootstrap_execution.provision_registry_directory(registry, uid, gid, False))
            self.assertFalse(registry.exists())
            self.assertTrue(bootstrap_execution.provision_registry_directory(registry, uid, gid, True))
            info = registry.lstat()
            self.assertTrue(stat.S_ISDIR(info.st_mode))
            self.assertEqual(info.st_uid, uid)
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o700)
            marker = registry / 'identity.lock'
            marker.write_text('retained host-local record')
            self.assertFalse(bootstrap_execution.provision_registry_directory(registry, uid, gid, True))
            self.assertEqual(marker.read_text(), 'retained host-local record')
            registry.chmod(0o755)
            with self.assertRaises(SystemExit):
                bootstrap_execution.provision_registry_directory(registry, uid, gid, True)
            registry.chmod(0o700)
            link = root / 'registry-link'
            link.symlink_to(registry, target_is_directory=True)
            with self.assertRaises(SystemExit):
                bootstrap_execution.provision_registry_directory(link, uid, gid, False)


if __name__ == '__main__':
    unittest.main()
