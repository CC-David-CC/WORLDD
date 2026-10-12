"""Durable dependent receipts from actual coordinator-managed crash recovery."""
import fcntl
import io
import os
import sqlite3
import subprocess
import unittest
import test_session_application as existing
import launch_pb2 as launch
import process_scope_pb2 as process
from harness.wire import frame, read_message


class DependentCleanupTests(existing.SessionApplicationTests):
    def world_database(self):
        return self.world_journal / 'launch.db'

    def saved(self, cleanup, recovered):
        with sqlite3.connect('file:' + str(self.world_database()) + '?mode=ro', uri=True) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2,
                'Checked broker release has no durable dependent receipt migration')
            original = db.execute('SELECT record FROM launch WHERE singleton=1').fetchone()[0]
            rows = db.execute('SELECT record FROM dependent_cleanup').fetchall()
        self.assertEqual(launch.AuthorityLaunchRecord.FromString(original), cleanup)
        self.assertEqual(len(rows), 1)
        record = launch.AuthorityDependentCleanup.FromString(rows[0][0])
        self.assertEqual(record.schema_version, 1)
        self.assertEqual(record.world, cleanup)
        self.assertEqual(record.broker, self.read_record())
        self.assertEqual(record.controls, recovered.recovery)
        return rows[0][0]

    def stopped_application(self):
        self.held_application()
        return self.crash_world()

    def recover(self):
        result = self.request(recover_world=self.owner).owner
        self.assertTrue(result.channel_fenced)
        self.assertFalse(result.reservations)
        self.assertEqual(list(result.recovery.released), [self.lease])
        self.released()
        return result

    def test_successful_recovery_persists_exact_receipts_and_reopen_cannot_launch(self):
        cleanup = self.stopped_application()
        recovered = self.recover()
        before = self.saved(cleanup, recovered)
        self.assertEqual(self.recover(), recovered)
        self.assertEqual(self.saved(cleanup, recovered), before)
        # This existing library probe opens the journal without reconstructing any live handles.
        binary = existing.existing.base.baseline.supervisor.owned.existing.PROBE
        requests = [launch.AuthorityLaunchRequest(protocol_version=1, request_id=1, inspect=launch.AuthorityLaunchQuery()),
            launch.AuthorityLaunchRequest(protocol_version=1, request_id=2, control_owner=launch.AuthorityLaunchQuery()),
            launch.AuthorityLaunchRequest(protocol_version=1, request_id=3, prepare=cleanup.binding),
            launch.AuthorityLaunchRequest(protocol_version=1, request_id=4,
                start=process.AuthorityProcessLaunch(executable='/usr/bin/true'))]
        result = subprocess.run([str(binary), str(self.world_journal), str(self.cgroup), '-1'],
            input=b''.join(frame(request) for request in requests), capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        for marker in (b'AddressSanitizer', b'LeakSanitizer', b'runtime error:'):
            self.assertNotIn(marker, result.stderr)
        stream = io.BytesIO(result.stdout)
        replies = [read_message(stream, launch.AuthorityLaunchReply) for _ in requests]
        self.assertEqual(replies[0].view.record, cleanup)
        for reply in replies[1:]: self.assertTrue(reply.HasField('error'), reply)
        self.assertEqual(stream.read(), b'')
        self.assertEqual(self.saved(cleanup, recovered), before)
        self.finish_session()

    def test_readonly_journal_reports_failure_after_release_then_retries(self):
        cleanup = self.stopped_application()
        self.world_database().chmod(0o400)
        try:
            self.request(expect_error=True, recover_world=self.owner)
            state = self.request(inspect=self.owner).owner
            self.assertFalse(state.reservations); self.assertEqual(list(state.recovery.released), [self.lease])
            with sqlite3.connect('file:' + str(self.world_database()) + '?mode=ro', uri=True) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
        finally:
            self.world_database().chmod(0o600)
        recovered = self.recover()
        self.assertEqual(recovered, state)
        self.saved(cleanup, recovered)
        self.finish_session()

    def test_journal_lock_failure_preserves_exact_retry(self):
        cleanup = self.stopped_application()
        descriptor = os.open(self.world_journal / 'launch.lock', os.O_RDWR | os.O_CLOEXEC)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            rejected = self.request(expect_error=True, recover_world=self.owner)
            self.assertIn('already owned', rejected.error.message)
            state = self.request(inspect=self.owner).owner
            self.assertFalse(state.reservations); self.assertEqual(list(state.recovery.released), [self.lease])
        finally:
            os.close(descriptor)
        recovered = self.recover(); self.assertEqual(recovered, state)
        self.saved(cleanup, recovered)
        self.finish_session()

    def test_changed_launch_record_cannot_replace_retained_cleanup(self):
        cleanup = self.stopped_application()
        changed = launch.AuthorityLaunchRecord(); changed.CopyFrom(cleanup); changed.revision += 1
        with sqlite3.connect(self.world_database()) as db:
            db.execute('UPDATE launch SET record=? WHERE singleton=1', (changed.SerializeToString(),))
        self.request(expect_error=True, recover_world=self.owner)
        with sqlite3.connect(self.world_database()) as db:
            self.assertEqual(db.execute('SELECT record FROM launch WHERE singleton=1').fetchone()[0], changed.SerializeToString())
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            db.execute('UPDATE launch SET record=? WHERE singleton=1', (cleanup.SerializeToString(),))
        recovered = self.recover(); self.saved(cleanup, recovered)
        self.finish_session()

    def test_corrupted_or_changed_dependent_receipt_is_not_overwritten(self):
        cleanup = self.stopped_application(); recovered = self.recover()
        original = self.saved(cleanup, recovered)
        changes = [lambda x: setattr(x, 'schema_version', 99),
            lambda x: setattr(x.controls.cleanup.native_stopped.identity, 'credential_id', b'z' * 32),
            lambda x: setattr(x.broker.native, 'server_id', b'z' * 32),
            lambda x: setattr(x.controls.released[0], 'id', b'z' * 32)]
        for change in changes:
            record = launch.AuthorityDependentCleanup.FromString(original); change(record)
            encoded = record.SerializeToString()
            with sqlite3.connect(self.world_database()) as db:
                db.execute('UPDATE dependent_cleanup SET record=? WHERE singleton=1', (encoded,))
            self.request(expect_error=True, recover_world=self.owner)
            with sqlite3.connect(self.world_database()) as db:
                self.assertEqual(db.execute('SELECT record FROM dependent_cleanup WHERE singleton=1').fetchone()[0], encoded)
                db.execute('UPDATE dependent_cleanup SET record=? WHERE singleton=1', (original,))
        self.assertEqual(self.recover(), recovered)
        self.assertEqual(self.saved(cleanup, recovered), original)
        self.finish_session()


if __name__ == '__main__':
    names = ['DependentCleanupTests.' + name for name in DependentCleanupTests.__dict__ if name.startswith('test_')]
    unittest.main(defaultTest=names, verbosity=2)
