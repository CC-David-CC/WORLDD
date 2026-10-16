#!/usr/bin/env python3
"""Receipt-only reconciliation regressions. No runtime or host transport is used."""
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest

spec = importlib.util.spec_from_file_location('m07_receipt_paths', Path(__file__).with_name('m07_deployed_gate.py'))
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
    return path


class ReconciliationPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='m07-reconcile-path-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        candidate = '0' * 40
        names = sorted(gate.M07_REQUIRED)
        local = dict(candidate=candidate, required_tests=names, dedicated_crosshost=sorted(gate.M07_DEDICATED),
                     profiles={profile: {'commands': [{'name': 'core', 'exit_code': 0,
                         'junit_cases': len(names) - len(gate.M07_DEDICATED),
                         'all_registered_present': True, 'failed_or_skipped': False}]} for profile in gate.m06.PROFILES})
        self.local_path = write(root / 'local-run.json', local)
        local_runs = {host: dict(path=str(self.local_path), sha256=gate.m06.sha(self.local_path),
            remote_receipt=local, inventories={profile: {'names': names} for profile in gate.m06.PROFILES})
            for host in gate.m06.TEST_HOSTS}
        self.m06_path = write(root / 'm06/reconciliation.json',
            dict(candidate=candidate, status='passed_needs_primary_review', phase_complete=False, local_runs=local_runs))
        pins = {host: dict(path='/offline/deployment', provenance_sha256='a' * 64,
                          deployment_receipt_sha256=('b' if host == 'sb16' else 'c') * 64) for host in gate.m06.TEST_HOSTS}
        write(self.m06_path.parent / 'plan.json', dict(candidate=candidate, deployments=pins))
        rows = []
        for host in sorted(gate.m06.TEST_HOSTS):
            observer = 'sb16-2' if host == 'sb16' else 'sb16'
            for profile in gate.m06.PROFILES:
                evidence = root / 'cells' / host / profile
                evidence.mkdir(parents=True)
                log = evidence / 'test.log'
                log.write_bytes(b'Ran 1 test in 0.01s\n\nOK\n')
                receipt = write(evidence / 'receipt.json', dict(candidate=candidate, host=host, profile=profile,
                    direction=host + '-to-' + observer, m06_deployment_pins=pins,
                    deployment_provenance_sha256=pins[host]['provenance_sha256'],
                    deployment_receipt_sha256=pins[host]['deployment_receipt_sha256'],
                    observer_deployment=pins[observer], status='passed', qualification='UNQUALIFIED',
                    phase_complete=False, exit_code=0, cleanup={'adapter_scope_removed': True, 'cgroup_absent': True},
                    local_log_inventory=['fixture'], remote_log_inventory={'files': ['fixture']},
                    test_log=str(log), test_output_sha256=gate.m06.sha(log)))
                rows.append({'path': str(receipt)})
        self.index = write(root / 'workspace-results.json', rows)
        self.args = types.SimpleNamespace(candidate=candidate, m06_reconciliation=self.m06_path,
                                         workspace_results=self.index, output=root / 'reconciled')

    def test_json_string_local_receipt_path_reconciles_four_cells(self):
        report = json.loads(self.m06_path.read_text())
        self.assertIsInstance(report['local_runs']['sb16']['path'], str)
        self.assertEqual(gate.reconcile(self.args), 0)
        result = json.loads((self.args.output / 'm07-reconciliation.json').read_text())
        self.assertEqual(len(result['workspace_crosshost']), 4)
        self.assertEqual(result['m06_reconciliation_sha256'], gate.m06.sha(self.m06_path))

    def test_changed_local_receipt_bytes_fail_hash_check(self):
        self.local_path.write_bytes(self.local_path.read_bytes() + b' ')
        with self.assertRaisesRegex(RuntimeError, 'sb16 local gate receipt hash mismatch'):
            gate.reconcile(self.args)
        self.assertFalse(self.args.output.exists())

    def test_changed_workspace_log_still_fails(self):
        receipt_path = Path(json.loads(self.index.read_text())[0]['path'])
        log = Path(json.loads(receipt_path.read_text())['test_log'])
        log.write_bytes(b'FAILED\n')
        with self.assertRaisesRegex(RuntimeError, 'workspace cross-host receipt/log is stale or failed'):
            gate.reconcile(self.args)
        self.assertFalse(self.args.output.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
