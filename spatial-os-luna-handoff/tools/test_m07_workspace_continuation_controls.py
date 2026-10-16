#!/usr/bin/env python3
"""Focused controls for the actual pinned M07 workspace collector and dispatch."""
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

TOOLS = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collector = load('m07_workspace_collector_continuation')
executor = load('execute_workspace_crosshost_serial')


class WorkspaceCollectorControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.evidence = self.root / 'evidence'
        self.method = self.evidence / 'test_shared_workspace_geometry_changes_both_native_hosts'
        self.observer = self.method / 'authority-native-observer'
        self.observer.mkdir(parents=True)
        self.security = self.evidence / 'security'
        self.security_method = self.security / 'test_shared_workspace_geometry_changes_both_native_hosts'
        self.security_method.mkdir(parents=True)
        self.required = (
            self.method / 'peer-native.stderr', self.method / 'peer-resource-launch.json',
            self.method / 'peer-resource-terminal.txt', self.method / 'exact-unit-terminal.json',
            self.observer / 'observer.stderr', self.observer / 'observer-raw.stdout',
            self.observer / 'observer-ready.frame', self.observer / 'observer-resource-launch.json',
            self.observer / 'cleanup-receipts.json', self.observer / 'geometry-host-session.log',
            self.observer / 'geometry-process-identity-witness.txt',
        )
        for path in self.required:
            path.write_text('retained fixture evidence\n')
        self.required[1].write_text('{"unit":"peer.service"}\n')
        self.required[7].write_text('{"unit":"observer.service"}\n')
        self.required[9].write_text('host session clean\n')
        self.required[10].write_text('sudo-n process identity fallback used\n')
        (self.security_method / 'shared.log').write_text('deduplicate this file\n')
        (self.method / 'fixture-output.log').write_text('test output clean\n')

    def scan(self):
        return collector._scan_local_logs((self.evidence, self.security))

    def test_actual_local_fixture_layout_succeeds(self):
        rows = self.scan()
        names = {Path(row['path']).name for row in rows}
        self.assertIn('peer-native.stderr', names)
        self.assertIn('observer-raw.stdout', names)
        self.assertIn('geometry-host-session.log', names)
        self.assertNotIn('boundary.stdout', names)
        self.assertNotIn('boundary.stderr', names)

    def test_each_required_local_artifact_is_enforced(self):
        for path in self.required:
            with self.subTest(path=path.name):
                saved = path.read_bytes()
                path.unlink()
                with self.assertRaisesRegex(RuntimeError, 'required local native evidence'):
                    self.scan()
                path.write_bytes(saved)

    def test_nested_security_root_is_scanned_once(self):
        rows = self.scan()
        paths = [row['path'] for row in rows]
        self.assertEqual(len(paths), len(set(paths)))
        shared = [row for row in rows if Path(row['path']).name == 'shared.log']
        self.assertEqual(len(shared), 1)
        service = [row for row in rows if Path(row['path']).name == 'geometry-host-session.log']
        self.assertEqual(len(service), 1)

    def test_local_symlink_and_sanitizer_diagnostics_fail(self):
        link = self.method / 'linked.log'
        link.symlink_to(self.method / 'fixture-output.log')
        with self.assertRaisesRegex(RuntimeError, 'symlink'):
            self.scan()
        link.unlink()
        (self.method / 'fixture-output.log').write_text('AddressSanitizer: use-after-free\n')
        with self.assertRaisesRegex(RuntimeError, 'sanitizer'):
            self.scan()

    def test_remote_actual_inventory_requires_one_service_and_identity(self):
        files = [
            {'path': '/remote/geometry-host-session.log', 'name': 'geometry-host-session.log', 'sha256': 'a' * 64, 'bytes': 1},
            {'path': '/remote/geometry-process-identity-witness.txt', 'name': 'geometry-process-identity-witness.txt', 'sha256': 'b' * 64, 'bytes': 1},
            {'path': '/remote/support-a.log', 'name': 'support-a.log', 'sha256': 'c' * 64, 'bytes': 1},
            {'path': '/remote/support-b.log', 'name': 'support-b.log', 'sha256': 'd' * 64, 'bytes': 1},
            {'path': '/remote/support-c.log', 'name': 'support-c.log', 'sha256': 'e' * 64, 'bytes': 1},
        ]
        result = collector._validate_remote_inventory({'exists': True, 'files': files, 'bad': [], 'unsafe': []})
        self.assertEqual(result['service_log']['name'], 'geometry-host-session.log')
        self.assertEqual(result['process_identity']['name'], 'geometry-process-identity-witness.txt')
        for removed in ('geometry-host-session.log', 'geometry-process-identity-witness.txt'):
            with self.subTest(removed=removed):
                bad = [row for row in files if row['name'] != removed]
                with self.assertRaisesRegex(RuntimeError, 'exactly one'):
                    collector._validate_remote_inventory({'exists': True, 'files': bad, 'bad': [], 'unsafe': []})
        duplicated = files + [dict(files[0], path='/remote/duplicate.log')]
        with self.assertRaisesRegex(RuntimeError, 'exactly one'):
            collector._validate_remote_inventory({'exists': True, 'files': duplicated, 'bad': [], 'unsafe': []})

    def test_remote_symlink_and_sanitizer_flags_fail(self):
        row = {'path': '/remote/geometry-host-session.log', 'name': 'geometry-host-session.log', 'sha256': 'a' * 64, 'bytes': 1}
        identity = {'path': '/remote/geometry-process-identity-witness.txt', 'name': 'geometry-process-identity-witness.txt', 'sha256': 'b' * 64, 'bytes': 1}
        with self.assertRaisesRegex(RuntimeError, 'unsafe'):
            collector._validate_remote_inventory({'exists': True, 'files': [row, identity], 'bad': [], 'unsafe': ['/remote/link']})
        with self.assertRaisesRegex(RuntimeError, 'sanitizer'):
            collector._validate_remote_inventory({'exists': True, 'files': [row, identity], 'bad': ['/remote/log'], 'unsafe': []})


