"""Retire a real failed world's generation only after checked, durable cleanup."""
import hashlib
import io
import sqlite3
import subprocess
import unittest
import test_dependent_cleanup as durable
import broker_pb2 as broker
import core_pb2 as core
import issuer_pb2 as identity
import launch_pb2 as launch
import process_scope_pb2 as process
from harness.wire import frame, read_message


class CoordinatorRetirementTests(durable.DependentCleanupTests):
    def issuer_rows(self):
        with sqlite3.connect('file:' + str(self.issuer.state / 'identity.db') + '?mode=ro', uri=True) as db:
            return {name: db.execute('SELECT * FROM ' + name + ' ORDER BY participant_id,generation').fetchall()
                for name in ('authority_activation', 'authority_retirement')}

    def retired(self):
        reply = self.request(retire_world=self.owner)
        self.assertTrue(reply.HasField('retirement'), reply)
        return reply.retirement

    def durable_cleanup(self):
        self.cleanup = self.stopped_application(); self.recovered = self.recover()
        self.dependent_bytes = self.saved(self.cleanup, self.recovered)
        self.assertTrue(self.cleanup.HasField('fence'))

    def completed(self, receipt):
        self.assertEqual(receipt.request.owner, self.owner.launch)
        self.assertEqual(receipt.request.fence_id, self.cleanup.fence.fence_id)
        self.assertEqual(receipt.request.cleanup_sha256, hashlib.sha256(self.dependent_bytes).digest())
        self.assertEqual(receipt.registration, self.cleanup.fence.registration)
        self.assertEqual(receipt.supervisor_public_key, self.cleanup.authorization.supervisor_public_key)
        self.assertEqual(receipt.issuer_id, self.owner.issuer_id)
        with sqlite3.connect('file:' + str(self.world_database()) + '?mode=ro', uri=True) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 3)
            original = db.execute('SELECT record FROM launch WHERE singleton=1').fetchone()[0]
            dependent = db.execute('SELECT record FROM dependent_cleanup WHERE singleton=1').fetchone()[0]
            rows = db.execute('SELECT record FROM issuer_retirement').fetchall()
        self.assertEqual(original, self.cleanup.SerializeToString())
        self.assertEqual(dependent, self.dependent_bytes)
        self.assertEqual(rows, [(receipt.SerializeToString(),)])
        with sqlite3.connect('file:' + str(self.issuer.state / 'identity.db') + '?mode=ro', uri=True) as db:
            owner = (self.owner.launch.participant_id, self.owner.launch.generation)
            saved = db.execute('SELECT state FROM authority_retirement WHERE participant_id=? AND generation=?', owner).fetchone()[0]
            state = db.execute('SELECT active,applied_revision FROM authority_activation WHERE participant_id=? AND generation=?', owner).fetchone()
        self.assertEqual(saved, receipt.SerializeToString())
        self.assertEqual(state, (0, receipt.applied_revision))
        self.released()  # Independent native resize acknowledgement and live broker reply.

    def test_actual_cleanup_retires_exact_generation_and_retry_preserves_application(self):
        self.durable_cleanup(); before = self.issuer_rows()
        receipt = self.retired(); self.completed(receipt)
        after = self.issuer_rows()
        expected = [row[:-1] + (0,) if row[0] == self.owner.launch.participant_id else row
            for row in before['authority_activation']]
        self.assertEqual(after['authority_activation'], expected)
        self.assertEqual(len(after['authority_retirement']), 1)
        key = self.issuer.supervisor_key; original = key.read_bytes(); key.unlink()
        try:
            self.assertEqual(self.retired(), receipt)
            self.assertEqual(self.request(recover_world=self.owner).owner, self.recovered)
            self.assertEqual(self.issuer_rows(), after)
        finally:
            key.write_bytes(original); key.chmod(0o600)
        self.completed(receipt); self.finish_session()

    def test_live_unrecovered_and_substituted_owners_cannot_retire(self):
        self.held_application(); before = self.issuer_rows()
        self.request(expect_error=True, retire_world=self.owner)
        self.assertEqual(self.issuer_rows(), before)
        self.cleanup = self.crash_world(); before = self.issuer_rows()
        self.request(expect_error=True, retire_world=self.owner)
        self.assertEqual(self.issuer_rows(), before)
        self.recovered = self.recover(); self.dependent_bytes = self.saved(self.cleanup, self.recovered)
        for field in ('generation', 'launch_id', 'authorization_id'):
            changed = broker.ControlOwner(); changed.CopyFrom(self.owner)
            setattr(changed.launch, field, changed.launch.generation + 1 if field == 'generation' else b'x' * 32)
            self.request(expect_error=True, retire_world=changed)
            self.assertEqual(self.issuer_rows(), before)
        self.completed(self.retired()); self.finish_session()

    def test_unfenced_graceful_cleanup_cannot_retire(self):
        self.held_application(); self.act(stop=core.ActorStopRequest())
        cleanup = self.request(stop_world=self.owner).cleanup
        self.assertFalse(cleanup.HasField('fence'))
        recovered = self.request(recover_world=self.owner).owner
        original = self.saved(cleanup, recovered); before = self.issuer_rows()
        reply = self.request(expect_error=True, retire_world=self.owner)
        self.assertIn('issuer fence', reply.error.message)
        self.assertEqual(self.issuer_rows(), before)
        self.assertEqual(self.saved(cleanup, recovered), original)
        self.finish_session()

    def test_tampered_dependent_bytes_cannot_create_retirement_proof(self):
        self.durable_cleanup(); before = self.issuer_rows()
        original = self.dependent_bytes
        changed = launch.AuthorityDependentCleanup.FromString(original)
        changed.controls.released[0].id = b'x' * 32
        self.assertEqual(original[:2], b'\x08\x01')
        # This reordered protobuf is semantically equal. The saved byte digest must remain exact.
        reordered = original[2:] + original[:2]
        self.assertEqual(launch.AuthorityDependentCleanup.FromString(reordered),
            launch.AuthorityDependentCleanup.FromString(original))
        for encoded in (changed.SerializeToString(), reordered, b'\xff'):
            with sqlite3.connect(self.world_database()) as db:
                db.execute('UPDATE dependent_cleanup SET record=? WHERE singleton=1', (encoded,))
            try:
                self.request(expect_error=True, retire_world=self.owner)
                self.assertEqual(self.issuer_rows(), before)
                with sqlite3.connect(self.world_database()) as db:
                    self.assertEqual(db.execute('SELECT record FROM dependent_cleanup').fetchone()[0], encoded)
                    self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)
            finally:
                with sqlite3.connect(self.world_database()) as db:
                    db.execute('UPDATE dependent_cleanup SET record=? WHERE singleton=1', (original,))
        self.completed(self.retired()); self.finish_session()

    def test_wrong_signing_key_and_readonly_journal_do_not_retire(self):
        self.durable_cleanup(); before = self.issuer_rows()
        key = self.issuer.supervisor_key; original = key.read_bytes()
        other, _ = self.issuer.key('retirement-wrong-supervisor')
        key.write_bytes(other.read_bytes())
        try:
            reply = self.request(expect_error=True, retire_world=self.owner)
            self.assertIn('signing key differs', reply.error.message)
            self.assertEqual(self.issuer_rows(), before)
        finally:
            key.write_bytes(original)
        self.world_database().chmod(0o400)
        try:
            self.request(expect_error=True, retire_world=self.owner)
            self.assertEqual(self.issuer_rows(), before)
        finally:
            self.world_database().chmod(0o600)
        self.assertEqual(self.saved(self.cleanup, self.recovered), self.dependent_bytes)
        self.completed(self.retired()); self.finish_session()

    def test_issuer_write_failure_preserves_local_receipt_and_allows_exact_retry(self):
        self.durable_cleanup(); before = self.issuer_rows()
        with sqlite3.connect(self.issuer.state / 'identity.db') as db:
            db.execute("CREATE TRIGGER deny_coordinator_retire BEFORE INSERT ON authority_retirement BEGIN SELECT RAISE(ABORT,'fixture'); END")
        try:
            self.request(expect_error=True, retire_world=self.owner)
            self.assertEqual(self.issuer_rows(), before)
            self.assertEqual(self.saved(self.cleanup, self.recovered), self.dependent_bytes)
        finally:
            with sqlite3.connect(self.issuer.state / 'identity.db') as db: db.execute('DROP TRIGGER deny_coordinator_retire')
        self.completed(self.retired()); self.finish_session()

    def test_local_commit_failure_preserves_committed_issuer_receipt_for_retry(self):
        self.durable_cleanup()
        blocker = sqlite3.connect(self.world_database())
        try:
            blocker.execute('BEGIN IMMEDIATE')
            self.request(expect_error=True, retire_world=self.owner)
            self.assertEqual(self.saved(self.cleanup, self.recovered), self.dependent_bytes)
            with sqlite3.connect(self.issuer.state / 'identity.db') as db:
                rows = db.execute('SELECT state FROM authority_retirement').fetchall()
            self.assertEqual(len(rows), 1, 'Issuer must commit before this injected local write failure')
            receipt = identity.AuthorityRetirementState.FromString(rows[0][0])
            issuer_after = self.issuer_rows()
        finally:
            blocker.rollback(); blocker.close()
        self.assertEqual(self.retired(), receipt)
        self.assertEqual(self.issuer_rows(), issuer_after)
        self.completed(receipt); self.finish_session()

    def test_completed_journal_rejects_tampering_and_reopen_cannot_launch(self):
        self.durable_cleanup(); receipt = self.retired(); self.completed(receipt)
        before = self.issuer_rows()
        changes = [lambda x: setattr(x.request, 'cleanup_sha256', b'x' * 32),
            lambda x: setattr(x, 'retirement_id', b'x' * 32)]
        for change in changes:
            altered = identity.AuthorityRetirementState(); altered.CopyFrom(receipt); change(altered)
            with sqlite3.connect(self.world_database()) as db:
                db.execute('UPDATE issuer_retirement SET record=?', (altered.SerializeToString(),))
            try:
                self.request(expect_error=True, retire_world=self.owner)
                self.assertEqual(self.issuer_rows(), before)
            finally:
                with sqlite3.connect(self.world_database()) as db:
                    db.execute('UPDATE issuer_retirement SET record=?', (receipt.SerializeToString(),))
        binary = durable.existing.existing.base.baseline.supervisor.owned.existing.PROBE
        requests = [launch.AuthorityLaunchRequest(protocol_version=1, request_id=1, inspect=launch.AuthorityLaunchQuery()),
            launch.AuthorityLaunchRequest(protocol_version=1, request_id=2, control_owner=launch.AuthorityLaunchQuery()),
            launch.AuthorityLaunchRequest(protocol_version=1, request_id=3, prepare=self.cleanup.binding),
            launch.AuthorityLaunchRequest(protocol_version=1, request_id=4,
                start=process.AuthorityProcessLaunch(executable='/usr/bin/true'))]
        result = subprocess.run([str(binary), str(self.world_journal), str(self.cgroup), '-1'],
            input=b''.join(frame(request) for request in requests), capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        for marker in (b'AddressSanitizer', b'LeakSanitizer', b'runtime error:'): self.assertNotIn(marker, result.stderr)
        stream = io.BytesIO(result.stdout)
        replies = [read_message(stream, launch.AuthorityLaunchReply) for _ in requests]
        self.assertEqual(replies[0].view.record, self.cleanup)
        for reply in replies[1:]: self.assertTrue(reply.HasField('error'), reply)
        self.assertEqual(stream.read(), b'')
        self.assertEqual(self.retired(), receipt); self.completed(receipt); self.finish_session()


if __name__ == '__main__':
    names = ['CoordinatorRetirementTests.' + name for name in CoordinatorRetirementTests.__dict__ if name.startswith('test_')]
    unittest.main(defaultTest=names, verbosity=2)
