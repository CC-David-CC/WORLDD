"""A production-supervised copy broker must retire before its same-journal restart.

Run only through the declared native test-host CTest lane.
"""
import hashlib
import os
import sqlite3
import threading
from contextlib import closing

import file_copy_job_journal_pb2 as copy_journal
import sys
import time
import unittest
from unittest.mock import patch

import cabinet_pb2 as cabinet
import file_actions_pb2 as files
import test_workspace_copy_engine as engine
import test_workspace_copy_faults as copy_faults
sys.path.insert(0, str(engine.SOURCE / 'tests/native'))
from supervised_copy_broker_recovery import SupervisedCopyBrokerRecovery, wait_for_active_generation


class SupervisedFileActionCopyRecoveryTests(engine.WorkspaceCopyEngineTests):
    def setUp(self):
        self.supervised_copy_recovery_fixture_version = 2
        self.copy_fault_mode = 'pass-through'
        replace = patch.object(engine, 'StorageBrokerProcess', copy_faults.CopyFaultStorageBrokerProcess)
        replace.start()
        try:
            super().setUp()
            from copy_fault_supervised_world import context
            context(self)
        finally:
            replace.stop()

    def action(self, **fields):
        request = cabinet.FileActionRequest(protocol_version=1,
            operation_id=os.urandom(16), workspace_id='one', principal=self.principal(), **fields)
        return self.broker.request(file_action=request)

    def source_handles(self, name):
        page = self.action(browse=files.FileBrowse(page_size=64))
        root = page.file_action.directory.handle
        source = next(item.handle for item in page.file_action.directory.entries if item.name == name)
        return root, source

    def poll_terminal(self, job_id, timeout=30):
        deadline = time.monotonic() + timeout
        while True:
            result = self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.POLL,
                                                             job_id=job_id))
            self.assertTrue(result.file_action.HasField('copy_job'), result)
            receipt = result.file_action.copy_job
            if receipt.state in (files.FileCopyJobReceipt.COMPLETE, files.FileCopyJobReceipt.FAILED,
                                 files.FileCopyJobReceipt.CANCELLED, files.FileCopyJobReceipt.CONFLICT,
                                 files.FileCopyJobReceipt.UNCERTAIN):
                return receipt
            self.assertLess(time.monotonic(), deadline, 'Copy job did not reach a terminal state')
            time.sleep(.02)

    def _start_boundary_copy(self, source_name, destination_name, boundary_environment, ready_path):
        with patch.dict(os.environ, boundary_environment):
            self.workspace(attach_world=False)
        old = self.broker
        bootstrap = next((item for item in self.brokers if getattr(item, 'bootstrap_delegation', False)), None)
        self.assertIsNotNone(bootstrap, 'Initial broker must retain the execution delegation parent')
        self.assertEqual(bootstrap.delegation_parent_root.parent, old.resources.handle.cgroup)
        recovery = SupervisedCopyBrokerRecovery(self, old, self.authority_config)
        wait_for_active_generation(self, recovery.handle)
        denial = recovery.reject_live_replacement_at_issuer(type(old))
        self.assertEqual(denial.message, 'Issuer did not approve the retained participant connection')

        source = self.project / source_name
        payload = b'supervised prepared publication recovery payload'
        source.write_bytes(payload)
        root_handle, source_handle = self.source_handles(source_name.encode())
        started = self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.START,
            source_handle=source_handle, destination_directory_handle=root_handle,
            destination_basename=destination_name.encode(), maximum_bytes=4096, maximum_entries=8))
        self.assertTrue(started.file_action.HasField('copy_job'), started)
        job_id = started.file_action.copy_job.job_id
        journal = self.execution / 'file-operations' / 'file-operations.db'
        errors = []
        def poll_boundary():
            try:
                self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.POLL, job_id=job_id))
            except (EOFError, OSError):
                errors.append('transport-closed')
            except BaseException as error:
                errors.append(repr(error))
        poll_thread = threading.Thread(target=poll_boundary, daemon=True)
        poll_thread.start()
        deadline = time.monotonic() + 10
        while not ready_path.exists():
            self.assertLess(time.monotonic(), deadline, 'Production copy boundary was not reached')
            time.sleep(.005)
        # Kill immediately. The legacy prepared hook remains a five-second
        # notify-and-wait boundary for compatibility with its existing oracle.
        recovery.kill_actual_broker()
        cleanup, retirement = recovery.checked_cleanup_and_retire()
        self.assertTrue(cleanup.HasField('fence'))
        self.assertEqual(retirement.request.cleanup_sha256,
                         hashlib.sha256(cleanup.SerializeToString(deterministic=True)).digest())
        poll_thread.join(5)
        self.assertFalse(poll_thread.is_alive(), 'Blocked production poll did not close after SIGKILL')
        self.assertEqual(errors, ['transport-closed'])
        with closing(sqlite3.connect('file:'+str(journal)+'?mode=ro', uri=True, timeout=.5)) as db:
            before = copy_journal.FileCopyJobJournalRecord.FromString(db.execute(
                'SELECT record FROM file_copy_jobs WHERE workspace=? AND job_id=?', ('one', job_id)).fetchone()[0])
        self.assertIn(before.state, (copy_journal.FILE_COPY_JOB_JOURNAL_RUNNING,
                                     copy_journal.FILE_COPY_JOB_JOURNAL_CANCEL_REQUESTED))
        self.assertTrue(before.HasField('prepared_result'), 'Barrier follows durable prepared output')
        self.assertTrue(before.HasField('drained_process_scope'), 'Worker proof must come from production drain callback')
        self.assertEqual(before.drained_process_scope.SerializeToString(deterministic=True),
                         before.process_scope.SerializeToString(deterministic=True))
        stage_root = self.project / '.spatial-copy-staging'
        stage = stage_root / os.fsdecode(before.staging_name)
        worker_scope = old.resources.root / 'spatial-git-workers' / ('copy-' + job_id.hex())
        target = self.project / destination_name
        self.assertFalse(worker_scope.exists(), 'Prepared result must have a drained worker scope')
        return dict(old=old, recovery=recovery, job_id=job_id, journal=journal, before=before,
                    stage=stage, stage_root=stage_root, worker_scope=worker_scope,
                    target=target, payload=payload, poll_thread=poll_thread)

    def _restart_boundary_copy(self, context, sync_failure=False):
        old = context['old']
        recovery = context['recovery']
        if sync_failure:
            with patch.dict(os.environ, {'SPATIAL_TEST_COPY_RECOVERY_DESTINATION_SYNC_FAIL': '1'}):
                replacement = recovery.restart(type(old))
        else:
            replacement = recovery.restart(type(old))
        self.broker = replacement
        self.assertEqual(replacement.fixture.execution, old.fixture.execution)
        self.assertEqual(replacement.resources.handle.cgroup, old.resources.handle.cgroup)
        self.assertNotEqual(replacement.resources.root, old.resources.root)
        self.assertEqual(replacement.resources.root.parent, old.resources.root.parent)
        self.assertFalse(context['worker_scope'].exists(), 'Retired generation scope must stay absent')
        self.assertEqual(context['journal'], self.execution / 'file-operations' / 'file-operations.db')
        result = self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.POLL,
                                                        job_id=context['job_id']))
        self.assertTrue(result.file_action.HasField('copy_job'), result)
        with closing(sqlite3.connect('file:'+str(context['journal'])+'?mode=ro', uri=True, timeout=.5)) as db:
            after = copy_journal.FileCopyJobJournalRecord.FromString(db.execute(
                'SELECT record FROM file_copy_jobs WHERE workspace=? AND job_id=?',
                ('one', context['job_id'])).fetchone()[0])
        self.assertEqual(after.prepared_result, context['before'].prepared_result)
        self.assertEqual(after.drained_process_scope, context['before'].drained_process_scope)
        return result.file_action.copy_job, after

    def test_prepared_worker_proof_survives_supervised_prepublication_crash(self):
        marker = self.root / 'supervised-prepared-ready'
        context = self._start_boundary_copy('supervised-prepared-source.txt',
            'supervised-prepared-destination.txt',
            {'SPATIAL_TEST_COPY_PREPARED_NOTIFY': str(marker)}, marker)
        receipt, after = self._restart_boundary_copy(context)
        self.assertEqual(receipt.state, files.FileCopyJobReceipt.CANCELLED, receipt)
        self.assertFalse(context['target'].exists(), 'Prepublication recovery must not publish output')
        self.assertFalse(context['stage'].exists(), 'Recovery must remove the exact prepared stage')
        self.assertEqual(after.state, copy_journal.FILE_COPY_JOB_JOURNAL_CANCELLED)

    def test_published_prepared_supervised_restart_syncs_before_complete(self):
        barrier = self.root / 'supervised-post-rename-barrier'
        barrier.mkdir()
        context = self._start_boundary_copy('supervised-published-source.txt',
            'supervised-published-destination.txt',
            {'SPATIAL_TEST_COPY_POST_RENAME_BARRIER': str(barrier)}, barrier / 'ready')
        published = context['target'].stat()
        prepared = context['before'].prepared_result.output
        self.assertEqual((published.st_dev, published.st_ino), (prepared.device, prepared.inode))
        self.assertFalse((barrier / 'release').exists(), 'Crash boundary must remain held until broker death')
        receipt, after = self._restart_boundary_copy(context)
        self.assertEqual(receipt.state, files.FileCopyJobReceipt.COMPLETE, receipt)
        self.assertEqual(context['target'].read_bytes(), context['payload'])
        self.assertFalse(context['stage'].exists(), 'Successful recovery must remove the exact stage')
        self.assertEqual(after.state, copy_journal.FILE_COPY_JOB_JOURNAL_COMPLETE)

    def test_published_prepared_sync_failure_stays_uncertain_after_supervised_restart(self):
        barrier = self.root / 'supervised-post-rename-sync-failure-barrier'
        barrier.mkdir()
        context = self._start_boundary_copy('supervised-sync-failure-source.txt',
            'supervised-sync-failure-destination.txt',
            {'SPATIAL_TEST_COPY_POST_RENAME_BARRIER': str(barrier)}, barrier / 'ready')
        published = context['target'].stat()
        prepared = context['before'].prepared_result.output
        self.assertEqual((published.st_dev, published.st_ino), (prepared.device, prepared.inode))
        receipt, after = self._restart_boundary_copy(context, sync_failure=True)
        self.assertEqual(receipt.state, files.FileCopyJobReceipt.UNCERTAIN, receipt)
        self.assertEqual(context['target'].read_bytes(), context['payload'])
        self.assertTrue(context['stage'].exists(), 'Sync uncertainty must retain the exact stage')
        stage_stat = context['stage'].stat()
        self.assertEqual((stage_stat.st_dev, stage_stat.st_ino),
                         (context['before'].staging.device, context['before'].staging.inode))
        self.assertEqual(after.state, copy_journal.FILE_COPY_JOB_JOURNAL_UNCERTAIN)

    def test_sigkill_checked_retirement_and_restart_reconcile_same_copy_journal(self):
        self.workspace(attach_world=False)
        old = self.broker
        bootstrap=next((item for item in self.brokers if getattr(item,'bootstrap_delegation',False)),None)
        self.assertIsNotNone(bootstrap,'Initial Git/workspace broker must retain the execution delegation parent')
        self.assertEqual(bootstrap.delegation_parent_root.parent,old.resources.handle.cgroup,
                         'Bootstrap and supervised authority must use one delegated parent')
        self.assertEqual(bootstrap.delegation_parent_identity,
            old.resources.handle.owner.delegation.verify_pinned_directory(
                old.resources.handle.cgroup,bootstrap.delegation_parent['fd'],os.getuid()),
            'Supervised restart must retain the pinned bootstrap parent identity')
        self.assertEqual(bootstrap.delegation_parent['participant_id'],old.resources.handle.binding.participant_id)
        recovery = SupervisedCopyBrokerRecovery(self, old, self.authority_config)
        wait_for_active_generation(self, recovery.handle)
        denial = recovery.reject_live_replacement_at_issuer(type(old))
        self.assertEqual(denial.message, 'Issuer did not approve the retained participant connection')
        source = self.project / 'supervised-sigkill-source'
        source.mkdir()
        payload = bytes(range(256)) * 64
        for index in range(1024):
            (source / ('%04d.bin' % index)).write_bytes(payload)
        root_handle, source_handle = self.source_handles(b'supervised-sigkill-source')
        started = self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.START,
            source_handle=source_handle, destination_directory_handle=root_handle,
            destination_basename=b'supervised-sigkill-copy', maximum_bytes=128 * 1024 * 1024,
            maximum_entries=4096))
        self.assertTrue(started.file_action.HasField('copy_job'), started)
        job_id = started.file_action.copy_job.job_id
        worker_scope = old.resources.root / 'spatial-git-workers' / ('copy-' + job_id.hex())
        staging_root = self.project / '.spatial-copy-staging'
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if worker_scope.exists() and (worker_scope / 'cgroup.procs').read_text().split():
                break
            time.sleep(.005)
        self.assertTrue(worker_scope.exists(), 'Copy worker scope was not created')
        self.assertTrue((worker_scope / 'cgroup.procs').read_text().split(),
                        'Copy worker must be live before broker SIGKILL')
        self.assertTrue(list(staging_root.iterdir()), 'Copy staging must exist before broker SIGKILL')

        journal = self.execution / 'file-operations' / 'file-operations.db'
        with closing(sqlite3.connect('file:'+str(journal)+'?mode=ro', uri=True, timeout=.5)) as db:
            before = copy_journal.FileCopyJobJournalRecord.FromString(db.execute(
                'SELECT record FROM file_copy_jobs WHERE workspace=? AND job_id=?',
                ('one', job_id)).fetchone()[0])
        self.assertTrue(before.HasField('process_scope'))
        self.assertFalse(before.HasField('drained_process_scope'), 'Crash requires an undrained live worker')
        self.assertIn(before.state, (copy_journal.FILE_COPY_JOB_JOURNAL_QUEUED,
                                     copy_journal.FILE_COPY_JOB_JOURNAL_RUNNING))
        self.assertEqual(before.process_scope.scope_name, worker_scope.name)
        stage = staging_root / os.fsdecode(before.staging_name)
        self.assertEqual((stage.stat().st_dev, stage.stat().st_ino),
                         (before.staging.device, before.staging.inode))
        (self.copy_evidence/'before-kill-file-copy-record.pb').write_bytes(before.SerializeToString())
        self.assertTrue((worker_scope/'cgroup.procs').read_text().split(),
                        'Worker exited before the broker crash boundary')
        recovery.kill_actual_broker()
        cleanup, retirement = recovery.checked_cleanup_and_retire()
        self.assertTrue(cleanup.HasField('fence'))
        self.assertEqual(retirement.request.cleanup_sha256,
                         __import__('hashlib').sha256(cleanup.SerializeToString(deterministic=True)).digest())

        replacement = recovery.restart(type(old))
        self.broker = replacement
        self.assertEqual(replacement.fixture.execution, old.fixture.execution)
        self.assertEqual(replacement.resources.handle.cgroup, old.resources.handle.cgroup,
                         'Supervised restart must reuse the participant delegation parent')
        self.assertNotEqual(replacement.resources.root, old.resources.root,
                            'Supervised generations must receive distinct ProcessScope roots')
        self.assertEqual(replacement.resources.root.parent, old.resources.root.parent,
                         'Generation roots must be immediate children of the shared delegation parent')
        self.assertEqual(recovery.copy_journal, self.execution / 'file-operations' / 'file-operations.db')
        recovered = self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.POLL, job_id=job_id))
        self.assertTrue(recovered.file_action.HasField('copy_job'), recovered)
        self.assertEqual(recovered.file_action.copy_job.state, files.FileCopyJobReceipt.CANCELLED,
                         recovered.file_action.copy_job)
        with closing(sqlite3.connect('file:'+str(journal)+'?mode=ro', uri=True, timeout=.5)) as db:
            after = copy_journal.FileCopyJobJournalRecord.FromString(db.execute(
                'SELECT record FROM file_copy_jobs WHERE workspace=? AND job_id=?',
                ('one', job_id)).fetchone()[0])
        for name in ('process_scope', 'process_scope_parent', 'staging', 'staging_root'):
            self.assertEqual(getattr(after, name), getattr(before, name))
        self.assertEqual(after.staging_name, before.staging_name)
        (self.copy_evidence/'after-restart-file-copy-record.pb').write_bytes(after.SerializeToString())
        self.assertFalse((self.project / 'supervised-sigkill-copy').exists())
        self.assertEqual(list(staging_root.iterdir()), [], 'Recovery must remove recorded staging only')
        self.assertFalse(worker_scope.exists(), 'Recovery must retire the recorded worker scope')
        self.assertFalse((old.resources.root / 'spatial-git-workers').exists(),
                         'Recovery must remove the recorded worker parent')

        small_followup = self.project / 'after-supervised-sigkill-source.txt'
        small_followup.write_bytes(b'bounded post-recovery copy')
        root_handle, source_handle = self.source_handles(b'after-supervised-sigkill-source.txt')
        next_started = self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.START,
            source_handle=source_handle, destination_directory_handle=root_handle,
            destination_basename=b'after-supervised-sigkill-copy.txt',
            maximum_bytes=4096, maximum_entries=8))
        self.assertTrue(next_started.file_action.HasField('copy_job'), next_started)
        terminal = self.poll_terminal(next_started.file_action.copy_job.job_id)
        self.assertEqual(terminal.state, files.FileCopyJobReceipt.COMPLETE, terminal)
        self.assertEqual((self.project / 'after-supervised-sigkill-copy.txt').read_bytes(),
                         b'bounded post-recovery copy')


if __name__ == '__main__':
    unittest.main(defaultTest='SupervisedFileActionCopyRecoveryTests.test_sigkill_checked_retirement_and_restart_reconcile_same_copy_journal',
                   verbosity=2)
