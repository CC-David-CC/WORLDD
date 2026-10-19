"""Real issuer approval cannot override participant loss or uncertain local delivery."""
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import unittest
from google.protobuf import text_format
import test_authority_supervisor as existing
import issuer_pb2 as identity
import launch_pb2 as launch
import process_scope_pb2 as process


from harness.issuer_relay import IssuerRelay

class ApprovalInterruptionTests(existing.AuthoritySupervisorTests):
    def logs(self):
        return '\n'.join(path.read_text(errors='replace') for path in self.root.glob('supervisor-*.log'))

    def until(self, callback, predicate, message):
        deadline = time.monotonic() + 4
        while True:
            value = callback()
            if predicate(value): return value
            self.assertLess(time.monotonic(), deadline, message)
            time.sleep(.005)

    def approvals(self):
        with sqlite3.connect('file:' + str(self.issuer.state / 'identity.db') + '?mode=ro', uri=True) as db:
            return db.execute('SELECT authorization_id,participant_id,service_incarnation,launch_id,generation '
                'FROM authority_launch_authorization ORDER BY authorization_id').fetchall()

    def interrupted(self, mode):
        self.blocked_read = threading.Event(); self.approval_committed = threading.Event()
        self.approval = None
        def participant_reply(relay, request, reply):
            if mode != 'approval_reply_lost' and request.HasField('revocations') and request.revocations.HasField('after_revision'):
                self.assertTrue(reply.HasField('revocations'), reply)
                self.blocked_read.set()
                relay.stop.wait(4)
                return False
            return True
        self.participant_relay = IssuerRelay(self, self.issuer, participant_reply)
        def approval_reply(relay, request, reply):
            if not request.HasField('authority_launch_complete'): return True
            self.assertTrue(reply.HasField('authority_launch_authorization'), reply)
            self.approval = identity.AuthorityLaunchAuthorization()
            self.approval.CopyFrom(reply.authority_launch_authorization)
            self.approval_committed.set()
            if mode == 'approval_reply_lost':
                relay.drop(); return False
            self.assertTrue(self.blocked_read.wait(2), 'Participant never requested its next revocation read')
            if mode == 'participant_closed': self.participant_relay.drop()
            cause = 'Issuer TLS connection failed' if mode == 'participant_closed' else 'Issuer client deadline exceeded'
            self.until(self.logs, lambda text: 'stage=revocations cause=' + cause in text,
                'Participant did not record the injected interruption')
            return True
        self.approval_relay = IssuerRelay(self, self.issuer, approval_reply)
        config = self.config()
        runtime = identity.AuthorityParticipantRuntimeConfig()
        text_format.Parse(Path(config.participant.runtime_config).read_text(), runtime)
        runtime.issuer_client_config = str(self.issuer.config_file(self.participant_relay.port, io_timeout_ms=700))
        Path(config.participant.runtime_config).write_text(text_format.MessageToString(runtime))
        # The observer's approval call must outlive the explicit 700 ms fault-test participant deadline.
        config.authorization.issuer_client_config = str(self.issuer.config_file(self.approval_relay.port, io_timeout_ms=3000))
        self.marker = self.root / 'must-not-execute'
        config.launch.CopyFrom(process.AuthorityProcessLaunch(executable='/usr/bin/python3',
            arguments=['-c', 'from pathlib import Path;Path(' + repr(str(self.marker)) + ').touch()']))
        proc, channel = self.supervisor_process(config)
        reply = self.message(channel, launch.AuthorityLaunchReply)
        self.assertTrue(reply.HasField('error'), reply)
        expected = 'Issuer TLS connection failed' if mode == 'approval_reply_lost' else 'Participant failed during supervisor approval'
        self.assertIn(expected, reply.error.message)
        self.assertEqual(proc.wait(timeout=5), 2)
        self.assertTrue(self.approval_committed.is_set())
        record = self.checked_cleanup()
        self.assertEqual(reply.error.cleanup_record, record)
        self.assertFalse(record.HasField('authorization'))
        self.assertFalse(self.child_config.exists()); self.assertFalse(self.marker.exists())
        self.assertEqual(proc.stdout.read(), b''); self.assertEqual(self.activation_rows(), [])
        self.assertEqual(self.approval.request.service.service_incarnation, record.binding.service_incarnation)
        self.assertEqual(self.approval.request.launch_id, record.process.launch_id)
        self.assertEqual(self.approval.generation, 0)
        self.assertEqual(self.approvals(), [(self.approval.authorization_id, record.binding.participant_id,
            record.binding.service_incarnation, record.process.launch_id, 0)])
        for relay in (self.participant_relay, self.approval_relay):
            relay.close()
            self.assertEqual(relay.connections, 1)
        self.assertEqual(self.approval_relay.operations.count('authority_launch_begin'), 1)
        self.assertEqual(self.approval_relay.operations.count('authority_launch_complete'), 1)
        self.assertIsNone(self.server.poll())
        target = os.environ.get('SPATIAL_SECURITY_EVIDENCE')
        if target:
            Path(target).mkdir(parents=True, exist_ok=True)
            (Path(target) / (self._testMethodName + '-witness.json.log')).write_text(json.dumps(dict(
                injected_fault=mode, supervisor_error=reply.error.message, issuer_committed=True,
                issuer_generation=self.approval.generation, local_authorization=False, executed=False,
                process_scope_stopped=record.HasField('process_stopped'), native_scope_stopped=record.HasField('native_stopped'),
                participant_requests=self.participant_relay.operations, supervisor_requests=self.approval_relay.operations), indent=2) + '\n')
        return record

    def test_participant_loss_during_committed_approval_stops_scopes_and_allows_fresh_launch(self):
        old = self.interrupted('participant_closed')
        old_path = self.journal / 'launch.db'; old_bytes = old_path.read_bytes()
        self.journal = self.root / 'replay-journal'; self.journal.mkdir(mode=0o700)
        replay, channel = self.supervisor_process(self.config(), start_server=False)
        denied = self.message(channel, launch.AuthorityLaunchReply)
        self.assertTrue(denied.HasField('error'), denied)
        self.assertEqual(replay.wait(timeout=5), 2)
        rejected = self.checked_cleanup()
        self.assertEqual(denied.error.cleanup_record, rejected)
        self.assertFalse(rejected.HasField('authorization'))
        self.assertEqual(rejected.binding.service_incarnation, old.binding.service_incarnation)
        self.assertEqual(len(self.approvals()), 1); self.assertEqual(self.activation_rows(), [])
        self.assertFalse(self.child_config.exists()); self.assertFalse(self.marker.exists())
        self.journal = self.root / 'fresh-journal'; self.journal.mkdir(mode=0o700)
        self.binding.service_incarnation = os.urandom(32)
        proc, channel = self.supervisor_process(self.config(), start_server=False)
        initial = self.message(channel, launch.AuthorityLaunchReply)
        self.assertTrue(initial.HasField('view'), initial)
        self.auth = self.child_update(proc, 'authenticated').authenticated
        self.assertTrue(self.child_update(proc, 'revocations').revocations.initial)
        active = self.activate_child(proc)
        current = self.read_record()
        self.assertEqual(active.generation, 1)
        self.assertNotEqual(current.binding.service_incarnation, old.binding.service_incarnation)
        self.assertNotEqual(current.authorization.authorization_id, self.approval.authorization_id)
        self.assertNotEqual(current.process.launch_id, old.process.launch_id)
        self.assertNotEqual(current.native.credential_id, old.native.credential_id)
        self.assertEqual(old_path.read_bytes(), old_bytes); self.assertFalse(self.marker.exists())
        self.assertEqual(len(self.approvals()), 2)
        self.assertEqual([row for row in self.approvals() if row[0] == self.approval.authorization_id][0][-1], 0)
        self.assertTrue(self.control(channel, fence=launch.AuthorityLaunchQuery()).HasField('view'))
        self.assertEqual(proc.wait(timeout=5), 0); self.checked_cleanup()

    def test_participant_timeout_during_approval_never_executes(self):
        self.interrupted('participant_timeout')

    def test_lost_committed_approval_reply_never_retries_or_executes(self):
        self.interrupted('approval_reply_lost')

    def test_active_revocation_timeout_stops_scopes_without_completing_barrier(self):
        withheld = threading.Event()
        def participant_reply(relay, request, reply):
            if request.HasField('revocations') and reply.HasField('revocations') and reply.revocations.events:
                self.assertEqual(reply.revocations.through_revision, 1)
                withheld.set()
                relay.stop.wait(4)
                return False
            return True
        relay = IssuerRelay(self, self.issuer, participant_reply)
        config = self.config()
        runtime = identity.AuthorityParticipantRuntimeConfig()
        text_format.Parse(Path(config.participant.runtime_config).read_text(), runtime)
        runtime.issuer_client_config = str(self.issuer.config_file(relay.port, io_timeout_ms=700))
        Path(config.participant.runtime_config).write_text(text_format.MessageToString(runtime))
        proc, channel = self.running(config)
        active = self.activate_child(proc)
        before = self.activation_rows()
        self.issuer.peer = self.issuer.connect()
        self.revoke()
        self.assertTrue(withheld.wait(2), 'No committed revocation reply reached the relay')
        error = self.message(channel, launch.AuthorityLaunchReply)
        self.assertTrue(error.HasField('error'), error)
        self.assertEqual(proc.wait(timeout=5), 2)
        record = self.checked_cleanup()
        self.assertEqual(error.error.cleanup_record, record)
        self.assertIn('stage=revocations cause=Issuer client deadline exceeded', self.logs())
        self.assertTrue(record.HasField('fence'))
        self.assertEqual(record.fence.generation, active.generation)
        self.assertEqual(record.fence.service_incarnation, self.binding.service_incarnation)
        self.assertEqual(self.activation_rows(), before)
        self.issuer.peer = self.issuer.connect()
        barrier = self.barrier()
        self.assertFalse(barrier.complete)
        self.assertEqual((barrier.participant_count, barrier.pending_count), (1, 1))
        self.assertEqual(len(barrier.obligations), 1)
        obligation = barrier.obligations[0]
        self.assertEqual(obligation.registration, self.issuer.registration)
        self.assertEqual(obligation.generation, active.generation)
        self.assertEqual(obligation.service_incarnation, self.binding.service_incarnation)
        self.assertEqual(obligation.applied_revision, 0)
        self.assertFalse(obligation.complete)
        self.assertFalse(obligation.HasField('retirement'))
        relay.close()
        self.assertEqual(relay.connections, 1)
        self.assertEqual(relay.operations.count('authority_activate'), 1)
        self.assertNotIn('authority_applied', relay.operations)
        self.assertNotIn('authority_release', relay.operations)
        original_path = self.journal / 'launch.db'; original_journal = original_path.read_bytes()
        self.journal = self.root / 'unretired-replacement'; self.journal.mkdir(mode=0o700)
        self.binding.service_incarnation = os.urandom(32)
        replacement = self.config()
        self.child_config = self.root / 'unretired-child.textproto'
        replacement.child_participant_config = str(self.child_config)
        replacement.authorization.previous_generation = active.generation
        marker = self.root / 'unretired-must-not-execute'
        replacement.launch.CopyFrom(process.AuthorityProcessLaunch(executable='/usr/bin/python3',
            arguments=['-c', 'from pathlib import Path;Path(' + repr(str(marker)) + ').touch()']))
        denied_proc, denied_channel = self.supervisor_process(replacement, start_server=False)
        denied = self.message(denied_channel, launch.AuthorityLaunchReply)
        self.assertTrue(denied.HasField('error'), denied)
        self.assertIn(denied.error.message, ('Issuer rejected supervisor approval challenge',
            'Issuer did not approve the retained participant connection'))
        self.assertEqual(denied_proc.wait(timeout=5), 2)
        rejected = self.checked_cleanup()
        self.assertEqual(denied.error.cleanup_record, rejected)
        self.assertFalse(rejected.HasField('authorization'))
        self.assertFalse(self.child_config.exists())
        self.assertFalse(marker.exists())
        self.assertEqual(denied_proc.stdout.read(), b'')
        self.assertEqual(self.activation_rows(), before)
        self.assertEqual(len(self.approvals()), 1)
        self.assertEqual(original_path.read_bytes(), original_journal)
        self.issuer.peer = self.issuer.connect()
        self.assertEqual(self.barrier(), barrier)
        self.assertIsNone(self.server.poll())
        target = os.environ.get('SPATIAL_SECURITY_EVIDENCE')
        if target:
            Path(target).mkdir(parents=True, exist_ok=True)
            (Path(target) / (self._testMethodName + '-witness.json.log')).write_text(json.dumps(dict(
                injected_fault='committed_revocation_reply_withheld', generation=active.generation,
                pending_count=barrier.pending_count, applied_revision=obligation.applied_revision,
                fenced=True, process_scope_stopped=record.HasField('process_stopped'),
                native_scope_stopped=record.HasField('native_stopped'), replacement_executed=False,
                participant_requests=relay.operations), indent=2) + '\n')


if __name__ == '__main__':
    names = ['ApprovalInterruptionTests.' + name for name in ApprovalInterruptionTests.__dict__ if name.startswith('test_')]
    unittest.main(defaultTest=names, verbosity=2)
