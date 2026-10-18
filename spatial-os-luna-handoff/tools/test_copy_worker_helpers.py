#!/usr/bin/env python3
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import with_copy_worker_helpers as lifecycle


class CandidateSelectionTests(unittest.TestCase):
    def test_profile_is_forwarded_to_existing_instrumentation_preflight(self):
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary)
            expected = {}
            for name in lifecycle.HELPER_NAMES:
                path = build / name
                path.write_bytes(name.encode())
                path.chmod(0o755)
                expected[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            calls = []
            fake = SimpleNamespace(instrumentation_preflight=lambda path, profile: (
                calls.append((Path(path), profile)) or {
                    'profile': profile,
                    'binaries': {name: {'sha256': value} for name, value in expected.items()},
                }
            ))
            with mock.patch.dict(sys.modules, {'copy_fixture_profiles': fake}):
                candidates, preflight = lifecycle._candidate_helpers(build, 'ASan-UBSan')
            self.assertEqual(calls, [(build, 'ASan-UBSan')])
            self.assertEqual(preflight['profile'], 'ASan-UBSan')
            self.assertEqual({key: value['sha256'] for key, value in candidates.items()}, expected)

    def test_missing_profile_artifact_fails_before_any_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary)
            present = build / lifecycle.HELPER_NAMES[0]
            present.write_bytes(b'tree')
            present.chmod(0o755)
            first_hash = hashlib.sha256(present.read_bytes()).hexdigest()
            with mock.patch.dict(sys.modules, {'copy_fixture_profiles': SimpleNamespace(
                    instrumentation_preflight=lambda path, profile: {
                        'profile': profile, 'binaries': {lifecycle.HELPER_NAMES[0]: {'sha256': first_hash}}})}):
                with self.assertRaises(FileNotFoundError):
                    lifecycle._candidate_helpers(build, 'Debug')

    def test_running_helper_and_deleted_executable_paths_block_replacement(self):
        import io
        target = Path('/usr/local/libexec/spatial-copy-tree-worker')
        for suffix in ('', ' (deleted)'):
            output = io.StringIO()
            entries = [SimpleNamespace(name='321'), SimpleNamespace(name='322'), SimpleNamespace(name='323')]
            def readlink(path):
                if path in ('/proc/322/exe', '/proc/323/exe'):
                    return '/usr/bin/sudo'
                if path == '/proc/1/exe':
                    return '/usr/lib/systemd/systemd'
                return str(target) + suffix
            def open_process_file(path, mode='r', *args, **kwargs):
                if path == '/proc/322/stat':
                    return io.BytesIO(b'322 (sudo) S 323 1 1')
                if path == '/proc/323/stat':
                    return io.BytesIO(b'323 (sudo) S 1 1 1')
                return io.BytesIO(b'worker\0')
            with mock.patch('os.scandir', return_value=entries), \
                 mock.patch('os.getppid', return_value=322), \
                 mock.patch('os.readlink', side_effect=readlink), \
                 mock.patch('builtins.open', side_effect=open_process_file), \
                 mock.patch.object(sys, 'argv', ['-c', str(target)]), \
                 mock.patch.object(sys, 'stdout', output):
                with self.assertRaises(SystemExit) as raised:
                    exec(compile(lifecycle.PROCESS_INSPECTOR_SOURCE, '<inspector-test>', 'exec'), {})
            self.assertEqual(raised.exception.code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload['matches'], [{'pid': 321, 'executable': str(target) + suffix}])


