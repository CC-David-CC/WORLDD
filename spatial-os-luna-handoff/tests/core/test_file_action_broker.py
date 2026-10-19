from pathlib import Path
from contextlib import closing
import os
import subprocess
import select
import signal
import stat
import sqlite3
import struct
import time
import unittest
import uuid
import sys
from unittest.mock import patch

import test_workspace_copy_engine as engine
import test_workspace_copy_faults as copy_faults
sys.path.insert(0, str(engine.SOURCE / 'tools'))
from copy_fixture_image_cleanup import cleanup_file_action_group, run_after_capacity_preflight
sys.path.insert(0, str(engine.SOURCE / 'tests/native'))
from supervised_copy_broker_recovery import (
    SupervisedCopyBrokerRecovery, wait_for_active_generation)
import broker_pb2 as broker
import grants_pb2 as grant
import issuer_pb2 as issuer
import launch_pb2 as launch
import cabinet_pb2 as cabinet
import file_actions_pb2 as files
import file_copy_job_journal_pb2 as copy_journal
import core_pb2 as core
from harness.wire import MAX_FRAME
from harness.copy_progress_timing import COPY_JOB_OBSERVATION_SECONDS, poll_copy_job_before_deadline
from test_workspace_authority import BrokerProcess, WorkspaceAuthorityTests, PROJECT, POLICY, world_authority

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

    def test_file_action_refuses_unrecovered_legacy_sibling_staging_layout(self):
        self.workspace(attach_world=False)
        legacy = self.project.parent/('.spatial-copy-staging-'+self.project.name)
        legacy.mkdir(mode=0o700)
        marker = legacy/'preserved'
        marker.write_bytes(b'keep for explicit recovery')
        reply = self.action(browse=files.FileBrowse(page_size=64))
        self.expect_file_error(reply, files.FileActionError.UNAVAILABLE)
        self.assertEqual(marker.read_bytes(), b'keep for explicit recovery')
        self.assertFalse((self.project/'.spatial-copy-staging').exists())

    def test_file_action_copy_staging_root_is_hidden_and_reserved(self):
        self.workspace(attach_world=False)
        stage_root = self.project/'.spatial-copy-staging'
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
            expected_generation=trashed.file_action.trash.generation))
        self.expect_file_error(restored, files.FileActionError.INVALID_ARGUMENT)
        self.assertTrue(stage_root.is_dir())
        self.assertEqual(list(stage_root.iterdir()), [])

    def test_file_action_namespace_legacy_digest_replay_conflicts_without_effect(self):
        self.workspace(attach_world=False)
        self.action(browse=files.FileBrowse(page_size=16))
        request = cabinet.FileActionRequest(
            protocol_version=1, operation_id=uuid.uuid4().bytes, workspace_id='one',
            principal=self.principal(),
            create=files.FileCreate(basename=b'legacy-replay.txt', content=b'must not appear'))
        trusted = cabinet.FileActionRequest(); trusted.CopyFrom(request)
        trusted.ClearField('authority_components'); trusted.authority_components.append(b'tree-one')
        legacy_digest = __import__('hashlib').sha256(
            trusted.SerializeToString(deterministic=True)).digest()
        root = os.stat(self.project)
        principal = request.principal.issuer_id + request.principal.actor_id
        legacy_reply = files.FileActionReply(protocol_version=1, operation_id=request.operation_id)
        legacy_reply.object.size = 22
        database = self.execution/'file-operations'/'file-operations.db'
        with closing(sqlite3.connect(database)) as db:
            db.execute('INSERT INTO file_operations '
                '(workspace,operation,principal,worktree,device,inode,exact_right,digest,engine,state,outcome) '
                'VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                ('one', request.operation_id, principal, 'tree-one', root.st_dev, root.st_ino,
                 grant.FILE_CREATE, legacy_digest, bytes(16), 2,
                 legacy_reply.SerializeToString(deterministic=True)))
            db.commit()
            before = db.execute('SELECT * FROM file_operations WHERE workspace=? AND operation=?',
                ('one', request.operation_id)).fetchone()
            before_raw = tuple(bytes(value) if isinstance(value, memoryview) else value
                for value in before)
        reply = self.broker.request(file_action=request)
        self.expect_file_error(reply, files.FileActionError.CONFLICT)
        self.assertFalse((self.project/'legacy-replay.txt').exists())
        with closing(sqlite3.connect(database)) as db:
            after = db.execute('SELECT * FROM file_operations WHERE workspace=? AND operation=?',
                ('one', request.operation_id)).fetchone()
        after_raw = tuple(bytes(value) if isinstance(value, memoryview) else value
            for value in after)
        self.assertEqual(after_raw, before_raw, 'legacy completed row must remain unchanged')

    def test_file_action_namespace_copy_admin_destination_rejected_before_effect(self):
        self.workspace(attach_world=False)
        (self.project/'namespace-source.txt').write_bytes(b'copy control')
        root_handle, source_handle = self._copy_source_handles('namespace-source.txt')
        operation = uuid.uuid4().bytes
        request = cabinet.FileActionRequest(
            protocol_version=1, operation_id=operation, workspace_id='one',
            principal=self.principal(), copy_job=files.FileCopyJob(
                command=files.FileCopyJob.START, source_handle=source_handle,
                destination_directory_handle=root_handle, destination_basename=b'.git',
                maximum_bytes=1024, maximum_entries=8))
        denied = self.broker.request(file_action=request)
        self.expect_file_error(denied, files.FileActionError.INVALID_ARGUMENT)
        stage = self.project/'.spatial-copy-staging'
        self.assertTrue(stage.is_dir())
        self.assertEqual(list(stage.iterdir()), [])
        database = self.execution/'file-operations'/'file-operations.db'
        with closing(sqlite3.connect(database)) as db:
            rows = db.execute('SELECT count(*) FROM file_copy_jobs '
                'WHERE workspace=? AND initiating_operation=?', ('one', operation)).fetchone()[0]
        self.assertEqual(rows, 0, 'rejected admin destination must not create a durable copy job')
        root_handle, source_handle = self._copy_source_handles('namespace-source.txt')
        control = self.action(copy_job=files.FileCopyJob(
            command=files.FileCopyJob.START, source_handle=source_handle,
            destination_directory_handle=root_handle, destination_basename=b'namespace-control.txt',
            maximum_bytes=1024, maximum_entries=8))
        self.assertTrue(control.file_action.HasField('copy_job'), control)
        receipt = self._poll_copy_to_terminal(control.file_action.copy_job.job_id)
        self.assertEqual(receipt.state, files.FileCopyJobReceipt.COMPLETE, receipt)
        self.assertEqual((self.project/'namespace-control.txt').read_bytes(), b'copy control')

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
        staging = self.project/'.spatial-copy-staging'
        before_public = {path.name for path in self.project.iterdir()
                         if path.name != '.spatial-copy-staging'}
        before_stage = None
        if staging.exists() or staging.is_symlink():
            info = staging.lstat()
            self.assertTrue(stat.S_ISDIR(info.st_mode), 'private staging root must be a real directory')
            self.assertFalse(stat.S_ISLNK(info.st_mode), 'private staging root must not be a symlink')
            before_stage = (info.st_dev, info.st_ino)
            self.assertEqual(list(staging.iterdir()), [], 'existing private staging root must be empty')
        reply=self.action(copy_job=files.FileCopyJob(
            command=files.FileCopyJob.START,maximum_bytes=1024,maximum_entries=8))
        self.expect_file_error(reply,files.FileActionError.INVALID_ARGUMENT)
        after = list(self.project.iterdir())
        self.assertEqual({path.name for path in after if path.name != '.spatial-copy-staging'},
                         before_public, 'invalid root-source copy must not publish a project entry')
        stage_info = staging.lstat()
        self.assertTrue(stat.S_ISDIR(stage_info.st_mode), 'private staging root must be a real directory')
        self.assertFalse(stat.S_ISLNK(stage_info.st_mode), 'private staging root must not be a symlink')
        self.assertEqual(stage_info.st_mode & 0o777, 0o700)
        self.assertEqual(list(staging.iterdir()), [], 'invalid root-source copy must leave private staging empty')
        if before_stage is not None:
            info = staging.lstat()
            self.assertEqual((info.st_dev, info.st_ino), before_stage,
                             'existing private staging identity must remain unchanged')

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

    def test_storage_owner_authenticated_btrfs_copy_and_quota_rejection(self):
        storage_volume=os.environ.get('SPATIAL_TEST_STORAGE_VOLUME')
        launch_config=os.environ.get('SPATIAL_TEST_STORAGE_LAUNCH_CONFIG')
        quota=os.environ.get('SPATIAL_TEST_STORAGE_COPY_QUOTA_BYTES')
        self.assertTrue(storage_volume, 'SETUP FAILURE: SPATIAL_TEST_STORAGE_VOLUME is required')
        self.assertTrue(launch_config, 'SETUP FAILURE: SPATIAL_TEST_STORAGE_LAUNCH_CONFIG is required')
        self.assertEqual(quota, str(96*1024*1024), 'SETUP FAILURE: expected 96 MiB StorageOwner quota')
        self.execution_override=Path(storage_volume).resolve(strict=True)
        self.broker_factory = lambda fixture, state, config=None: BrokerProcess(
            fixture, state, config, storage_options=(self.execution_override, Path(launch_config)))
        self.assertTrue(self.execution_override.is_dir(), 'SETUP FAILURE: storage volume is not a directory')
        self.workspace(attach_world=False)
        self.assertEqual(self.execution, self.execution_override)
        self.assertTrue(self.project.is_relative_to(self.execution),
            'registered PROJECT must be created below the provisioned StorageOwner volume')
        def rootid(path):
            result=__import__('subprocess').run(['btrfs','inspect-internal','rootid',str(path)],
                check=True,capture_output=True,text=True,timeout=10)
            return int(result.stdout.strip())
        volume_rootid=rootid(self.execution)
        project_rootid=rootid(self.project)
        self.assertNotEqual(project_rootid, volume_rootid,
            'StorageOwner PROJECT must be a registered Btrfs subvolume below the volume root')
        page=self.action(browse=files.FileBrowse(page_size=64))
        self.assertTrue(page.file_action.HasField('directory'), page)
        stage_root=self.project/'.spatial-copy-staging'
        self.assertTrue(stage_root.is_dir())
        self.assertEqual(rootid(stage_root), project_rootid,
            'private staging root must share the registered PROJECT Btrfs subvolume')
        (self.project/'authorized-source.bin').write_bytes(b'authorized btrfs copy')
        root_handle,source_handle=self._copy_source_handles('authorized-source.bin')
        started=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.START,
            source_handle=source_handle,destination_directory_handle=root_handle,
            destination_basename=b'authorized-small-copy',maximum_bytes=1024*1024,
            maximum_entries=8))
        self.assertTrue(started.file_action.HasField('copy_job'),started)
        receipt=self._poll_copy_to_terminal(started.file_action.copy_job.job_id,timeout=60)
        self.assertEqual(receipt.state,files.FileCopyJobReceipt.COMPLETE,receipt)
        self.assertEqual((self.project/'authorized-small-copy').read_bytes(),b'authorized btrfs copy')
        self.assertEqual(list(stage_root.iterdir()),[])

        source=self.project/'owner-quota-source.bin'
        with source.open('wb') as stream:
            for _ in range(64):
                stream.write(os.urandom(1024*1024))
            stream.flush(); os.fsync(stream.fileno())
        source.chmod(0o600)
        root_handle,source_handle=self._copy_source_handles(source.name)
        limited=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.START,
            source_handle=source_handle,destination_directory_handle=root_handle,
            destination_basename=b'quota-must-reject',maximum_bytes=128*1024*1024,
            maximum_entries=8))
        self.assertTrue(limited.file_action.HasField('copy_job'),limited)
        rejected=self._poll_copy_to_terminal(limited.file_action.copy_job.job_id,timeout=90)
        self.assertEqual(rejected.state,files.FileCopyJobReceipt.FAILED,rejected)
        self.assertEqual(rejected.failure_code,files.FileActionError.EXHAUSTED,rejected)
        self.assertFalse((self.project/'quota-must-reject').exists())
        self.assertEqual(list(stage_root.iterdir()),[])

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
        self.assertEqual(list((self.project/'.spatial-copy-staging').iterdir()), [])

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

        self.assertEqual(list((self.project/'.spatial-copy-staging').iterdir()), [])
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
        self.assertEqual(list((self.project/'.spatial-copy-staging').iterdir()), [])

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
        self._assert_copy_job_revocation_stops_without_followup_request()

    def test_file_action_copy_job_revocation_reconnects_after_owner_idle_timeout(self):
        self.workspace(attach_world=False)
        # Demonstrate transport expiry before the worker starts.
        time.sleep(self.config.idle_timeout_ms / 1000 + .2)
        self.closed(self.owner_peer)
        self.owner_peer.close()
        self._assert_copy_job_revocation_stops_without_followup_request(workspace_ready=True)

    def _assert_copy_job_revocation_stops_without_followup_request(self, workspace_ready=False):
        if not workspace_ready:
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
        stage_root=self.project/'.spatial-copy-staging'
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
        observed=[];deadline=time.monotonic()+COPY_JOB_OBSERVATION_SECONDS
        while True:
            polled=poll_copy_job_before_deadline(
                lambda: self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.POLL,job_id=job_id)),
                deadline)
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
        running=None;deadline=time.monotonic()+COPY_JOB_OBSERVATION_SECONDS
        while time.monotonic()<deadline:
            polled=poll_copy_job_before_deadline(
                lambda: self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.POLL,job_id=cancel_id)),
                deadline)
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
        self.assertEqual(list((self.project/'.spatial-copy-staging').iterdir()), [])
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


