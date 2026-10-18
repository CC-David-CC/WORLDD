from pathlib import Path
from contextlib import closing
import os
import select
import signal
import sqlite3
import struct
import time
import uuid
import broker_pb2 as broker
import grants_pb2 as grant
import issuer_pb2 as issuer
import cabinet_pb2 as cabinet
import file_actions_pb2 as files
import file_copy_job_journal_pb2 as copy_journal
import core_pb2 as core
from harness.wire import MAX_FRAME
from test_workspace_authority import WorkspaceAuthorityTests, PROJECT, POLICY, world_authority

class FileActionBrokerRegressionTests(WorkspaceAuthorityTests):
    """Named source regressions for the private broker-to-FileEngine boundary."""

    def action(self, **fields):
        request = cabinet.FileActionRequest(
            protocol_version=1, operation_id=uuid.uuid4().bytes,
            workspace_id='one', principal=self.principal(), **fields)
        return self.broker.request(file_action=request)

    def expect_file_error(self, reply, code):
        self.assertTrue(reply.HasField('file_action'), reply)
        self.assertTrue(reply.file_action.HasField('error'), reply.file_action)
        self.assertEqual(reply.file_action.error.code, code, reply.file_action)

    def test_file_action_copy_staging_root_is_hidden_and_reserved(self):
        self.workspace(attach_world=False)
        stage_root = self.project.parent/('.spatial-copy-staging-'+self.project.name)
        page = self.action(browse=files.FileBrowse(page_size=64))
        self.assertTrue(page.file_action.HasField('directory'), page)
        self.assertTrue(stage_root.is_dir())
        self.assertNotIn(b'.spatial-copy-staging',
                         {entry.name for entry in page.file_action.directory.entries})
        created = self.action(create=files.FileCreate(
            basename=b'.spatial-copy-staging', content=b'forbidden'))
        self.expect_file_error(created, files.FileActionError.INVALID_ARGUMENT)

        (self.project/'reserved-target-source').write_bytes(b'preserve')
        root_handle, source_handle = self._copy_source_handles('reserved-target-source')
        page = self.action(browse=files.FileBrowse(page_size=64))
        source_entry = next(entry for entry in page.file_action.directory.entries
                            if entry.name == b'reserved-target-source')
        renamed = self.action(rename=files.FileRename(
            file_handle=source_handle, expected_generation=source_entry.generation,
            new_basename=b'.spatial-copy-staging'))
        self.expect_file_error(renamed, files.FileActionError.INVALID_ARGUMENT)
        copied = self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.START,
            source_handle=source_handle, destination_directory_handle=root_handle,
            destination_basename=b'.spatial-copy-staging',
            maximum_bytes=1024, maximum_entries=8))
        self.expect_file_error(copied, files.FileActionError.INVALID_ARGUMENT)

        trashed = self.action(trash=files.FileTrash(file_handle=source_handle,
            expected_generation=source_entry.generation))
        self.assertTrue(trashed.file_action.HasField('trash'), trashed)
        restored = self.action(restore=files.FileRestore(
            trash_handle=trashed.file_action.trash.handle,
            destination_directory_handle=root_handle,
            destination_basename=b'.spatial-copy-staging',
            expected_generation=page.file_action.directory.generation))
        self.expect_file_error(restored, files.FileActionError.INVALID_ARGUMENT)
        self.assertTrue(stage_root.is_dir())
        self.assertEqual(list(stage_root.iterdir()), [])

    def test_file_action_authorized_browse_read_create_and_exact_retry(self):
        self.workspace(attach_world=False)
        (self.project/'known.txt').write_bytes(b'known payload')
        browse_request = cabinet.FileActionRequest(
            protocol_version=1, operation_id=uuid.uuid4().bytes, workspace_id='one',
            principal=self.principal(), browse=files.FileBrowse(page_size=64))
        browse = self.broker.request(file_action=browse_request)
        self.assertTrue(browse.HasField('file_action') and browse.file_action.HasField('directory'), browse)
        entry = next(item for item in browse.file_action.directory.entries if item.name == b'known.txt')
        read = self.action(read=files.FileRead(file_handle=entry.handle, maximum_bytes=128))
        self.assertEqual(read.file_action.content.data, b'known payload')
        create_request = cabinet.FileActionRequest(
            protocol_version=1, operation_id=uuid.uuid4().bytes, workspace_id='one',
            principal=self.principal(),
            create=files.FileCreate(basename=b'created.txt', content=b'created'))
        created = self.broker.request(file_action=create_request)
        self.assertTrue(created.file_action.HasField('object'), created)
        self.assertEqual((self.project/'created.txt').read_bytes(), b'created')
        repeated = self.broker.request(file_action=create_request)
        self.assertEqual(repeated.file_action.SerializeToString(deterministic=True),
                         created.file_action.SerializeToString(deterministic=True))
        changed = cabinet.FileActionRequest()
        changed.CopyFrom(create_request); changed.create.content = b'changed'
        self.expect_file_error(self.broker.request(file_action=changed), files.FileActionError.CONFLICT)

    def test_file_action_terminal_journal_failure_preserves_uncertainty_and_never_replays(self):
        from unittest.mock import patch
        with patch.dict('os.environ', {'SPATIAL_TEST_FILE_JOURNAL_FAIL_FINISH_ONCE':'1'}):
            self.workspace(attach_world=False)
        request = cabinet.FileActionRequest(
            protocol_version=1, operation_id=uuid.uuid4().bytes, workspace_id='one',
            principal=self.principal(),
            create=files.FileCreate(basename=b'journal-fault.txt', content=b'committed-once'))
        first = self.broker.request(file_action=request)
        self.assertTrue(first.HasField('file_action') and first.file_action.HasField('error'), first)
        self.assertEqual(first.file_action.error.code, files.FileActionError.UNCERTAIN, first)
        self.assertEqual(first.file_action.error.recovery_operation_id, request.operation_id)
        target = self.project/'journal-fault.txt'
        self.assertEqual(target.read_bytes(), b'committed-once')
        retry = self.broker.request(file_action=request)
        self.assertTrue(retry.file_action.HasField('error'), retry)
        self.assertEqual(retry.file_action.error.code, files.FileActionError.UNCERTAIN, retry)
        self.assertEqual(retry.file_action.error.recovery_operation_id, request.operation_id)
        self.assertEqual(target.read_bytes(), b'committed-once')

    def test_file_action_revocation_during_terminal_journal_wait_preserves_uncertainty(self):
        import os, threading, time
        from unittest.mock import patch
        marker=self.root/'finish-boundary.marker'
        env={'SPATIAL_TEST_FILE_JOURNAL_FINISH_NOTIFY':str(marker),
             'SPATIAL_TEST_FILE_JOURNAL_FINISH_DELAY_MS':'700'}
        with patch.dict(os.environ,env): self.workspace(attach_world=False)
        request=cabinet.FileActionRequest(protocol_version=1,operation_id=uuid.uuid4().bytes,
            workspace_id='one',principal=self.principal(),
            create=files.FileCreate(basename=b'terminal-revocation.txt',content=b'effected'))
        revoke_error=[]
        def revoke_when_finishing():
            deadline=time.monotonic()+5
            while time.monotonic()<deadline and not marker.exists(): time.sleep(.005)
            if not marker.exists(): revoke_error.append('terminal journal did not enter'); return
            try:
                self.owner_peer=self.connect()
                self.revoke(kind=issuer.ACTOR_TARGET)
            except BaseException as error:
                revoke_error.append(repr(error))
        thread=threading.Thread(target=revoke_when_finishing,daemon=True); thread.start()
        reply=self.broker.request(file_action=request)
        thread.join(6)
        self.assertFalse(thread.is_alive(),'revocation worker did not finish')
        self.assertFalse(revoke_error,revoke_error)
        self.assertTrue(reply.HasField('file_action') and reply.file_action.HasField('error'),reply)
        self.assertEqual(reply.file_action.error.code,files.FileActionError.UNCERTAIN,reply)
        self.assertEqual(reply.file_action.error.recovery_operation_id,request.operation_id)
        self.assertEqual((self.project/'terminal-revocation.txt').read_bytes(),b'effected')
        with closing(sqlite3.connect(self.execution/'file-operations'/'file-operations.db')) as db:
            row=db.execute('SELECT state FROM file_operations WHERE workspace=? AND operation=?',
                ('one',request.operation_id)).fetchone()
        self.assertEqual(row,(3,),'Revocation after possible effect lost durable UNCERTAIN state')
        retry=self.broker.request(file_action=request)
        self.assertEqual(retry.file_action.error.code,files.FileActionError.UNAUTHORIZED)
        self.assertEqual((self.project/'terminal-revocation.txt').read_bytes(),b'effected')

    def test_file_action_create_only_grant_needs_no_browse_or_read(self):
        self.workspace(policy=['files.write'], roots=lambda: [
            self.workspace_terms(rights=[grant.FILE_CREATE], depth=0)])
        request = cabinet.FileActionRequest(
            protocol_version=1, operation_id=uuid.uuid4().bytes, workspace_id='one',
            principal=self.principal(),
            create=files.FileCreate(basename=b'only-create.txt', content=b'allowed'))
        reply = self.broker.request(file_action=request)
        self.assertTrue(reply.file_action.HasField('object'), reply)
        self.assertEqual((self.project/'only-create.txt').read_bytes(), b'allowed')

    def test_file_action_rejects_wrong_principal_scope_and_policy(self):
        self.workspace(attach_world=False)
        wrong = self.principal(); wrong.actor_id = bytes([0x77])*32
        request = cabinet.FileActionRequest(
            protocol_version=1, operation_id=uuid.uuid4().bytes, workspace_id='one',
            principal=wrong, browse=files.FileBrowse(page_size=32))
        self.expect_file_error(self.broker.request(file_action=request), files.FileActionError.UNAUTHORIZED)
        omitted = cabinet.FileActionRequest(
            protocol_version=1, operation_id=uuid.uuid4().bytes, workspace_id='one',
            browse=files.FileBrowse(page_size=32))
        self.expect_file_error(self.broker.request(file_action=omitted), files.FileActionError.INVALID_ARGUMENT)
        request = cabinet.FileActionRequest(
            protocol_version=1, operation_id=uuid.uuid4().bytes, workspace_id='one',
            principal=self.principal(), authority_components=[b'caller-controlled'],
            browse=files.FileBrowse(page_size=32))
        self.expect_file_error(self.broker.request(file_action=request), files.FileActionError.INVALID_ARGUMENT)

    def test_file_action_copy_rejects_project_root_source(self):
        self.workspace(attach_world=False)
        before={path.name for path in self.project.iterdir()}
        reply=self.action(copy_job=files.FileCopyJob(
            command=files.FileCopyJob.START,maximum_bytes=1024,maximum_entries=8))
        self.expect_file_error(reply,files.FileActionError.INVALID_ARGUMENT)
        self.assertEqual({path.name for path in self.project.iterdir()},before)

    def _copy_source_handles(self, name):
        page=self.action(browse=files.FileBrowse(page_size=64))
        root_handle=page.file_action.directory.handle
        source_handle=next(item.handle for item in page.file_action.directory.entries if item.name==name.encode())
        return root_handle,source_handle

    def _poll_copy_to_terminal(self, job_id, timeout=30):
        deadline=time.monotonic()+timeout
        while True:
            reply=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.POLL,job_id=job_id))
            self.assertTrue(reply.file_action.HasField('copy_job'),reply)
            receipt=reply.file_action.copy_job
            if receipt.state in (files.FileCopyJobReceipt.COMPLETE,
                                 files.FileCopyJobReceipt.FAILED,
                                 files.FileCopyJobReceipt.CANCELLED,
                                 files.FileCopyJobReceipt.CONFLICT,
                                 files.FileCopyJobReceipt.UNCERTAIN):
                return receipt
            self.assertLess(time.monotonic(),deadline,'copy job did not reach a terminal state')
            time.sleep(.02)

    def test_file_action_copy_job_regular_file_bytes_and_stable_poll(self):
        self.workspace(attach_world=False)
        source=self.project/'single.bin'; source.write_bytes(b'file copy payload'); source.chmod(0o640)
        root_handle,source_handle=self._copy_source_handles('single.bin')
        started=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.START,
            source_handle=source_handle,destination_directory_handle=root_handle,
            destination_basename=b'single-copy.bin',maximum_bytes=1024,maximum_entries=8))
        self.assertTrue(started.file_action.HasField('copy_job'),started)
        job_id=started.file_action.copy_job.job_id
        receipt=self._poll_copy_to_terminal(job_id)
        self.assertEqual(receipt.state,files.FileCopyJobReceipt.COMPLETE,receipt)
        self.assertEqual(receipt.bytes_copied,len(b'file copy payload'))
        self.assertEqual(receipt.entries_copied,1)
        self.assertEqual(len(receipt.content_digest),32)
        copied=self.project/'single-copy.bin'
        self.assertEqual(copied.read_bytes(),b'file copy payload')
        self.assertEqual(copied.stat().st_mode & 0o777,0o640)
        self.assertEqual(source.read_bytes(),b'file copy payload')
        repeated=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.POLL,job_id=job_id))
        self.assertEqual(repeated.file_action.copy_job.SerializeToString(deterministic=True),
                         receipt.SerializeToString(deterministic=True))

    def test_file_action_copy_job_start_retry_uses_one_durable_job(self):
        self._copy_start_retry(False)

    def test_file_action_copy_job_start_lost_ack_retry_recovers_durable_job(self):
        self._copy_start_retry(True)

    def _copy_start_retry(self, lose_ack):
        from unittest.mock import patch
        environment = {'SPATIAL_TEST_FILE_JOURNAL_FAIL_FINISH_ONCE': '1'} if lose_ack else {}
        with patch.dict(os.environ, environment):
            self.workspace(attach_world=False)
        source = self.project/'start-retry-source'
        payload = b'exactly one durable copy'
        source.write_bytes(payload)
        root_handle, source_handle = self._copy_source_handles(source.name)
        request = cabinet.FileActionRequest(protocol_version=1, operation_id=uuid.uuid4().bytes,
            workspace_id='one', principal=self.principal(), copy_job=files.FileCopyJob(
                command=files.FileCopyJob.START, source_handle=source_handle,
                destination_directory_handle=root_handle, destination_basename=b'start-retry-copy',
                maximum_bytes=1024, maximum_entries=8))
        first = self.broker.request(file_action=request)
        if lose_ack:
            self.expect_file_error(first, files.FileActionError.UNCERTAIN)
        else:
            self.assertTrue(first.file_action.HasField('copy_job'), first)
        database = self.execution/'file-operations'/'file-operations.db'
        def durable_rows():
            with closing(sqlite3.connect(database)) as db:
                jobs = db.execute('SELECT job_id FROM file_copy_jobs WHERE workspace=?', ('one',)).fetchall()
                operation = db.execute('SELECT state,outcome FROM file_operations WHERE workspace=? AND operation=?',
                    ('one', request.operation_id)).fetchone()
            self.assertEqual(len(jobs), 1, 'START retry created another durable job')
            return jobs[0][0], operation
        job_id, before = durable_rows()
        if not lose_ack:
            self.assertEqual(first.file_action.copy_job.job_id, job_id)
            self.assertEqual(before[0], 2)
        retry = self.broker.request(file_action=request)
        self.assertTrue(retry.file_action.HasField('copy_job'), retry)
        self.assertEqual(retry.file_action.copy_job.job_id, job_id)
        self.assertIn(retry.file_action.copy_job.state,
                      (files.FileCopyJobReceipt.QUEUED, files.FileCopyJobReceipt.RUNNING))
        _, completed_operation = durable_rows()
        self.assertEqual(completed_operation[0], 2)
        if not lose_ack:
            self.assertEqual(completed_operation, before, 'completed START outcome was rewritten')
        changed = cabinet.FileActionRequest()
        changed.CopyFrom(request)
        changed.copy_job.destination_basename = b'different-copy'
        self.expect_file_error(self.broker.request(file_action=changed), files.FileActionError.CONFLICT)
        terminal = self._poll_copy_to_terminal(job_id)
        self.assertEqual(terminal.state, files.FileCopyJobReceipt.COMPLETE, terminal)
        target = self.project/'start-retry-copy'
        identity = (target.stat().st_dev, target.stat().st_ino)
        self.assertEqual(target.read_bytes(), payload)
        for restart in (False, True):
            if restart:
                self.broker.close()
                self.broker = type(self.broker)(self, self.execution, self.authority_config)
            repeated = self.broker.request(file_action=request)
            self.assertTrue(repeated.file_action.HasField('copy_job'), repeated)
            self.assertEqual(repeated.file_action.copy_job.SerializeToString(deterministic=True),
                             terminal.SerializeToString(deterministic=True))
            self.assertEqual(durable_rows(), (job_id, completed_operation))
            self.assertEqual((target.stat().st_dev, target.stat().st_ino), identity)
            self.assertEqual(target.read_bytes(), payload)
            self.assertFalse((self.project/'different-copy').exists())

    def test_file_action_copy_job_terminal_receipt_survives_broker_restart(self):
        self.workspace(pair=True, attach_world=False)
        source=self.project/'restart-poll-source'
        source.write_bytes(b'durable terminal receipt')
        root_handle,source_handle=self._copy_source_handles('restart-poll-source')
        started=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.START,
            source_handle=source_handle,destination_directory_handle=root_handle,
            destination_basename=b'restart-poll-copy',maximum_bytes=1024,maximum_entries=8))
        self.assertTrue(started.file_action.HasField('copy_job'),started)
        job_id=started.file_action.copy_job.job_id
        terminal=self._poll_copy_to_terminal(job_id)
        self.assertEqual(terminal.state,files.FileCopyJobReceipt.COMPLETE,terminal)

        # A new broker process has no in-memory FileEngine job map.
        old_root=self.broker.resources.root if self.broker.resources else None
        self.broker.close()
        if old_root is not None:
            self.assertFalse(old_root.exists(), 'graceful restart must remove the prior broker cgroup root')
        self.broker=type(self.broker)(self,self.execution,self.authority_config)
        poll=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.POLL,job_id=job_id))
        self.assertTrue(poll.file_action.HasField('copy_job'),poll)
        self.assertEqual(poll.file_action.copy_job.SerializeToString(deterministic=True),
                         terminal.SerializeToString(deterministic=True))
        cancel=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.CANCEL,job_id=job_id))
        self.assertTrue(cancel.file_action.HasField('copy_job'),cancel)
        self.assertEqual(cancel.file_action.copy_job.SerializeToString(deterministic=True),
                         terminal.SerializeToString(deterministic=True))

        foreign=self.principal(); foreign.actor_id=bytes([0x77])*32
        foreign_poll=cabinet.FileActionRequest(protocol_version=1,operation_id=uuid.uuid4().bytes,
            workspace_id='one',principal=foreign,copy_job=files.FileCopyJob(
                command=files.FileCopyJob.POLL,job_id=job_id))
        self.expect_file_error(self.broker.request(file_action=foreign_poll),
                               files.FileActionError.UNAUTHORIZED)
        other_worktree=cabinet.FileActionRequest(protocol_version=1,
            operation_id=uuid.uuid4().bytes,workspace_id='two',principal=self.principal(),
            copy_job=files.FileCopyJob(command=files.FileCopyJob.POLL,job_id=job_id))
        self.expect_file_error(self.broker.request(file_action=other_worktree),
                               files.FileActionError.NOT_FOUND)

    def test_file_action_copy_job_sigkill_restart_reconciles_retained_scope(self):
        self._copy_job_sigkill_restart(False)

    def test_file_action_uncertain_generation_keeps_independent_jobs_available(self):
        self._copy_job_sigkill_restart(True)

    def test_file_action_recovered_prepared_running_cancels_without_publication(self):
        self._copy_job_sigkill_restart(False,'running')

    def test_file_action_recovered_prepared_cancel_requested_keeps_identity(self):
        self._copy_job_sigkill_restart(False,'cancel_requested')

    def test_file_action_recovered_published_prepared_syncs_before_complete(self):
        self._copy_job_sigkill_restart(False,'published')

    def test_file_action_recovered_publication_sync_failure_stays_uncertain(self):
        self._copy_job_sigkill_restart(False,'sync_failure')

    def test_file_action_missing_scope_without_proof_retains_uncertainty(self):
        self._copy_job_sigkill_restart(False,'missing_scope')

    def test_file_action_replaced_scope_without_proof_preserves_evidence(self):
        self._copy_job_sigkill_restart(False,'replaced_scope')

    def test_file_action_replaced_scope_with_old_proof_stays_uncertain(self):
        self._copy_job_sigkill_restart(False,'replaced_scope_with_proof')

    def test_file_action_completed_worker_drain_proof_survives_prepublication_crash(self):
        self._completed_worker_proof_restart(False)

    def test_file_action_completed_worker_proof_reconciles_published_crash(self):
        self._completed_worker_proof_restart(True)

    def _completed_worker_proof_restart(self,published):
        import threading
        from unittest.mock import patch
        marker=self.root/'copy-prepared-notify'
        with patch.dict(os.environ,{'SPATIAL_TEST_COPY_PREPARED_NOTIFY':str(marker)}):
            self.workspace(attach_world=False)
        old=self.broker
        self.assertIsNotNone(old.resources,'proof regression requires production resource delegation')
        (self.project/'proof-source').write_bytes(b'completed worker')
        root_handle,source_handle=self._copy_source_handles('proof-source')
        started=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.START,
            source_handle=source_handle,destination_directory_handle=root_handle,
            destination_basename=b'proof-destination',maximum_bytes=4096,maximum_entries=8))
        self.assertTrue(started.file_action.HasField('copy_job'),started)
        job_id=started.file_action.copy_job.job_id
        errors=[]
        def poll_until_boundary():
            try:
                while True:
                    self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.POLL,job_id=job_id))
                    time.sleep(.01)
            except (EOFError,OSError):errors.append('transport-closed')
            except BaseException as error:errors.append(repr(error))
        thread=threading.Thread(target=poll_until_boundary,daemon=True);thread.start()
        deadline=time.monotonic()+10
        while not marker.exists():
            self.assertLess(time.monotonic(),deadline,'prepared crash boundary was not reached');time.sleep(.005)
        scope=old.resources.root/'spatial-git-workers'/('copy-'+job_id.hex())
        self.assertFalse(scope.exists(),'normal completed worker scope should already be removed')
        database=self.execution/'file-operations'/'file-operations.db'
        with closing(sqlite3.connect(database)) as db:
            before=copy_journal.FileCopyJobJournalRecord.FromString(db.execute(
                'SELECT record FROM file_copy_jobs WHERE workspace=? AND job_id=?',('one',job_id)).fetchone()[0])
        self.assertTrue(before.HasField('prepared_result'))
        self.assertTrue(before.HasField('drained_process_scope'))
        self.assertEqual(before.drained_process_scope.SerializeToString(deterministic=True),
                         before.process_scope.SerializeToString(deterministic=True))
        pidfd=os.pidfd_open(old.pid)
        try:os.kill(old.pid,signal.SIGKILL);self.assertTrue(select.select([pidfd],[],[],5)[0])
        finally:os.close(pidfd)
        thread.join(5);self.assertFalse(thread.is_alive());self.assertEqual(errors,['transport-closed'])
        if published:
            stage=self.project.parent/('.spatial-copy-staging-'+self.project.name)/os.fsdecode(before.staging_name)
            (stage/os.fsdecode(before.prepared_result.staging_output_name)).rename(self.project/'proof-destination')
        self.broker=type(old)(self,self.execution,self.authority_config)
        result=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.POLL,job_id=job_id))
        self.assertTrue(result.file_action.HasField('copy_job'),result)
        self.assertEqual(result.file_action.copy_job.state,
                         files.FileCopyJobReceipt.COMPLETE if published else files.FileCopyJobReceipt.CANCELLED,result)
        self.assertEqual((self.project/'proof-destination').exists(),published)
        if published:self.assertEqual((self.project/'proof-destination').read_bytes(),b'completed worker')
        with closing(sqlite3.connect(database)) as db:
            after=copy_journal.FileCopyJobJournalRecord.FromString(db.execute(
                'SELECT record FROM file_copy_jobs WHERE workspace=? AND job_id=?',('one',job_id)).fetchone()[0])
        self.assertEqual(after.drained_process_scope,before.drained_process_scope)
        self.assertEqual(after.prepared_result,before.prepared_result)
        self.assertFalse((self.project.parent/('.spatial-copy-staging-'+self.project.name)/os.fsdecode(before.staging_name)).exists())
        old.close()

    def _copy_job_sigkill_restart(self, retain_uncertain, prepared_case=None):
        self.workspace(attach_world=False, pair=retain_uncertain or prepared_case in ('missing_scope','replaced_scope','replaced_scope_with_proof'))
        old=self.broker
        if retain_uncertain or prepared_case:
            self.assertIsNotNone(old.resources,'generation regression requires production resource delegation')
        if not old.resources:
            self.skipTest('retained cgroup recovery requires the production resource delegate')
        source=self.project/'sigkill-source';source.mkdir()
        payload=bytes(range(256))*64
        for index in range(4096):
            (source/f'{index:04}.bin').write_bytes(payload)
        root_handle,source_handle=self._copy_source_handles('sigkill-source')
        started=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.START,
            source_handle=source_handle,destination_directory_handle=root_handle,
            destination_basename=b'sigkill-copy',maximum_bytes=128*1024*1024,maximum_entries=8192))
        self.assertTrue(started.file_action.HasField('copy_job'),started)
        job_id=started.file_action.copy_job.job_id
        old_root=old.resources.root
        worker_scope=old_root/'spatial-git-workers'/('copy-'+job_id.hex())
        stage_root=self.project.parent/('.spatial-copy-staging-'+self.project.name)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if worker_scope.exists() and (worker_scope/'cgroup.procs').read_text().split():
                break
            time.sleep(.005)
        self.assertTrue(worker_scope.exists(),'copy worker scope was not created')
        self.assertTrue((worker_scope/'cgroup.procs').read_text().split(),
                        'copy worker must be live before the broker crash')
        self.assertTrue(list(stage_root.iterdir()),'copy staging must exist before the broker crash')

        # Kill workspaced. Keep its process-scope owner alive so the old root remains.
        old_pidfd=os.pidfd_open(old.pid)
        try:
            os.kill(old.pid,signal.SIGKILL)
            self.assertTrue(select.select([old_pidfd],[],[],5)[0],
                            'SIGKILL did not stop the old broker promptly')
        finally:
            os.close(old_pidfd)

        if prepared_case:
            self._prepared_generation_restart(old,worker_scope,stage_root,job_id,prepared_case)
            return
        if retain_uncertain:
            self._uncertain_generation_restarts(old, old_root, worker_scope, stage_root, job_id)
            return
        replacement=type(old)(self,self.execution,self.authority_config)
        self.assertIsNotNone(replacement.resources)
        self.assertNotEqual(replacement.resources.root,old_root,
                            'replacement broker must use a distinct resource root')
        self.assertTrue(old_root.exists(),
                        'retained resource owner must keep the old root alive during recovery')
        self.broker=replacement
        recovered=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.POLL,job_id=job_id))
        self.assertTrue(recovered.file_action.HasField('copy_job'),recovered)
        self.assertEqual(recovered.file_action.copy_job.state,files.FileCopyJobReceipt.CANCELLED,
                         recovered.file_action.copy_job)
        self.assertFalse((self.project/'sigkill-copy').exists(),
                         'recovery must not publish an unprepared copy')
        self.assertEqual(list(stage_root.iterdir()),[],
                         'recovery must remove the exact recorded staging directory')
        self.assertFalse(worker_scope.exists(),
                         'recovery must kill and remove the exact recorded worker scope')
        self.assertFalse((old_root/'spatial-git-workers').exists(),
                         'recovery must remove the recorded Git-worker parent')

        # A new operation must run after recovery. Then retire the old empty owner root.
        new_root_handle,new_source_handle=self._copy_source_handles('sigkill-source')
        next_started=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.START,
            source_handle=new_source_handle,destination_directory_handle=new_root_handle,
            destination_basename=b'after-sigkill-copy',maximum_bytes=128*1024*1024,maximum_entries=8192))
        self.assertTrue(next_started.file_action.HasField('copy_job'),next_started)
        next_receipt=self._poll_copy_to_terminal(next_started.file_action.copy_job.job_id)
        self.assertEqual(next_receipt.state,files.FileCopyJobReceipt.COMPLETE,next_receipt)
        self.assertTrue((self.project/'after-sigkill-copy').is_dir())
        old.close()
        self.assertFalse(old_root.exists(),'retired owner must remove its empty old root')

    def _prepared_generation_restart(self,old,worker_scope,stage_root,job_id,case):
        # Stop the actual orphan worker without removing its journaled cgroup.
        # Construct the precise prepared crash boundary while workspaced is dead.
        (worker_scope/'cgroup.kill').write_text('1\n')
        deadline=time.monotonic()+5
        while 'populated 0\n' not in (worker_scope/'cgroup.events').read_text():
            self.assertLess(time.monotonic(),deadline);time.sleep(.01)
        database=self.execution/'file-operations'/'file-operations.db'
        with closing(sqlite3.connect(database)) as db:
            record=copy_journal.FileCopyJobJournalRecord.FromString(db.execute(
                'SELECT record FROM file_copy_jobs WHERE workspace=? AND job_id=?',('one',job_id)).fetchone()[0])
            stage=stage_root/os.fsdecode(record.staging_name)
            output=stage/'.spatial-copy-output-prepared-regression';output.mkdir()
            (output/'payload').write_bytes(b'prepared')
            info=output.stat()
            prepared=record.prepared_result
            prepared.staging_output_name=os.fsencode(output.name)
            prepared.output.CopyFrom(copy_journal.FileCopyFilesystemIdentity(device=info.st_dev,
                inode=info.st_ino,type=copy_journal.FileCopyFilesystemIdentity.DIRECTORY))
            prepared.bytes_copied=8;prepared.entries_copied=2;prepared.content_sha256=b'd'*32
            record.state=(copy_journal.FILE_COPY_JOB_JOURNAL_CANCEL_REQUESTED if case=='cancel_requested'
                          else copy_journal.FILE_COPY_JOB_JOURNAL_RUNNING)
            record.updated_at_unix_ms+=1
            if case=='replaced_scope_with_proof':
                record.drained_process_scope.CopyFrom(record.process_scope)
            prepared_bytes=prepared.SerializeToString(deterministic=True)
            db.execute('UPDATE file_copy_jobs SET state=?,updated_at=?,record=? WHERE workspace=? AND job_id=?',
                (record.state,record.updated_at_unix_ms,record.SerializeToString(),'one',job_id));db.commit()
        if case in ('missing_scope','replaced_scope','replaced_scope_with_proof'):
            worker_scope.rmdir()
            if case in ('replaced_scope','replaced_scope_with_proof'):
                worker_scope.mkdir()
                self.assertNotEqual(worker_scope.stat().st_ino,record.process_scope.inode)
        published=case in ('published','sync_failure')
        if published:output.rename(self.project/'sigkill-copy')
        from unittest.mock import patch
        with patch.dict(os.environ,{'SPATIAL_TEST_COPY_RECOVERY_DESTINATION_SYNC_FAIL':
                                     '1' if case=='sync_failure' else '0'}):
            self.broker=type(old)(self,self.execution,self.authority_config)
        result=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.POLL,job_id=job_id))
        self.assertTrue(result.file_action.HasField('copy_job'),result)
        expected=(files.FileCopyJobReceipt.UNCERTAIN if case in ('sync_failure','missing_scope','replaced_scope','replaced_scope_with_proof') else
                  files.FileCopyJobReceipt.COMPLETE if published else files.FileCopyJobReceipt.CANCELLED)
        self.assertEqual(result.file_action.copy_job.state,expected,result)
        self.assertEqual((self.project/'sigkill-copy').exists(),published)
        self.assertEqual(worker_scope.exists(),case in ('replaced_scope','replaced_scope_with_proof'))
        self.assertEqual(stage.exists(),case in ('sync_failure','missing_scope','replaced_scope','replaced_scope_with_proof'))
        with closing(sqlite3.connect(database)) as db:
            recovered=copy_journal.FileCopyJobJournalRecord.FromString(db.execute(
                'SELECT record FROM file_copy_jobs WHERE workspace=? AND job_id=?',('one',job_id)).fetchone()[0])
        self.assertEqual(recovered.prepared_result.SerializeToString(deterministic=True),prepared_bytes)
        self.assertGreaterEqual(recovered.updated_at_unix_ms,record.updated_at_unix_ms)
        if case in ('missing_scope','replaced_scope','replaced_scope_with_proof'):
            self.assertEqual(recovered.HasField('drained_process_scope'),case=='replaced_scope_with_proof')
            self.expect_file_error(self.action(browse=files.FileBrowse(page_size=8)),files.FileActionError.STALE_HANDLE)
            self.broker.call('git.status',worktree='tree-two')
        else:
            self.assertEqual(recovered.drained_process_scope,recovered.process_scope)
        old.close()

    def _uncertain_generation_restarts(self, old, old_root, worker_scope, stage_root, job_id):
        # The broker is dead. Seed a valid immutable uncertainty receipt at the
        # durable crash boundary while preserving real scope and stage identities.
        database=self.execution/'file-operations'/'file-operations.db'
        with closing(sqlite3.connect(database)) as db:
            row=db.execute('SELECT record FROM file_copy_jobs WHERE workspace=? AND job_id=?',
                           ('one',job_id)).fetchone()
            self.assertIsNotNone(row)
            record=copy_journal.FileCopyJobJournalRecord.FromString(row[0])
            record.state=copy_journal.FILE_COPY_JOB_JOURNAL_UNCERTAIN
            record.updated_at_unix_ms+=1
            record.terminal_receipt.CopyFrom(files.FileCopyJobReceipt(job_id=job_id,
                state=files.FileCopyJobReceipt.UNCERTAIN,detail='retained crash uncertainty',
                bytes_copied=record.bytes_progress,entries_copied=record.entries_progress))
            original=record.SerializeToString(deterministic=True)
            db.execute('UPDATE file_copy_jobs SET state=?,updated_at=?,record=? WHERE workspace=? AND job_id=?',
                (record.state,record.updated_at_unix_ms,original,'one',job_id));db.commit()
            parent=db.execute('SELECT state,root_device,root_inode,boot_id,identity,root_path FROM git_worker_parent').fetchone()
        stage=stage_root/os.fsdecode(record.staging_name)
        marker=stage/'uncertainty-evidence';marker.write_bytes(b'preserve exact staged evidence')
        marker_identity=marker.stat()
        with closing(sqlite3.connect(self.execution/'execution.db')) as db:
            registered = db.execute('SELECT path FROM worktrees WHERE id=? AND state=?',
                                    ('tree-two','ready')).fetchone()
        self.assertIsNotNone(registered, 'independent workspace lacks a registered worktree')
        independent=Path(registered[0])
        self.assertEqual(independent.parent,self.project.parent)
        (independent/'independent-source').write_bytes(b'new bounded copy')
        previous=old
        for generation in range(3):
            self.broker=type(old)(self,self.execution,self.authority_config)
            # Poll forces startup completion before checking its effects.
            poll=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.POLL,job_id=job_id))
            self.assertTrue(poll.file_action.HasField('copy_job'),poll)
            self.assertEqual(poll.file_action.copy_job.SerializeToString(deterministic=True),
                             record.terminal_receipt.SerializeToString(deterministic=True))
            self.assertFalse((self.project/'sigkill-copy').exists())
            foreign=self.principal();foreign.actor_id=bytes([0x77])*32
            denied=self.broker.request(file_action=cabinet.FileActionRequest(protocol_version=1,
                operation_id=uuid.uuid4().bytes,workspace_id='one',principal=foreign,
                copy_job=files.FileCopyJob(command=files.FileCopyJob.POLL,job_id=job_id)))
            self.expect_file_error(denied,files.FileActionError.UNAUTHORIZED)
            if generation:
                blocked=self.action(browse=files.FileBrowse(page_size=8))
                self.expect_file_error(blocked,files.FileActionError.STALE_HANDLE)
            self.assertFalse(worker_scope.exists(),'exact same-boot live scope must stop before dispatch')
            self.assertEqual(marker.read_bytes(),b'preserve exact staged evidence')
            self.assertEqual((marker.stat().st_dev,marker.stat().st_ino),
                             (marker_identity.st_dev,marker_identity.st_ino))
            with closing(sqlite3.connect(database)) as db:
                self.assertEqual(db.execute('SELECT record FROM file_copy_jobs WHERE workspace=? AND job_id=?',
                                           ('one',job_id)).fetchone()[0],original)
                self.assertIn(parent,db.execute('SELECT state,root_device,root_inode,boot_id,identity,root_path '
                                               'FROM retained_git_worker_parents').fetchall())
                self.assertEqual(db.execute('SELECT count(*) FROM file_copy_jobs WHERE workspace=?',
                                            ('one',)).fetchone()[0],1,'uncertain job must never relaunch')
            # Retire the old owner after its exact worker cleanup. Later startups
            # cannot prove same-boot parent cleanup and must quarantine only A.
            previous.close()
            self.assertNotEqual(self.broker.resources.root,old_root)
            principal=self.principal()
            actor='identity:'+principal.issuer_id.hex()+':'+principal.actor_id.hex()
            launched=self.broker.request(launch=broker.LaunchRequest(workspace='two',actor=actor,principal=principal,
                command=core.ProcessLaunchRequest(operation_id='independent-app-'+str(generation),profile='development',
                    argv=['/usr/bin/python3','-c',
                          'import time;from pathlib import Path;time.sleep(.3);Path("app-alive-'+str(generation)+'").write_text("alive")'])))
            self.assertTrue(launched.HasField('launched'),launched)
            still_uncertain=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.POLL,job_id=job_id))
            self.assertEqual(still_uncertain.file_action.copy_job.SerializeToString(deterministic=True),
                             record.terminal_receipt.SerializeToString(deterministic=True))
            if generation:
                self.expect_file_error(self.action(browse=files.FileBrowse(page_size=8)),files.FileActionError.STALE_HANDLE)
            self.broker.call('git.status',worktree='tree-two')
            def independent_action(**fields):
                return self.broker.request(file_action=cabinet.FileActionRequest(protocol_version=1,
                    operation_id=uuid.uuid4().bytes,workspace_id='two',principal=self.principal(),**fields))
            page=independent_action(browse=files.FileBrowse(page_size=64))
            self.assertTrue(page.file_action.HasField('directory'),page)
            entry=next(item for item in page.file_action.directory.entries if item.name==b'independent-source')
            started=independent_action(copy_job=files.FileCopyJob(command=files.FileCopyJob.START,
                source_handle=entry.handle,destination_directory_handle=page.file_action.directory.handle,
                destination_basename=('fresh-'+str(generation)).encode(),maximum_bytes=4096,maximum_entries=8))
            self.assertTrue(started.file_action.HasField('copy_job'),started)
            deadline=time.monotonic()+10
            while True:
                result=independent_action(copy_job=files.FileCopyJob(command=files.FileCopyJob.POLL,
                    job_id=started.file_action.copy_job.job_id))
                self.assertTrue(result.file_action.HasField('copy_job'),result)
                if result.file_action.copy_job.state not in (files.FileCopyJobReceipt.QUEUED,files.FileCopyJobReceipt.RUNNING):break
                self.assertLess(time.monotonic(),deadline,'independent copy did not complete')
                time.sleep(.01)
            self.assertEqual(result.file_action.copy_job.state,files.FileCopyJobReceipt.COMPLETE,result)
            self.assertEqual((independent/('fresh-'+str(generation))).read_bytes(),b'new bounded copy')
            self.broker.call('process.wait',id=launched.launched.process_id,timeout_seconds=5)
            self.assertEqual((independent/('app-alive-'+str(generation))).read_text(),'alive')
            self.assertFalse((self.project/'sigkill-copy').exists())
            previous=self.broker
        # No old receipt, staging evidence, or parent binding was rewritten.

    def test_file_action_copy_job_no_overwrite(self):
        self.workspace(attach_world=False)
        source=self.project/'no-overwrite-source'; source.write_bytes(b'source')
        destination=self.project/'occupied'; destination.write_bytes(b'keep existing')
        root_handle,source_handle=self._copy_source_handles('no-overwrite-source')
        result=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.START,
            source_handle=source_handle,destination_directory_handle=root_handle,
            destination_basename=b'occupied',maximum_bytes=1024,maximum_entries=8))
        self.expect_file_error(result,files.FileActionError.CONFLICT)
        self.assertEqual(destination.read_bytes(),b'keep existing')
        self.assertEqual(list((self.project.parent/('.spatial-copy-staging-'+self.project.name)).iterdir()), [])

    def test_file_action_copy_job_foreign_principal_cannot_poll_owner_job(self):
        self.workspace(attach_world=False)
        source=self.project/'principal-source'; source.write_bytes(b'owner data')
        root_handle,source_handle=self._copy_source_handles('principal-source')
        started=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.START,
            source_handle=source_handle,destination_directory_handle=root_handle,
            destination_basename=b'principal-copy',maximum_bytes=1024,maximum_entries=8))
        self.assertTrue(started.file_action.HasField('copy_job'),started)
        job_id=started.file_action.copy_job.job_id
        foreign=self.principal(); foreign.actor_id=bytes([0x77])*32
        request=cabinet.FileActionRequest(protocol_version=1,operation_id=uuid.uuid4().bytes,
            workspace_id='one',principal=foreign,copy_job=files.FileCopyJob(
                command=files.FileCopyJob.POLL,job_id=job_id))
        self.expect_file_error(self.broker.request(file_action=request),files.FileActionError.UNAUTHORIZED)
        receipt=self._poll_copy_to_terminal(job_id)
        self.assertEqual(receipt.state,files.FileCopyJobReceipt.COMPLETE,receipt)
        self.assertEqual((self.project/'principal-copy').read_bytes(),b'owner data')

    def test_file_action_copy_job_cancel_stops_worker_and_removes_stage(self):
        self.workspace(attach_world=False)
        source=self.project/'cancel-source'; source.mkdir()
        for index in range(64): (source/f'{index:03}.bin').write_bytes(bytes([index])*4096)
        root_handle,source_handle=self._copy_source_handles('cancel-source')
        started=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.START,
            source_handle=source_handle,destination_directory_handle=root_handle,
            destination_basename=b'cancel-copy',maximum_bytes=1024*1024,maximum_entries=128))
        self.assertTrue(started.file_action.HasField('copy_job'),started)
        job_id=started.file_action.copy_job.job_id
        cancelled=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.CANCEL,job_id=job_id))
        self.assertTrue(cancelled.file_action.HasField('copy_job'),cancelled)
        self.assertEqual(cancelled.file_action.copy_job.state,files.FileCopyJobReceipt.CANCELLED,
                         cancelled.file_action.copy_job)
        self.assertFalse((self.project/'cancel-copy').exists())
        with closing(sqlite3.connect(self.execution/'file-operations'/'file-operations.db')) as db:
            durable=copy_journal.FileCopyJobJournalRecord.FromString(db.execute(
                'SELECT record FROM file_copy_jobs WHERE workspace=? AND job_id=?',('one',job_id)).fetchone()[0])
        self.assertEqual(durable.state,copy_journal.FILE_COPY_JOB_JOURNAL_CANCELLED)
        self.assertTrue(durable.HasField('drained_process_scope'))
        self.assertEqual(durable.drained_process_scope,durable.process_scope)
        self.assertEqual(durable.terminal_receipt,cancelled.file_action.copy_job)

        self.assertEqual(list((self.project.parent/('.spatial-copy-staging-'+self.project.name)).iterdir()), [])
        repeated=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.POLL,job_id=job_id))
        self.assertEqual(repeated.file_action.copy_job.SerializeToString(deterministic=True),
                         cancelled.file_action.copy_job.SerializeToString(deterministic=True))

    def test_file_action_copy_job_cancel_releases_worker_capacity(self):
        self.workspace(attach_world=False)
        source=self.project/'cancel-restart-source'
        source.mkdir()
        (source/'payload.bin').write_bytes(b'cancel then start another copy')
        root_handle,source_handle=self._copy_source_handles('cancel-restart-source')
        started=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.START,
            source_handle=source_handle,destination_directory_handle=root_handle,
            destination_basename=b'cancelled-stage',maximum_bytes=1024*1024,maximum_entries=8))
        self.assertTrue(started.file_action.HasField('copy_job'),started)
        job_id=started.file_action.copy_job.job_id
        cancelled=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.CANCEL,job_id=job_id))
        self.assertTrue(cancelled.file_action.HasField('copy_job'),cancelled)
        self.assertEqual(cancelled.file_action.copy_job.state,files.FileCopyJobReceipt.CANCELLED,
                         cancelled.file_action.copy_job)
        self.assertEqual(list((self.project.parent/('.spatial-copy-staging-'+self.project.name)).iterdir()), [])

        # Cancellation must release the stopped worker reservation for a later job.
        root_handle,source_handle=self._copy_source_handles('cancel-restart-source')
        restarted=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.START,
            source_handle=source_handle,destination_directory_handle=root_handle,
            destination_basename=b'after-cancel',maximum_bytes=1024*1024,maximum_entries=8))
        self.assertTrue(restarted.file_action.HasField('copy_job'),restarted)
        receipt=self._poll_copy_to_terminal(restarted.file_action.copy_job.job_id)
        self.assertEqual(receipt.state,files.FileCopyJobReceipt.COMPLETE,receipt)
        self.assertEqual((self.project/'after-cancel'/'payload.bin').read_bytes(),
                         b'cancel then start another copy')

    def test_file_action_copy_job_revocation_stops_without_followup_request(self):
        self.workspace(attach_world=False)
        source=self.project/'revoked-source'; source.mkdir()
        payload=bytes(range(256))*64
        for index in range(4096):
            (source/f'{index:04}.bin').write_bytes(payload)
        root_handle,source_handle=self._copy_source_handles('revoked-source')
        def copy_worker_pids():
            found=set()
            for proc in Path('/proc').glob('[0-9]*'):
                try:
                    command=(proc/'cmdline').read_bytes()
                except (FileNotFoundError, PermissionError, ProcessLookupError):
                    continue
                if b'spatial-copy-worker' in command:
                    found.add(proc.name)
            return found
        prior_workers=copy_worker_pids()
        started=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.START,
            source_handle=source_handle,destination_directory_handle=root_handle,
            destination_basename=b'revoked-copy',maximum_bytes=128*1024*1024,maximum_entries=8192))
        self.assertTrue(started.file_action.HasField('copy_job'),started)
        self.assertFalse((self.project/'revoked-copy').exists(), 'copy must still be active before revocation')
        stage_root=self.project.parent/('.spatial-copy-staging-'+self.project.name)
        self.assertTrue(list(stage_root.iterdir()), 'copy staging must exist before revocation')
        deadline=time.monotonic()+3
        active_workers=set()
        while time.monotonic()<deadline:
            active_workers=copy_worker_pids()-prior_workers
            if active_workers:
                break
            time.sleep(0.01)
        self.assertTrue(active_workers, 'copy worker must be alive before authority is revoked')
        time.sleep(0.1)
        active_workers &= copy_worker_pids()
        self.assertTrue(active_workers, 'copy worker must remain active immediately before revocation')

        # Revoke while the worker is active. Do not poll or cancel the job afterward.
        self.revoke(kind=issuer.ACTOR_TARGET)
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            alive=copy_worker_pids() & active_workers
            staged=list(stage_root.iterdir())
            if not alive and not staged:
                break
            time.sleep(0.02)
        self.assertFalse(copy_worker_pids() & active_workers,
                         'revocation must stop the copy worker without another request')
        self.assertFalse((self.project/'revoked-copy').exists(),
                         'revoked copy must never publish its staged output')
        self.assertEqual(list(stage_root.iterdir()), [],
                         'revocation must remove staging without another request')
        with closing(sqlite3.connect(self.execution/'file-operations'/'file-operations.db')) as db:
            durable=copy_journal.FileCopyJobJournalRecord.FromString(db.execute(
                'SELECT record FROM file_copy_jobs WHERE workspace=? AND job_id=?',
                ('one',started.file_action.copy_job.job_id)).fetchone()[0])
        self.assertEqual(durable.state,copy_journal.FILE_COPY_JOB_JOURNAL_CANCELLED)
        self.assertEqual(durable.terminal_receipt.state,files.FileCopyJobReceipt.CANCELLED)
        self.assertTrue(durable.HasField('drained_process_scope'))
        self.assertEqual(durable.drained_process_scope,durable.process_scope)

    def test_file_action_copy_job_start_poll_complete_and_repeat(self):
        self.workspace(attach_world=False)
        source=self.project/'copy-source'
        nested=source/'nested'
        nested.mkdir(parents=True)
        (source/'root.txt').write_bytes(b'root bytes')
        (nested/'child.txt').write_bytes(b'child bytes')
        (source/'root.txt').chmod(0o640)
        nested.chmod(0o750)
        before_source=((source/'root.txt').read_bytes(),(nested/'child.txt').read_bytes(),
                       (source/'root.txt').stat().st_mode & 0o777,nested.stat().st_mode & 0o777)
        page=self.action(browse=files.FileBrowse(page_size=64))
        root_handle=page.file_action.directory.handle
        source_handle=next(item.handle for item in page.file_action.directory.entries
                           if item.name==b'copy-source')
        started=self.action(copy_job=files.FileCopyJob(
            command=files.FileCopyJob.START,source_handle=source_handle,
            destination_directory_handle=root_handle,destination_basename=b'copy-result',
            maximum_bytes=1024*1024,maximum_entries=32))
        self.assertTrue(started.file_action.HasField('copy_job'),started)
        job_id=started.file_action.copy_job.job_id
        self.assertEqual(len(job_id),16)
        deadline=time.monotonic()+30
        while True:
            polled=self.action(copy_job=files.FileCopyJob(
                command=files.FileCopyJob.POLL,job_id=job_id))
            self.assertTrue(polled.file_action.HasField('copy_job'),polled)
            receipt=polled.file_action.copy_job
            if receipt.state in (files.FileCopyJobReceipt.COMPLETE,
                                 files.FileCopyJobReceipt.FAILED,
                                 files.FileCopyJobReceipt.CANCELLED,
                                 files.FileCopyJobReceipt.CONFLICT,
                                 files.FileCopyJobReceipt.UNCERTAIN):
                break
            self.assertLess(time.monotonic(),deadline,'copy job did not reach a terminal state')
            time.sleep(.02)
        self.assertEqual(receipt.state,files.FileCopyJobReceipt.COMPLETE,receipt)
        self.assertEqual(receipt.bytes_copied,len(b'root bytes')+len(b'child bytes'))
        # The receipt counts the copied root directory, nested directory, and two files.
        self.assertEqual(receipt.entries_copied,4)
        self.assertEqual(len(receipt.content_digest),32)
        copied=self.project/'copy-result'
        self.assertEqual((copied/'root.txt').read_bytes(),b'root bytes')
        self.assertEqual((copied/'nested'/'child.txt').read_bytes(),b'child bytes')
        self.assertEqual((copied/'root.txt').stat().st_mode & 0o777,0o640)
        self.assertEqual((copied/'nested').stat().st_mode & 0o777,0o750)
        self.assertEqual(((source/'root.txt').read_bytes(),(nested/'child.txt').read_bytes(),
                          (source/'root.txt').stat().st_mode & 0o777,nested.stat().st_mode & 0o777),
                         before_source)
        repeated=self.action(copy_job=files.FileCopyJob(
            command=files.FileCopyJob.POLL,job_id=job_id))
        self.assertEqual(repeated.file_action.copy_job.SerializeToString(deterministic=True),
                         receipt.SerializeToString(deterministic=True))

    def test_file_action_copy_job_reports_bounded_live_progress(self):
        self.workspace(attach_world=False)
        source=self.project/'many-entry-source';source.mkdir()
        for index in range(12000):
            (source/f'entry-{index:05d}').write_bytes(b'payload')
        root_handle,source_handle=self._copy_source_handles('many-entry-source')
        started=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.START,
            source_handle=source_handle,destination_directory_handle=root_handle,
            destination_basename=b'progress-copy',maximum_bytes=1024*1024,
            maximum_entries=20000))
        self.assertTrue(started.file_action.HasField('copy_job'),started)
        job_id=started.file_action.copy_job.job_id
        observed=[];deadline=time.monotonic()+60
        while True:
            polled=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.POLL,job_id=job_id))
            self.assertTrue(polled.file_action.HasField('copy_job'),polled)
            receipt=polled.file_action.copy_job
            if receipt.state==files.FileCopyJobReceipt.RUNNING:
                observed.append((receipt.bytes_copied,receipt.entries_copied))
            if receipt.state not in (files.FileCopyJobReceipt.QUEUED,files.FileCopyJobReceipt.RUNNING):
                break
            self.assertLess(time.monotonic(),deadline,'copy job exceeded its absolute observation deadline')
            time.sleep(.005)
        self.assertTrue(observed,'copy completed without exposing a RUNNING snapshot')
        previous=(0,0);unchanged_positive=False
        for current in observed:
            self.assertGreaterEqual(current[0],previous[0]);self.assertGreaterEqual(current[1],previous[1])
            self.assertLessEqual(current[0],1024*1024);self.assertLessEqual(current[1],20000)
            if current==previous and current[1]>0: unchanged_positive=True
            previous=current
        self.assertTrue(unchanged_positive,'repeated POLL did not preserve an unchanged positive snapshot')
        self.assertGreater(previous[1],0,'copy completed without exposing positive live progress')
        self.assertEqual(receipt.state,files.FileCopyJobReceipt.COMPLETE,receipt)
        self.assertGreaterEqual(receipt.bytes_copied,previous[0]);self.assertGreaterEqual(receipt.entries_copied,previous[1])
        with closing(sqlite3.connect(self.execution/'file-operations'/'file-operations.db')) as db:
            durable=copy_journal.FileCopyJobJournalRecord.FromString(db.execute(
                'SELECT record FROM file_copy_jobs WHERE workspace=? AND job_id=?',('one',job_id)).fetchone()[0])
        self.assertGreaterEqual(durable.bytes_progress,previous[0])
        self.assertGreaterEqual(durable.entries_progress,previous[1])

        root_handle,source_handle=self._copy_source_handles('many-entry-source')
        cancel_start=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.START,
            source_handle=source_handle,destination_directory_handle=root_handle,
            destination_basename=b'progress-cancel',maximum_bytes=1024*1024,
            maximum_entries=20000))
        cancel_id=cancel_start.file_action.copy_job.job_id
        running=None;deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            polled=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.POLL,job_id=cancel_id))
            running=polled.file_action.copy_job
            if running.state==files.FileCopyJobReceipt.RUNNING and (running.bytes_copied or running.entries_copied):
                break
            self.assertIn(running.state,(files.FileCopyJobReceipt.QUEUED,files.FileCopyJobReceipt.RUNNING),running)
            time.sleep(.005)
        self.assertTrue(running and running.state==files.FileCopyJobReceipt.RUNNING and running.entries_copied>0,
                        'cancellation case never reached live progress')
        cancelled=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.CANCEL,job_id=cancel_id))
        self.assertEqual(cancelled.file_action.copy_job.state,files.FileCopyJobReceipt.CANCELLED)
        self.assertGreaterEqual(cancelled.file_action.copy_job.bytes_copied,running.bytes_copied)
        self.assertGreaterEqual(cancelled.file_action.copy_job.entries_copied,running.entries_copied)
        with closing(sqlite3.connect(self.execution/'file-operations'/'file-operations.db')) as db:
            durable_cancel=copy_journal.FileCopyJobJournalRecord.FromString(db.execute(
                'SELECT record FROM file_copy_jobs WHERE workspace=? AND job_id=?',('one',cancel_id)).fetchone()[0])
        self.assertEqual(durable_cancel.terminal_receipt,cancelled.file_action.copy_job)
        self.assertEqual(durable_cancel.bytes_progress,cancelled.file_action.copy_job.bytes_copied)
        self.assertEqual(durable_cancel.entries_progress,cancelled.file_action.copy_job.entries_copied)
        self.assertFalse((self.project/'progress-cancel').exists())

    def test_file_action_copy_job_absolute_deadline_drains_without_poll(self):
        old=os.environ.get('SPATIAL_TEST_COPY_DEADLINE_MS')
        os.environ['SPATIAL_TEST_COPY_DEADLINE_MS']='50'
        def restore_deadline():
            if old is None: os.environ.pop('SPATIAL_TEST_COPY_DEADLINE_MS',None)
            else: os.environ['SPATIAL_TEST_COPY_DEADLINE_MS']=old
        self.addCleanup(restore_deadline)
        self.workspace(pair=True,attach_world=False)
        source=self.project/'deadline-source';source.mkdir()
        for index in range(12000): (source/f'entry-{index:05d}').write_bytes(b'payload')
        (self.project_two/'independent-source.txt').write_bytes(b'independent workspace remains usable')
        root_handle,source_handle=self._copy_source_handles('deadline-source')
        started=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.START,
            source_handle=source_handle,destination_directory_handle=root_handle,
            destination_basename=b'deadline-copy',maximum_bytes=1024*1024,
            maximum_entries=20000))
        job_id=started.file_action.copy_job.job_id
        self.assertIsNotNone(self.broker.resources,'deadline regression needs the production process-scope owner')
        database=self.execution/'file-operations'/'file-operations.db'
        with closing(sqlite3.connect(database)) as db:
            initial=copy_journal.FileCopyJobJournalRecord.FromString(db.execute(
                'SELECT record FROM file_copy_jobs WHERE workspace=? AND job_id=?',('one',job_id)).fetchone()[0])
        self.assertTrue(initial.HasField('process_scope'))
        worker_scope=self.broker.resources.root/'spatial-git-workers'/initial.process_scope.scope_name
        deadline=time.monotonic()+10
        terminal_record=None
        while time.monotonic()<deadline:
            with closing(sqlite3.connect(database)) as db:
                row=db.execute('SELECT record FROM file_copy_jobs WHERE workspace=? AND job_id=?',
                               ('one',job_id)).fetchone()
            self.assertIsNotNone(row,'copy job journal row disappeared')
            current=copy_journal.FileCopyJobJournalRecord.FromString(row[0])
            if current.state==copy_journal.FILE_COPY_JOB_JOURNAL_FAILED:
                terminal_record=current
                break
            time.sleep(.01)
        self.assertIsNotNone(terminal_record,'main-loop scheduler did not persist terminal failure without POLL')
        self.assertEqual(terminal_record.terminal_receipt.state,files.FileCopyJobReceipt.FAILED)
        self.assertEqual(terminal_record.terminal_receipt.failure_code,files.FileActionError.UNAVAILABLE)
        self.assertEqual(terminal_record.drained_process_scope,terminal_record.process_scope)
        self.assertFalse(worker_scope.exists(),'exact journaled ProcessScope must be removed before the first POLL')
        stage_identity=terminal_record.staging_root
        stage_roots=[]
        for base in (self.project,self.project.parent):
            with os.scandir(base) as entries:
                for entry in entries:
                    try:
                        if not entry.is_dir(follow_symlinks=False): continue
                        info=entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if info.st_dev==stage_identity.device and info.st_ino==stage_identity.inode:
                        stage_roots.append(Path(entry.path))
        self.assertEqual(len(stage_roots),1,'journaled staging-root identity must resolve uniquely')
        self.assertEqual(list(stage_roots[0].iterdir()),[],
                         'identity-matched staging must be removed before the first POLL')
        self.assertFalse((self.project/'deadline-copy').exists(),'expired job must not publish')
        terminal=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.POLL,job_id=job_id))
        self.assertEqual(terminal.file_action.copy_job,terminal_record.terminal_receipt)
        self.assertEqual(terminal.file_action.copy_job.state,files.FileCopyJobReceipt.FAILED)
        self.assertEqual(terminal.file_action.copy_job.failure_code,files.FileActionError.UNAVAILABLE)
        self.assertIn('deadline expired',terminal.file_action.copy_job.detail)
        self.assertFalse((self.project/'deadline-copy').exists())

        self.assertEqual(self.broker.call('git.status',worktree='tree-two')['branch'],'branch-two')
        independent_page=self.broker.request(file_action=cabinet.FileActionRequest(
            protocol_version=1,operation_id=uuid.uuid4().bytes,workspace_id='two',
            principal=self.principal(),browse=files.FileBrowse(page_size=16)))
        self.assertTrue(independent_page.file_action.HasField('directory'),independent_page)
        independent_root=independent_page.file_action.directory.handle
        independent_source=next(entry.handle for entry in independent_page.file_action.directory.entries
                                if entry.name==b'independent-source.txt')
        independent_started=self.broker.request(file_action=cabinet.FileActionRequest(
            protocol_version=1,operation_id=uuid.uuid4().bytes,workspace_id='two',
            principal=self.principal(),copy_job=files.FileCopyJob(
                command=files.FileCopyJob.START,source_handle=independent_source,
                destination_directory_handle=independent_root,destination_basename=b'independent-copy.txt',
                maximum_bytes=1024,maximum_entries=8)))
        independent_job=independent_started.file_action.copy_job.job_id
        independent_deadline=time.monotonic()+10
        while True:
            copied=self.broker.request(file_action=cabinet.FileActionRequest(
                protocol_version=1,operation_id=uuid.uuid4().bytes,workspace_id='two',
                principal=self.principal(),copy_job=files.FileCopyJob(
                    command=files.FileCopyJob.POLL,job_id=independent_job)))
            copy_receipt=copied.file_action.copy_job
            if copy_receipt.state not in (files.FileCopyJobReceipt.QUEUED,
                                          files.FileCopyJobReceipt.RUNNING): break
            self.assertLess(time.monotonic(),independent_deadline,'independent workspace copy stalled')
            time.sleep(.01)
        self.assertEqual(copy_receipt.state,files.FileCopyJobReceipt.COMPLETE,copy_receipt)
        self.assertEqual((self.project_two/'independent-copy.txt').read_bytes(),
                         b'independent workspace remains usable')

    def test_file_action_copy_job_entry_limit_counts_root_and_children(self):
        self.workspace(attach_world=False)
        source=self.project/'bounded-source'
        nested=source/'nested'
        nested.mkdir(parents=True)
        (source/'root.txt').write_bytes(b'root bytes')
        (nested/'child.txt').write_bytes(b'child bytes')
        page=self.action(browse=files.FileBrowse(page_size=64))
        root_handle=page.file_action.directory.handle
        source_handle=next(item.handle for item in page.file_action.directory.entries
                           if item.name==b'bounded-source')

        denied=self.action(copy_job=files.FileCopyJob(
            command=files.FileCopyJob.START,source_handle=source_handle,
            destination_directory_handle=root_handle,destination_basename=b'too-small',
            maximum_bytes=1024*1024,maximum_entries=3))
        self.assertTrue(denied.file_action.HasField('copy_job'),denied)
        denied_id=denied.file_action.copy_job.job_id
        deadline=time.monotonic()+30
        while True:
            failed=self.action(copy_job=files.FileCopyJob(
                command=files.FileCopyJob.POLL,job_id=denied_id))
            self.assertTrue(failed.file_action.HasField('copy_job'),failed)
            receipt=failed.file_action.copy_job
            if receipt.state in (files.FileCopyJobReceipt.FAILED,
                                 files.FileCopyJobReceipt.CANCELLED,
                                 files.FileCopyJobReceipt.CONFLICT,
                                 files.FileCopyJobReceipt.UNCERTAIN,
                                 files.FileCopyJobReceipt.COMPLETE):
                break
            self.assertLess(time.monotonic(),deadline,'entry-limit failure did not reach a terminal state')
            time.sleep(.02)
        self.assertEqual(receipt.state,files.FileCopyJobReceipt.FAILED,receipt)
        self.assertEqual(receipt.failure_code,files.FileActionError.EXHAUSTED,receipt)
        self.assertFalse((self.project/'too-small').exists())
        self.assertEqual(list((self.project.parent/('.spatial-copy-staging-'+self.project.name)).iterdir()), [])
        repeated=self.action(copy_job=files.FileCopyJob(
            command=files.FileCopyJob.POLL,job_id=denied_id))
        self.assertEqual(repeated.file_action.copy_job.SerializeToString(deterministic=True),
                         receipt.SerializeToString(deterministic=True))

        # The prior stage changed the project-root generation. Refresh both handles.
        root_handle,source_handle=self._copy_source_handles('bounded-source')
        accepted=self.action(copy_job=files.FileCopyJob(
            command=files.FileCopyJob.START,source_handle=source_handle,
            destination_directory_handle=root_handle,destination_basename=b'exact-limit',
            maximum_bytes=1024*1024,maximum_entries=4))
        self.assertTrue(accepted.file_action.HasField('copy_job'),accepted)
        accepted_id=accepted.file_action.copy_job.job_id
        while True:
            completed=self.action(copy_job=files.FileCopyJob(
                command=files.FileCopyJob.POLL,job_id=accepted_id))
            self.assertTrue(completed.file_action.HasField('copy_job'),completed)
            result=completed.file_action.copy_job
            if result.state in (files.FileCopyJobReceipt.COMPLETE,
                                files.FileCopyJobReceipt.FAILED,
                                files.FileCopyJobReceipt.CANCELLED,
                                files.FileCopyJobReceipt.CONFLICT,
                                files.FileCopyJobReceipt.UNCERTAIN):
                break
            self.assertLess(time.monotonic(),deadline,'exact entry limit did not reach a terminal state')
            time.sleep(.02)
        self.assertEqual(result.state,files.FileCopyJobReceipt.COMPLETE,result)
        self.assertEqual(result.entries_copied,4)
        self.assertEqual((self.project/'exact-limit'/'nested'/'child.txt').read_bytes(),b'child bytes')

    def test_file_action_copy_job_rejects_destination_inside_source(self):
        self.workspace(attach_world=False)
        source=self.project/'source-tree'
        inside=source/'inside'
        inside.mkdir(parents=True)
        (source/'file.txt').write_bytes(b'unchanged')
        page=self.action(browse=files.FileBrowse(page_size=64))
        root_handle=page.file_action.directory.handle
        source_handle=next(item.handle for item in page.file_action.directory.entries
                           if item.name==b'source-tree')
        children=self.action(browse=files.FileBrowse(directory_handle=source_handle,page_size=64))
        inside_handle=next(item.handle for item in children.file_action.directory.entries
                           if item.name==b'inside')
        result=self.action(copy_job=files.FileCopyJob(
            command=files.FileCopyJob.START,source_handle=source_handle,
            destination_directory_handle=inside_handle,destination_basename=b'recursive',
            maximum_bytes=1024*1024,maximum_entries=32))
        self.expect_file_error(result,files.FileActionError.INVALID_ARGUMENT)
        self.assertEqual((source/'file.txt').read_bytes(),b'unchanged')
        self.assertEqual(list(inside.iterdir()),[])

    def test_file_action_invalid_and_oversized_envelopes_have_no_effect(self):
        self.workspace(attach_world=False)
        bad_id = cabinet.FileActionRequest(
            protocol_version=1, operation_id=b'short', workspace_id='one',
            principal=self.principal(), create=files.FileCreate(basename=b'bad.txt'))
        self.expect_file_error(self.broker.request(file_action=bad_id), files.FileActionError.INVALID_ARGUMENT)
        self.assertFalse((self.project/'bad.txt').exists())

        # The transport contract rejects an excessive length before reading a body.
        # Send the raw prefix because the normal client frame() helper forbids it.
        self.broker.stream.sendall(struct.pack('!I', MAX_FRAME + 1))
        returncode = self.broker.proc.wait(timeout=5)
        self.assertNotEqual(returncode, 0, 'Broker accepted an over-limit frame length')
        self.assertFalse((self.project/'large.txt').exists())
        self.assertFalse((self.project/'bad.txt').exists())

    def test_file_action_revoked_principal_is_denied_on_next_action(self):
        def revoke_actor():
            self.owner_peer = self.connect()
            self.revoke(kind=issuer.ACTOR_TARGET)
        relay = world_authority.RelayingIssuer(self, revoke_actor)
        self.workspace(issuer_port=relay.port)
        first = self.action(browse=files.FileBrowse(page_size=32))
        self.assertTrue(first.file_action.HasField('directory'), first)
        relay.armed.set()
        revoked = self.action(browse=files.FileBrowse(page_size=32))
        self.expect_file_error(revoked, files.FileActionError.UNAUTHORIZED)
        self.assertTrue(relay.delivered.is_set())

    def test_file_action_missing_root_returns_stale(self):
        self.workspace(attach_world=False)
        displaced = self.project.with_name(self.project.name+'.missing-test')
        self.project.rename(displaced)
        try:
            self.expect_file_error(self.action(browse=files.FileBrowse(page_size=32)),
                                   files.FileActionError.STALE_HANDLE)
        finally:
            displaced.rename(self.project)

    def test_file_action_replaced_root_fails_stale_without_rebinding(self):
        self.workspace(attach_world=False)
        (self.project/'known.txt').write_bytes(b'known')
        first = self.action(browse=files.FileBrowse(page_size=32))
        self.assertTrue(first.file_action.HasField('directory'), first)
        original = self.project.with_name(self.project.name+'.pinned-test')
        self.project.rename(original)
        entry = next(item for item in first.file_action.directory.entries if item.name == b'known.txt') if any(
            item.name == b'known.txt' for item in first.file_action.directory.entries) else None
        try:
            self.project.symlink_to(original, target_is_directory=True)
            second = self.action(browse=files.FileBrowse(page_size=32))
            self.expect_file_error(second, files.FileActionError.STALE_HANDLE)
            if entry:
                stale = self.action(read=files.FileRead(file_handle=entry.handle, maximum_bytes=1))
                self.expect_file_error(stale, files.FileActionError.STALE_HANDLE)
        finally:
            if self.project.is_symlink():
                self.project.unlink()
            original.rename(self.project)


    def test_file_action_handle_from_old_engine_incarnation_is_stale(self):
        from test_workspace_authority import BrokerProcess
        self.workspace(attach_world=False)
        (self.project/'incarnation.txt').write_bytes(b'incarnation')
        page=self.action(browse=files.FileBrowse(page_size=64))
        entry=next(item for item in page.file_action.directory.entries if item.name==b'incarnation.txt')
        self.broker.close()
        self.broker=BrokerProcess(self,self.execution,self.authority_config)
        stale=self.action(read=files.FileRead(file_handle=entry.handle,maximum_bytes=32))
        self.expect_file_error(stale,files.FileActionError.STALE_HANDLE)

    def test_file_action_workspace_policy_denies_granted_right(self):
        self.workspace(policy=['workspace.select'], roots=lambda: [self.workspace_terms()])
        request = cabinet.FileActionRequest(
            protocol_version=1, operation_id=uuid.uuid4().bytes, workspace_id='one',
            principal=self.principal(), browse=files.FileBrowse(page_size=32))
        self.expect_file_error(self.broker.request(file_action=request), files.FileActionError.UNAUTHORIZED)

if __name__ == '__main__':
    import unittest
    unittest.main(verbosity=2)
