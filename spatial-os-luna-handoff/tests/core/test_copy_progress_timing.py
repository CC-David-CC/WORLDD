"""Host-independent timing controls and actual configured launch-argument checks."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from harness.copy_progress_timing import COPY_JOB_OBSERVATION_SECONDS, poll_copy_job_before_deadline


class ObservationTimingTests(unittest.TestCase):
    def test_timely_receipt_is_returned_unchanged(self):
        self.assertEqual(COPY_JOB_OBSERVATION_SECONDS, 300)
        for receipt in ({'state': 'COMPLETE'}, {'state': 'RUNNING', 'entries': 4096}):
            with self.subTest(receipt=receipt):
                self.assertIs(poll_copy_job_before_deadline(lambda: receipt, 300, now=lambda: 299), receipt)

    def test_expired_observation_does_not_poll(self):
        calls = []
        with self.assertRaisesRegex(AssertionError, 'absolute observation deadline'):
            poll_copy_job_before_deadline(lambda: calls.append('poll'), 300, now=lambda: 300)
        self.assertEqual(calls, [])

    def test_late_complete_cannot_bypass_deadline(self):
        clock = [299.0]
        def poll():
            clock[0] = 300.001
            return {'state': 'COMPLETE'}
        with self.assertRaisesRegex(AssertionError, 'absolute observation deadline'):
            poll_copy_job_before_deadline(poll, 300, now=lambda: clock[0])

    def test_late_positive_progress_cannot_reach_cancel(self):
        clock = [299.0]
        calls = []
        def poll():
            calls.append('poll')
            clock[0] = 300.001
            return {'state': 'RUNNING', 'entries': 4096}
        with self.assertRaisesRegex(AssertionError, 'absolute observation deadline'):
            receipt = poll_copy_job_before_deadline(poll, 300, now=lambda: clock[0])
            if receipt['entries'] > 0:
                calls.append('cancel')
        self.assertEqual(calls, ['poll'])

    def test_reply_at_exact_deadline_is_rejected(self):
        clock = [299.0]
        def poll():
            clock[0] = 300.0
            return {'state': 'COMPLETE'}
        with self.assertRaisesRegex(AssertionError, 'absolute observation deadline'):
            poll_copy_job_before_deadline(poll, 300, now=lambda: clock[0])

    def test_fresh_progress_does_not_extend_absolute_deadline(self):
        clock = [0.0]
        deadline = clock[0] + COPY_JOB_OBSERVATION_SECONDS
        def poll_at(value):
            def poll():
                clock[0] = value
                return {'state': 'RUNNING', 'entries': value}
            return poll
        for value in (100, 200, 299):
            receipt = poll_copy_job_before_deadline(poll_at(value), deadline, now=lambda: clock[0])
            self.assertEqual(receipt['entries'], value)
        with self.assertRaisesRegex(AssertionError, 'absolute observation deadline'):
            poll_copy_job_before_deadline(poll_at(300), deadline, now=lambda: clock[0])


def configured_launch_suite(inventories):
    spec = importlib.util.spec_from_file_location('timing_resource_delegate', ROOT/'tools/with_resource_delegate.py')
    delegate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(delegate)
    broker = 'workspace-file-action-broker'
    storage = 'workspace-file-action-storage-owner-btrfs'

    class ConfiguredLaunchTests(unittest.TestCase):
        def assert_launch(self, inventory, name, profile):
            rows = [row for row in inventory['tests'] if row['name'] == name]
            self.assertEqual(len(rows), 1)
            row = rows[0]
            command = row['command']
            adapter = [i for i, value in enumerate(command) if value.endswith('/tools/with_adapter_resources.py')]
            resource = [i for i, value in enumerate(command) if value.endswith('/tools/with_resource_delegate.py')]
            self.assertEqual(len(adapter), 1)
            self.assertEqual(len(resource), 1)
            expected = '--copy-profile='+profile
            self.assertEqual([value for value in command if value.startswith('--copy-profile=')], [expected])
            self.assertEqual(command[adapter[0]+1:adapter[0]+3], [expected, '--'])
            extended = name == broker
            prefix = ['--native', '--copy-engine-v2', '--'] if extended else ['--native', '--']
            self.assertEqual(command[resource[0]+1:resource[0]+1+len(prefix)], prefix)
            properties = {item['name']: item['value'] for item in row['properties']}
            self.assertEqual(properties['TIMEOUT'], 1080 if extended else 360)
            separator = command.index('--', resource[0]+1)
            inner = command[separator+1:]
            service = delegate.build_service_command(
                unit='timing-control.service', username='fixture-owner', native=True,
                copy_engine_v2=extended, working_directory=Path('/tmp'), environment={}, argv=inner)
            self.assertIn('RuntimeMaxSec='+('900' if extended else '240'), service)
            for value in ('TimeoutStopSec=5s', 'MemoryMax=1536M', 'MemorySwapMax=0', 'TasksMax=256', 'CPUQuota=400%'):
                self.assertIn(value, service)
            self.assertEqual(service[-len(inner):], inner)
            return command

        def test_actual_debug_launches(self):
            for name in (broker, storage):
                with self.subTest(name=name):
                    self.assert_launch(inventories['Debug'], name, 'Debug')

        def test_actual_sanitizer_launches(self):
            for name in (broker, storage):
                with self.subTest(name=name):
                    self.assert_launch(inventories['ASan-UBSan'], name, 'ASan-UBSan')

        def mutated_sanitizer(self):
            inventory = copy.deepcopy(inventories['ASan-UBSan'])
            row = next(row for row in inventory['tests'] if row['name'] == broker)
            return inventory, row

        def test_missing_sanitizer_profile_is_rejected(self):
            inventory, row = self.mutated_sanitizer()
            row['command'].remove('--copy-profile=ASan-UBSan')
            with self.assertRaises(AssertionError):
                self.assert_launch(inventory, broker, 'ASan-UBSan')

        def test_debug_profile_for_sanitizer_is_rejected(self):
            inventory, row = self.mutated_sanitizer()
            index = row['command'].index('--copy-profile=ASan-UBSan')
            row['command'][index] = '--copy-profile=Debug'
            with self.assertRaises(AssertionError):
                self.assert_launch(inventory, broker, 'ASan-UBSan')

        def test_duplicate_profile_is_rejected(self):
            inventory, row = self.mutated_sanitizer()
            row['command'].append('--copy-profile=ASan-UBSan')
            with self.assertRaises(AssertionError):
                self.assert_launch(inventory, broker, 'ASan-UBSan')

        def test_profile_after_adapter_separator_is_rejected(self):
            inventory, row = self.mutated_sanitizer()
            row['command'].remove('--copy-profile=ASan-UBSan')
            row['command'].append('--copy-profile=ASan-UBSan')
            with self.assertRaises(AssertionError):
                self.assert_launch(inventory, broker, 'ASan-UBSan')

        def test_short_outer_profile_or_ctest_budget_is_rejected(self):
            inventory, row = self.mutated_sanitizer()
            row['command'].remove('--copy-engine-v2')
            with self.assertRaises(AssertionError):
                self.assert_launch(inventory, broker, 'ASan-UBSan')
            inventory, row = self.mutated_sanitizer()
            next(item for item in row['properties'] if item['name'] == 'TIMEOUT')['value'] = 300
            with self.assertRaises(AssertionError):
                self.assert_launch(inventory, broker, 'ASan-UBSan')

        def test_deadline_tooling_has_a_30_second_registration(self):
            for profile, inventory in inventories.items():
                with self.subTest(profile=profile):
                    rows = [row for row in inventory['tests'] if row['name'] == 'copy-progress-timing-tooling']
                    self.assertEqual(len(rows), 1)
                    row = rows[0]
                    self.assertTrue(Path(row['command'][0]).name.startswith('python3'))
                    self.assertEqual(row['command'][1], '-B')
                    self.assertTrue(row['command'][2].endswith('/tests/core/test_copy_progress_timing.py'))
                    self.assertEqual(len(row['command']), 3)
                    properties = {item['name']: item['value'] for item in row['properties']}
                    self.assertEqual(properties['TIMEOUT'], 30)
                    self.assertFalse(properties.get('DISABLED', False))
                    self.assertFalse(properties.get('WILL_FAIL', False))

        def test_profile_flags_are_limited_to_the_two_registrations(self):
            for profile, inventory in inventories.items():
                with self.subTest(profile=profile):
                    explicit = {row['name'] for row in inventory['tests']
                                if any(value.startswith('--copy-profile=') for value in row.get('command', []))}
                    self.assertEqual(explicit, {broker, storage})
                    authority = next(row for row in inventory['tests'] if row['name'] == 'workspace-authority')
                    self.assertNotIn('--copy-engine-v2', authority['command'])
                    self.assertEqual(next(item['value'] for item in authority['properties']
                                          if item['name'] == 'TIMEOUT'), 240)

    return unittest.defaultTestLoader.loadTestsFromTestCase(ConfiguredLaunchTests)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug-inventory', type=Path)
    parser.add_argument('--san-inventory', type=Path)
    args = parser.parse_args()
    if bool(args.debug_inventory) != bool(args.san_inventory):
        parser.error('Provide both actual configured inventories together')
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ObservationTimingTests)
    if args.debug_inventory:
        suite.addTests(configured_launch_suite({
            'Debug': json.loads(args.debug_inventory.read_text()),
            'ASan-UBSan': json.loads(args.san_inventory.read_text())}))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