def load_tests(loader, tests, pattern):
    selected = unittest.TestSuite()
    recovery_names = {
        'test_file_action_copy_job_sigkill_restart_reconciles_retained_scope',
        'test_file_action_uncertain_generation_keeps_independent_jobs_available',
        'test_file_action_recovered_prepared_running_cancels_without_publication',
        'test_file_action_recovered_prepared_cancel_requested_keeps_identity',
        'test_file_action_recovered_published_prepared_syncs_before_complete',
        'test_file_action_recovered_publication_sync_failure_stays_uncertain',
        'test_file_action_missing_scope_without_proof_retains_uncertainty',
        'test_file_action_replaced_scope_without_proof_preserves_evidence',
        'test_file_action_replaced_scope_with_old_proof_stays_uncertain',
        'test_file_action_active_replaced_parent_refuses_startup',
        'test_file_action_completed_worker_drain_proof_survives_prepublication_crash',
        'test_file_action_completed_worker_proof_reconciles_published_crash',
    }
    # Preserve the whole ordinary class. CTest -k selects both its file_action_
    # cases and the separately registered StorageOwner quota case.
    for case in loader.loadTestsFromTestCase(FileActionBrokerRegressionTests):
        selected.addTest(case)
    for case in loader.loadTestsFromTestCase(SupervisedLegacyFileActionRecoveryTests):
        if case.id().rsplit('.', 1)[-1] in recovery_names:
            selected.addTest(case)
    return selected


