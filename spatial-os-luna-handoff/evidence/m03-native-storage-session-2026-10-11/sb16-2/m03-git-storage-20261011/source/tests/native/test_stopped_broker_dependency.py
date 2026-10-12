"""Durable world recovery from checked broker scope cleanup, without fake reservations."""
import fcntl
import hashlib
import io
import os
from pathlib import Path
import select
import signal
import sqlite3
import subprocess
import unittest
import test_broker_retirement as existing
import broker_pb2 as broker
import issuer_pb2 as identity
import launch_pb2 as launch
import process_scope_pb2 as process
from harness.wire import frame, read_message


class StoppedBrokerDependencyTests(existing.BrokerRetirementTests):
    def world_database(self):
        return self.world_journal / 'launch.db'

    def world_local(self):
        with sqlite3.connect('file:' + str(self.world_database()) + '?mode=ro', uri=True) as db:
            return (db.execute('PRAGMA user_version').fetchone()[0], db.execute('SELECT record FROM launch').fetchone()[0])

    def stopped_pair(self):
        self.pin_broker(stop_world=False)
        self.world_cleanup = self.crash_world(); self.world_stopped = True
        signal.pidfd_send_signal(self.pins[self.broker_pid], signal.SIGKILL); self.exited()
        self.checked(); self.broker_retirement = self.retired(); self.completed(self.broker_retirement)

    def dependency(self):
        reply = self.request(recover_stopped_broker_world=self.owner)
        self.assertTrue(reply.HasField('dependent'), reply)
        return reply.dependent

    def saved(self, value):
        self.assertEqual(self.world_local(), (2, self.world_cleanup.SerializeToString()))
        with sqlite3.connect('file:' + str(self.world_database()) + '?mode=ro', uri=True) as db:
            rows = db.execute('SELECT record FROM dependent_cleanup').fetchall()
        self.assertEqual(rows, [(value.SerializeToString(),)])
        self.assertEqual(value.schema_version, 2)
        self.assertEqual(value.world, self.world_cleanup); self.assertEqual(value.broker, self.cleanup)
        self.assertFalse(value.HasField('controls'))
        return rows[0][0]

    def retire_world(self):
        reply = self.request(retire_world=self.owner)
        self.assertTrue(reply.HasField('retirement'), reply)
        return reply.retirement

    def world_retired(self, receipt, dependent_bytes):
        self.assertEqual(receipt.request.owner, self.owner.launch)
        self.assertEqual(receipt.request.fence_id, self.world_cleanup.fence.fence_id)
        self.assertEqual(receipt.request.cleanup_sha256, hashlib.sha256(dependent_bytes).digest())
        self.assertEqual(self.world_local(), (3, self.world_cleanup.SerializeToString()))
        with sqlite3.connect('file:' + str(self.world_database()) + '?mode=ro', uri=True) as db:
            self.assertEqual(db.execute('SELECT record FROM dependent_cleanup').fetchall(), [(dependent_bytes,)])
            self.assertEqual(db.execute('SELECT record FROM issuer_retirement').fetchall(), [(receipt.SerializeToString(),)])
        with sqlite3.connect('file:' + str(self.issuer.state / 'identity.db') + '?mode=ro', uri=True) as db:
            owner = (self.owner.launch.participant_id, self.owner.launch.generation)
            saved = db.execute('SELECT state FROM authority_retirement WHERE participant_id=? AND generation=?', owner).fetchone()[0]
            active = db.execute('SELECT active,applied_revision FROM authority_activation WHERE participant_id=? AND generation=?', owner).fetchone()
        self.assertEqual(saved, receipt.SerializeToString()); self.assertEqual(active, (0, receipt.applied_revision))

    def test_actual_pair_cleanup_persists_scope_proof_and_retires_world(self):
        self.stopped_pair(); before = self.issuer_rows()
        proof = self.dependency(); encoded = self.saved(proof)
        self.assertEqual(self.issuer_rows(), before)
        self.assertEqual(self.dependency(), proof)
        receipt = self.retire_world(); self.world_retired(receipt, encoded)
        after = self.issuer_rows()
        expected = [row[:-1] + (0,) if row[0] == self.owner.launch.participant_id else row
            for row in before['authority_activation']]
        self.assertEqual(after['authority_activation'], expected)
        self.assertEqual(len(after['authority_retirement']), 2)
        key = self.issuer.supervisor_key; original = key.read_bytes(); key.unlink()
        try:
            self.assertEqual(self.dependency(), proof)
            self.assertEqual(self.retire_world(), receipt)
            self.assertEqual(self.retired(), self.broker_retirement)
            self.assertEqual(self.issuer_rows(), after)
        finally: key.write_bytes(original); key.chmod(0o600)
        self.world_retired(receipt, encoded); self.completed(self.broker_retirement); self.finish()

    def test_live_unchecked_and_substituted_owners_cannot_recover(self):
        self.pin_broker(stop_world=False)
        self.request(expect_error=True, recover_stopped_broker_world=self.owner)
        self.world_cleanup = self.crash_world(); self.world_stopped = True
        self.request(expect_error=True, recover_stopped_broker_world=self.owner)
        signal.pidfd_send_signal(self.pins[self.broker_pid], signal.SIGKILL); self.exited()
        self.request(expect_error=True, recover_stopped_broker_world=self.owner)
        self.request(expect_error=True, retire_world=self.owner)
        self.checked(); before = self.issuer_rows()
        for field in ('generation', 'launch_id', 'authorization_id'):
            changed = broker.ControlOwner(); changed.CopyFrom(self.owner)
            setattr(changed.launch, field, changed.launch.generation + 1 if field == 'generation' else b'x' * 32)
            self.request(expect_error=True, recover_stopped_broker_world=changed)
            self.assertEqual(self.issuer_rows(), before)
        # Checked scope cleanup suffices for dependency recovery before broker retirement.
        encoded = self.saved(self.dependency())
        self.world_retired(self.retire_world(), encoded)
        with sqlite3.connect(self.issuer.state / 'identity.db') as db:
            self.assertEqual(db.execute('SELECT active FROM authority_activation WHERE participant_id=?',
                (self.original.binding.participant_id,)).fetchone(), (1,))
        self.completed(self.retired()); self.finish()

    def test_missing_world_supervisor_receipt_cannot_be_replaced_by_broker_cleanup(self):
        self.pin_broker(stop_world=False)
        status = Path('/proc/' + str(self.world_ids.worldd_pid) + '/status').read_text()
        pid = int(next(row.split()[1] for row in status.splitlines() if row.startswith('PPid:')))
        pin = os.pidfd_open(pid)
        try:
            signal.pidfd_send_signal(pin, signal.SIGKILL)
            self.assertEqual(select.select([pin], [], [], 8)[0], [pin])
        finally: os.close(pin)
        signal.pidfd_send_signal(self.pins[self.broker_pid], signal.SIGKILL); self.exited()
        self.checked(); before = self.issuer_rows()
        self.request(expect_error=True, stop_world=self.owner)
        self.request(expect_error=True, recover_stopped_broker_world=self.owner)
        self.request(expect_error=True, retire_world=self.owner)
        self.assertEqual(self.issuer_rows(), before)
        self.assertEqual(self.world_local()[0], 1)

    def test_missing_broker_supervisor_receipt_keeps_world_dependency_pending(self):
        self.pin_broker(stop_world=False)
        self.world_cleanup = self.crash_world(); before = self.issuer_rows()
        signal.pidfd_send_signal(self.pins[self.supervisor_pid], signal.SIGKILL)
        pin = self.pins[self.supervisor_pid]; self.assertEqual(select.select([pin], [], [], 8)[0], [pin])
        self.request(expect_error=True, stop_broker=launch.AuthorityLaunchQuery())
        self.request(expect_error=True, recover_stopped_broker_world=self.owner)
        self.request(expect_error=True, retire_world=self.owner)
        self.assertEqual(self.issuer_rows(), before)
        self.assertEqual(self.world_local(), (1, self.world_cleanup.SerializeToString()))

    def test_failed_local_storage_cannot_authorize_world_retirement(self):
        self.stopped_pair(); before = self.issuer_rows()
        descriptor = os.open(self.world_journal / 'launch.lock', os.O_RDWR | os.O_CLOEXEC)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.request(expect_error=True, recover_stopped_broker_world=self.owner)
        finally: os.close(descriptor)
        self.world_database().chmod(0o400)
        try: self.request(expect_error=True, recover_stopped_broker_world=self.owner)
        finally: self.world_database().chmod(0o600)
        blocker = sqlite3.connect(self.world_database())
        try:
            blocker.execute('BEGIN IMMEDIATE')
            self.request(expect_error=True, recover_stopped_broker_world=self.owner)
        finally: blocker.rollback(); blocker.close()
        self.request(expect_error=True, retire_world=self.owner)
        self.assertEqual(self.world_local(), (1, self.world_cleanup.SerializeToString()))
        self.assertEqual(self.issuer_rows(), before)
        encoded = self.saved(self.dependency()); self.world_retired(self.retire_world(), encoded); self.finish()

    def test_changed_launch_and_dependent_bytes_are_not_replaced_or_signed(self):
        self.stopped_pair(); before = self.issuer_rows(); original = self.world_cleanup.SerializeToString()
        changed = launch.AuthorityLaunchRecord(); changed.CopyFrom(self.world_cleanup); changed.revision += 1
        self.assertEqual(original[:2], b'\x08\x02')
        for encoded in (changed.SerializeToString(), original[2:] + original[:2], b'\xff'):
            with sqlite3.connect(self.world_database()) as db: db.execute('UPDATE launch SET record=?', (encoded,))
            try:
                self.request(expect_error=True, recover_stopped_broker_world=self.owner)
                self.assertEqual(self.issuer_rows(), before)
                self.assertEqual(self.world_local(), (1, encoded))
            finally:
                with sqlite3.connect(self.world_database()) as db: db.execute('UPDATE launch SET record=?', (original,))
        proof = self.dependency(); original = self.saved(proof)
        changes = [lambda x: setattr(x, 'schema_version', 99),
            lambda x: x.controls.SetInParent(),
            lambda x: setattr(x.broker.process_stopped.identity, 'launch_id', b'x' * 32),
            lambda x: setattr(x.broker.native, 'server_id', b'x' * 32),
            lambda x: setattr(x.broker, 'revision', x.broker.revision + 1)]
        encodings = []
        for change in changes:
            value = launch.AuthorityDependentCleanup(); value.CopyFrom(proof); change(value)
            encodings.append(value.SerializeToString())
        self.assertEqual(original[:2], b'\x08\x02')
        reordered = original[2:] + original[:2]
        self.assertEqual(launch.AuthorityDependentCleanup.FromString(reordered), proof)
        for encoded in encodings + [reordered, b'\xff']:
            with sqlite3.connect(self.world_database()) as db: db.execute('UPDATE dependent_cleanup SET record=?', (encoded,))
            try:
                self.request(expect_error=True, recover_stopped_broker_world=self.owner)
                self.request(expect_error=True, retire_world=self.owner)
                self.assertEqual(self.issuer_rows(), before)
                with sqlite3.connect(self.world_database()) as db:
                    self.assertEqual(db.execute('SELECT record FROM dependent_cleanup').fetchone()[0], encoded)
            finally:
                with sqlite3.connect(self.world_database()) as db: db.execute('UPDATE dependent_cleanup SET record=?', (original,))
        self.assertEqual(self.dependency(), proof)
        self.world_retired(self.retire_world(), original); self.finish()

    def test_existing_live_broker_proof_survives_broker_exit_without_replacement(self):
        self.pin_broker(stop_world=False)
        self.world_cleanup = self.crash_world(); self.world_stopped = True
        recovered = self.request(recover_world=self.owner).owner
        self.assertEqual(list(recovered.recovery.released), [self.lease])
        with sqlite3.connect(self.world_database()) as db:
            original = db.execute('SELECT record FROM dependent_cleanup').fetchone()[0]
        proof = launch.AuthorityDependentCleanup.FromString(original)
        self.assertEqual(proof.schema_version, 1); self.assertEqual(proof.broker, self.original)
        signal.pidfd_send_signal(self.pins[self.broker_pid], signal.SIGKILL); self.exited()
        self.checked(); self.broker_retirement = self.retired(); self.completed(self.broker_retirement)
        self.request(expect_error=True, recover_stopped_broker_world=self.owner)
        receipt = self.retire_world(); self.world_retired(receipt, original)
        self.request(expect_error=True, recover_stopped_broker_world=self.owner)
        self.assertEqual(self.retire_world(), receipt); self.finish()

    def test_issuer_and_local_failure_preserve_exact_scope_proof_for_retry(self):
        self.stopped_pair(); encoded = self.saved(self.dependency()); before = self.issuer_rows()
        with sqlite3.connect(self.issuer.state / 'identity.db') as db:
            db.execute("CREATE TRIGGER deny_scope_retire BEFORE INSERT ON authority_retirement BEGIN SELECT RAISE(ABORT,'fixture'); END")
        try:
            self.request(expect_error=True, retire_world=self.owner)
            self.assertEqual(self.issuer_rows(), before)
        finally:
            with sqlite3.connect(self.issuer.state / 'identity.db') as db: db.execute('DROP TRIGGER deny_scope_retire')
        blocker = sqlite3.connect(self.world_database())
        try:
            blocker.execute('BEGIN IMMEDIATE')
            self.request(expect_error=True, retire_world=self.owner)
            self.assertEqual(self.world_local(), (2, self.world_cleanup.SerializeToString()))
            with sqlite3.connect(self.issuer.state / 'identity.db') as db:
                rows = db.execute('SELECT state FROM authority_retirement WHERE participant_id=?',
                    (self.owner.launch.participant_id,)).fetchall()
            self.assertEqual(len(rows), 1, 'Issuer must commit before the local failure')
            receipt = identity.AuthorityRetirementState.FromString(rows[0][0]); after = self.issuer_rows()
        finally: blocker.rollback(); blocker.close()
        self.assertEqual(self.retire_world(), receipt); self.assertEqual(self.issuer_rows(), after)
        self.world_retired(receipt, encoded); self.finish()

    def test_reopened_dependency_and_retirement_journals_cannot_recreate_execution(self):
        self.stopped_pair(); encoded = self.saved(self.dependency())
        binary = existing.existing.existing.existing.base.baseline.supervisor.owned.existing.PROBE
        for version in (2, 3):
            if version == 3: self.world_retired(self.retire_world(), encoded)
            self.assertEqual(self.world_local()[0], version)
            requests = [launch.AuthorityLaunchRequest(protocol_version=1, request_id=1, inspect=launch.AuthorityLaunchQuery()),
                launch.AuthorityLaunchRequest(protocol_version=1, request_id=2, control_owner=launch.AuthorityLaunchQuery()),
                launch.AuthorityLaunchRequest(protocol_version=1, request_id=3, prepare=self.world_cleanup.binding),
                launch.AuthorityLaunchRequest(protocol_version=1, request_id=4, start=process.AuthorityProcessLaunch(executable='/usr/bin/true'))]
            result = subprocess.run([str(binary), str(self.world_journal), str(self.cgroup), '-1'],
                input=b''.join(frame(request) for request in requests), capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            for marker in (b'AddressSanitizer', b'LeakSanitizer', b'runtime error:'): self.assertNotIn(marker, result.stderr)
            stream = io.BytesIO(result.stdout); replies = [read_message(stream, launch.AuthorityLaunchReply) for _ in requests]
            self.assertEqual(replies[0].view.record, self.world_cleanup)
            for reply in replies[1:]: self.assertTrue(reply.HasField('error'), reply)
            self.assertEqual(stream.read(), b'')
        self.finish()


if __name__ == '__main__':
    names = ['StoppedBrokerDependencyTests.' + name for name in StoppedBrokerDependencyTests.__dict__ if name.startswith('test_')]
    unittest.main(defaultTest=names, verbosity=2)
