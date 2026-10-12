"""Independent kernel witness after a real authenticated actor launches an application."""
import json
import os
from pathlib import Path
import unittest
from test_workspace_authority import WorkspaceAuthorityTests


class AuthenticatedResourceTests(WorkspaceAuthorityTests):
    def test_authenticated_launch_has_kernel_budgets_and_actual_broker_identity(self):
        world = self.workspace(); peer = world.peers[0]; self.select(peer)
        process = self.launch_process(peer,
            "from pathlib import Path\nimport time\nPath('ready').touch()\nwhile not Path('release').exists():time.sleep(.01)")
        self.until(lambda:(self.project/'ready').exists(), bool)
        identities = world.management('runtime.identities')
        self.assertEqual(identities['workspaced_pid'], self.broker.pid)
        self.assertNotEqual(self.broker.pid, self.broker.proc.pid)
        def group(pid):
            return Path('/sys/fs/cgroup')/Path(f'/proc/{pid}/cgroup').read_text().strip().split('::',1)[1].lstrip('/')
        manager = group(self.broker.pid)
        self.assertEqual(manager.name, 'broker-manager')
        applications = [p.parent for p in manager.parent.rglob('cgroup.procs')
            if len(p.relative_to(manager.parent).parts) == 4]
        self.assertEqual(len(applications),1)
        application = applications[0]
        limits = {name:(application/name).read_text().strip() for name in
            ('memory.max','memory.swap.max','pids.max','cpu.max','io.max')}
        self.assertEqual(limits['memory.max'],'134217728')
        self.assertEqual(limits['memory.swap.max'],'0')
        self.assertEqual(limits['pids.max'],'16')
        self.assertEqual(limits['cpu.max'],'100000 100000')
        self.assertIn('wbps=16777216',limits['io.max'])
        descriptors = [os.readlink(path) for path in Path(f'/proc/{self.broker.pid}/fd').iterdir()]
        self.assertFalse(any('fixture-resource-policy' in name for name in descriptors))
        if os.environ.get('SPATIAL_SCOPE_EVIDENCE'):
            destination = Path(os.environ['SPATIAL_SCOPE_EVIDENCE'])/'authenticated-kernel.json'
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(dict(broker_pid=self.broker.pid, owner_pid=self.broker.proc.pid,
                manager=str(manager),application=str(application),limits=limits,broker_fds=descriptors),indent=2)+'\n')
        (self.project/'release').touch(); self.wait_job(world,process)


if __name__ == '__main__':
    suite = unittest.TestSuite([AuthenticatedResourceTests('test_authenticated_launch_has_kernel_budgets_and_actual_broker_identity')])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