class _LegacyFileActionRecoveryHelpers:
    def action(self, **fields):
        request = cabinet.FileActionRequest(protocol_version=1,
            operation_id=os.urandom(16), workspace_id='one', principal=self.principal(), **fields)
        return self.broker.request(file_action=request)

    def expect_file_error(self, reply, code):
        self.assertTrue(reply.HasField('file_action'), reply)
        self.assertTrue(reply.file_action.HasField('error'), reply.file_action)
        self.assertEqual(reply.file_action.error.code, code, reply.file_action)

    def _copy_source_handles(self, name):
        page=self.action(browse=files.FileBrowse(page_size=64))
        root_handle=page.file_action.directory.handle
        source_handle=next(item.handle for item in page.file_action.directory.entries if item.name==name.encode())
        return root_handle,source_handle

    def _poll_copy_to_terminal(self, job_id, timeout=COPY_JOB_OBSERVATION_SECONDS):
        deadline=time.monotonic()+timeout
        while True:
            reply=poll_copy_job_before_deadline(
                lambda:self.action(copy_job=files.FileCopyJob(
                    command=files.FileCopyJob.POLL,job_id=job_id)),deadline)
            self.assertTrue(reply.file_action.HasField('copy_job'),reply)
            receipt=reply.file_action.copy_job
            if receipt.state in (files.FileCopyJobReceipt.COMPLETE, files.FileCopyJobReceipt.FAILED,
                                 files.FileCopyJobReceipt.CANCELLED, files.FileCopyJobReceipt.CONFLICT,
                                 files.FileCopyJobReceipt.UNCERTAIN):
                return receipt
            time.sleep(.02)


