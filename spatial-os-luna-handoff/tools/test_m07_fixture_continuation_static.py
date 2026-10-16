#!/usr/bin/env python3
"""Focused source controls for the pinned M06 fixture continuation."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import types
from types import SimpleNamespace
import unittest

HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = load('m06_deployed_gate')
executor = load('execute_workspace_crosshost_serial')
FIXTURE_SOURCE = next(path for path in (
    HERE.parent / 'tests/core/actor_transition_crosshost_fixture.py',
    HERE.parent / 'source/actor_transition_crosshost_fixture.py',
) if path.is_file())
FIXTURE_CORE = FIXTURE_SOURCE.parent
sys.path.insert(0, str(FIXTURE_CORE))
fixture_spec = importlib.util.spec_from_file_location('m07_fixture_control_subject', FIXTURE_SOURCE)
fixture_subject = importlib.util.module_from_spec(fixture_spec)
fixture_spec.loader.exec_module(fixture_subject)


class FixtureContinuationControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.deployment = self.root / 'deployment'
        (self.deployment / 'source').mkdir(parents=True)
        self.fixture = self.root / 'fixture.py'
        self.helper = self.root / 'host_helper.py'
        self.fixture.write_text('# fixture\n')
        self.helper.write_text('# helper\n')
        self.manifest_path = self.root / 'continuation.json'
        self.manifest = {
            'schema': 1,
            'candidate': '1e58e65dec132a0a16f004ca4527034dca081f77',
            'semantic_fixture': {'path': str(self.fixture), 'sha256': self.digest(self.fixture)},
            'host_helper': {'path': str(self.helper), 'sha256': self.digest(self.helper)},
        }
        self.seal_manifest()

    @staticmethod
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def seal_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest) + '\n')
        self.manifest_sha = self.digest(self.manifest_path)

    def select(self, candidate=None, sha256=None):
        return gate._fixture_continuation(
            self.manifest_path, sha256 or self.manifest_sha,
            candidate or self.manifest['candidate'], self.deployment)

    def test_pinned_fixture_pair_selection_succeeds(self):
        selected = self.select()
        self.assertEqual(selected['semantic_fixture'],
                         {'path': str(self.fixture.resolve()), 'sha256': self.digest(self.fixture)})
        self.assertEqual(selected['host_helper'],
                         {'path': str(self.helper.resolve()), 'sha256': self.digest(self.helper)})
        self.assertEqual(selected['candidate'], self.manifest['candidate'])

    def test_wrong_manifest_pin_candidate_schema_and_file_pin_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'SHA256 differs'):
            self.select(sha256='0' * 64)
        with self.assertRaisesRegex(RuntimeError, 'candidate differs'):
            self.select(candidate='0' * 40)
        self.manifest['schema'] = 2
        self.seal_manifest()
        with self.assertRaisesRegex(RuntimeError, 'schema/keys differ'):
            self.select()
        self.manifest['schema'] = 1
        self.manifest['host_helper']['sha256'] = '0' * 64
        self.seal_manifest()
        with self.assertRaisesRegex(RuntimeError, 'SHA256 differs'):
            self.select()

    def test_linked_fixture_inputs_are_rejected(self):
        link = self.root / 'linked-helper.py'
        link.symlink_to(self.helper)
        self.manifest['host_helper'] = {'path': str(link), 'sha256': self.digest(self.helper)}
        self.seal_manifest()
        with self.assertRaisesRegex(RuntimeError, 'non-symlink'):
            self.select()

    def test_default_fixture_path_remains_the_unmodified_bundle_path(self):
        source = FIXTURE_SOURCE.read_text()
        ast.parse(source)
        self.assertIn("HOST_HELPER_OVERRIDE = _pinned_host_helper_from_argv()", source)
        self.assertIn("else fixture_path(self.bundle, 'actor_transition_crosshost_host.py')", source)
        self.assertIn("fixture-selection.json", source)

    def test_override_loads_exact_alternate_basename_ignoring_adjacent_decoy(self):
        selected_path = self.root / 'alternate_helper_name.py'
        selected_path.write_text("IDENTITY = 'selected-alternate'\n")
        (self.root / 'actor_transition_crosshost_host.py').write_text(
            "IDENTITY = 'adjacent-decoy'\n")
        expected = self.digest(selected_path)
        cached = sys.modules.get(fixture_subject.HOST_HELPER_MODULE_NAME)
        try:
            module = fixture_subject._load_pinned_host_helper(selected_path, expected)
            self.assertEqual(module.IDENTITY, 'selected-alternate')
            self.assertEqual(Path(module.__file__), selected_path.resolve())
            self.assertIs(sys.modules[fixture_subject.HOST_HELPER_MODULE_NAME], module)
        finally:
            if cached is None:
                sys.modules.pop(fixture_subject.HOST_HELPER_MODULE_NAME, None)
            else:
                sys.modules[fixture_subject.HOST_HELPER_MODULE_NAME] = cached

    def test_override_replaces_a_cached_decoy_module(self):
        selected_path = self.root / 'helper.py'
        selected_path.write_text("IDENTITY = 'selected-source'\n")
        expected = self.digest(selected_path)
        cached = sys.modules.get(fixture_subject.HOST_HELPER_MODULE_NAME)
        decoy = types.ModuleType(fixture_subject.HOST_HELPER_MODULE_NAME)
        decoy.IDENTITY = 'cached-decoy'
        sys.modules[fixture_subject.HOST_HELPER_MODULE_NAME] = decoy
        try:
            module = fixture_subject._load_pinned_host_helper(selected_path, expected)
            self.assertEqual(module.IDENTITY, 'selected-source')
            self.assertIsNot(module, decoy)
            self.assertIs(sys.modules[fixture_subject.HOST_HELPER_MODULE_NAME], module)
        finally:
            if cached is None:
                sys.modules.pop(fixture_subject.HOST_HELPER_MODULE_NAME, None)
            else:
                sys.modules[fixture_subject.HOST_HELPER_MODULE_NAME] = cached

    def test_default_helper_import_behavior_is_unchanged(self):
        self.assertIsNone(fixture_subject.HOST_HELPER_OVERRIDE)
        self.assertEqual(Path(fixture_subject.host_support.__file__).name,
                         'actor_transition_crosshost_host.py')
        self.assertIn('actor_transition_crosshost_host', sys.modules)

    def test_executor_tool_pin_closure_uses_reviewed_candidate_dependencies(self):
        expected = {
            'prepare_crosshost_plan.py': (
                Path('/home/dflanag3/spatial-artifacts/m07-fixture-repaired-crosshost-coordination-20261016/review-01/candidate/prepare_crosshost_plan.py'),
                'fba21bd6bc7d658706b2094043eecb6248bba664d76cc9b4257d46abdec682e5'),
            'launch_m06_crosshost.sh': (
                Path('/home/dflanag3/spatial-artifacts/m07-fixture-repaired-crosshost-coordination-20261016/review-01/candidate/launch_m06_crosshost.sh'),
                '1d8a55904453d6c67723867573449ee730aea3166410c93553ac741b8b192b69'),
        }
        verified = executor._validate_tool_dependencies()
        closure = executor._tool_pin_paths(verified)
        for name, (path, digest) in expected.items():
            self.assertEqual(verified[name], (path, digest))
            self.assertIn(path, closure)
        self.assertIn(Path(executor.__file__).with_name('m06_deployed_gate.py'), closure)

    def test_executor_tool_pin_closure_rejects_absent_dependency(self):
        missing = self.root / 'missing-tool.py'
        with self.assertRaisesRegex(RuntimeError, 'dependency is absent'):
            executor._validate_tool_dependencies({'missing-tool.py': (missing, '0' * 64)})

    def test_executor_tool_pin_closure_rejects_mismatched_dependency(self):
        changed = self.root / 'changed-tool.py'
        changed.write_text('# changed\n')
        with self.assertRaisesRegex(RuntimeError, 'SHA256 differs'):
            executor._validate_tool_dependencies({'changed-tool.py': (changed, '0' * 64)})

    def test_m06_argv_requires_the_exact_pinned_continuation_arguments(self):
        args = SimpleNamespace(
            m06_runner_dir=Path('/fixture/m06-run'),
            plan=Path('/fixture/plan.json'),
            fixture_continuation_manifest='/fixture/continuation.json',
            fixture_continuation_manifest_sha256='a' * 64)
        command = executor._m06_command(args)
        self.assertEqual(command[6], '/home/dflanag3/spatial-artifacts/m07-crosshost-fixture-continuation-20261016/review02/tools/m06_deployed_gate.py')
        self.assertEqual(command[7:10], ['crosshost-handoff', '--plan', str(args.plan)])
        self.assertEqual(command[-4:], ['--fixture-continuation-manifest', args.fixture_continuation_manifest,
                                        '--fixture-continuation-manifest-sha256',
                                        args.fixture_continuation_manifest_sha256])
        self.assertEqual(executor._validate_m06_command(command, args), command)
        wrong = command[:-2]
        with self.assertRaisesRegex(RuntimeError, 'argv differs'):
            executor._validate_m06_command(wrong, args)


if __name__ == '__main__':
    unittest.main()
