"""World setup must start new issuer operations on a live TLS connection."""
import time
import unittest
from unittest.mock import patch

import test_session_application as existing
from test_supervisor_registry import SupervisorRegistryTests


class SessionWorldIssuerSetupTests(existing.SessionApplicationTests):
    def issuer_fixture(self):
        if self._testMethodName == 'test_plain_peer_world_registration_after_idle_expiry':
            return SupervisorRegistryTests('runTest')
        return super().issuer_fixture()

    def start_after_idle_expiry(self):
        key = self.issuer.key
        delayed = []

        def slow_world_key(name):
            result = key(name)
            if name == 'world-authority':
                delayed.append(name)
                # Reproduce slow sanitizer setup without changing issuer policy.
                time.sleep(self.issuer.config.idle_timeout_ms / 1000 + .25)
            return result

        with patch.object(self.issuer, 'key', side_effect=slow_world_key):
            proc, owner, record = self.ready()
        self.assertEqual(delayed, ['world-authority'])
        self.assertIsNone(proc.poll())
        self.assertEqual(record.binding.participant_id, b'w' * 32)
        self.assertEqual(owner.launch.participant_id, record.binding.participant_id)
        self.assertGreater(self.world_ids.worldd_pid, 1)
        self.assertGreater(self.world_ids.spatialwm_pid, 1)
        self.assertNotEqual(self.world_ids.worldd_pid, self.world_ids.spatialwm_pid)

    def test_healthy_peer_world_registration_after_idle_expiry(self):
        self.start_after_idle_expiry()

    def test_plain_peer_world_registration_after_idle_expiry(self):
        self.start_after_idle_expiry()


if __name__ == '__main__':
    suite = unittest.TestSuite(SessionWorldIssuerSetupTests(name)
        for name in SessionWorldIssuerSetupTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
