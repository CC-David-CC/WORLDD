"""Reproduce issuer loss before fencing using real retained native ownership."""
import json
import os
from pathlib import Path
import select
import signal
import socket
import sqlite3
import subprocess
import sys
import unittest
import time

import hashlib
import io

from google.protobuf import text_format
import test_stopped_broker_dependency as existing
import test_supervisor_registry as registry
from test_identity_issuer import BINARY
import issuer_pb2 as identity
import launch_pb2 as launch
import session_probe_pb2 as probe
from harness.wire import frame


class RestartableIssuer(registry.SupervisorRegistryTests):
    def launch(self, address='127.0.0.1'):
        if not hasattr(self, 'retained_listener'):
            self.retained_listener = socket.socket()
            self.retained_listener.bind((address, 0)); self.retained_listener.listen(32)
            self.port = self.retained_listener.getsockname()[1]
            self.addCleanup(self.retained_listener.close)
        self.config_path.write_text(text_format.MessageToString(self.config)); self.config_path.chmod(0o600)
        log = (self.root / ('tls-' + str(len(self.daemons)) + '.log')).open('wb')
        descriptor = self.retained_listener.fileno()
        proc = subprocess.Popen([str(BINARY), '--state-dir', str(self.state), '--listen-fd', str(descriptor),
            '--tls-config', str(self.config_path)], pass_fds=(descriptor,), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=log)
        self.daemons.append((proc, log)); return proc


