"""API controls use real generated messages and explicit lifecycle doubles.

These controls launch no project process. Runtime checks still belong on test hosts.
"""
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
import os
import sys

import launch_pb2 as launch
import issuer_pb2 as issuer
from supervised_copy_broker_recovery import finish_checked_retirement, require_active_generation_denial

NATIVE = Path(__file__).resolve().parents[1]/'native'
sys.path.insert(0, str(NATIVE))
from supervised_delegation_parent import cleanup_owned_parents, execution_key, verify_pinned_directory
from copy_fault_supervised_world import enforce_fixture_generation_policy, fixture_version


class AdapterContractTests(unittest.TestCase):
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
        handle = SimpleNamespace(binding=record.binding, initial=initial,
            proc=SimpleNamespace(returncode=2), control=SimpleNamespace(closed=control_closed),
            retirement=None)
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
        reply.error.message = 'Service already has an active generation'
        require_active_generation_denial(self, reply)
        for message in ('Previous participant generation is still active', 'Malformed launch'):
            reply.error.message = message
            with self.assertRaises(AssertionError):
                require_active_generation_denial(self, reply)
        reply.view.record.SetInParent()
        with self.assertRaises(AssertionError):
            require_active_generation_denial(self, reply)
        self.assertEqual(set(launch.AuthorityLaunchError.DESCRIPTOR.fields_by_name),
                         {'message', 'cleanup_record'})



    def test_fixture_defaults_to_original_v1_active_generation_guard(self):
        self.assertEqual(fixture_version(self), 1)
        active_rows = [(b'participant', 1, b'incarnation', b'registration', 1, 1, 1)]
        with self.assertRaisesRegex(AssertionError, 'Previous participant generation is still active'):
            enforce_fixture_generation_policy(self, fixture_version(self), active_rows)

    def test_fixture_v2_is_explicit_and_allows_live_candidate_to_reach_issuer(self):
        self.supervised_copy_recovery_fixture_version = 2
        self.assertEqual(fixture_version(self), 2)
        active_rows = [(b'participant', 1, b'incarnation', b'registration', 1, 1, 1)]
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
