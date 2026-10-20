"""Kernel observations of the configured non-native production-broker adapter."""
import json
import os
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
from google.protobuf import text_format
import broker_pb2 as broker
from harness.rpc import Client, TransportError
from test_processes import ProcessTests
from test_workspaces import HOST


class AdapterResourceTests(ProcessTests):
    def group(self, pid):
        return Path('/sys/fs/cgroup') / Path(f'/proc/{pid}/cgroup').read_text().strip().split('::', 1)[1].lstrip('/')

    def test_explicit_configuration_reaches_kernel_and_closes_private_descriptor(self):
        process = self.launch("import pathlib,time\npathlib.Path('ready').touch()\nwhile not pathlib.Path('release').exists():time.sleep(.01)")
        self.until(lambda: (self.project/'ready').exists())
        identities = self.call('runtime.identities')
        manager = self.group(identities['workspaced_pid'])
        self.assertEqual(manager.name, 'broker-manager')
        world = self.group(identities['worldd_pid'])
        self.assertFalse(world.is_relative_to(manager.parent))
        leaves = [p.parent for p in manager.parent.rglob('cgroup.procs') if len(p.relative_to(manager.parent).parts) == 4]
        self.assertEqual(len(leaves), 1)
        leaf = leaves[0]
        self.assertEqual((leaf/'memory.max').read_text().strip(), str(128*1024*1024))
        self.assertEqual((leaf/'memory.swap.max').read_text().strip(), '0')
        self.assertEqual((leaf/'pids.max').read_text().strip(), '16')
        self.assertEqual((leaf/'cpu.max').read_text().strip(), '100000 100000')
        self.assertGreaterEqual(len((leaf/'cgroup.procs').read_text().split()), 3)
        links = [os.readlink(p) for p in Path(f"/proc/{identities['workspaced_pid']}/fd").iterdir()]
        self.assertFalse(any('broker-resource-policy' in link for link in links), links)
        (self.root/'resource-kernel.json').write_text(json.dumps(dict(manager=str(manager), world=str(world),
            application=str(leaf), limits={name:(leaf/name).read_text() for name in
                ['memory.max','memory.swap.max','pids.max','cpu.max','io.max','cgroup.procs']}, broker_fds=links), indent=2))
        (self.project/'release').touch(); self.wait_job(process)

    def test_missing_configuration_denies_application_before_pending_record(self):
        self.client.stop()
        with patch.dict(os.environ, {'SPATIAL_TEST_BROKER_CONFIG': ''}): self.start()
        self.a = self.actor('a')
        self.actor_error('UNAVAILABLE', self.a, 'process.launch', argv=['/usr/bin/true'], operation_id='denied', profile='development')
        with self.registry() as db: self.assertEqual(db.execute('SELECT count(*) FROM processes').fetchone()[0], 0)

    def test_invalid_delegation_cannot_create_broker_state(self):
        config = broker.BrokerProcessConfig()
        text_format.Parse(Path(os.environ['SPATIAL_TEST_BROKER_CONFIG']).read_text(), config)
        config.delegated_cgroup = str(self.root)
        path = self.root/'invalid.textproto'; path.write_text(text_format.MessageToString(config)); path.chmod(0o600)
        state = self.root/'invalid-state'; logs = self.root/'invalid-logs'
        client = Client([HOST], state, logs, extra_env={'SPATIAL_TEST_BROKER_CONFIG': str(path)})
        self.addCleanup(client.stop)
        with self.assertRaises(TransportError): client.call('system.hello')
        self.assertNotEqual(client.proc.wait(timeout=5), 0)
        self.assertFalse((state/'execution').exists())
        self.assertIn('Expected owned delegated cgroup v2 directory', next(logs.glob('stderr-*.log')).read_text())

    def test_abrupt_supervisor_death_retains_the_broker_tree(self):
        process = self.launch("import pathlib,time\npathlib.Path('ready').touch()\nwhile True:time.sleep(.01)")
        self.until(lambda: (self.project/'ready').exists())
        root = self.group(self.call('runtime.identities')['workspaced_pid']).parent
        root_identity = (root.stat().st_dev, root.stat().st_ino)
        descendants = self.descendants(); self.assertTrue(descendants)
        self.client.stop(crash=True)
        self.assertTrue(root.exists())
        self.assertEqual((root.stat().st_dev, root.stat().st_ino), root_identity)
        self.until(lambda: self.gone(descendants))

    def test_clean_channel_close_removes_the_retained_broker_tree(self):
        process = self.launch("import pathlib,time\npathlib.Path('ready').touch()\nwhile True:time.sleep(.01)")
        self.until(lambda: (self.project/'ready').exists())
        root = self.group(self.call('runtime.identities')['workspaced_pid']).parent
        root_identity = (root.stat().st_dev, root.stat().st_ino)
        descendants = self.descendants(); self.assertTrue(descendants)
        self.client.proc.stdin.close()
        self.assertEqual(self.client.proc.wait(timeout=12), 0)
        self.assertFalse(root.exists())
        self.assertTrue(self.gone(descendants))

        # Reopen the same execution journal. The broker must bind a fresh root
        # generation after checked retirement of the old Git parent.
        self.client = Client([HOST], self.state, self.root/'logs', extra_env={
            'SPATIAL_TEST_BROKER_CONFIG': os.environ['SPATIAL_TEST_BROKER_CONFIG']})
        self.addCleanup(self.client.stop)
        self.assertEqual(self.client.call('system.hello')['implementation'], 'production')
        self.a = self.actor('a')
        self.assertEqual(self.selected(self.a), 'one')
        new_root = self.group(self.client.call('runtime.identities')['workspaced_pid']).parent
        self.assertTrue(new_root.exists())
        self.assertNotEqual((new_root.stat().st_dev, new_root.stat().st_ino), root_identity)
        self.launch("import pathlib,time\npathlib.Path('restarted-ready').touch()\nwhile True:time.sleep(.01)")
        self.until(lambda: (self.project/'restarted-ready').exists())
        new_descendants = self.descendants(); self.assertTrue(new_descendants)
        self.client.proc.stdin.close()
        self.assertEqual(self.client.proc.wait(timeout=12), 0)
        self.assertFalse(new_root.exists())
        self.until(lambda: self.gone(new_descendants))


if __name__ == '__main__':
    import sys
    names = sys.argv[1:] or [name for name in AdapterResourceTests.__dict__ if name.startswith('test_')]
    suite = unittest.TestSuite(AdapterResourceTests(name) for name in names)
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