class RolePathControls(unittest.TestCase):
    def test_actual_attempt_prefixed_authority_and_observer_paths_are_accepted(self):
        root = Path('/qualified/root')
        attempt = 'collector-workspace-fixed-03'
        profile = 'Debug'
        direction = 'sb16-to-sb16-2'
        scope = root / 'evidence' / ('workspace-' + attempt) / profile / direction
        executor._validate_role_output_paths(root, attempt, profile, direction, 'sb16',
            scope / 'authority-sb16', scope / 'observer-sb16-2')
        with self.assertRaisesRegex(RuntimeError, 'exact attempt'):
            executor._validate_role_output_paths(root, attempt, profile, direction, 'sb16',
                root / 'evidence' / attempt / profile / direction / 'authority-sb16',
                scope / 'observer-sb16-2')


class ExactSourceControls(unittest.TestCase):
    def test_verified_source_bytes_are_compiled_without_importlib_cache(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'pinned.py'
            source.write_bytes(b'value = "verified source\\n"\n')
            expected = hashlib.sha256(source.read_bytes()).hexdigest()
            module = collector._load_verified_source(source, expected, 'pinned_control')
            self.assertEqual(module.value, 'verified source\n')
            source.write_bytes(b'value = "changed"\n')
            with self.assertRaisesRegex(RuntimeError, 'hash differs'):
                collector._load_verified_source(source, expected, 'pinned_control')


class PinnedDispatchControls(unittest.TestCase):
    def test_tool_pin_mismatch_stops_before_dispatch(self):
        class FakeTransport:
            def __init__(self):
                self.calls = []
                self.dispatches = 0

            def fs(self, host, operation, path):
                self.calls.append((host, operation, path))
                if path == '/external/m07_workspace_collector_continuation.py':
                    return '0' * 64
                return executor.M06_SIBLING_SHA256

            def create(self, *args, **kwargs):
                self.dispatches += 1
                raise AssertionError('dispatch must not occur')

        transport = FakeTransport()
        args = SimpleNamespace(m07_tool_path='/external/m07_workspace_collector_continuation.py',
                               m07_tool_sha256='1' * 64)
        with self.assertRaisesRegex(RuntimeError, 'pin differs on sb16'):
            executor._validate_m07_tool(args, transport)
        self.assertEqual(transport.dispatches, 0)
        self.assertEqual(transport.calls[0][0], 'sb16')

    def test_all_four_cells_and_reconcile_use_exact_pinned_tool_argv(self):
        args = SimpleNamespace(m07_tool_path='/external/m07_workspace_collector_continuation.py',
                               m07_tool_sha256='a' * 64,
                               m06_reconciliation=Path('/external/m06.json'))
        commands = []
        for profile in executor.PROFILES:
            for direction in executor.DIRECTIONS:
                commands.append(executor._workspace_command(
                    args, direction.split('-to-')[0], profile, direction,
                    '/external/staging', '/external/run', '/external/role.json',
                    '/external/plan.json', '/external/output'))
        self.assertEqual(len(commands), 4)
        for command in commands:
            self.assertIn(args.m07_tool_path, command)
            self.assertEqual(command[command.index('--continuation-sha256') + 1], args.m07_tool_sha256)
        final = executor._reconcile_command(args, '/external/reconcile-run',
                                            '/external/reconcile-output', '/external/index.json')
        self.assertIn(args.m07_tool_path, final)
        self.assertEqual(final[final.index('--continuation-sha256') + 1], args.m07_tool_sha256)


if __name__ == '__main__':
    unittest.main()