class SupervisedLegacyFileActionRecoveryTests(_LegacyFileActionRecoveryHelpers, engine.WorkspaceCopyEngineTests):
    """Legacy crash oracles on a real supervised generation.

    Retirement removes the old generation root before same-journal recovery.
    Missing-scope retains absence; replacement cases create a new foreign cgroup
    after retirement and assert its distinct identity is preserved.
    """
    # Explicit headroom covers the 64 MiB source, a same-sized staged/output copy, and metadata.
    # Pair tests transiently reserve 224 + 160 MiB during repository import, then
    # bind workspace two down to 64 MiB. Final reservations total 288 MiB.
    SOURCE_POLICY_BYTES_BY_SCENARIO = {
        **engine.WorkspaceCopyEngineTests.SOURCE_POLICY_BYTES_BY_SCENARIO,
        'test_file_action_copy_job_sigkill_restart_reconciles_retained_scope': dict(
            actor_state_bytes=32*2**20, workspace_bytes=224*2**20),
        'test_file_action_uncertain_generation_keeps_independent_jobs_available': dict(
            actor_state_bytes=32*2**20, workspace_bytes=160*2**20,
            source_workspace_one_bytes=224*2**20, source_workspace_two_bytes=64*2**20),
        'test_file_action_recovered_prepared_running_cancels_without_publication': dict(
            actor_state_bytes=32*2**20, workspace_bytes=224*2**20),
        'test_file_action_recovered_prepared_cancel_requested_keeps_identity': dict(
            actor_state_bytes=32*2**20, workspace_bytes=224*2**20),
        'test_file_action_recovered_published_prepared_syncs_before_complete': dict(
            actor_state_bytes=32*2**20, workspace_bytes=224*2**20),
        'test_file_action_recovered_publication_sync_failure_stays_uncertain': dict(
            actor_state_bytes=32*2**20, workspace_bytes=224*2**20),
        'test_file_action_missing_scope_without_proof_retains_uncertainty': dict(
            actor_state_bytes=32*2**20, workspace_bytes=160*2**20,
            source_workspace_one_bytes=224*2**20, source_workspace_two_bytes=64*2**20),
        'test_file_action_replaced_scope_without_proof_preserves_evidence': dict(
            actor_state_bytes=32*2**20, workspace_bytes=160*2**20,
            source_workspace_one_bytes=224*2**20, source_workspace_two_bytes=64*2**20),
        'test_file_action_replaced_scope_with_old_proof_stays_uncertain': dict(
            actor_state_bytes=32*2**20, workspace_bytes=160*2**20,
            source_workspace_one_bytes=224*2**20, source_workspace_two_bytes=64*2**20),
        'test_file_action_active_replaced_parent_refuses_startup': dict(
            actor_state_bytes=32*2**20, workspace_bytes=160*2**20,
            source_workspace_one_bytes=224*2**20, source_workspace_two_bytes=64*2**20),
    }

    def provision(self,operation):
        source_policy=self.SOURCE_POLICY_BYTES_BY_SCENARIO.get(self._testMethodName,{})
        one_bytes=source_policy.get('source_workspace_one_bytes')
        two_bytes=source_policy.get('source_workspace_two_bytes')
        if operation=='create':
            import json
            definition=engine.broker.StorageVolumeDefinition()
            engine.text_format.Parse(self.definition_path.read_text(),definition)
            owner=definition.owner_policy
            if one_bytes is not None:
                for workspace,budget in (('one',one_bytes),('two',two_bytes)):
                    selected=owner.workspaces.add(workspace=workspace)
                    selected.total_bytes=budget
                    selected.actor_state_default_bytes=owner.default_policy.actor_state_default_bytes
                    for profile in owner.default_policy.profiles:
                        selected.profiles.add().CopyFrom(profile)
            evidence=self.copy_evidence/'volume.json'
            record=json.loads(evidence.read_text())
            record['definition_hex']=definition.SerializeToString(deterministic=True).hex()
            record['capacity_bytes']=definition.owner_policy.volume_bytes-definition.owner_policy.control_plane_bytes-definition.owner_policy.filesystem_reserve_bytes
            record['source_workspace_ceiling_bytes']=one_bytes or owner.default_policy.total_bytes
            record['repository_initial_reservation_bytes']=owner.default_policy.total_bytes
            reservations={'one':one_bytes or owner.default_policy.total_bytes}
            if two_bytes is not None:reservations['two']=two_bytes
            record['workspace_reservations_bytes']=reservations
            evidence.write_text(json.dumps(record,indent=2)+'\n')
            self.definition_path.write_text(engine.text_format.MessageToString(definition))
        return super().provision(operation)

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

    def test_file_action_active_replaced_parent_refuses_startup(self):
        self._copy_job_sigkill_restart(False,'active_replaced_parent')

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
        recovery=SupervisedCopyBrokerRecovery(self,old,self.authority_config)
        wait_for_active_generation(self,recovery.handle)
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
        recovery.kill_actual_broker()
        thread.join(5);self.assertFalse(thread.is_alive());self.assertEqual(errors,['transport-closed'])
        if published:
            stage=self.project/'.spatial-copy-staging'/os.fsdecode(before.staging_name)
            (stage/os.fsdecode(before.prepared_result.staging_output_name)).rename(self.project/'proof-destination')
        recovery.checked_cleanup_and_retire()
        self.assertFalse(old.resources.root.exists(),
                         'verified supervisor retirement must remove the exact generation root')
        self.broker=recovery.restart(type(old))
        self.assertNotEqual(self.broker.resources.root,old.resources.root,
                            'replacement broker must use a distinct resource root')
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
        self.assertFalse((self.project/'.spatial-copy-staging'/os.fsdecode(before.staging_name)).exists())

    def _copy_job_sigkill_restart(self, retain_uncertain, prepared_case=None):
        self.workspace(attach_world=False, pair=retain_uncertain or prepared_case in ('missing_scope','replaced_scope','replaced_scope_with_proof','active_replaced_parent'))
        old=self.broker
        self.assertIsNotNone(old.resources,'generation regression requires production resource delegation')
        recovery=SupervisedCopyBrokerRecovery(self,old,self.authority_config)
        wait_for_active_generation(self,recovery.handle)
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
        stage_root=self.project/'.spatial-copy-staging'
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if worker_scope.exists() and (worker_scope/'cgroup.procs').read_text().split():
                break
            time.sleep(.005)
        self.assertTrue(worker_scope.exists(),'copy worker scope was not created')
        self.assertTrue((worker_scope/'cgroup.procs').read_text().split(),
                        'copy worker must be live before the broker crash')
        self.assertTrue(list(stage_root.iterdir()),'copy staging must exist before the broker crash')

        if prepared_case or retain_uncertain:
            # Stop supervisor pre-prune while synthetic crash state is staged.
            # Resume only after journal and filesystem mutations finish.
            gate=self._pause_supervisor_for_journal_edit(recovery)
            try:
                recovery.kill_actual_broker()
                if prepared_case:
                    self._prepared_generation_restart(
                        old,recovery,worker_scope,stage_root,job_id,prepared_case,gate)
                else:
                    self._uncertain_generation_restarts(
                        old,recovery,old_root,worker_scope,stage_root,job_id,gate)
            finally:
                if gate.get('pidfd') is not None:
                    self._resume_supervisor_journal_gate(recovery,gate)
            return

        # Kill workspaced and retain its live generation for checked supervisor cleanup.
        recovery.kill_actual_broker()
        recovery.checked_cleanup_and_retire()
        self.assertFalse(old_root.exists(),
                         'verified supervisor retirement must remove the exact generation root')
        replacement=recovery.restart(type(old))
        self.assertNotEqual(replacement.resources.root,old_root,
                            'replacement broker must use a distinct resource root')
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

        # A new operation must run after recovery.
        new_root_handle,new_source_handle=self._copy_source_handles('sigkill-source')
        next_started=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.START,
            source_handle=new_source_handle,destination_directory_handle=new_root_handle,
            destination_basename=b'after-sigkill-copy',maximum_bytes=128*1024*1024,maximum_entries=8192))
        self.assertTrue(next_started.file_action.HasField('copy_job'),next_started)
        next_receipt=self._poll_copy_to_terminal(next_started.file_action.copy_job.job_id)
        self.assertEqual(next_receipt.state,files.FileCopyJobReceipt.COMPLETE,next_receipt)
        self.assertTrue((self.project/'after-sigkill-copy').is_dir())


    def _supervisor_state(self,pid):
        for line in Path('/proc/%d/status'%pid).read_text().splitlines():
            if line.startswith('State:'):
                return line.split()[1]
        self.fail('Supervisor status lacks a State field')

    def _pause_supervisor_for_journal_edit(self,recovery,timeout_seconds=5):
        pid=recovery.handle.proc.pid
        descriptor=os.pidfd_open(pid)
        stop_sent=False
        try:
            self.assertFalse(select.select([descriptor],[],[],0)[0],
                             'Supervisor exited before the crash boundary was staged')
            signal.pidfd_send_signal(descriptor,signal.SIGSTOP)
            stop_sent=True
            deadline=time.monotonic()+timeout_seconds
            while self._supervisor_state(pid) not in ('T','t'):
                self.assertFalse(select.select([descriptor],[],[],0)[0],
                                 'Supervisor exited before SIGSTOP was observed')
                self.assertLess(time.monotonic(),deadline,'Supervisor did not stop before journal mutation')
                time.sleep(.005)
            return {'pidfd':descriptor,'pid':pid}
        except BaseException:
            if stop_sent and not select.select([descriptor],[],[],0)[0]:
                try:signal.pidfd_send_signal(descriptor,signal.SIGCONT)
                except ProcessLookupError:pass
            os.close(descriptor)
            raise

    def _resume_supervisor_journal_gate(self,recovery,gate,timeout_seconds=5):
        descriptor=gate['pidfd'];pid=gate['pid']
        try:
            self.assertEqual(pid,recovery.handle.proc.pid)
            if not select.select([descriptor],[],[],0)[0]:
                signal.pidfd_send_signal(descriptor,signal.SIGCONT)
            deadline=time.monotonic()+timeout_seconds
            while not select.select([descriptor],[],[],0)[0]:
                try:state=self._supervisor_state(pid)
                except FileNotFoundError:
                    break  # Supervisor completed pre-prune and exited after SIGCONT.
                if state not in ('T','t'):
                    break
                self.assertLess(time.monotonic(),deadline,
                                'Supervisor did not resume after journal mutation')
                time.sleep(.005)
        finally:
            # Resume the pinned process even if a state assertion failed.
            if not select.select([descriptor],[],[],0)[0]:
                try:signal.pidfd_send_signal(descriptor,signal.SIGCONT)
                except ProcessLookupError:pass
            os.close(descriptor)
            gate['pidfd']=None

    def _startup_refusal_issuer_rows(self,participant):
        orders={'authority_activation':'generation','authority_fence':'generation',
                'authority_retirement':'generation','authority_obligation':'revision',
                'authority_launch_authorization':'authorization_id'}
        with closing(sqlite3.connect('file:'+str(self.state/'identity.db')+'?mode=ro',
                                    uri=True,timeout=.5)) as database:
            return {table:database.execute('SELECT * FROM '+table+
                ' WHERE participant_id=? ORDER BY '+order,(participant,)).fetchall()
                for table,order in orders.items()}

    def _finish_unactivated_startup_refusal(self,candidate,issuer_before):
        import json
        from harness.copy_fault_evidence import durable_write
        resources=candidate.resources;handle=resources.handle
        self.assertIsNone(handle.retirement)
        candidate.close()
        self.assertEqual(candidate.owner.poll(),0,'Storage owner must finish actual EOF cleanup')
        self.assertTrue(resources.closed)
        self.assertEqual(handle.proc.wait(timeout=15),2,'Startup refusal must retain supervisor failure')
        self.assertTrue(handle.child_dead,'Actual candidate death must be witnessed')
        try:
            if handle.terminal is None:handle.terminal=handle.receive()
            reply=handle.terminal
            self.assertTrue(reply.HasField('error'),reply)
            self.assertEqual(reply.error.message,'Authority child exited before relay completion')
            self.assertTrue(reply.error.HasField('cleanup_record'),reply)
            record=reply.error.cleanup_record
            self.assertEqual(record,handle.record(),'Terminal cleanup must match the durable journal')
            durable_write(handle.evidence/'cleanup.pb',record.SerializeToString(deterministic=True))
            self.assertEqual(handle.initial.view.record.stage,launch.START_ATTEMPTED)
            self.assertEqual(record.stage,launch.OWNED_SCOPES_STOPPED)
            self.assertEqual(record.binding,handle.binding)
            for name in ('process','native','authorization'):
                self.assertEqual(getattr(record,name),getattr(handle.initial.view.record,name))
            self.assertEqual(record.process_stopped.identity,record.process)
            self.assertEqual(record.native_stopped.identity,record.native)
            self.assertFalse(resources.root.exists(),'Checked local cleanup must remove only its own scope')
            self.assertFalse(record.HasField('fence'),'Unactivated authorization cannot prove a failed-generation fence')
            self.assertIsNone(handle.retirement)
            self.assertFalse((handle.evidence/'retirement.pb').exists())
            authorization=record.authorization
            self.assertTrue(record.HasField('authorization'))
            self.assertEqual(authorization.generation,0)
            self.assertEqual(authorization.request.service.registration,handle.supervision.registration)
            self.assertEqual(authorization.request.service.service_incarnation,handle.binding.service_incarnation)
            self.assertEqual(authorization.request.launch_id,record.process.launch_id)
            self.assertEqual(authorization.request.previous_generation,handle.previous_generation)
            self.assertEqual(authorization.request.supervision_revision,handle.supervision.revision)
            self.assertEqual(authorization.supervisor_public_key,handle.public)
            issuer_after=self._startup_refusal_issuer_rows(handle.binding.participant_id)
            # Preserve issuer facts before TemporaryDirectory teardown. Local
            # cleanup does not release this authorization or prove issuer retirement.
            snapshot={phase:{table:[[value.hex() if isinstance(value,bytes) else value for value in row]
                for row in rows] for table,rows in state.items()}
                for phase,state in (('before',issuer_before),('after',issuer_after))}
            durable_write(handle.evidence/'unactivated-issuer-state.json',
                          (json.dumps(snapshot,sort_keys=True,indent=2)+'\n').encode())
            authorizations='authority_launch_authorization'
            for table in issuer_before:
                if table!=authorizations:
                    self.assertEqual(issuer_after[table],issuer_before[table],
                                     'Unactivated refusal changed '+table)
            rows=issuer_after[authorizations]
            self.assertEqual([row for row in rows if row[0]!=authorization.authorization_id],
                             issuer_before[authorizations],'Prior launch authorizations changed')
            retained=[row for row in rows if row[0]==authorization.authorization_id]
            self.assertEqual(len(retained),1,'Exact unconsumed authorization must remain retained')
            row=retained[0]
            self.assertEqual(row[:4],(authorization.authorization_id,handle.binding.participant_id,
                                     handle.binding.service_incarnation,record.process.launch_id))
            self.assertEqual(issuer.AuthorityLaunchBegin.FromString(row[4]),authorization.request)
            self.assertEqual(row[5:],(handle.public,0))
        finally:
            handle.close_control()
            handle.preserve()

    def _prepared_generation_restart(self,old,recovery,worker_scope,stage_root,job_id,case,gate):
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
            prepared_bytes=prepared.SerializeToString(deterministic=True)
            db.execute('UPDATE file_copy_jobs SET state=?,updated_at=?,record=? WHERE workspace=? AND job_id=?',
                (record.state,record.updated_at_unix_ms,record.SerializeToString(),'one',job_id));db.commit()
        old_root=old.resources.root
        old_root_info=old_root.stat()
        old_worker_info=worker_scope.stat()
        # Supervisor pre-prune was gated before broker SIGKILL. The real worker is
        # still journaled and now empty; release pre-prune only after this mutation.
        self._resume_supervisor_journal_gate(recovery,gate)
        # Keep the genuine old worker cgroup intact until checked supervisor
        # cleanup retires it. Missing/replaced paths are evaluated after retirement.
        recovery.checked_cleanup_and_retire()
        self.assertFalse(old_root.exists(),
                         'verified supervisor retirement must remove the exact generation root')
        with closing(sqlite3.connect(database)) as db:
            retired_record=copy_journal.FileCopyJobJournalRecord.FromString(db.execute(
                'SELECT record FROM file_copy_jobs WHERE workspace=? AND job_id=?',('one',job_id)).fetchone()[0])
            self.assertTrue(retired_record.HasField('drained_process_scope'),
                            'checked supervisor pre-prune must record the actual worker drain')
            self.assertEqual(retired_record.drained_process_scope,retired_record.process_scope)
            if case in ('missing_scope','replaced_scope'):
                # Construct the explicit no-proof recovery boundary only after the
                # real generation has drained, retired, and disappeared.
                retired_record.ClearField('drained_process_scope')
                db.execute('UPDATE file_copy_jobs SET state=?,updated_at=?,record=? WHERE workspace=? AND job_id=?',
                    (retired_record.state,retired_record.updated_at_unix_ms,
                     retired_record.SerializeToString(deterministic=True),'one',job_id))
                db.commit()
                self.assertFalse(retired_record.HasField('drained_process_scope'))
        terminal_bytes=None
        if case in ('replaced_scope','replaced_scope_with_proof'):
            # Replaced-parent quarantine requires a valid terminal receipt.
            # A nonterminal job must instead fail startup before dispatch.
            retired_record.state=copy_journal.FILE_COPY_JOB_JOURNAL_UNCERTAIN
            retired_record.updated_at_unix_ms+=1
            retired_record.terminal_receipt.CopyFrom(files.FileCopyJobReceipt(
                job_id=job_id,state=files.FileCopyJobReceipt.UNCERTAIN,
                detail='retained crash uncertainty',
                bytes_copied=retired_record.bytes_progress,
                entries_copied=retired_record.entries_progress))
            terminal_bytes=retired_record.SerializeToString(deterministic=True)
            with closing(sqlite3.connect(database)) as db:
                db.execute('UPDATE file_copy_jobs SET state=?,updated_at=?,record=? WHERE workspace=? AND job_id=?',
                    (retired_record.state,retired_record.updated_at_unix_ms,terminal_bytes,'one',job_id))
                db.commit()
        foreign_identity=None
        if case in ('replaced_scope','replaced_scope_with_proof','active_replaced_parent'):
            # This object is created only after checked retirement. It is a new foreign
            # occupant at the stale journal path, never the old worker scope.
            old_root.mkdir()
            worker_parent=old_root/'spatial-git-workers'
            worker_parent.mkdir()
            worker_scope.mkdir()
            foreign_root_info=old_root.stat()
            foreign_worker_info=worker_scope.stat()
            self.assertNotEqual((foreign_root_info.st_dev,foreign_root_info.st_ino),
                                (old_root_info.st_dev,old_root_info.st_ino))
            self.assertNotEqual((foreign_worker_info.st_dev,foreign_worker_info.st_ino),
                                (old_worker_info.st_dev,old_worker_info.st_ino))
            self.assertNotEqual((foreign_worker_info.st_dev,foreign_worker_info.st_ino),
                                (record.process_scope.device,record.process_scope.inode))
            foreign_identity=((foreign_root_info.st_dev,foreign_root_info.st_ino),
                              (foreign_worker_info.st_dev,foreign_worker_info.st_ino))
            self.addCleanup(self._remove_owned_foreign_cgroups,old_root,worker_parent,worker_scope,foreign_identity)
        elif case == 'missing_scope':
            self.assertFalse(worker_scope.exists())
        if case == 'active_replaced_parent':
            # Checked retirement was genuine. The foreign root cannot verify
            # the retained parent of this still-RUNNING job.
            with closing(sqlite3.connect(database)) as db:
                running_row=db.execute(
                    'SELECT state,updated_at,record FROM file_copy_jobs WHERE workspace=? AND job_id=?',
                    ('one',job_id)).fetchone()
            self.assertEqual(retired_record.state,copy_journal.FILE_COPY_JOB_JOURNAL_RUNNING)
            self.assertEqual(running_row[0],copy_journal.FILE_COPY_JOB_JOURNAL_RUNNING)
            issuer_before=self._startup_refusal_issuer_rows(recovery.handle.binding.participant_id)
            candidate_log=self.root/('copy-fault-broker-'+str(len(self.brokers))+'.log')
            candidate=recovery.restart(type(old))
            self.broker=candidate
            self.assertNotEqual(candidate.resources.root,old_root)
            descriptor=candidate.resources.handle.pidfd
            deadline=time.monotonic()+10
            while not select.select([descriptor],[],[],max(0,deadline-time.monotonic()))[0]:
                self.assertLess(time.monotonic(),deadline,
                                'nonterminal parent conflict did not stop the candidate broker')
            candidate.log.flush()
            self.assertIn(b'Active copy parent identity cannot be verified',
                          candidate_log.read_bytes())
            # START_ATTEMPTED does not prove activation. This candidate exits
            # before consuming its authorization; verify local cleanup separately.
            self._finish_unactivated_startup_refusal(candidate,issuer_before)
            self.assertEqual((old_root.stat().st_dev,old_root.stat().st_ino),foreign_identity[0])
            self.assertEqual((worker_scope.stat().st_dev,worker_scope.stat().st_ino),foreign_identity[1])
            self.assertTrue(stage.exists(),'failed startup must retain prepared staging')
            retained=output.stat()
            self.assertEqual((retained.st_dev,retained.st_ino),
                             (retired_record.prepared_result.output.device,
                              retired_record.prepared_result.output.inode))
            self.assertEqual((output/'payload').read_bytes(),b'prepared')
            self.assertEqual(prepared_bytes,retired_record.prepared_result.SerializeToString(deterministic=True))
            self.assertFalse((self.project/'sigkill-copy').exists())
            with closing(sqlite3.connect(database)) as db:
                self.assertEqual(db.execute(
                    'SELECT state,updated_at,record FROM file_copy_jobs WHERE workspace=? AND job_id=?',
                    ('one',job_id)).fetchone(),running_row,
                    'startup refusal must preserve the complete RUNNING journal row')
            return
        published=case in ('published','sync_failure')
        if published:output.rename(self.project/'sigkill-copy')
        from unittest.mock import patch
        with patch.dict(os.environ,{'SPATIAL_TEST_COPY_RECOVERY_DESTINATION_SYNC_FAIL':
                                     '1' if case=='sync_failure' else '0'}):
            self.broker=recovery.restart(type(old))
        self.assertNotEqual(self.broker.resources.root,old_root,
                            'replacement broker must use a distinct resource root')
        result=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.POLL,job_id=job_id))
        quarantined=case in ('missing_scope','replaced_scope','replaced_scope_with_proof')
        if quarantined:
            self.expect_file_error(result,files.FileActionError.UNCERTAIN)
            self.assertEqual(result.file_action.error.detail,
                'copy recovery disposition quarantines this worktree; saved evidence retained')
        else:
            self.assertTrue(result.file_action.HasField('copy_job'),result)
            expected=(files.FileCopyJobReceipt.UNCERTAIN if case=='sync_failure' else
                      files.FileCopyJobReceipt.COMPLETE if published else files.FileCopyJobReceipt.CANCELLED)
            self.assertEqual(result.file_action.copy_job.state,expected,result)
        self.assertEqual((self.project/'sigkill-copy').exists(),published)
        self.assertEqual(worker_scope.exists(),case in ('replaced_scope','replaced_scope_with_proof'))
        if foreign_identity is not None:
            current_root=old_root.stat();current_worker=worker_scope.stat()
            self.assertEqual((current_root.st_dev,current_root.st_ino),foreign_identity[0],
                             'recovery must preserve the exact post-retirement foreign generation root')
            self.assertEqual((current_worker.st_dev,current_worker.st_ino),foreign_identity[1],
                             'recovery must preserve the exact post-retirement foreign worker cgroup')
        self.assertEqual(stage.exists(),case in ('sync_failure','missing_scope','replaced_scope','replaced_scope_with_proof'))
        if case in ('replaced_scope','replaced_scope_with_proof'):
            retained=output.stat()
            self.assertEqual((retained.st_dev,retained.st_ino),
                             (retired_record.prepared_result.output.device,
                              retired_record.prepared_result.output.inode))
            self.assertEqual((output/'payload').read_bytes(),b'prepared')
        with closing(sqlite3.connect(database)) as db:
            recovered=copy_journal.FileCopyJobJournalRecord.FromString(db.execute(
                'SELECT record FROM file_copy_jobs WHERE workspace=? AND job_id=?',('one',job_id)).fetchone()[0])
        self.assertEqual(recovered.prepared_result.SerializeToString(deterministic=True),prepared_bytes)
        if terminal_bytes is not None:
            with closing(sqlite3.connect(database)) as db:
                self.assertEqual(db.execute(
                    'SELECT record FROM file_copy_jobs WHERE workspace=? AND job_id=?',
                    ('one',job_id)).fetchone()[0],terminal_bytes,
                    'quarantine must preserve the complete durable terminal record')
        self.assertGreaterEqual(recovered.updated_at_unix_ms,record.updated_at_unix_ms)
        if case in ('missing_scope','replaced_scope','replaced_scope_with_proof'):
            self.assertEqual(recovered.state,copy_journal.FILE_COPY_JOB_JOURNAL_UNCERTAIN)
            self.assertEqual(recovered.HasField('drained_process_scope'),case=='replaced_scope_with_proof')
            quarantined_browse=self.action(browse=files.FileBrowse(page_size=8))
            self.expect_file_error(quarantined_browse,files.FileActionError.UNCERTAIN)
            self.assertEqual(quarantined_browse.file_action.error.detail,
                'copy recovery disposition quarantines this worktree; saved evidence retained')
            self.broker.call('git.status',worktree='tree-two')
        else:
            self.assertEqual(recovered.drained_process_scope,recovered.process_scope)
        # Foreign replacement cgroups are cleaned by the supervised fixture owner.

    def _remove_owned_foreign_cgroups(self,root,worker_parent,worker_scope,identity):
        root_info=root.stat();worker_info=worker_scope.stat()
        self.assertEqual((root_info.st_dev,root_info.st_ino),identity[0],
                         'refusing to remove a changed foreign generation root')
        self.assertEqual((worker_info.st_dev,worker_info.st_ino),identity[1],
                         'refusing to remove a changed foreign worker cgroup')
        for scope in (worker_scope,root):
            self.assertEqual((scope/'cgroup.procs').read_text().split(),[],
                             'refusing to remove a populated test-created cgroup')
            self.assertIn('populated 0',(scope/'cgroup.events').read_text())
        worker_scope.rmdir()
        worker_parent.rmdir()
        root.rmdir()

    def _uncertain_generation_restarts(self, old, recovery, old_root, worker_scope, stage_root, job_id, gate):
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
        self._resume_supervisor_journal_gate(recovery,gate)
        recovery.checked_cleanup_and_retire()
        self.assertFalse(old_root.exists(),
                         'verified supervisor retirement must remove the exact generation root')
        self.broker=recovery.restart(type(old))
        self.assertNotEqual(self.broker.resources.root,old_root)
        for generation in range(3):
            # Poll forces startup completion before checking its effects.
            poll=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.POLL,job_id=job_id))
            self.expect_file_error(poll,files.FileActionError.UNCERTAIN)
            self.assertEqual(poll.file_action.error.detail,
                'copy recovery disposition quarantines this worktree; saved evidence retained')
            self.assertFalse((self.project/'sigkill-copy').exists())
            foreign=self.principal();foreign.actor_id=bytes([0x77])*32
            denied=self.broker.request(file_action=cabinet.FileActionRequest(protocol_version=1,
                operation_id=uuid.uuid4().bytes,workspace_id='one',principal=foreign,
                copy_job=files.FileCopyJob(command=files.FileCopyJob.POLL,job_id=job_id)))
            self.expect_file_error(denied,files.FileActionError.UNAUTHORIZED)
            blocked=self.action(browse=files.FileBrowse(page_size=8))
            self.expect_file_error(blocked,files.FileActionError.UNCERTAIN)
            self.assertEqual(blocked.file_action.error.detail,
                'copy recovery disposition quarantines this worktree; saved evidence retained')
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
            # Later same-journal restarts each follow real SIGKILL, checked cleanup,
            # and issuer retirement. The stale journal parent remains quarantined.
            self.assertNotEqual(self.broker.resources.root,old_root)
            principal=self.principal()
            actor='identity:'+principal.issuer_id.hex()+':'+principal.actor_id.hex()
            launched=self.broker.request(launch=broker.LaunchRequest(workspace='two',actor=actor,principal=principal,
                command=core.ProcessLaunchRequest(operation_id='independent-app-'+str(generation),profile='development',
                    argv=['/usr/bin/python3','-c',
                          'import time;from pathlib import Path;time.sleep(.3);Path("app-alive-'+str(generation)+'").write_text("alive")'])))
            self.assertTrue(launched.HasField('launched'),launched)
            still_uncertain=self.action(copy_job=files.FileCopyJob(command=files.FileCopyJob.POLL,job_id=job_id))
            self.expect_file_error(still_uncertain,files.FileActionError.UNCERTAIN)
            self.assertEqual(still_uncertain.file_action.error.detail,
                'copy recovery disposition quarantines this worktree; saved evidence retained')
            self.expect_file_error(self.action(browse=files.FileBrowse(page_size=8)),
                                   files.FileActionError.UNCERTAIN)
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
            if generation < 2:
                current=self.broker
                next_recovery=SupervisedCopyBrokerRecovery(self,current,self.authority_config)
                wait_for_active_generation(self,next_recovery.handle)
                next_recovery.kill_actual_broker()
                next_recovery.checked_cleanup_and_retire()
                self.assertFalse(current.resources.root.exists(),
                                 'verified supervisor retirement must remove each generation root')
                self.broker=next_recovery.restart(type(current))
                self.assertNotEqual(self.broker.resources.root,current.resources.root,
                                    'each repeated restart must use a distinct resource root')

