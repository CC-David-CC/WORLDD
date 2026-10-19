"""API controls use real generated messages and explicit lifecycle doubles.

These controls launch no project process. Runtime checks still belong on test hosts.
"""
import ast
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, call, patch
import os
import socket
import sys
import types

import launch_pb2 as launch
import issuer_pb2 as issuer
from harness.resource_broker import supervised_test_launch
from supervised_copy_broker_recovery import (finish_checked_retirement, require_broker_identity_observation,
    require_candidate_supervisor_denial, wait_for_active_generation)

NATIVE = Path(__file__).resolve().parents[1]/'native'
sys.path.insert(0, str(NATIVE))
from supervised_delegation_parent import cleanup_owned_parents, execution_key, verify_pinned_directory
from copy_fault_supervised_world import enforce_fixture_generation_policy, fixture_version
from test_workspace_copy_faults import initialize_lost_ack_supervisors


class AdapterContractTests(unittest.TestCase):
    def readiness_registration(self, *, revision=3, issuer_id=b'issuer', public_key=b'key'):
        return issuer.AuthorityParticipantRecord(
            participant=issuer.AuthorityParticipant(id=b'participant', public_key=public_key),
            registered_revision=revision, issuer_id=issuer_id)

    def readiness_row(self, *, active=1, generation=7, incarnation=b'incarnation', registration=None):
        if registration is None:
            registration = self.readiness_registration()
        return (b'participant', generation, incarnation,
                registration.SerializeToString(deterministic=True), 1, active, active)

    def readiness_clock(self, timeout=5):
        clock = SimpleNamespace(now=100., sleeps=[])
        def sleep(seconds):
            self.assertGreater(seconds, 0)
            self.assertLessEqual(seconds, .01)
            if clock.now + seconds > 100 + timeout + 1e-9:
                raise RuntimeError('Readiness attempted to sleep beyond its absolute deadline')
            clock.sleeps.append(seconds)
            clock.now += seconds
        for name, replacement in (('monotonic', lambda: clock.now), ('sleep', sleep)):
            patcher = patch('supervised_copy_broker_recovery.time.' + name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        return clock

    def readiness_fixture(self, row_sequence):
        handle = SimpleNamespace(
            binding=SimpleNamespace(participant_id=b'participant', service_incarnation=b'incarnation'),
            previous_generation=6,
            supervision=SimpleNamespace(registration=self.readiness_registration()),
            owner=SimpleNamespace(activation_rows=Mock(side_effect=row_sequence)),
            proc=SimpleNamespace(poll=Mock(return_value=None)), pidfd=42, initial=None)
        patcher = patch('supervised_copy_broker_recovery.select.select', return_value=([], [], []))
        handle.pidfd_poll = patcher.start()
        self.addCleanup(patcher.stop)
        return handle

    def test_active_readiness_waits_through_absent_and_pending_rows(self):
        clock = self.readiness_clock()
        pending = self.readiness_row(active=0)
        history = self.readiness_row(active=0, generation=6, incarnation=b'old-incarnation',
                                     registration=self.readiness_registration(revision=2))
        active = self.readiness_row()
        handle = self.readiness_fixture([[], [pending], [history, active]])
        row = wait_for_active_generation(self, handle)
        self.assertIs(row, active)
        self.assertEqual(row[3], handle.supervision.registration.SerializeToString(deterministic=True))
        self.assertEqual(clock.sleeps, [.01, .01])
        self.assertEqual(handle.owner.activation_rows.call_args_list,
                         [call([b'participant'])] * 3)
        self.assertEqual(handle.proc.poll.call_count, 3)
        self.assertEqual(handle.pidfd_poll.call_args_list,
                         [call([handle.pidfd], [], [], 0)] * 3)

    def test_active_readiness_rejects_wrong_generation_incarnation_or_registration(self):
        good = self.readiness_row()
        cases = {
            'participant': (b'other-participant',) + good[1:],
            'generation': self.readiness_row(generation=6),
            'incarnation': self.readiness_row(incarnation=b'other-incarnation'),
            'registration revision': self.readiness_row(registration=self.readiness_registration(revision=4)),
            'registration issuer': self.readiness_row(registration=self.readiness_registration(issuer_id=b'other')),
            'registration key': self.readiness_row(registration=self.readiness_registration(public_key=b'other')),
        }
        for name, row in cases.items():
            with self.subTest(field=name):
                handle = self.readiness_fixture([[row]])
                with patch('supervised_copy_broker_recovery.time.sleep',
                           side_effect=RuntimeError('Known-bad active generation attempted to wait')):
                    with self.assertRaisesRegex(AssertionError, 'Unexpected active generation'):
                        wait_for_active_generation(self, handle)
                self.assertEqual(handle.owner.activation_rows.call_count, 1)

    def test_active_readiness_rejects_multiple_active_rows_before_waiting(self):
        good = self.readiness_row()
        for other in (good, self.readiness_row(generation=6)):
            with self.subTest(other=other):
                handle = self.readiness_fixture([[other, good]])
                with patch('supervised_copy_broker_recovery.time.sleep',
                           side_effect=RuntimeError('Multiple active generations attempted to wait')):
                    with self.assertRaisesRegex(AssertionError, 'Unexpected active generation'):
                        wait_for_active_generation(self, handle)
                self.assertEqual(handle.owner.activation_rows.call_count, 1)

    def test_active_readiness_fails_promptly_when_supervisor_or_pidfd_dies_pending(self):
        pending = [self.readiness_row(active=0)]
        for failure in ('supervisor', 'pidfd'):
            with self.subTest(failure=failure):
                clock = self.readiness_clock()
                handle = self.readiness_fixture([pending, pending])
                handle.proc.poll.side_effect = [None, 9 if failure == 'supervisor' else None]
                handle.pidfd_poll.side_effect = [([], [], []),
                    ([handle.pidfd] if failure == 'pidfd' else [], [], [])]
                def sleep_once(seconds):
                    if clock.sleeps:
                        raise RuntimeError('Dead supervisor or broker attempted another wait')
                    clock.sleeps.append(seconds)
                with patch('supervised_copy_broker_recovery.time.sleep', sleep_once):
                    with self.assertRaisesRegex(AssertionError, 'Supervisor failed before active generation readiness'):
                        wait_for_active_generation(self, handle)
                self.assertEqual(clock.sleeps, [.01])
                self.assertEqual(handle.proc.poll.call_count, 2)
                self.assertEqual(handle.pidfd_poll.call_count, 2)

    def test_active_readiness_preserves_typed_startup_error_with_dead_process(self):
        handle = self.readiness_fixture([[self.readiness_row(active=0)]])
        handle.proc.poll.return_value = 9
        handle.pidfd_poll.return_value = ([handle.pidfd], [], [])
        handle.initial = launch.AuthorityLaunchReply(protocol_version=1)
        handle.initial.error.message = 'Issuer did not approve the retained participant connection'
        with patch('supervised_copy_broker_recovery.time.sleep',
                   side_effect=RuntimeError('Startup error attempted to wait')):
            with self.assertRaisesRegex(AssertionError,
                    'Supervisor startup failed.*Issuer did not approve the retained participant connection'):
                wait_for_active_generation(self, handle)
        self.assertEqual(handle.initial.error.message, 'Issuer did not approve the retained participant connection')
        self.assertEqual(handle.proc.poll.call_count, 1)
        self.assertEqual(handle.pidfd_poll.call_count, 1)

    def test_active_readiness_timeout_uses_absolute_deadline_without_wall_clock_wait(self):
        for rows in ([], [self.readiness_row(active=0)]):
            for timeout in (5, .025):
                with self.subTest(rows=rows, timeout=timeout):
                    clock = self.readiness_clock(timeout)
                    handle = self.readiness_fixture(None)
                    handle.owner.activation_rows.side_effect = None
                    handle.owner.activation_rows.return_value = rows
                    arguments = {} if timeout == 5 else {'timeout_seconds': timeout}
                    with self.assertRaisesRegex(AssertionError,
                            'Timed out waiting for exact active generation: rows=.*process=None pidfd_ready=False'):
                        wait_for_active_generation(self, handle, **arguments)
                    self.assertAlmostEqual(clock.now, 100 + timeout)
                    self.assertAlmostEqual(sum(clock.sleeps), timeout)
                    self.assertEqual(handle.owner.activation_rows.call_count, len(clock.sleeps) + 1)
                    self.assertEqual(handle.proc.poll.call_count, handle.owner.activation_rows.call_count)
                    self.assertEqual(handle.pidfd_poll.call_count, handle.owner.activation_rows.call_count)
                    if timeout == .025:
                        self.assertEqual(len(clock.sleeps), 3)
                        self.assertAlmostEqual(clock.sleeps[-1], .005)

    def test_privileged_broker_identity_requires_exact_live_pidfd_identity(self):
        identity = dict(pid=27182, start_ticks=9384, executable='/usr/libexec/workspaced')
        observed = dict(pid=27182, ppid=27100, starttime=9384, exe='/usr/libexec/workspaced')
        require_broker_identity_observation(identity, observed, 27100, False)
        cases = (
            ('pid', dict(observed, pid=27183), 27100, False),
            ('executable', dict(observed, exe='/tmp/other'), 27100, False),
            ('starttime', dict(observed, starttime=9385), 27100, False),
            ('parent pid', dict(observed, ppid=27101), 27100, False),
            ('readable pidfd', observed, 27100, True),
        )
        for name, candidate, parent, readable in cases:
            with self.subTest(field=name):
                with self.assertRaises(AssertionError):
                    require_broker_identity_observation(identity, candidate, parent, readable)

    def lifecycle(self, *, stage=launch.OWNED_SCOPES_STOPPED, control_closed=False):
        events = []
        record = launch.AuthorityLaunchRecord(stage=stage)
        record.binding.participant_id = b'participant'
        record.binding.registered_revision = 3
        record.process.SetInParent()
        record.native.SetInParent()
        record.authorization.SetInParent()
        record.process_stopped.identity.CopyFrom(record.process)
        record.native_stopped.identity.CopyFrom(record.native)
        record.fence.generation = 7
        record.fence.fence_id = b'fence'
        record.fence.service_incarnation = b'incarnation'
        record.fence.launch_id = b'launch'
        record.fence.supervision_revision = 4
        record.fence.authorization_id = b'authorization'
        initial = launch.AuthorityLaunchReply(protocol_version=1)
        initial.view.record.CopyFrom(record)
        control, peer = socket.socketpair()
        self.addCleanup(control.close)
        self.addCleanup(peer.close)
        if control_closed:
            control.close()
        handle = SimpleNamespace(binding=record.binding, initial=initial,
            proc=SimpleNamespace(returncode=2), control=control, retirement=None)
        def cleanup():
            events.append('cleanup')
            return record
        def retire(value, digest):
            events.append('retire')
            self.assertIs(value, record)
            handle.retirement = issuer.AuthorityRetirementState()
            request = handle.retirement.request
            request.cleanup_sha256 = digest
            request.fence_id = record.fence.fence_id
            request.owner.CopyFrom(issuer.AuthorityFenceBegin(
                participant_id=record.binding.participant_id,
                registered_revision=record.binding.registered_revision,
                generation=record.fence.generation,
                service_incarnation=record.fence.service_incarnation,
                launch_id=record.fence.launch_id,
                supervision_revision=record.fence.supervision_revision,
                authorization_id=record.fence.authorization_id))
        handle.failed_cleanup = cleanup
        handle.retire = retire
        resources = SimpleNamespace(closed=False)
        def close():
            events.append('close')
            resources.closed = True
        broker = SimpleNamespace(owner=SimpleNamespace(poll=lambda: 0), close=close)
        return events, record, handle, resources, broker

    def test_checked_cleanup_precedes_exact_single_retirement(self):
        events, record, handle, resources, broker = self.lifecycle()
        checked, retirement = finish_checked_retirement(self, broker, resources, handle)
        self.assertEqual(events, ['close', 'cleanup', 'retire'])
        self.assertIs(checked, record)
        self.assertEqual(retirement.request.cleanup_sha256,
                         hashlib.sha256(record.SerializeToString(deterministic=True)).digest())
        with self.assertRaisesRegex(AssertionError, 'already retired'):
            finish_checked_retirement(self, broker, resources, handle)
        self.assertEqual(events, ['close', 'cleanup', 'retire'])

    def test_discarded_control_refuses_late_cleanup_and_retirement(self):
        events, _, handle, resources, broker = self.lifecycle(control_closed=True)
        with self.assertRaisesRegex(AssertionError, 'discarded'):
            finish_checked_retirement(self, broker, resources, handle)
        self.assertEqual(events, ['close'])
        self.assertIsNone(handle.retirement)

    def test_unstopped_scope_refuses_retirement(self):
        events, _, handle, resources, broker = self.lifecycle(stage=launch.START_ATTEMPTED)
        with self.assertRaises(AssertionError):
            finish_checked_retirement(self, broker, resources, handle)
        self.assertEqual(events, ['close', 'cleanup'])
        self.assertIsNone(handle.retirement)

    def test_wrong_stopped_identity_and_missing_fence_refuse_retirement(self):
        for mutation in ('scope', 'fence'):
            events, record, handle, resources, broker = self.lifecycle()
            if mutation == 'scope':
                record.process_stopped.identity.scope_name = 'substituted-scope'
            else:
                record.ClearField('fence')
            with self.assertRaises(AssertionError):
                finish_checked_retirement(self, broker, resources, handle)
            self.assertEqual(events, ['close', 'cleanup'])
            self.assertIsNone(handle.retirement)

    def test_wrong_generation_denial_and_view_are_not_activation_denials(self):
        reply = launch.AuthorityLaunchReply(protocol_version=1)
        reply.error.message = 'Issuer did not approve the retained participant connection'
        reply.error.cleanup_record.stage = launch.OWNED_SCOPES_STOPPED
        require_candidate_supervisor_denial(self, reply)
        for message in ('Service already has an active generation',
                        'Previous participant generation is still active', 'Malformed launch'):
            reply.error.message = message
            with self.assertRaises(AssertionError):
                require_candidate_supervisor_denial(self, reply)
        reply.error.message = 'Issuer did not approve the retained participant connection'
        reply.error.ClearField('cleanup_record')
        with self.assertRaises(AssertionError):
            require_candidate_supervisor_denial(self, reply)
        reply.error.cleanup_record.stage = launch.START_ATTEMPTED
        with self.assertRaises(AssertionError):
            require_candidate_supervisor_denial(self, reply)
        reply.error.cleanup_record.stage = launch.OWNED_SCOPES_STOPPED
        reply.view.record.SetInParent()
        with self.assertRaises(AssertionError):
            require_candidate_supervisor_denial(self, reply)
        self.assertEqual(set(launch.AuthorityLaunchError.DESCRIPTOR.fields_by_name),
                         {'message', 'cleanup_record'})



    def test_supervised_fixture_bootstrap_serializes_only_selected_copy_hooks(self):
        environment = {
            'SPATIAL_TEST_COPY_PREPARED_NOTIFY': '/fixture-root/ready',
            'SPATIAL_TEST_COPY_RECOVERY_DESTINATION_SYNC_FAIL': '1',
            'SPATIAL_TEST_UNRELATED_SENTINEL': 'must-not-cross',
        }
        executable, arguments = supervised_test_launch(
            '/build/workspaced', ['--state-dir', '/fixture/state'], environment)
        self.assertEqual(executable, '/usr/bin/python3')
        self.assertEqual(arguments[0], '-c')
        code = arguments[1]
        calls = [node for node in ast.walk(ast.parse(code)) if isinstance(node, ast.Call)]
        update = next(node for node in calls if isinstance(node.func, ast.Attribute) and
                      isinstance(node.func.value, ast.Attribute) and
                      isinstance(node.func.value.value, ast.Name) and
                      node.func.value.value.id == 'os' and node.func.value.attr == 'environ' and
                      node.func.attr == 'update')
        execv = next(node for node in calls if isinstance(node.func, ast.Attribute) and
                     isinstance(node.func.value, ast.Name) and node.func.value.id == 'os' and
                     node.func.attr == 'execv')
        self.assertEqual(ast.literal_eval(update.args[0]), {
            'SPATIAL_TEST_COPY_PREPARED_NOTIFY': '/fixture-root/ready',
            'SPATIAL_TEST_COPY_RECOVERY_DESTINATION_SYNC_FAIL': '1',
        })
        self.assertEqual(ast.literal_eval(execv.args[0]), '/build/workspaced')
        forwarded = execv.args[1]
        self.assertEqual(ast.literal_eval(forwarded.elts[0]), '/build/workspaced')
        self.assertIsInstance(forwarded.elts[1], ast.Starred)
        self.assertEqual(ast.unparse(forwarded.elts[1].value), 'sys.argv[1:]')
        self.assertEqual(arguments[2:], ['--state-dir', '/fixture/state'])
        self.assertNotIn('must-not-cross', code)

        resolved_authority = '/fixture/private/authority.textproto'
        logical_arguments = ['--state-dir', '/fixture/state', '--authority-config', resolved_authority]
        appended_arguments = ['--resource-config-fd', '143', '--cleanup-bootstrap-fd', '144']
        isolated_os = types.ModuleType('os')
        isolated_os.environ = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}
        isolated_os.execv = Mock()
        isolated_sys = types.ModuleType('sys')
        isolated_sys.argv = ['-c', *logical_arguments, *appended_arguments]
        with patch.dict(sys.modules, {'os': isolated_os, 'sys': isolated_sys}):
            exec(compile(code, '<supervised-fixture-bootstrap>', 'exec'), {})
        isolated_os.execv.assert_called_once_with('/build/workspaced', [
            '/build/workspaced', *logical_arguments, *appended_arguments])
        self.assertEqual(isolated_os.environ['SPATIAL_TEST_COPY_PREPARED_NOTIFY'], '/fixture-root/ready')
        self.assertEqual(isolated_os.environ['SPATIAL_TEST_COPY_RECOVERY_DESTINATION_SYNC_FAIL'], '1')
        self.assertNotIn('SPATIAL_TEST_UNRELATED_SENTINEL', isolated_os.environ)

    def test_lost_ack_initializer_sets_v2_before_context_and_preserves_default(self):
        selected=SimpleNamespace()
        observed=[]
        def context_factory(fixture):
            version=fixture_version(fixture)
            observed.append(version)
            return SimpleNamespace(fixture_version=version)
        owner=initialize_lost_ack_supervisors(selected,context_factory)
        self.assertEqual(observed,[2])
        self.assertEqual(owner.fixture_version,2)
        self.assertEqual(fixture_version(selected),2)
        unrelated=SimpleNamespace()
        self.assertEqual(fixture_version(unrelated),1)
        self.assertFalse(hasattr(unrelated,'supervised_copy_recovery_fixture_version'))

    def test_fixture_defaults_to_original_v1_active_generation_guard(self):
        self.assertEqual(fixture_version(self), 1)
        active_rows = [self.readiness_row(generation=1)]
        with self.assertRaisesRegex(AssertionError, 'Previous participant generation is still active'):
            enforce_fixture_generation_policy(self, fixture_version(self), active_rows)

    def test_fixture_v2_is_explicit_and_allows_live_candidate_to_reach_issuer(self):
        self.supervised_copy_recovery_fixture_version = 2
        self.assertEqual(fixture_version(self), 2)
        active_rows = [self.readiness_row(generation=1)]
        enforce_fixture_generation_policy(self, fixture_version(self), active_rows)
        self.supervised_copy_recovery_fixture_version = 3
        with self.assertRaisesRegex(ValueError, 'Unsupported supervised copy recovery fixture version'):
            fixture_version(self)

    def test_delegation_key_separates_execution_identity_and_participant(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root/'execution-one'; second = root/'execution-two'
            first.mkdir(); second.mkdir()
            alias = root/'execution-alias'; alias.symlink_to(first, target_is_directory=True)
            key, canonical = execution_key(first, b'participant-a')
            same_key, same_path = execution_key(alias, b'participant-a')
            other_participant, _ = execution_key(first, b'participant-b')
            other_execution, _ = execution_key(second, b'participant-a')
            self.assertEqual((key, canonical), (same_key, same_path))
            self.assertNotEqual(key, other_participant)
            self.assertNotEqual(key, other_execution)

    def test_pinned_delegation_parent_rejects_named_path_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); named = root/'delegation'; named.mkdir()
            descriptor = os.open(named, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:
                verify_pinned_directory(named, descriptor, os.getuid())
                moved = root/'original'; named.rename(moved); named.mkdir()
                with self.assertRaisesRegex(AssertionError, 'no longer identifies'):
                    verify_pinned_directory(named, descriptor, os.getuid())
            finally:
                os.close(descriptor)

    def test_delegation_parent_owner_cleanup_waits_for_all_handles(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)/'delegation'; parent.mkdir()
            (parent/'cgroup.procs').write_text('')
            descriptor = os.open(parent, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            record = dict(path=parent, fd=descriptor, uid=os.getuid())
            handles = [SimpleNamespace(closed=True), SimpleNamespace(closed=False)]
            try:
                with self.assertRaisesRegex(AssertionError, 'before every handle retires'):
                    cleanup_owned_parents(handles, {('execution', b'participant'): record})
                self.assertTrue(parent.exists())
                handles[1].closed = True
                def remove_fixture_parent(path):
                    (path/'cgroup.procs').unlink()
                    path.rmdir()
                parents = {('execution', b'participant'): record}
                cleanup_owned_parents(handles, parents, remove_fixture_parent)
                self.assertFalse(parent.exists())
                self.assertEqual(parents, {})
                cleanup_owned_parents(handles, parents, remove_fixture_parent)
            finally:
                try: os.close(descriptor)
                except OSError: pass

    def runner(self):
        path = Path(__file__).resolve().parents[2]/'tools/run_file_action_supervised_copy_recovery.py'
        spec = importlib.util.spec_from_file_location('supervised_copy_runner', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_profile_and_exact_selector_reach_nested_native_argv(self):
        runner = self.runner()
        for profile in ('Debug', 'ASan-UBSan'):
            command = runner.runtime_command(Path('/build'), Path('/source'), profile,
                'test_selected', Path('/evidence'))
            wrapper = command.index('/source/tools/with_copy_worker_helpers.py')
            self.assertEqual(command[wrapper+1:wrapper+7],
                ['--build', '/build', '--profile', profile, '--evidence', '/evidence/helper-lifecycle'])
            self.assertIn('--native', command)
            self.assertIn('--copy-engine-v2', command)
            adapter = command.index('/source/tools/with_adapter_resources.py')
            expected = ['--copy-profile=ASan-UBSan', '--'] if profile == 'ASan-UBSan' else ['--']
            self.assertEqual(command[adapter+1:adapter+1+len(expected)], expected)
            self.assertEqual(command[-2:], ['-v', 'SupervisedFileActionCopyRecoveryTests.test_selected'])

    def test_invalid_profile_and_selector_fail_before_runtime_argv(self):
        runner = self.runner()
        source = Path(__file__).resolve().parents[2]
        runner.validate_selector(source,
            'test_sigkill_checked_retirement_and_restart_reconcile_same_copy_journal')
        for selector in ('test_missing', 'test_valid --another-selector', 'test_valid\n'):
            with self.assertRaises(ValueError):
                runner.validate_selector(source, selector)
        with self.assertRaises(ValueError):
            runner.runtime_command(Path('/build'), source, 'Release', 'test_selected', Path('/evidence'))
        with self.assertRaises(ValueError):
            runner.runtime_command(Path('/build'), source, 'Debug', 'test_selected --bad', Path('/evidence'))

    def test_capacity_uses_measured_backups_and_separate_reserve(self):
        runner = self.runner()
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            for name in ('spatial-copy-tree-worker', 'spatial-copy-finalize-worker'):
                (build/name).write_bytes(b'helper')
            plan = runner.capacity_plan(build, 3*2**30)
            self.assertEqual(plan['helper_backup_bytes'], 12)
            with self.assertRaisesRegex(RuntimeError, 'Insufficient capacity'):
                runner.capacity_plan(build, plan['projected_bytes']+plan['reserve_bytes']-1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
