#!/usr/bin/env python3
"""Real TLS world and broker lifetimes. No native session is required."""
import unittest

import test_world_shared_state as baseline
from test_geometry_native_crosshost import BrokerPersistentTlsWorld, NativeCrossHostTests
from test_workspace_authority import BrokerProcess


class TlsWorldLifecycleTests(baseline.SharedWorldStateTests):
    close_worlds_and_brokers = NativeCrossHostTests.close_workspace_witness_brokers

    def setUp(self):
        super().setUp()
        self.brokers = []
        self.addCleanup(self.close_worlds_and_brokers)

    def test_plain_persistent_tls_world_close_twice(self):
        world = self.new_world()
        self.open_owner(world)
        self.assertIsNone(world.proc.poll())
        self.assertIsNone(world.broker_channel)
        self.assertNotIn('--workspace-fd', world.proc.args)
        world.close()
        self.assertEqual(world.proc.returncode, 0)
        world.close()
        self.assertEqual(world.proc.returncode, 0)

    def test_mixed_plain_and_broker_tls_world_cleanup_twice(self):
        plain = self.new_world()
        self.open_owner(plain)
        broker = BrokerProcess(self, self.root / 'lifecycle-execution')
        self.assertEqual(broker.call('system.hello')['implementation'], 'production')
        local = self.scoped_world(authenticate=False)
        local.close()
        world = BrokerPersistentTlsWorld(self, local, broker)
        self.worlds.append(world)
        self.assertEqual(world.management('system.hello')['implementation'], 'production')
        self.open_owner(world)
        self.assertIsNone(plain.proc.poll())
        self.assertIsNone(world.proc.poll())
        self.assertIn('--workspace-fd', world.proc.args)
        relay = world.broker_channel
        self.assertIsNotNone(relay)
        self.assertIn(relay, broker.channels)
        self.assertTrue(relay.thread.is_alive())
        self.close_worlds_and_brokers()
        self.assertEqual(plain.proc.returncode, 0)
        self.assertEqual(world.proc.returncode, 0)
        self.assertEqual(broker.proc.returncode, 0)
        self.assertFalse(relay.thread.is_alive())
        self.assertEqual(broker.channels, [])
        self.assertEqual(self.worlds, [])
        self.assertEqual(self.brokers, [])
        self.close_worlds_and_brokers()


for _name in dir(baseline.SharedWorldStateTests):
    if _name.startswith('test_'):
        setattr(TlsWorldLifecycleTests, _name, None)


if __name__ == '__main__':
    suite = unittest.TestSuite(TlsWorldLifecycleTests(name) for name in (
        'test_plain_persistent_tls_world_close_twice',
        'test_mixed_plain_and_broker_tls_world_cleanup_twice'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())