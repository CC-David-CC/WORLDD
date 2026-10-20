"""Authenticated retirement after actual retained broker crash cleanup."""
import fcntl
import hashlib
import io
import os
import select
import signal
import sqlite3
import subprocess
import unittest
import test_session_broker_cleanup as existing
import issuer_pb2 as identity
import launch_pb2 as launch
import process_scope_pb2 as process
from harness.wire import frame, read_message


class BrokerRetirementTests(existing.SessionBrokerCleanupTests):
    def database(self):
        return self.journal / 'launch.db'

    def issuer_rows(self):
        with sqlite3.connect('file:' + str(self.issuer.state / 'identity.db') + '?mode=ro', uri=True) as db:
            return {name: db.execute('SELECT * FROM ' + name + ' ORDER BY participant_id,generation').fetchall()
                for name in ('authority_activation', 'authority_retirement')}

    def local(self):
        with sqlite3.connect('file:' + str(self.database()) + '?mode=ro', uri=True) as db:
            return (db.execute('PRAGMA user_version').fetchone()[0],
                db.execute('SELECT record FROM launch WHERE singleton=1').fetchone()[0])

    def crash(self, stop_world=True):
        self.pin_broker(stop_world=stop_world)
        self.world_stopped = stop_world
        signal.pidfd_send_signal(self.pins[self.broker_pid], signal.SIGKILL)
        self.exited()

    def checked(self):
        self.cleanup = self.request(stop_broker=launch.AuthorityLaunchQuery()).cleanup
        self.matching_cleanup(self.cleanup)
        version, self.cleanup_bytes = self.local()
        self.assertEqual(version, 1)
        self.assertEqual(self.cleanup_bytes, self.cleanup.SerializeToString())

    def retired(self):
        reply = self.request(retire_broker=launch.AuthorityLaunchQuery())
        self.assertTrue(reply.HasField('retirement'), reply)
        return reply.retirement

    def completed(self, receipt):
        original = self.original
        expected = identity.AuthorityFenceBegin(participant_id=original.binding.participant_id,
            registered_revision=original.binding.registered_revision,
            generation=original.authorization.request.previous_generation + 1,
            service_incarnation=original.binding.service_incarnation, launch_id=original.process.launch_id,
            supervision_revision=original.authorization.request.supervision_revision,
            authorization_id=original.authorization.authorization_id)
        self.assertEqual(receipt.request.owner, expected)
        self.assertEqual(receipt.request.fence_id, self.cleanup.fence.fence_id)
        self.assertEqual(receipt.request.cleanup_sha256, hashlib.sha256(self.cleanup_bytes).digest())
        self.assertEqual(receipt.registration, self.cleanup.fence.registration)
        self.assertEqual(receipt.supervisor_public_key, original.authorization.supervisor_public_key)
        self.assertEqual(receipt.issuer_id, original.binding.issuer_id)
        self.assertEqual(self.local(), (4, self.cleanup_bytes))
        with sqlite3.connect('file:' + str(self.database()) + '?mode=ro', uri=True) as db:
            self.assertEqual(db.execute('SELECT record FROM issuer_retirement').fetchall(), [(receipt.SerializeToString(),)])
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE name='dependent_cleanup'").fetchall(), [])
        with sqlite3.connect('file:' + str(self.issuer.state / 'identity.db') + '?mode=ro', uri=True) as db:
            owner = (expected.participant_id, expected.generation)
            saved = db.execute('SELECT state FROM authority_retirement WHERE participant_id=? AND generation=?', owner).fetchone()[0]
            active = db.execute('SELECT active,applied_revision FROM authority_activation WHERE participant_id=? AND generation=?', owner).fetchone()
        self.assertEqual(saved, receipt.SerializeToString())
        self.assertEqual(active, (0, receipt.applied_revision))
        self.assertEqual(self.request(stop_broker=launch.AuthorityLaunchQuery()).cleanup, self.cleanup)

    def finish(self):
        if not self.world_stopped:
            world = self.request(stop_world=self.owner).cleanup
            self.assertEqual(world.stage, launch.OWNED_SCOPES_STOPPED)
            self.assertEqual(world.process, self.world_record.process)
            self.assertEqual(world.native, self.world_record.native)
        self.finish_session()

    def test_actual_broker_retirement_preserves_other_generation_and_exact_retry(self):
        self.crash(stop_world=False); self.checked()
        independent = self.supervisor.issue(workspace=b'independent', application=b'survivor')
        survivor = self.connect(independent.cookie); native_identity = survivor.inspect()
        before = self.issuer_rows(); receipt = self.retired(); self.completed(receipt)
        expected = [row[:-1] + (0,) if row[0] == self.original.binding.participant_id else row
            for row in before['authority_activation']]
        after = self.issuer_rows()
        self.assertEqual(after['authority_activation'], expected)
        self.assertEqual(len(after['authority_retirement']), 1)
        self.assertEqual(survivor.inspect(), native_identity)
        key = self.issuer.supervisor_key; original = key.read_bytes(); key.unlink()
        try:
            self.assertEqual(self.retired(), receipt)
            self.assertEqual(self.issuer_rows(), after)
        finally:
            key.write_bytes(original); key.chmod(0o600)
        # Retirement of the broker does not discharge the world's separate cleanup.
        self.request(expect_error=True, stop_session=launch.AuthorityLaunchQuery())
        self.completed(receipt); self.finish()

    def test_live_and_unchecked_broker_cannot_retire(self):
        self.pin_broker(); self.world_stopped = True
        before = self.issuer_rows()
        self.request(expect_error=True, retire_broker=launch.AuthorityLaunchQuery())
        self.assertEqual(self.issuer_rows(), before)
        signal.pidfd_send_signal(self.pins[self.broker_pid], signal.SIGKILL); self.exited()
        self.request(expect_error=True, retire_broker=launch.AuthorityLaunchQuery())
        self.assertEqual(self.issuer_rows(), before)
        self.checked(); self.completed(self.retired()); self.finish()

    def test_missing_fence_in_retained_cleanup_cannot_authorize_retirement(self):
        # Normal explicit stop fences an active copy-cleanup participant before
        # draining its owned scope. Remove the fence only from retained evidence
        # to exercise the production journal/retained-receipt boundary.
        self.crash(); self.checked()
        self.assertTrue(self.cleanup.HasField('fence'))
        before = self.issuer_rows()
        unfenced = launch.AuthorityLaunchRecord(); unfenced.CopyFrom(self.cleanup)
        unfenced.ClearField('fence')
        unfenced_bytes = unfenced.SerializeToString()
        self.assertNotEqual(unfenced_bytes, self.cleanup_bytes)
        with sqlite3.connect(self.database()) as db:
            db.execute('UPDATE launch SET record=?', (unfenced_bytes,))
        try:
            rejected = self.request(expect_error=True, retire_broker=launch.AuthorityLaunchQuery())
            self.assertIn('journal differs', rejected.error.message)
            self.assertEqual(self.issuer_rows(), before)
            self.assertEqual(self.local(), (1, unfenced_bytes))
        finally:
            with sqlite3.connect(self.database()) as db:
                db.execute('UPDATE launch SET record=?', (self.cleanup_bytes,))
        self.assertEqual(self.local(), (1, self.cleanup_bytes))
        self.completed(self.retired()); self.finish()

    def test_changed_or_reordered_cleanup_bytes_cannot_authorize_retirement(self):
        self.crash(); self.checked(); before = self.issuer_rows()
        changed = launch.AuthorityLaunchRecord(); changed.CopyFrom(self.cleanup); changed.revision += 1
        self.assertEqual(self.cleanup_bytes[:2], b'\x08\x02')
        reordered = self.cleanup_bytes[2:] + self.cleanup_bytes[:2]
        self.assertEqual(launch.AuthorityLaunchRecord.FromString(reordered), self.cleanup)
        for encoded in (changed.SerializeToString(), reordered, b'\xff'):
            with sqlite3.connect(self.database()) as db: db.execute('UPDATE launch SET record=?', (encoded,))
            try:
                self.request(expect_error=True, retire_broker=launch.AuthorityLaunchQuery())
                self.assertEqual(self.issuer_rows(), before)
                self.assertEqual(self.local(), (1, encoded))
            finally:
                with sqlite3.connect(self.database()) as db: db.execute('UPDATE launch SET record=?', (self.cleanup_bytes,))
        self.completed(self.retired()); self.finish()

    def test_wrong_key_locked_and_readonly_journal_leave_issuer_unchanged(self):
        self.crash(); self.checked(); before = self.issuer_rows()
        key = self.issuer.supervisor_key; original = key.read_bytes()
        other, _ = self.issuer.key('broker-retirement-wrong-key'); key.write_bytes(other.read_bytes())
        try:
            rejected = self.request(expect_error=True, retire_broker=launch.AuthorityLaunchQuery())
            self.assertIn('signing key differs', rejected.error.message)
            self.assertEqual(self.issuer_rows(), before)
        finally: key.write_bytes(original)
        descriptor = os.open(self.journal / 'launch.lock', os.O_RDWR | os.O_CLOEXEC)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            rejected = self.request(expect_error=True, retire_broker=launch.AuthorityLaunchQuery())
            self.assertIn('already owned', rejected.error.message)
            self.assertEqual(self.issuer_rows(), before)
        finally: os.close(descriptor)
        self.database().chmod(0o400)
        try:
            self.request(expect_error=True, retire_broker=launch.AuthorityLaunchQuery())
            self.assertEqual(self.issuer_rows(), before)
        finally: self.database().chmod(0o600)
        self.assertEqual(self.local(), (1, self.cleanup_bytes))
        self.completed(self.retired()); self.finish()

    def test_issuer_write_failure_rolls_back_and_retry_completes(self):
        self.crash(); self.checked(); before = self.issuer_rows()
        with sqlite3.connect(self.issuer.state / 'identity.db') as db:
            db.execute("CREATE TRIGGER deny_broker_retire BEFORE INSERT ON authority_retirement BEGIN SELECT RAISE(ABORT,'fixture'); END")
        try:
            self.request(expect_error=True, retire_broker=launch.AuthorityLaunchQuery())
            self.assertEqual(self.issuer_rows(), before)
            self.assertEqual(self.local(), (1, self.cleanup_bytes))
        finally:
            with sqlite3.connect(self.issuer.state / 'identity.db') as db: db.execute('DROP TRIGGER deny_broker_retire')
        self.completed(self.retired()); self.finish()

    def test_local_commit_failure_recovers_original_committed_issuer_receipt(self):
        self.crash(); self.checked()
        blocker = sqlite3.connect(self.database())
        try:
            blocker.execute('BEGIN IMMEDIATE')
            self.request(expect_error=True, retire_broker=launch.AuthorityLaunchQuery())
            self.assertEqual(self.local(), (1, self.cleanup_bytes))
            with sqlite3.connect(self.issuer.state / 'identity.db') as db:
                rows = db.execute('SELECT state FROM authority_retirement').fetchall()
            self.assertEqual(len(rows), 1, 'Issuer must commit before the injected local failure')
            receipt = identity.AuthorityRetirementState.FromString(rows[0][0]); after = self.issuer_rows()
        finally: blocker.rollback(); blocker.close()
        self.assertEqual(self.retired(), receipt)
        self.assertEqual(self.issuer_rows(), after)
        self.completed(receipt); self.finish()

    def test_saved_receipt_tampering_is_rejected_and_reopen_cannot_launch(self):
        self.crash(); self.checked(); receipt = self.retired(); self.completed(receipt); before = self.issuer_rows()
        for field in ('digest', 'retirement_id'):
            changed = identity.AuthorityRetirementState(); changed.CopyFrom(receipt)
            if field == 'digest': changed.request.cleanup_sha256 = b'x' * 32
            else: changed.retirement_id = b'x' * 32
            with sqlite3.connect(self.database()) as db:
                db.execute('UPDATE issuer_retirement SET record=?', (changed.SerializeToString(),))
            try:
                self.request(expect_error=True, retire_broker=launch.AuthorityLaunchQuery())
                self.assertEqual(self.issuer_rows(), before)
            finally:
                with sqlite3.connect(self.database()) as db:
                    db.execute('UPDATE issuer_retirement SET record=?', (receipt.SerializeToString(),))
        binary = existing.existing.existing.base.baseline.supervisor.owned.existing.PROBE
        requests = [launch.AuthorityLaunchRequest(protocol_version=1, request_id=1, inspect=launch.AuthorityLaunchQuery()),
            launch.AuthorityLaunchRequest(protocol_version=1, request_id=2, control_owner=launch.AuthorityLaunchQuery()),
            launch.AuthorityLaunchRequest(protocol_version=1, request_id=3, prepare=self.cleanup.binding),
            launch.AuthorityLaunchRequest(protocol_version=1, request_id=4, start=process.AuthorityProcessLaunch(executable='/usr/bin/true'))]
        result = subprocess.run([str(binary), str(self.journal), str(self.cgroup), '-1'],
            input=b''.join(frame(request) for request in requests), capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        for marker in (b'AddressSanitizer', b'LeakSanitizer', b'runtime error:'): self.assertNotIn(marker, result.stderr)
        stream = io.BytesIO(result.stdout); replies = [read_message(stream, launch.AuthorityLaunchReply) for _ in requests]
        self.assertEqual(replies[0].view.record, self.cleanup)
        for reply in replies[1:]: self.assertTrue(reply.HasField('error'), reply)
        self.assertEqual(stream.read(), b'')
        self.assertEqual(self.retired(), receipt); self.completed(receipt); self.finish()

    def test_supervisor_death_without_checked_cleanup_cannot_retire(self):
        self.pin_broker(); before = self.issuer_rows()
        signal.pidfd_send_signal(self.pins[self.supervisor_pid], signal.SIGKILL)
        pin = self.pins[self.supervisor_pid]; self.assertEqual(select.select([pin], [], [], 8)[0], [pin])
        self.request(expect_error=True, stop_broker=launch.AuthorityLaunchQuery())
        for _ in range(2):
            self.request(expect_error=True, retire_broker=launch.AuthorityLaunchQuery())
            self.assertEqual(self.issuer_rows(), before)


if __name__ == '__main__':
    names = ['BrokerRetirementTests.' + name for name in BrokerRetirementTests.__dict__ if name.startswith('test_')]
    unittest.main(defaultTest=names, verbosity=2)