class DeferredFenceTests(existing.StoppedBrokerDependencyTests):
    def setUp(self):
        super().setUp()
        self.evidence = Path(os.environ.get('SPATIAL_SECURITY_EVIDENCE') or self.root) / self._testMethodName
        self.evidence.mkdir(mode=0o700, parents=True, exist_ok=False)

    def issuer_fixture(self):
        return RestartableIssuer('runTest')

    def preserve(self, name, message):
        (self.evidence / (name + '.pb')).write_bytes(message.SerializeToString())

    def barrier(self):
        peer = self.issuer.connect()
        try:
            operation = identity.IssuerOperation(barrier_read=identity.AuthorityBarrierRead(revision=1))
            reply = self.issuer.execute(peer, self.issuer.challenge(peer, operation))
            self.assertTrue(reply.HasField('authority_barrier'), reply)
            return reply.authority_barrier
        finally: peer.close()

    def observe(self, **operation):
        self.coordinator_sequence += 1
        request = probe.SessionProbeRequest(protocol_version=1, request_id=self.coordinator_sequence, **operation)
        self.coordinator.stdin.write(frame(request)); self.coordinator.stdin.flush()
        reply = self.message(self.coordinator.stdout, probe.SessionProbeReply)
        self.assertEqual(reply.protocol_version, 1); self.assertEqual(reply.request_id, request.request_id)
        return reply

    def snapshot_processes(self, label):
        rows = []
        pins = {**getattr(self, 'pins', {}), **getattr(self, 'world_pins', {})}
        for pid, pin in pins.items():
            row = dict(pid=pid, pidfd=pin, exited=bool(select.select([pin], [], [], 0)[0]))
            for name in ('status', 'wchan', 'cgroup'):
                try: row[name] = Path('/proc', str(pid), name).read_text()
                except (FileNotFoundError, ProcessLookupError, PermissionError) as error: row[name] = type(error).__name__
            rows.append(row)
        (self.evidence / (label + '-processes.json')).write_text(json.dumps(dict(
            monotonic=time.monotonic(), broker_pid=self.broker_pid,
            broker_supervisor_pid=self.supervisor_pid, processes=rows), indent=2) + '\n')
        for path in self.issuer.root.glob('tls-*.log'):
            (self.evidence / (label + '-' + path.name)).write_bytes(path.read_bytes())

    def exited(self, include_supervisor=True):
        self.snapshot_processes('before-exit-wait')
        try: super().exited(include_supervisor)
        finally: self.snapshot_processes('after-exit-wait')

    def prepare_recovery(self):
        self.pin_broker(stop_world=False)
        self.preserve('world-before', self.world_record); self.preserve('broker-before', self.original)
        independent = self.supervisor.issue(workspace=b'issuer-loss-independent', application=b'survivor')
        survivor = self.connect(independent.cookie); survivor_identity = survivor.inspect()
        project_marker = self.project / 'issuer-loss-uncommitted.txt'; project_marker.write_text('preserved project data\n')
        world_pid = self.world_ids.worldd_pid
        supervisor_pid = int(next(row.split()[1] for row in Path('/proc/' + str(world_pid) + '/status').read_text().splitlines() if row.startswith('PPid:')))
        world_pins = {pid: os.pidfd_open(pid) for pid in (world_pid, self.world_ids.spatialwm_pid, supervisor_pid)}
        self.world_pins = world_pins
        for pin in world_pins.values(): self.addCleanup(os.close, pin)
        issuer_proc = self.issuer.daemons[-1][0]
        issuer_pin = os.pidfd_open(issuer_proc.pid); self.addCleanup(os.close, issuer_pin)
        peer = self.issuer.connect()
        self.addCleanup(peer.close)
        operation = identity.IssuerOperation(identity_revoke=identity.IdentityRevoke(
            target=identity.IdentityReference(kind=identity.CREDENTIAL_TARGET, id=self.claims.credential_id),
            expected_security_revision=0, reason='issuer loss before native scope fencing'))
        challenge = self.issuer.challenge(peer, operation)
        for pin in (world_pins[world_pid], self.pins[self.broker_pid]): signal.pidfd_send_signal(pin, signal.SIGSTOP)
        try:
            committed = self.issuer.execute(peer, challenge)
            self.assertTrue(committed.HasField('identity_status'), committed)
            before = self.barrier(); self.preserve('pending-before', before)
            self.assertEqual((before.participant_count, before.pending_count, before.complete), (2, 2, False))
            self.assertTrue(all(item.applied_revision == 0 and not item.complete for item in before.obligations))
            signal.pidfd_send_signal(issuer_pin, signal.SIGKILL)
            self.assertEqual(issuer_proc.wait(timeout=3), -signal.SIGKILL)
        finally:
            # Resume only still-live fixture children; their supervisors own further teardown.
            for pin in (world_pins[world_pid], self.pins[self.broker_pid]):
                try: signal.pidfd_send_signal(pin, signal.SIGCONT)
                except ProcessLookupError: pass
        # Identity loss disables service authority. A separate owned child crash starts supervisor recovery.
        for pin in (world_pins[world_pid], self.pins[self.broker_pid]):
            signal.pidfd_send_signal(pin, signal.SIGKILL)
        self.exited()
        for pin in world_pins.values(): self.assertEqual(select.select([pin], [], [], 8)[0], [pin])
        self.world_cleanup = self.request(stop_world=self.owner).cleanup; self.world_stopped = True
        self.cleanup = self.request(stop_broker=launch.AuthorityLaunchQuery()).cleanup
        for name, actual, original in (('world-cleanup', self.world_cleanup, self.world_record), ('broker-cleanup', self.cleanup, self.original)):
            self.assertEqual(actual.stage, launch.OWNED_SCOPES_STOPPED)
            self.assertEqual(actual.process_stopped.identity, original.process)
            self.assertEqual(actual.native_stopped.identity, original.native)
            self.assertFalse((self.cgroup / actual.process.scope_name).exists())
            self.preserve(name, actual)
        self.assertEqual(survivor.inspect(), survivor_identity)
        self.assertEqual(project_marker.read_text(), 'preserved project data\n')
        before_restart = self.issuer.snapshot_all()
        original_port = self.issuer.port
        self.issuer.peer = self.issuer.ready()
        self.assertEqual(self.issuer.port, original_port)
        self.assertEqual(self.issuer.snapshot_all(), before_restart)
        after = self.barrier(); self.preserve('pending-after-restart', after)
        self.assertEqual(after, before)
        self.issuer.closed(peer)
        dependent = self.dependency(); self.saved(dependent); self.preserve('dependent-cleanup', dependent)
        self.dependent_bytes = dependent.SerializeToString()
        self.assertFalse(self.world_cleanup.HasField('fence'))
        self.assertFalse(self.cleanup.HasField('fence'))
        self.survivor = survivor; self.survivor_identity = survivor_identity; self.project_marker = project_marker

    def targets(self):
        return ((self.world_database(), self.world_cleanup, self.dependent_bytes, dict(retire_world=self.owner), 5, 7),
            (self.database(), self.cleanup, self.cleanup.SerializeToString(), dict(retire_broker=launch.AuthorityLaunchQuery()), 6, 8))

    def originals(self):
        for path, record, encoded, operation, pending_schema, retired_schema in self.targets():
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT record FROM launch').fetchall(), [(record.SerializeToString(),)])
                if pending_schema == 5:
                    self.assertEqual(db.execute('SELECT record FROM dependent_cleanup').fetchall(), [(encoded,)])

    def issuer_restart(self):
        proc = self.issuer.daemons[-1][0]; pin = os.pidfd_open(proc.pid)
        try: signal.pidfd_send_signal(pin, signal.SIGKILL); self.assertEqual(proc.wait(timeout=3), -signal.SIGKILL)
        finally: os.close(pin)
        before = self.issuer.snapshot_all(); old_port = self.issuer.port
        self.issuer.peer = self.issuer.ready()
        self.assertEqual(self.issuer.port, old_port); self.assertEqual(self.issuer.snapshot_all(), before)

    def finish_recovery(self):
        for path, record, encoded, operation, pending_schema, retired_schema in self.targets():
            receipt = self.request(**operation).retirement
            self.assertEqual(receipt.applied_revision, 0)
            self.assertEqual(receipt.request.cleanup_sha256, hashlib.sha256(encoded).digest())
            self.assertEqual(receipt.request.owner.participant_id, record.binding.participant_id)
            self.assertEqual(receipt.request.owner.launch_id, record.process.launch_id)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], retired_schema)
                fences = db.execute('SELECT record FROM recovery_fence').fetchall(); self.assertEqual(len(fences), 1)
                fence = identity.AuthorityFenceState.FromString(fences[0][0])
                self.assertEqual(fence.fence_id, receipt.request.fence_id)
                self.assertEqual(fence.launch_id, record.process.launch_id)
                self.assertEqual(db.execute('SELECT record FROM issuer_retirement').fetchall(), [(receipt.SerializeToString(),)])
            self.preserve('world-fence' if pending_schema == 5 else 'broker-fence', fence)
            self.preserve('world-retirement' if pending_schema == 5 else 'broker-retirement', receipt)
            # An already verified retry needs neither a new key read nor another remote effect.
            key = self.issuer.supervisor_key; original = key.read_bytes(); key.unlink()
            try: self.assertEqual(self.request(**operation).retirement, receipt)
            finally: key.write_bytes(original); key.chmod(0o600)
            self.originals()
        final = self.barrier(); self.preserve('final-barrier', final)
        self.assertTrue(final.complete); self.assertEqual(final.pending_count, 0)
        self.assertEqual(final.participant_count, 2)
        self.assertTrue(all(row.applied_revision == 0 and row.HasField('retirement') for row in final.obligations))
        self.assertEqual(self.survivor.inspect(), self.survivor_identity)
        self.assertEqual(self.project_marker.read_text(), 'preserved project data\n')
        self.finish_session()

    def test_issuer_restart_retires_exact_failed_generations(self):
        self.prepare_recovery(); self.finish_recovery()

    def test_failed_local_fence_commit_retries_original_remote_fence(self):
        self.prepare_recovery()
        for path, record, encoded, operation, pending_schema, retired_schema in self.targets():
            blocker = sqlite3.connect(path)
            try:
                blocker.execute('BEGIN IMMEDIATE')
                self.request(expect_error=True, **operation)
                with sqlite3.connect(path) as db:
                    self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2 if pending_schema == 5 else 1)
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='recovery_fence'").fetchone()[0], 0)
            finally: blocker.rollback(); blocker.close()
            with sqlite3.connect(self.issuer.state / 'identity.db') as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM authority_fence WHERE participant_id=?',
                    (record.binding.participant_id,)).fetchone()[0], 1)
            self.assertEqual(self.barrier().pending_count, 2); self.originals()
        self.issuer_restart(); self.finish_recovery()

    def pending_fences(self):
        with sqlite3.connect(self.issuer.state / 'identity.db') as db:
            db.execute("CREATE TRIGGER deny_deferred_retire BEFORE INSERT ON authority_retirement BEGIN SELECT RAISE(ABORT,'fixture'); END")
        try:
            for path, record, encoded, operation, pending_schema, retired_schema in self.targets():
                self.request(expect_error=True, **operation)
                with sqlite3.connect(path) as db:
                    self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], pending_schema)
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM recovery_fence').fetchone()[0], 1)
                self.originals(); self.assertEqual(self.barrier().pending_count, 2)
        finally:
            with sqlite3.connect(self.issuer.state / 'identity.db') as db: db.execute('DROP TRIGGER deny_deferred_retire')

    def test_pending_fences_survive_restart_and_reject_changed_fence(self):
        self.prepare_recovery(); self.pending_fences(); self.issuer_restart()
        before = self.issuer.snapshot_all()
        for path, record, encoded, operation, pending_schema, retired_schema in self.targets():
            with sqlite3.connect(path) as db: original = db.execute('SELECT record FROM recovery_fence').fetchone()[0]
            altered = identity.AuthorityFenceState.FromString(original); altered.generation += 1
            for value in (altered.SerializeToString(), b'\xff', None):
                with sqlite3.connect(path) as db:
                    db.execute('DELETE FROM recovery_fence')
                    if value is not None: db.execute('INSERT INTO recovery_fence VALUES(1,?)', (value,))
                try:
                    self.request(expect_error=True, **operation); self.assertEqual(self.issuer.snapshot_all(), before)
                finally:
                    with sqlite3.connect(path) as db:
                        db.execute('DELETE FROM recovery_fence'); db.execute('INSERT INTO recovery_fence VALUES(1,?)', (original,))
        self.finish_recovery()

    def test_wrong_key_and_changed_cleanup_cannot_request_a_fence(self):
        self.prepare_recovery(); before = self.issuer.snapshot_all()
        key = self.issuer.supervisor_key; original_key = key.read_bytes()
        wrong, _ = self.issuer.key('deferred-wrong-key'); key.write_bytes(wrong.read_bytes())
        try:
            for path, record, encoded, operation, pending_schema, retired_schema in self.targets():
                rejected = self.request(expect_error=True, **operation)
                self.assertIn('signing key differs', rejected.error.message)
                self.assertEqual(self.issuer.snapshot_all(), before)
        finally: key.write_bytes(original_key)
        for path, record, encoded, operation, pending_schema, retired_schema in self.targets():
            original = record.SerializeToString()
            altered = launch.AuthorityLaunchRecord.FromString(original); altered.ClearField('native_stopped')
            for value in (original[2:] + original[:2], altered.SerializeToString()):
                with sqlite3.connect(path) as db: db.execute('UPDATE launch SET record=?', (value,))
                try:
                    self.request(expect_error=True, **operation); self.assertEqual(self.issuer.snapshot_all(), before)
                finally:
                    with sqlite3.connect(path) as db: db.execute('UPDATE launch SET record=?', (original,))
        self.finish_recovery()

    def test_remote_retirement_commit_survives_failed_local_write(self):
        self.prepare_recovery(); self.pending_fences()
        for path, record, encoded, operation, pending_schema, retired_schema in self.targets():
            blocker = sqlite3.connect(path)
            try:
                blocker.execute('BEGIN IMMEDIATE'); self.request(expect_error=True, **operation)
                with sqlite3.connect(path) as db: self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], pending_schema)
                with sqlite3.connect(self.issuer.state / 'identity.db') as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM authority_retirement WHERE participant_id=?',
                        (record.binding.participant_id,)).fetchone()[0], 1)
            finally: blocker.rollback(); blocker.close()
        self.issuer_restart(); before = self.issuer.snapshot_all(); self.finish_recovery()
        self.assertEqual(self.issuer.snapshot_all(), before)


if __name__ == '__main__':
    names = ['DeferredFenceTests.' + name for name in DeferredFenceTests.__dict__ if name.startswith('test_')]
    unittest.main(defaultTest=names, verbosity=2)
