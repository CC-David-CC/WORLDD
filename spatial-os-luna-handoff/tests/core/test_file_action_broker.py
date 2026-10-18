from pathlib import Path
from contextlib import closing
import sqlite3
import struct
import uuid
import broker_pb2 as broker
import grants_pb2 as grant
import issuer_pb2 as issuer
import cabinet_pb2 as cabinet
import file_actions_pb2 as files
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

    def test_file_action_copy_execution_remains_unimplemented(self):
        self.workspace(attach_world=False)
        before={path.name for path in self.project.iterdir()}
        reply=self.action(copy_job=files.FileCopyJob(
            command=files.FileCopyJob.START,maximum_bytes=1024,maximum_entries=8))
        self.expect_file_error(reply,files.FileActionError.UNIMPLEMENTED)
        self.assertEqual({path.name for path in self.project.iterdir()},before)

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
