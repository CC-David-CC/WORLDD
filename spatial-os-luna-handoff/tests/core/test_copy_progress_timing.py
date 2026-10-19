"""Host-independent timing controls and actual configured launch-argument checks."""
import argparse
import ast
import copy
import importlib.util
import json
import re
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


def configured_launch_suite(inventories, baseline_inventories=None):
    spec = importlib.util.spec_from_file_location('timing_resource_delegate', ROOT/'tools/with_resource_delegate.py')
    delegate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(delegate)
    broker = 'workspace-file-action-broker'
    storage = 'workspace-file-action-storage-owner-btrfs'
    gateway = 'workspace-actor-cabinet-gateway'

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
            timeout = {broker: 1080, storage: 360, gateway: 300}[name]
            self.assertEqual(properties['TIMEOUT'], timeout)
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
            for name in (broker, storage, gateway):
                with self.subTest(name=name):
                    self.assert_launch(inventories['Debug'], name, 'Debug')

        def test_actual_sanitizer_launches(self):
            for name in (broker, storage, gateway):
                with self.subTest(name=name):
                    self.assert_launch(inventories['ASan-UBSan'], name, 'ASan-UBSan')

        def mutated_sanitizer(self, name=broker):
            inventory = copy.deepcopy(inventories['ASan-UBSan'])
            row = next(row for row in inventory['tests'] if row['name'] == name)
            return inventory, row

        def test_missing_sanitizer_profile_is_rejected(self):
            for name in (broker, gateway):
                inventory, row = self.mutated_sanitizer(name)
                row['command'].remove('--copy-profile=ASan-UBSan')
                with self.subTest(name=name), self.assertRaises(AssertionError):
                    self.assert_launch(inventory, name, 'ASan-UBSan')

        def test_debug_profile_for_sanitizer_is_rejected(self):
            for name in (broker, gateway):
                inventory, row = self.mutated_sanitizer(name)
                index = row['command'].index('--copy-profile=ASan-UBSan')
                row['command'][index] = '--copy-profile=Debug'
                with self.subTest(name=name), self.assertRaises(AssertionError):
                    self.assert_launch(inventory, name, 'ASan-UBSan')

        def test_duplicate_profile_is_rejected(self):
            for name in (broker, gateway):
                inventory, row = self.mutated_sanitizer(name)
                row['command'].append('--copy-profile=ASan-UBSan')
                with self.subTest(name=name), self.assertRaises(AssertionError):
                    self.assert_launch(inventory, name, 'ASan-UBSan')

        def test_profile_after_adapter_separator_is_rejected(self):
            for name in (broker, gateway):
                inventory, row = self.mutated_sanitizer(name)
                row['command'].remove('--copy-profile=ASan-UBSan')
                row['command'].append('--copy-profile=ASan-UBSan')
                with self.subTest(name=name), self.assertRaises(AssertionError):
                    self.assert_launch(inventory, name, 'ASan-UBSan')

        def test_short_outer_profile_or_ctest_budget_is_rejected(self):
            inventory, row = self.mutated_sanitizer(broker)
            row['command'].remove('--copy-engine-v2')
            with self.assertRaises(AssertionError):
                self.assert_launch(inventory, broker, 'ASan-UBSan')
            inventory, row = self.mutated_sanitizer(gateway)
            row['command'].remove('--native')
            with self.assertRaises(AssertionError):
                self.assert_launch(inventory, gateway, 'ASan-UBSan')
            inventory, row = self.mutated_sanitizer(broker)
            next(item for item in row['properties'] if item['name'] == 'TIMEOUT')['value'] = 300
            with self.assertRaises(AssertionError):
                self.assert_launch(inventory, broker, 'ASan-UBSan')
            inventory, row = self.mutated_sanitizer(gateway)
            next(item for item in row['properties'] if item['name'] == 'TIMEOUT')['value'] = 240
            with self.assertRaises(AssertionError):
                self.assert_launch(inventory, gateway, 'ASan-UBSan')

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

        def test_gateway_selector_closes_declared_cabinet_methods(self):
            def methods(path, class_name):
                tree = ast.parse(path.read_text(encoding='utf-8'))
                node = next(item for item in tree.body
                            if isinstance(item, ast.ClassDef) and item.name == class_name)
                return {item.name for item in node.body
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and item.name.startswith('test_')}

            gateway_methods = methods(ROOT/'tests/core/test_actor_cabinet_gateway.py',
                                     'ActorCabinetGatewayTests')
            cabinet_cases = {name for name in gateway_methods if name.startswith('test_actor_cabinet_')}
            copy_case = 'test_actor_cabinet_copy_requires_file_copy_and_completes_through_actor_path'
            self.assertEqual(len(cabinet_cases), 24)
            self.assertIn(copy_case, cabinet_cases)
            inherited = set()
            for path, class_name in (
                ('tests/core/test_workspace_presentation_actor.py', 'WorkspacePresentationActorTests'),
                ('tests/core/test_workspace_authority.py', 'WorkspaceAuthorityTests'),
                ('tests/core/test_world_identity.py', 'WorldIdentityFixture'),
            ):
                inherited |= methods(ROOT/path, class_name)
            candidate_names = gateway_methods | inherited

            for profile, inventory in inventories.items():
                with self.subTest(profile=profile):
                    row = next(row for row in inventory['tests'] if row['name'] == gateway)
                    command = row['command']
                    indexes = [index for index, value in enumerate(command) if value == '-k']
                    self.assertEqual(len(indexes), 1)
                    selector = command[indexes[0]+1]
                    self.assertEqual(selector, 'actor_cabinet_')
                    selected = {name for name in candidate_names if selector in name}
                    self.assertEqual(selected, cabinet_cases)
                    old_filter = 'actor_cabinet_gateway_'
                    old_selected = {name for name in candidate_names if old_filter in name}
                    self.assertNotIn(copy_case, old_selected)
                    self.assertEqual(len(old_selected), 23)

        def test_profile_flags_cover_every_adapter_registration(self):
            def validate(candidate, lane):
                self.assertEqual(len(candidate['tests']), 238)
                rows = []
                for row in candidate['tests']:
                    command = row.get('command', [])
                    adapters = [index for index, value in enumerate(command)
                                if value.endswith('/tools/with_adapter_resources.py')]
                    if adapters:
                        self.assertEqual(len(adapters), 1, row['name'])
                        index = adapters[0]
                        expected = '--copy-profile='+lane
                        self.assertEqual(command[index+1:index+3], [expected, '--'], row['name'])
                        self.assertEqual([value for value in command
                                          if value.startswith('--copy-profile=')], [expected], row['name'])
                        rows.append(row)
                    else:
                        self.assertFalse(any(value.startswith('--copy-profile=') for value in command),
                                         row['name'])
                self.assertEqual(len(rows), 56)
                authority = next(row for row in candidate['tests'] if row['name'] == 'workspace-authority')
                self.assertNotIn('--copy-engine-v2', authority['command'])
                self.assertEqual(next(item['value'] for item in authority['properties']
                                      if item['name'] == 'TIMEOUT'), 240)
                return rows

            for profile, inventory in inventories.items():
                with self.subTest(profile=profile):
                    validate(inventory, profile)
                    for defect in ('missing', 'duplicate', 'wrong-profile', 'after-separator'):
                        changed = copy.deepcopy(inventory)
                        row = next(item for item in changed['tests'] if item['name'] == 'workspace-authority')
                        command = row['command']
                        index = command.index('--copy-profile='+profile)
                        if defect == 'missing':
                            command.pop(index)
                        elif defect == 'duplicate':
                            command.insert(index+1, '--copy-profile='+profile)
                        elif defect == 'wrong-profile':
                            command[index] = '--copy-profile=Debug' if profile == 'ASan-UBSan' else '--copy-profile=ASan-UBSan'
                        else:
                            command.pop(index)
                            command.append('--copy-profile='+profile)
                        with self.subTest(defect=defect), self.assertRaises(AssertionError):
                            validate(changed, profile)

        def test_original_238_registration_metadata_is_preserved(self):

            def normalize(value, old_source, old_build, new_source, new_build):
                if isinstance(value, str):
                    value = value.replace(str(old_source), '<SOURCE>').replace(str(new_source), '<SOURCE>').replace(str(old_build), '<BUILD>').replace(str(new_build), '<BUILD>')
                    return re.sub(r'(--unit=[^\"]+-)[0-9a-f]{64}', r'\1<UNIT_HASH>', value)
                if isinstance(value, list):
                    return [normalize(item, old_source, old_build, new_source, new_build) for item in value]
                if isinstance(value, dict):
                    return {key: normalize(item, old_source, old_build, new_source, new_build) for key, item in value.items()}
                return value

            for profile, old_inventory in baseline_inventories.items():
                with self.subTest(profile=profile):
                    new_inventory = inventories[profile]
                    self.assertEqual(len(old_inventory['tests']), 238)
                    self.assertEqual([row['name'] for row in new_inventory['tests']],
                                     [row['name'] for row in old_inventory['tests']])
                    old_rows = {row['name']: row for row in old_inventory['tests']}
                    new_rows = {row['name']: row for row in new_inventory['tests']}
                    old_adapter = next(value for row in old_inventory['tests'] for value in row.get('command', [])
                                       if value.endswith('/tools/with_adapter_resources.py'))
                    old_source = Path(old_adapter).parent.parent
                    old_build = Path(next(item['value'] for row in old_inventory['tests']
                                          for item in row.get('properties', [])
                                          if item['name'] == 'WORKING_DIRECTORY'))
                    new_build = Path(next(item['value'] for row in new_inventory['tests']
                                          for item in row.get('properties', [])
                                          if item['name'] == 'WORKING_DIRECTORY'))
                    new_adapter = next(value for row in new_inventory['tests'] for value in row.get('command', [])
                                       if value.endswith('/tools/with_adapter_resources.py'))
                    new_source = Path(new_adapter).parent.parent
                    old_ctest = old_build / 'CTestTestfile.cmake'
                    new_ctest = new_build / 'CTestTestfile.cmake'
                    def add_test_lines(path):
                        rows = {}
                        for line in path.read_text().splitlines():
                            match = re.match(r'\s*add_test\(\[=\[([^]]+)\]=\]', line)
                            if match:
                                rows[match.group(1)] = line
                        return rows
                    old_lines = add_test_lines(old_ctest)
                    new_lines = add_test_lines(new_ctest)
                    self.assertEqual(set(old_lines), set(old_rows))
                    self.assertEqual(set(new_lines), set(new_rows))
                    for name, old_row in old_rows.items():
                        new_row = new_rows[name]
                        expected_command = normalize(old_row['command'], old_source, old_build, new_source, new_build)
                        adapters = [index for index, value in enumerate(expected_command)
                                    if value.endswith('/tools/with_adapter_resources.py')]
                        if adapters:
                            self.assertEqual(len(adapters), 1, name)
                            index = adapters[0]
                            flag = '--copy-profile='+profile
                            if expected_command[index+1:index+2] != [flag]:
                                self.assertEqual(expected_command[index+1:index+2], ['--'], name)
                                expected_command.insert(index+1, flag)
                        self.assertEqual(normalize(new_lines[name], old_source, old_build, new_source, new_build),
                                         normalize(old_lines[name], old_source, old_build, new_source, new_build)
                                         .replace('with_adapter_resources.py\" \"--\"',
                                                  'with_adapter_resources.py\" \"--copy-profile='+profile+'\" \"--\"')
                                         if adapters and '--copy-profile='+profile not in old_lines[name] else
                                         normalize(old_lines[name], old_source, old_build, new_source, new_build), name)
                        self.assertEqual(normalize(new_row['properties'], old_source, old_build, new_source, new_build),
                                         normalize(old_row['properties'], old_source, old_build, new_source, new_build), name)

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ConfiguredLaunchTests)
    if baseline_inventories is None:
        suite = unittest.TestSuite(test for test in suite
                                  if test._testMethodName != 'test_original_238_registration_metadata_is_preserved')
    return suite


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug-inventory', type=Path)
    parser.add_argument('--san-inventory', type=Path)
    parser.add_argument('--baseline-debug-inventory', type=Path)
    parser.add_argument('--baseline-san-inventory', type=Path)
    args = parser.parse_args()
    if bool(args.debug_inventory) != bool(args.san_inventory):
        parser.error('Provide both actual configured inventories together')
    if bool(args.baseline_debug_inventory) != bool(args.baseline_san_inventory):
        parser.error('Provide both baseline inventories together')
    if args.baseline_debug_inventory and not args.debug_inventory:
        parser.error('Baseline comparison requires both new configured inventories')
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ObservationTimingTests)
    if args.debug_inventory:
        inventories = {
            'Debug': json.loads(args.debug_inventory.read_text()),
            'ASan-UBSan': json.loads(args.san_inventory.read_text())}
        baselines = None
        if args.baseline_debug_inventory:
            baselines = {
                'Debug': json.loads(args.baseline_debug_inventory.read_text()),
                'ASan-UBSan': json.loads(args.baseline_san_inventory.read_text())}
        suite.addTests(configured_launch_suite(inventories, baselines))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
