#!/usr/bin/env python3
import contextlib
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
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
        nested = '|'.join(gate.COPY_HELPER_NESTED_CTESTS)
        dedicated_pattern = gate.ctest_exclusion_expression(dedicated).removeprefix('^').removesuffix('$')
        protected_core = ['ctest', '--test-dir', str(build), '--parallel', '1', '-V', '-E',
                          f'^({dedicated_pattern}|{nested})$',
                          '--output-junit', str(target / 'core-protected.xml')]
        self.assertEqual(wrapped[9:], protected_core)
        recovery = next(command for name, command, _ in enabled
                        if name == 'core-supervised-copy-recovery')
        self.assertEqual(recovery[0:7], ['ctest', '--test-dir', str(build), '--parallel', '1', '-V', '-R'])
        self.assertEqual(recovery[7], '^(' + nested + ')$')
        self.assertEqual(recovery[-1], str(target / 'core-self-managed.xml'))
        self.assertEqual(next(command for name, command, _ in default if name == 'harness'),
                         next(command for name, command, _ in enabled if name == 'harness'))

    def test_supervised_recovery_partition_executes_without_nested_lock_timeout(self):
        import m06_deployed_gate as gate
        source = Path(__file__).resolve().parents[1]
        build = Path('/tmp/profile-build')
        target = Path('/tmp/attempt/Debug')
        stages = gate.profile_stage_commands(source, build, Path('/tmp/off'), target,
                                             gate.DEDICATED_CROSSHOST, copy_worker_helpers=True,
                                             profile='Debug')
        outer_stage = next(command for name, command, _ in stages if name == 'core')
        recovery_stage = next(command for name, command, _ in stages
                              if name == 'core-supervised-copy-recovery')
        nested = tuple(gate.COPY_HELPER_NESTED_CTESTS)
        names = ('ordinary-core-a', 'ordinary-core-b', *nested)
        cmake = (source / 'CMakeLists.txt').read_text()
        runner = (source / 'tools/run_file_action_supervised_copy_recovery.py').read_text()
        self.assertIn('add_test(NAME workspace-copy-supervised-sigkill-recovery', cmake)
        for case_name in ('prepared-worker-proof', 'published-prepared', 'published-sync-failure'):
            self.assertIn('add_supervised_copy_recovery_case(' + case_name, cmake)
        self.assertIn('tools/run_file_action_supervised_copy_recovery.py', cmake)
        self.assertIn('tools/with_copy_worker_helpers.py', runner)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock = root / 'helper.lock'
            marker = root / 'invocations.txt'
            child = root / 'nested_helper.py'
            fake_ctest = root / 'fake_ctest.py'
            fake_outer = root / 'fake_outer.py'
            child.write_text(
                'import fcntl,os,sys\n'
                'fd=os.open(sys.argv[1], os.O_RDWR|os.O_CREAT, 0o600)\n'
                'fcntl.flock(fd, fcntl.LOCK_EX)\n'
                'with open(sys.argv[2], "a") as stream: stream.write(sys.argv[3]+"\\n")\n'
                'os.close(fd)\n')
            fake_ctest.write_text(
                'import json,os,re,subprocess,sys\n'
                'names=json.loads(os.environ["FAKE_CTEST_NAMES"])\n'
                'nested=set(json.loads(os.environ["FAKE_CTEST_NESTED"]))\n'
                'argv=sys.argv[1:]\n'
                'selected=names\n'
                'if "-R" in argv: selected=[n for n in names if re.search(argv[argv.index("-R")+1], n)]\n'
                'if "-E" in argv: selected=[n for n in selected if not re.search(argv[argv.index("-E")+1], n)]\n'
                'for name in selected:\n'
                ' if name in nested:\n'
                '  try: subprocess.run([sys.executable,os.environ["FAKE_NESTED_HELPER"],os.environ["FAKE_LOCK"],os.environ["FAKE_MARKER"],name],check=True,timeout=.75)\n'
                '  except subprocess.TimeoutExpired:\n'
                '   open(os.environ["FAKE_MARKER"],"a").write("timeout:"+name+"\\n"); sys.exit(73)\n'
                ' else: open(os.environ["FAKE_MARKER"],"a").write(name+"\\n")\n')
            fake_outer.write_text(
                'import fcntl,os,subprocess,sys\n'
                'fd=os.open(sys.argv[1],os.O_RDWR|os.O_CREAT,0o600)\n'
                'fcntl.flock(fd,fcntl.LOCK_EX)\n'
                'result=subprocess.run(sys.argv[3:])\n'
                'os.close(fd)\n'
                'sys.exit(result.returncode)\n')
            environment = os.environ.copy()
            environment.update({
                'FAKE_CTEST_NAMES': json.dumps(names),
                'FAKE_CTEST_NESTED': json.dumps(nested),
                'FAKE_NESTED_HELPER': str(child),
                'FAKE_LOCK': str(lock),
                'FAKE_MARKER': str(marker),
            })

            def ctest_args(command):
                position = command.index('ctest')
                return command[position + 1:]

            protected_args = ctest_args(outer_stage)
            normal_outer = [sys.executable, str(fake_outer), str(lock), '--',
                            sys.executable, str(fake_ctest), *protected_args]
            result = subprocess.run(normal_outer, env=environment, capture_output=True, text=True,
                                    timeout=5, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            selected_args = recovery_stage[1:]
            recovery = subprocess.run([sys.executable, str(fake_ctest), *selected_args],
                                      env=environment, capture_output=True, text=True,
                                      timeout=5, check=False)
            self.assertEqual(recovery.returncode, 0, recovery.stderr)
            self.assertEqual(set(marker.read_text().splitlines()), set(names))
            self.assertEqual(len(marker.read_text().splitlines()), len(names))

            marker.write_text('')
            legacy_args = list(protected_args)
            legacy_args[legacy_args.index('-E') + 1] = gate.ctest_exclusion_expression(gate.DEDICATED_CROSSHOST)
            legacy_outer = [sys.executable, str(fake_outer), str(lock), '--',
                            sys.executable, str(fake_ctest), *legacy_args]
            legacy = subprocess.run(legacy_outer, env=environment, capture_output=True, text=True,
                                    timeout=5, check=False)
            self.assertEqual(legacy.returncode, 73)
            self.assertIn('timeout:' + nested[0], marker.read_text())

    def test_split_junit_inventory_requires_all_231_cases_once_and_merges_core_xml(self):
        import xml.etree.ElementTree as ET
        import m06_deployed_gate as gate
        names = [f'local-core-{index:03d}' for index in range(231)]
        managed = set(names[-4:])
        protected_cases = [ET.Element('testcase', name=name) for name in names[:-4]]
        managed_cases = [ET.Element('testcase', name=name) for name in names[-4:]]
        summary = gate.validate_core_junit_partitions(protected_cases, managed_cases, names, managed)
        self.assertEqual(summary, {
            'protected_junit_cases': 227, 'self_managed_junit_cases': 4, 'junit_cases': 231,
            'all_registered_present': True, 'failed_or_skipped': False})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            protected_xml = root / 'protected.xml'
            managed_xml = root / 'managed.xml'
            combined_xml = root / 'core.xml'
            for path, cases in ((protected_xml, protected_cases), (managed_xml, managed_cases)):
                suite = ET.Element('testsuite')
                suite.extend(cases)
                ET.ElementTree(suite).write(path, encoding='utf-8', xml_declaration=True)
            totals = gate.merge_core_junit_files((protected_xml, managed_xml), combined_xml)
            combined_names = [case.attrib['name'] for case in ET.parse(combined_xml).getroot().iter('testcase')]
            receipt = gate.attach_split_core_junit_receipt({'exit_code': 0}, summary, totals)
            self.assertEqual(totals['tests'], 231)
            self.assertEqual(len(combined_names), 231)
            self.assertEqual(len(set(combined_names)), 231)
            self.assertEqual(set(combined_names), set(names))
            self.assertEqual(receipt['junit_cases'], len(names))
            self.assertEqual(receipt['protected_junit_cases'], 227)
            self.assertEqual(receipt['self_managed_junit_cases'], 4)
            self.assertEqual(receipt['junit_case_sources']['combined']['path'], 'core.xml')
            self.assertTrue(receipt['all_registered_present'])
            self.assertFalse(receipt['failed_or_skipped'])

    def test_split_junit_rejects_missing_duplicate_unexpected_failure_and_skip(self):
        import xml.etree.ElementTree as ET
        import m06_deployed_gate as gate
        names = [f'local-core-{index:03d}' for index in range(231)]
        managed_names = names[-4:]
        protected_cases = [ET.Element('testcase', name=name) for name in names[:-4]]
        managed_cases = [ET.Element('testcase', name=name) for name in managed_names]
        missing_protected = gate.validate_core_junit_partitions(
            protected_cases[1:], managed_cases, names, managed_names)
        missing_managed = gate.validate_core_junit_partitions(
            protected_cases, managed_cases[1:], names, managed_names)
        self.assertFalse(missing_protected['all_registered_present'])
        self.assertFalse(missing_managed['all_registered_present'])
        duplicate = gate.validate_core_junit_partitions(
            protected_cases + [protected_cases[0]], managed_cases, names, managed_names)
        self.assertFalse(duplicate['all_registered_present'])
        unexpected_cases = list(protected_cases)
        unexpected_cases[0] = ET.Element('testcase', name='unexpected-core-test')
        unexpected = gate.validate_core_junit_partitions(
            unexpected_cases, managed_cases, names, managed_names)
        self.assertFalse(unexpected['all_registered_present'])
        for tag in ('failure', 'skipped'):
            cases = list(protected_cases)
            cases[0] = ET.Element('testcase', name=names[0])
            cases[0].append(ET.Element(tag))
            rejected = gate.validate_core_junit_partitions(cases, managed_cases, names, managed_names)
            self.assertTrue(rejected['all_registered_present'])
            self.assertTrue(rejected['failed_or_skipped'])

    def test_independent_helper_lifecycle_invocations_remain_serialized(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / str(os.getuid())
            runtime.mkdir(mode=0o700)
            code = (
                'import os,pathlib,sys,time; import with_copy_worker_helpers as h; '
                'h.LOCK_ROOT=pathlib.Path(sys.argv[1]); fd=h._acquire_lock(); '
                'print(time.monotonic_ns(),flush=True); time.sleep(.35); os.close(fd)'
            )
            env = os.environ.copy()
            env['PYTHONPATH'] = str(TOOLS)
            first = subprocess.Popen([sys.executable, '-c', code, temporary], env=env,
                                     stdout=subprocess.PIPE, text=True)
            second = None
            try:
                ready, _, _ = select.select([first.stdout], [], [], 3)
                self.assertTrue(ready, 'first lifecycle invocation did not acquire the lock')
                first_started = int(first.stdout.readline().strip())
                second = subprocess.Popen([sys.executable, '-c', code, temporary], env=env,
                                          stdout=subprocess.PIPE, text=True)
                ready, _, _ = select.select([second.stdout], [], [], 3)
                self.assertTrue(ready, 'second lifecycle invocation remained blocked')
                second_started = int(second.stdout.readline().strip())
                self.assertEqual(first.wait(timeout=3), 0)
                self.assertEqual(second.wait(timeout=3), 0)
                self.assertGreaterEqual(second_started - first_started, 250_000_000)
            finally:
                for child in (first, second):
                    if child is not None and child.poll() is None:
                        child.terminate()
                        child.wait(timeout=3)

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
            host_session='session', held_input='held', selected_profiles=None,
        )
        captured = {}
        with mock.patch.object(gate, 'require_deployment_inventory'), \
             mock.patch.object(gate, 'require_storage_lane_configuration', return_value={}), \
             mock.patch.object(gate.m06, 'gate', side_effect=lambda value: captured.update(vars(value)) or 0):
            self.assertEqual(gate.run_local(arguments), 0)
        self.assertIs(captured['copy_worker_helpers'], True)
        self.assertEqual(captured['required_tests'], sorted(gate.m07.M07_REQUIRED | gate.M08_REQUIRED))

    def test_m08_refuses_partial_profile_and_collector_modes_before_gate(self):
        import argparse
        import m08_deployed_gate as gate
        base = dict(deployment=Path('/tmp/deployment'), attempt='attempt',
                    native_controller='native', real_world='world', recovery='recovery',
                    host_session='session', held_input='held')
        invalid = [
            dict(selected_profiles=['ASan-UBSan']),
            dict(selected_profiles=['Debug']),
            dict(collector_continuation=Path('/tmp/continuation.json')),
            dict(collector_continuation_sha256='a'*64),
        ]
        for extra in invalid:
            arguments=argparse.Namespace(**(base | extra))
            with self.subTest(extra=extra), \
                 mock.patch.object(gate,'require_deployment_inventory') as inventory, \
                 mock.patch.object(gate.m06,'gate') as run_gate, \
                 mock.patch.object(gate,'require_storage_lane_configuration') as storage, \
                 self.assertRaisesRegex(RuntimeError,'M08 run-local requires both ordered profiles|M08 collector continuation is unsupported'):
                gate.run_local(arguments)
            inventory.assert_not_called();run_gate.assert_not_called();storage.assert_not_called()

    def test_m08_accepts_explicit_full_ordered_profile_selection(self):
        import argparse
        import m08_deployed_gate as gate
        arguments=argparse.Namespace(
            deployment=Path('/tmp/deployment'), attempt='attempt',
            native_controller='native', real_world='world', recovery='recovery',
            host_session='session', held_input='held', selected_profiles=list(gate.m06.PROFILES))
        captured={}
        with mock.patch.object(gate,'require_deployment_inventory'), \
             mock.patch.object(gate,'require_storage_lane_configuration',return_value={}), \
             mock.patch.object(gate.m06,'gate',side_effect=lambda value: captured.update(vars(value)) or 0):
            self.assertEqual(gate.run_local(arguments),0)
        self.assertEqual(captured['selected_profiles'],list(gate.m06.PROFILES))


if __name__ == '__main__':
    unittest.main(verbosity=2)