def _iter_test_cases(test):
    if isinstance(test, unittest.TestSuite):
        for item in test:
            yield from _iter_test_cases(item)
    else:
        yield test


class PassedFixtureImageCleanupRunner(unittest.TextTestRunner):
    def run(self, test):
        cases = list(_iter_test_cases(test))
        image_cases = [case for case in cases if isinstance(case, engine.WorkspaceCopyEngineTests)]
        if image_cases:
            evidence_root = Path(os.environ["SPATIAL_SECURITY_EVIDENCE"])
            evidence_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            capacity, result = run_after_capacity_preflight(
                evidence_root, len(image_cases), lambda: super(PassedFixtureImageCleanupRunner, self).run(test))
            print("FIXTURE IMAGE CAPACITY PREFLIGHT: " + repr(capacity), flush=True)
        else:
            result = super().run(test)
        summary = cleanup_file_action_group(
            result, cases, engine.WorkspaceCopyEngineTests, engine.BUILD / 'python',
            runner=subprocess.run)
        if summary['status'] == 'removed':
            print('PASS-ONLY FIXTURE IMAGE CLEANUP: ' + repr(summary), flush=True)
        return result


if __name__ == '__main__':
    unittest.main(verbosity=2, testRunner=PassedFixtureImageCleanupRunner)
