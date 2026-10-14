"""Operator success follows real native cancellation or checked recovery."""
import hashlib
import io
import json
import os
from pathlib import Path
import select
import signal
import sqlite3
import subprocess
import unittest

import test_session_application as application
import core_pb2 as core
import issuer_pb2 as identity
import launch_pb2 as launch
import world_session_pb2 as session
from harness.wire import frame, read_message


class OperatorRevocationTests(application.SessionApplicationTests):
    def setUp(self):
        super().setUp()
        self.evidence = Path(os.environ['SPATIAL_SECURITY_EVIDENCE']) / self._testMethodName
        self.evidence.mkdir(parents=True, exist_ok=False)
        self.commands = []
        self.addCleanup(self.preserve_commands)

    def preserve_commands(self):
        for index, (proc, log) in enumerate(self.commands):
            if proc.poll() is None:
                proc.terminate(); proc.wait(timeout=3)
            log.close()
            if proc.stdin and not proc.stdin.closed: proc.stdin.close()
            if proc.stdout and not proc.stdout.closed: proc.stdout.close()
            (self.evidence / f'command-{index}-exit.txt').write_text(str(proc.returncode) + '\n')

    def save(self, name, message):
        (self.evidence / (name + '.pb')).write_bytes(message.SerializeToString())

    def pause_world(self):
        self.held_application()
        self.marker = self.project / 'operator-uncommitted.txt'
        self.marker.write_text('preserved after credential revocation\n')
        self.world_pin = os.pidfd_open(self.world_ids.worldd_pid)
        self.addCleanup(os.close, self.world_pin)
        self.addCleanup(self.resume_world)
        signal.pidfd_send_signal(self.world_pin, signal.SIGSTOP)
        status = Path('/proc', str(self.world_ids.worldd_pid), 'status')
        self.until(lambda: status.read_text(), lambda text: 'State:\tT' in text)
        self.save('world-before', self.world_record)
        self.save('broker-before', self.read_record())
        self.save('owner-before', self.request(inspect=self.owner).owner)
        self.target = identity.IdentityReference(kind=identity.CREDENTIAL_TARGET, id=self.claims.credential_id)

    def resume_world(self):
        try: signal.pidfd_send_signal(self.world_pin, signal.SIGCONT)
        except ProcessLookupError: pass

    def start_command(self, wait=5000, resume=False):
        command = identity.RevocationCommand(protocol_version=1, wait_ms=wait)
        if resume:
            command.resume.CopyFrom(identity.RevocationCompletionRead(target=self.target, revision=1))
        else:
            command.revoke.CopyFrom(identity.IdentityRevoke(target=self.target,
                expected_security_revision=0, reason='native operator completion witness'))
        binary = Path(self.world_config_binary()).with_name('spatial-revoke')
        self.assertTrue(binary.is_file(), 'Production spatial-revoke executable is missing')
        index = len(self.commands)
        log = (self.evidence / f'command-{index}.log').open('wb')
        proc = subprocess.Popen([str(binary), '--config', str(self.issuer.config_file()),
            '--key', str(self.issuer.owner)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log)
        self.commands.append((proc, log))
        self.save(f'command-{index}-request', command)
        proc.stdin.write(frame(command)); proc.stdin.close(); proc.stdin = None
        return proc

    def world_config_binary(self):
        # This is the exact executable configuration already used by the retained session.
        config = launch.AuthoritySupervisorConfig()
        from google.protobuf import text_format
        text_format.Parse((self.root / 'session-world.textproto').read_text(), config)
        return config.launch.executable

    def barrier(self):
        peer = self.issuer.connect()
        try:
            operation = identity.IssuerOperation(barrier_read=identity.AuthorityBarrierRead(revision=1))
            reply = self.issuer.execute(peer, self.issuer.challenge(peer, operation))
            self.assertTrue(reply.HasField('authority_barrier'), reply)
            return reply.authority_barrier
        finally: peer.close()

    def committed(self, proc):
        peer = self.issuer.connect()
        try:
            status = self.until(lambda: peer.call(credential_check=self.actor.credential).identity_status,
                lambda value: value.security_revision == 1)
        finally: peer.close()
        self.assertTrue(status.credential_revoked); self.assertFalse(status.actor_revoked)
        self.save('committed-identity', status)
        pending = self.barrier(); self.save('barrier-held', pending)
        self.assertEqual(pending.participant_count, 2)
        self.assertGreaterEqual(pending.pending_count, 1); self.assertFalse(pending.complete)
        owned = [item for item in pending.obligations if item.registration.participant.id == self.owner.launch.participant_id]
        self.assertEqual(len(owned), 1); self.assertEqual(owned[0].applied_revision, 0)
        self.assertFalse(owned[0].complete)
        self.assertIsNone(proc.poll())
        self.assertFalse(select.select([proc.stdout], [], [], 0)[0], 'Command replied while native cancellation was pending')
        rows = self.events()
        self.assertTrue(all(any(row.get('event') == event and row.get('pressed') for row in rows) for event in ('key', 'button')))
        self.assertFalse(any(row.get('event') in ('key', 'button') and not row.get('pressed') for row in rows))
        (self.evidence / 'native-held.json').write_text(json.dumps(rows, indent=2) + '\n')

    def finish_command(self, proc, state):
        index = next(index for index, item in enumerate(self.commands) if item[0] is proc)
        output, _ = proc.communicate(timeout=8)
        (self.evidence / f'command-{index}-reply.frame').write_bytes(output)
        stream = io.BytesIO(output); result = read_message(stream, identity.RevocationResult)
        self.assertEqual(stream.read(), b'')
        self.assertEqual(result.protocol_version, 1); self.assertEqual(result.target, self.target)
        self.assertEqual(result.state, state, result); self.assertEqual(result.revision, 1)
        self.assertEqual(proc.returncode, 0 if state == identity.REVOCATION_COMPLETE else 1)
        self.save(f'command-{index}-reply', result)
        return result

    def assert_cancelled(self, result, retired=False):
        self.assertTrue(result.barrier.complete); self.assertEqual(result.barrier.pending_count, 0)
        self.assertEqual(result.barrier.participant_count, 2)
        self.assertEqual(result.barrier.target, self.target)
        self.released()  # Independent confined X client sees releases and responds to a resize command.
        state = self.request(inspect=self.owner).owner; self.save('owner-after', state)
        self.assertFalse(state.reservations); self.assertEqual(state.channel_fenced, retired)
        self.assertEqual(self.marker.read_text(), 'preserved after credential revocation\n')
        try:
            reply = self.actor_call(self.stream, inspect=session.WorldSessionInspect())
            self.assertEqual(reply.error.code, core.UNAUTHORIZED, reply)
        except (EOFError, ConnectionError): pass
        barrier = self.barrier(); self.save('barrier-after', barrier)
        self.assertTrue(barrier.complete)
        self.assertEqual(sum(item.HasField('retirement') for item in barrier.obligations), int(retired))
        (self.evidence / 'native-released.json').write_text(json.dumps(self.events(), indent=2) + '\n')

    def test_completion_waits_for_real_key_button_and_lease_release(self):
        self.pause_world()
        command = self.start_command(); self.committed(command)
        self.resume_world()
        result = self.finish_command(command, identity.REVOCATION_COMPLETE)
        self.assert_cancelled(result)
        self.request(stop_world=self.owner); self.finish_session()

    def test_pending_timeout_resumes_without_another_revocation(self):
        self.pause_world()
        command = self.start_command(wait=300); self.committed(command)
        result = self.finish_command(command, identity.REVOCATION_COMMITTED)
        self.assertEqual(result.error.code, core.TIMEOUT)
        self.assertGreater(result.barrier.pending_count, 0)
        self.assertFalse(any(row.get('event') in ('key', 'button') and not row.get('pressed') for row in self.events()))
        self.resume_world()
        result = self.finish_command(self.start_command(resume=True), identity.REVOCATION_COMPLETE)
        self.assert_cancelled(result)
        with sqlite3.connect('file:' + str(self.issuer.state / 'identity.db') + '?mode=ro', uri=True) as db:
            self.assertEqual(db.execute('SELECT revision FROM revocation').fetchall(), [(1,)])
        self.request(stop_world=self.owner); self.finish_session()

    def test_crash_requires_checked_recovery_and_retirement_before_completion(self):
        self.pause_world()
        command = self.start_command(wait=10000); self.committed(command)
        cleanup = self.crash_world(); self.save('crashed-world-cleanup', cleanup)
        self.assertTrue(cleanup.HasField('fence'))
        self.request(expect_error=True, retire_world=self.owner)
        self.assertGreater(self.barrier().pending_count, 0)
        self.assertIsNone(command.poll())
        recovered = self.request(recover_world=self.owner).owner
        self.save('recovered-owner', recovered)
        self.assertFalse(recovered.reservations); self.assertEqual(list(recovered.recovery.released), [self.lease])
        pending = self.barrier(); self.save('barrier-before-retirement', pending)
        self.assertGreater(pending.pending_count, 0); self.assertIsNone(command.poll())
        with sqlite3.connect('file:' + str(self.world_journal / 'launch.db') + '?mode=ro', uri=True) as db:
            dependent = db.execute('SELECT record FROM dependent_cleanup').fetchone()[0]
        receipt = self.request(retire_world=self.owner).retirement; self.save('world-retirement', receipt)
        self.assertEqual(receipt.applied_revision, 0)
        self.assertEqual(receipt.request.cleanup_sha256, hashlib.sha256(dependent).digest())
        result = self.finish_command(command, identity.REVOCATION_COMPLETE)
        self.assert_cancelled(result, retired=True)
        self.finish_session()


if __name__ == '__main__':
    suite = unittest.TestSuite(OperatorRevocationTests(name) for name in OperatorRevocationTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