class ProcessInspectorTests(unittest.TestCase):
    def test_unreadable_root_process_is_reported_as_incomplete_visibility(self):
        import io
        own_pid = str(os.getpid())
        entries = [SimpleNamespace(name='1'), SimpleNamespace(name='2'), SimpleNamespace(name=own_pid)]
        output = io.StringIO()
        def readlink(path):
            if path == '/proc/2/exe':
                return '/usr/bin/bash'
            raise PermissionError('root proc entry')
        with mock.patch('os.scandir', return_value=entries), \
             mock.patch('os.getppid', return_value=2), \
             mock.patch('os.readlink', side_effect=readlink), \
             mock.patch.object(sys, 'argv', ['-c', '/usr/local/libexec/spatial-copy-tree-worker']), \
             mock.patch.object(sys, 'stdout', output):
            with self.assertRaises(SystemExit) as raised:
                exec(compile(lifecycle.PROCESS_INSPECTOR_SOURCE, '<inspector-test>', 'exec'), {})
        self.assertEqual(raised.exception.code, 2)
        report = json.loads(output.getvalue())
        self.assertEqual(report['visibility_errors'], [{'pid': 1, 'error': 'PermissionError'}])
        self.assertEqual(report['matches'], [])

    def test_privileged_inspector_uses_fixed_code_and_explicit_path_argv(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            paths = (Path('/usr/local/libexec/spatial-copy-tree-worker'),
                     Path('/usr/local/libexec/spatial-copy-finalize-worker'))
            report = {}
            result = SimpleNamespace(returncode=0, stdout=json.dumps({
                'schema': 1, 'inspector_pid': 77, 'matches': [], 'visibility_errors': []}), stderr='')
            with mock.patch.object(lifecycle.subprocess, 'run', return_value=result) as run:
                lifecycle._inspect_helper_processes(paths, report, evidence)
            argv = run.call_args.args[0]
            self.assertEqual(argv[:6], ['sudo', '-n', '/usr/bin/python3', '-I', '-S', '-c'])
            self.assertEqual(argv[6], lifecycle.PROCESS_INSPECTOR_SOURCE)
            self.assertEqual(argv[7:], [str(path) for path in paths])
            self.assertEqual(run.call_args.kwargs['timeout'], 30)
            self.assertIn('if pid in excluded_pids:', lifecycle.PROCESS_INSPECTOR_SOURCE)
        self.assertIn('normalized_ancestor.endswith("/sudo")', lifecycle.PROCESS_INSPECTOR_SOURCE)
        self.assertEqual(report['process_inspections'][0]['inspector_pid'], 77)


class HelperLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.build = self.root / 'build'
        self.build.mkdir()
        self.helper_root = self.root / 'libexec'
        self.helper_root.mkdir()
        self.evidence_parent = self.root / 'evidence'
        self.evidence_parent.mkdir()
        self.candidates = {}
        for name in lifecycle.HELPER_NAMES:
            path = self.build / name
            path.write_bytes(('candidate-' + name).encode())
            path.chmod(0o755)
            self.candidates[name] = {
                'path': path,
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        self.patches = contextlib.ExitStack()
        self.patches.enter_context(mock.patch.object(lifecycle, 'HELPER_ROOT', self.helper_root))
        self.patches.enter_context(mock.patch.object(lifecycle, '_validate_helper_root'))
        self.patches.enter_context(mock.patch.object(lifecycle.socket, 'gethostname', return_value='sb16'))
        self.patches.enter_context(mock.patch.object(lifecycle, '_inspect_helper_processes', self.fake_inspector))
        self.patches.enter_context(mock.patch.object(lifecycle, '_acquire_lock', self.fake_lock))
        self.patches.enter_context(mock.patch.object(lifecycle, '_candidate_helpers', self.candidate_preflight))
        self.patches.enter_context(mock.patch.object(lifecycle, '_helper_state', self.fake_state))
        self.real_root_command = lifecycle._root_command
        self.root_command_patch = mock.patch.object(lifecycle, '_root_command', self.fake_root_command)
        self.root_command_patch.start()
        self.root_commands = []

    def tearDown(self):
        self.patches.close()
        self.root_command_patch.stop()
        self.temporary.cleanup()

    @staticmethod
    def fake_lock():
        return os.open(os.devnull, os.O_RDONLY)

    def fake_inspector(self, paths, report, evidence):
        report.setdefault('process_inspections', []).append({
            'argv': ['sudo', '-n', '/usr/bin/python3', '-I', '-S', '-c', lifecycle.PROCESS_INSPECTOR_SOURCE,
                     *map(str, paths)],
            'status': 'returned', 'exit': 0, 'stdout': '', 'stderr': '',
            'matches': [], 'visibility_errors': [],
        })
        lifecycle._atomic_json(evidence / 'lifecycle.json', report)

    def candidate_preflight(self, build, profile):
        if profile not in lifecycle.PROFILES:
            raise ValueError('unsupported profile')
        return self.candidates, {
            'profile': profile,
            'binaries': {name: {'sha256': row['sha256']} for name, row in self.candidates.items()},
        }

    @staticmethod
    def fake_state(path):
        path = Path(path)
        if not os.path.lexists(path):
            return None
        info = path.lstat()
        if not info.st_mode & 0o170000 == 0o100000:
            raise RuntimeError('not a regular file')
        return {
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'uid': 0,
            'gid': 0,
            'mode': info.st_mode & 0o7777,
        }

    def fake_root_command(self, argv, report, evidence):
        row = {'argv': ['sudo', '-n', *map(str, argv)], 'exit': 0, 'stdout': '', 'stderr': ''}
        self.root_commands.append(list(map(str, argv)))
        report.setdefault('root_commands', []).append(row)
        lifecycle._atomic_json(evidence / 'lifecycle.json', report)
        if argv[0] == 'install':
            source, target = Path(argv[-2]), Path(argv[-1])
            shutil.copyfile(source, target)
            os.chmod(target, int(argv[argv.index('-m') + 1], 8))
            return ''
        if argv[0] == 'rm':
            Path(argv[-1]).unlink()
            return ''
        raise AssertionError(argv)

    def evidence(self, name):
        return self.evidence_parent / name

    def targets(self):
        return [self.helper_root / name for name in lifecycle.HELPER_NAMES]

    def test_unapproved_host_refuses_before_evidence_or_mutation(self):
        evidence = self.evidence('wrong-host')
        with mock.patch.object(lifecycle.socket, 'gethostname', return_value='middle-child'):
            with self.assertRaisesRegex(RuntimeError, 'only on sb16 or sb16-2'):
                lifecycle.run_with_helpers(self.build, 'Debug', evidence, [sys.executable, '-c', 'pass'])
        self.assertFalse(evidence.exists())
        self.assertEqual(self.root_commands, [])

    def test_preflight_only_reports_candidates_state_and_process_check_without_mutation(self):
        evidence = self.evidence('preflight-only')
        status = lifecycle.run_with_helpers(self.build, 'Debug', evidence, [], preflight_only=True)
        self.assertEqual(status, 0)
        report = json.loads((evidence / 'lifecycle.json').read_text())
        self.assertEqual(report['preflight_status'], 'ready')
        self.assertEqual(report['cleanup_status'], 'not-needed')
        self.assertEqual(set(report['candidate_helpers']), set(lifecycle.HELPER_NAMES))
        self.assertEqual(set(report['previous_helpers']), set(lifecycle.HELPER_NAMES))
        self.assertEqual(len(report['process_inspections']), 1)
        self.assertEqual(report['root_commands'], [])
        self.assertTrue(all(not path.exists() for path in self.targets()))

    def test_absent_helpers_are_installed_and_restored_absent(self):
        status = lifecycle.run_with_helpers(self.build, 'Debug', self.evidence('absent'), [sys.executable, '-c', 'pass'])
        self.assertEqual(status, 0)
        self.assertTrue(all(not path.exists() for path in self.targets()))
        report = json.loads((self.evidence('absent') / 'lifecycle.json').read_text())
        self.assertEqual(report['cleanup_status'], 'confirmed')
        self.assertTrue(all(row['restored'] for row in report['restoration']))
        self.assertEqual(sum(command[0] == 'rm' for command in self.root_commands), 2)

    def test_prior_helpers_restore_bytes_mode_and_child_exit(self):
        originals = {}
        for path in self.targets():
            path.write_bytes(b'previous-' + path.name.encode())
            path.chmod(0o640)
            originals[path] = path.read_bytes()
        status = lifecycle.run_with_helpers(self.build, 'ASan-UBSan', self.evidence('previous'),
                                             [sys.executable, '-c', 'raise SystemExit(7)'])
        self.assertEqual(status, 7)
        for path, content in originals.items():
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual(path.stat().st_mode & 0o7777, 0o640)
        report = json.loads((self.evidence('previous') / 'lifecycle.json').read_text())
        self.assertEqual(report['child_exit'], 7)
        self.assertEqual(report['cleanup_status'], 'confirmed')

    def test_sigterm_cancels_owned_child_and_restores_helpers(self):
        command = [sys.executable, '-c',
                   'import os,signal,time; os.kill(os.getppid(), signal.SIGTERM); time.sleep(20)']
        with self.assertRaisesRegex(RuntimeError, 'failed before completion'):
            lifecycle.run_with_helpers(self.build, 'Debug', self.evidence('sigterm'), command)
        self.assertTrue(all(not path.exists() for path in self.targets()))
        report = json.loads((self.evidence('sigterm') / 'lifecycle.json').read_text())
        self.assertEqual(report['child_process']['status'], 'retired')
        self.assertEqual(report['cleanup_status'], 'confirmed')
        self.assertIn(signal.SIGTERM, report['child_process']['cancellation_signals'])
        self.assertTrue(report['child_signals'])

    def test_unretired_child_group_preserves_helpers_and_fails_cleanup(self):
        with mock.patch.object(lifecycle, '_run_owned_child', return_value=(143, False, 'retirement uncertain')):
            with self.assertRaisesRegex(RuntimeError, 'cleanup is unconfirmed'):
                lifecycle.run_with_helpers(self.build, 'Debug', self.evidence('unretired'),
                                           [sys.executable, '-c', 'pass'])
        self.assertTrue(all(path.exists() for path in self.targets()))
        report = json.loads((self.evidence('unretired') / 'lifecycle.json').read_text())
        self.assertEqual(report['cleanup_status'], 'failed')
        self.assertTrue(all(row['state'] == 'preserved-child-retirement-unconfirmed'
                            for row in report['restoration']))

    def test_internal_child_timeout_restores_helpers_before_gate_timeout(self):
        with mock.patch.object(lifecycle, 'CHILD_TIMEOUT_SECONDS', 0.01):
            with self.assertRaisesRegex(RuntimeError, 'failed before completion'):
                lifecycle.run_with_helpers(self.build, 'Debug', self.evidence('timeout'),
                                           [sys.executable, '-c', 'import time; time.sleep(1)'])
        self.assertTrue(all(not path.exists() for path in self.targets()))
        report = json.loads((self.evidence('timeout') / 'lifecycle.json').read_text())
        self.assertEqual(report['child_exit'], 124)
        self.assertEqual(report['cleanup_status'], 'confirmed')

    def test_midrun_replacement_is_preserved_and_cleanup_fails(self):
        target = self.targets()[0]
        command = [sys.executable, '-c',
                   'from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(b"replacement")', str(target)]
        with self.assertRaisesRegex(RuntimeError, 'cleanup is unconfirmed'):
            lifecycle.run_with_helpers(self.build, 'Debug', self.evidence('replacement'), command)
        self.assertEqual(target.read_bytes(), b'replacement')
        report = json.loads((self.evidence('replacement') / 'lifecycle.json').read_text())
        self.assertEqual(report['child_exit'], 0)
        self.assertEqual(report['cleanup_status'], 'failed')
        self.assertTrue(report['root_commands'])

    def test_root_mutations_have_a_bounded_timeout(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            report = {}
            result = SimpleNamespace(returncode=0, stdout='', stderr='')
            with mock.patch.object(lifecycle, '_root_command', self.real_root_command), \
                 mock.patch.object(lifecycle.subprocess, 'run', return_value=result) as run:
                lifecycle._root_command(['rm', '--', '/fixed/path'], report, evidence)
            self.assertEqual(run.call_args.kwargs['timeout'], lifecycle.ROOT_COMMAND_TIMEOUT_SECONDS)

    def test_preflight_only_candidate_failure_is_nonzero_and_journaled(self):
        evidence = self.evidence('preflight-candidate-failure')
        with mock.patch.object(lifecycle, '_candidate_helpers', side_effect=RuntimeError('missing candidate')):
            with self.assertRaisesRegex(RuntimeError, 'missing candidate'):
                lifecycle.run_with_helpers(self.build, 'Debug', evidence, [], preflight_only=True)
        report = json.loads((evidence / 'lifecycle.json').read_text())
        self.assertIn('missing candidate', report['execution_error'])
        self.assertEqual(report['root_commands'], [])
        self.assertEqual(self.root_commands, [])
        self.assertTrue(all(not path.exists() for path in self.targets()))

    def test_preflight_only_active_helper_refusal_is_nonzero_and_journaled(self):
        evidence = self.evidence('preflight-active-helper')
        with mock.patch.object(lifecycle, '_inspect_helper_processes',
                               side_effect=RuntimeError('Copy helper process is active')):
            with self.assertRaisesRegex(RuntimeError, 'Copy helper process is active'):
                lifecycle.run_with_helpers(self.build, 'Debug', evidence, [], preflight_only=True)
        report = json.loads((evidence / 'lifecycle.json').read_text())
        self.assertIn('Copy helper process is active', report['execution_error'])
        self.assertEqual(report['root_commands'], [])
        self.assertEqual(self.root_commands, [])
        self.assertTrue(all(not path.exists() for path in self.targets()))

    def test_preflight_only_inspector_visibility_error_is_nonzero_and_journaled(self):
        evidence = self.evidence('preflight-inspector-visibility')
        def fail_visibility(paths, report, evidence_path):
            report.setdefault('process_inspections', []).append({
                'status': 'returned', 'exit': 2, 'visibility_errors': [{'pid': 1, 'error': 'PermissionError'}]})
            lifecycle._atomic_json(evidence_path / 'lifecycle.json', report)
            raise RuntimeError('Privileged process inspector reported incomplete /proc visibility')
        with mock.patch.object(lifecycle, '_inspect_helper_processes', side_effect=fail_visibility):
            with self.assertRaisesRegex(RuntimeError, 'incomplete /proc visibility'):
                lifecycle.run_with_helpers(self.build, 'Debug', evidence, [], preflight_only=True)
        report = json.loads((evidence / 'lifecycle.json').read_text())
        self.assertIn('incomplete /proc visibility', report['execution_error'])
        self.assertEqual(report['root_commands'], [])
        self.assertEqual(self.root_commands, [])
        self.assertTrue(all(not path.exists() for path in self.targets()))

    def test_restore_command_failure_fails_closed_with_receipt(self):
        original = lifecycle._root_command
        def fail_remove(argv, report, evidence):
            if argv[0] == 'rm':
                report.setdefault('root_commands', []).append({
                    'argv': ['sudo', '-n', *map(str, argv)], 'exit': 1,
                    'stdout': '', 'stderr': 'injected restore failure'})
                lifecycle._atomic_json(evidence / 'lifecycle.json', report)
                raise RuntimeError('injected restore failure')
            return original(argv, report, evidence)
        with mock.patch.object(lifecycle, '_root_command', fail_remove):
            with self.assertRaisesRegex(RuntimeError, 'cleanup is unconfirmed'):
                lifecycle.run_with_helpers(self.build, 'Debug', self.evidence('rollback'), [sys.executable, '-c', 'pass'])
        report = json.loads((self.evidence('rollback') / 'lifecycle.json').read_text())
        self.assertEqual(report['cleanup_status'], 'failed')
        self.assertTrue(any(row.get('stderr') == 'injected restore failure' for row in report['root_commands']))

    def test_preflight_failure_performs_no_root_mutation(self):
        with mock.patch.object(lifecycle, '_candidate_helpers', side_effect=RuntimeError('missing artifact')):
            with self.assertRaisesRegex(RuntimeError, 'missing artifact'):
                lifecycle.run_with_helpers(self.build, 'Debug', self.evidence('preflight'), [sys.executable, '-c', 'pass'])
        self.assertEqual(self.root_commands, [])
        self.assertTrue(all(not path.exists() for path in self.targets()))


class GateWiringTests(unittest.TestCase):
    def test_default_m06_core_command_is_unchanged_and_m08_prefix_is_optin(self):
        import m06_deployed_gate as gate
        source = Path(__file__).resolve().parents[1]
        build = Path('/tmp/profile-build')
        production = Path('/tmp/production-off')
        target = Path('/tmp/attempt/Debug')
        dedicated = gate.DEDICATED_CROSSHOST
        default = gate.profile_stage_commands(source, build, production, target, dedicated)
        core = next(command for name, command, _ in default if name == 'core')
        self.assertEqual(core, ['ctest', '--test-dir', str(build), '--parallel', '1', '-V', '-E',
                                gate.ctest_exclusion_expression(dedicated), '--output-junit', str(target / 'core.xml')])
        enabled = gate.profile_stage_commands(source, build, production, target, dedicated,
                                              copy_worker_helpers=True, profile='ASan-UBSan')
        wrapped = next(command for name, command, _ in enabled if name == 'core')
        self.assertEqual(wrapped[:6], ['python3', str(source / 'tools/with_copy_worker_helpers.py'),
                                      '--build', str(build), '--profile', 'ASan-UBSan'])
        self.assertEqual(wrapped[6], '--evidence')
        self.assertEqual(wrapped[8], '--')
        self.assertEqual(wrapped[9:], core)
        self.assertEqual(next(command for name, command, _ in default if name == 'harness'),
                         next(command for name, command, _ in enabled if name == 'harness'))

    def test_preflight_only_cli_requires_no_child_command(self):
        with mock.patch.object(lifecycle, 'run_with_helpers', return_value=0) as run:
            status = lifecycle.main(['--build', '/tmp/build', '--profile', 'Debug',
                                     '--evidence', '/tmp/evidence', '--preflight-only'])
        self.assertEqual(status, 0)
        self.assertEqual(run.call_args.args, (Path('/tmp/build'), 'Debug', Path('/tmp/evidence'), []))
        self.assertIs(run.call_args.kwargs['preflight_only'], True)

    def test_m08_local_gate_opts_in_only_the_copy_helper_core_prefix(self):
        import argparse
        import m08_deployed_gate as gate
        arguments = argparse.Namespace(
            deployment=Path('/tmp/deployment'), attempt='attempt',
            native_controller='native', real_world='world', recovery='recovery',
            host_session='session', held_input='held',
        )
        captured = {}
        with mock.patch.object(gate, 'require_deployment_inventory'), \
             mock.patch.object(gate, 'require_storage_lane_configuration', return_value={}), \
             mock.patch.object(gate.m06, 'gate', side_effect=lambda value: captured.update(vars(value)) or 0):
            self.assertEqual(gate.run_local(arguments), 0)
        self.assertIs(captured['copy_worker_helpers'], True)
        self.assertEqual(captured['required_tests'], sorted(gate.m07.M07_REQUIRED | gate.M08_REQUIRED))


if __name__ == '__main__':
    unittest.main(verbosity=2)
