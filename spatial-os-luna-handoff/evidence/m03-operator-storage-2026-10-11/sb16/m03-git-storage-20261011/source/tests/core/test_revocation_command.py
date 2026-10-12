"""Operator completion against real issuer state and independent TLS response faults."""
import io
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest

import core_pb2 as core
import issuer_pb2 as pb
import key_pb2 as keys
from harness.wire import frame
from test_identity_issuer import read_message
from test_issuer_client import ClientFixture, FaultPeer
import test_identity_participant_barrier as barrier_fixture

REVOKE = Path(sys.argv.pop(1))


class RevocationCommandTests(ClientFixture):
    register_service = barrier_fixture.BarrierTests.register_service
    authenticate = barrier_fixture.BarrierTests.authenticate
    activate = barrier_fixture.BarrierTests.activate
    applied = barrier_fixture.BarrierTests.applied
    barrier = barrier_fixture.BarrierTests.barrier

    def setUp(self):
        super().setUp()
        self.peer = self.ready()
        self.registry_revision = 0
        self.record = self.decide(self.peer, self.enroll(self.peer)).enrollment
        self.subject = self.claims(self.record, self.info)
        self.target = pb.IdentityReference(kind=pb.ACTOR_TARGET, id=self.subject.actor_id)
        self.command_runs = []
        self.addCleanup(self.preserve_commands)

    def preserve_commands(self):
        destination = os.environ.get('SPATIAL_COMMAND_EVIDENCE')
        for index, (proc, log, request) in enumerate(self.command_runs):
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=3)
            log.close()
            if destination:
                out = Path(destination) / self.id() / str(index)
                out.mkdir(parents=True, exist_ok=False)
                (out / 'request.pb').write_bytes(request)
                (out / 'stderr.log').write_bytes(Path(log.name).read_bytes())
                if hasattr(proc, 'retained_output'):
                    (out / 'reply.pb').write_bytes(proc.retained_output)
                (out / 'exit.txt').write_text(str(proc.returncode) + '\n')
            for stream in (proc.stdin, proc.stdout):
                if stream and not stream.closed:
                    stream.close()

    def command(self, revision=0, wait=1000, target=None):
        return pb.RevocationCommand(protocol_version=1, wait_ms=wait,
            revoke=pb.IdentityRevoke(target=target or self.target,
                expected_security_revision=revision, reason='operator command fixture'))

    def start_command(self, command, port=None, owner=None, raw=None):
        self.assertTrue(REVOKE.is_file(), 'Production spatial-revoke executable is missing')
        log = (self.root / f'revoke-{len(self.command_runs)}.log').open('wb')
        request = frame(command) if raw is None else raw
        proc = subprocess.Popen([str(REVOKE), '--config', str(self.config_file(port)),
            '--key', str(owner or self.owner)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log)
        self.command_runs.append((proc, log, request))
        proc.stdin.write(request)
        proc.stdin.close()
        proc.stdin = None
        return proc

    def finish_command(self, proc, state):
        output, _ = proc.communicate(timeout=5)
        proc.retained_output = output
        stream = io.BytesIO(output)
        result = read_message(stream, pb.RevocationResult)
        self.assertEqual(stream.read(), b'')
        self.assertEqual(result.protocol_version, 1)
        self.assertEqual(result.state, state, result)
        self.assertEqual(proc.returncode, 0 if state == pb.REVOCATION_COMPLETE else 1)
        return result

    def service_pending(self):
        self.service = self.register_service()
        self.activate()

    def wait_for_commit(self):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = self.peer.call(credential_check=self.record.credential).identity_status
            if status.security_revision == 1:
                return
            time.sleep(.01)
        self.fail('Production command did not commit the revocation')

    def test_no_participants_completes_actual_actor_revocation(self):
        result = self.finish_command(self.start_command(self.command()), pb.REVOCATION_COMPLETE)
        self.assertEqual(result.revision, 1)
        self.assertEqual(result.barrier.target, self.target)
        self.assertEqual(result.barrier.participant_count, 0)
        self.assertFalse(result.HasField('error'))
        self.assertFalse(self.peer.call(credential_check=self.record.credential).identity_status.active)

    def test_credential_revocation_keeps_actor_identity_valid(self):
        target = pb.IdentityReference(kind=pb.CREDENTIAL_TARGET, id=self.subject.credential_id)
        result = self.finish_command(self.start_command(self.command(target=target)), pb.REVOCATION_COMPLETE)
        self.assertEqual(result.barrier.target, target)
        status = self.peer.call(credential_check=self.record.credential).identity_status
        self.assertTrue(status.credential_revoked)
        self.assertFalse(status.actor_revoked)

    def test_pending_cancellation_times_out_with_committed_receipt(self):
        self.service_pending()
        result = self.finish_command(self.start_command(self.command(wait=200)), pb.REVOCATION_COMMITTED)
        self.assertEqual(result.error.code, core.TIMEOUT)
        self.assertEqual((result.revision, result.barrier.pending_count), (1, 1))
        self.assertFalse(result.barrier.complete)
        self.assertEqual(self.barrier().pending_count, 1)

    def test_command_waits_for_authenticated_application_acknowledgement(self):
        self.service_pending()
        proc = self.start_command(self.command(wait=2000))
        self.wait_for_commit()
        self.assertEqual(self.barrier().pending_count, 1)
        self.assertIsNone(proc.poll())
        self.assertTrue(self.applied(0, 1).HasField('authority_activation'))
        result = self.finish_command(proc, pb.REVOCATION_COMPLETE)
        self.assertEqual(result.barrier.pending_count, 0)

    def test_resume_is_read_only_and_cannot_substitute_another_target(self):
        self.finish_command(self.start_command(self.command()), pb.REVOCATION_COMPLETE)
        before = self.snapshot()
        for target, expected in ((self.target, pb.REVOCATION_COMPLETE),
            (pb.IdentityReference(kind=pb.ACTOR_TARGET, id=b'x'*32), pb.REVOCATION_COMMIT_UNCERTAIN)):
            request = pb.RevocationCommand(protocol_version=1,
                resume=pb.RevocationCompletionRead(target=target, revision=1))
            self.finish_command(self.start_command(request), expected)
            self.assertEqual(self.snapshot(), before)

    def test_resume_after_issuer_restart_preserves_pending_obligation(self):
        self.service_pending()
        self.finish_command(self.start_command(self.command(wait=200)), pb.REVOCATION_COMMITTED)
        self.peer.close()
        self.service['peer'].close()
        self.stop_daemons()
        self.peer = self.ready()
        before = self.snapshot()
        request = pb.RevocationCommand(protocol_version=1, wait_ms=200,
            resume=pb.RevocationCompletionRead(target=self.target, revision=1))
        result = self.finish_command(self.start_command(request), pb.REVOCATION_COMMITTED)
        self.assertEqual(result.barrier.pending_count, 1)
        self.assertEqual(self.snapshot(), before)

    def test_stale_revision_duplicate_target_and_wrong_owner_do_not_mutate(self):
        before = self.snapshot()
        wrong, _ = self.key('wrong-owner')
        self.finish_command(self.start_command(self.command(), owner=wrong), pb.REVOCATION_REJECTED)
        result = self.finish_command(self.start_command(self.command(revision=1)), pb.REVOCATION_REJECTED)
        self.assertEqual(result.error.code, core.REVISION_CONFLICT)
        self.assertEqual(self.snapshot(), before)
        self.finish_command(self.start_command(self.command()), pb.REVOCATION_COMPLETE)
        before = self.snapshot()
        result = self.finish_command(self.start_command(self.command(revision=1)), pb.REVOCATION_REJECTED)
        self.assertEqual(result.error.code, core.INVALID_ARGUMENT)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_requests_fail_before_contacting_issuer(self):
        peer = FaultPeer(self)
        for raw in (b'', b'\0\0\0\5x', frame(self.command())*2,
                    frame(pb.RevocationCommand(protocol_version=1)),
                    frame(self.command(wait=0)), frame(self.command(revision=2**63))):
            with self.subTest(raw=raw[:12]):
                self.finish_command(self.start_command(None, port=peer.port, raw=raw), pb.REVOCATION_REJECTED)
        self.assertEqual(peer.connections, 0)

    def fault_peer(self, mode):
        upstream = self.connect()
        mutations = []
        operations = {}

        def respond(peer, request, ignored):
            wanted = request.request_id
            reply = upstream.request(request)
            reply.request_id = wanted
            if request.HasField('challenge') and reply.HasField('challenge'):
                operations[reply.challenge.nonce] = request.challenge
                if request.challenge.HasField('identity_revoke') and mode.startswith('challenge-'):
                    content = pb.IssuerChallenge.FromString(reply.challenge.signing_request.payload)
                    if mode == 'challenge-target': content.operation.identity_revoke.target.id = b'x'*32
                    elif mode == 'challenge-owner': content.owner_public_key = b'x'*32
                    elif mode == 'challenge-issuer': content.issuer_id = b'x'*32
                    elif mode == 'challenge-purpose': reply.challenge.signing_request.purpose = keys.ENROLLMENT_PROOF
                    reply.challenge.signing_request.payload = content.SerializeToString()
            elif request.HasField('execute'):
                operation = operations[request.execute.nonce]
                if operation.HasField('identity_revoke'):
                    mutations.append(request)
                    if mode == 'lost-commit':
                        peer.stream.shutdown(socket.SHUT_RDWR)
                        raise EOFError()
                    if mode == 'commit-target': reply.identity_status.actor_id = b'x'*32
                    elif mode == 'commit-revision': reply.identity_status.security_revision += 1
                    elif mode == 'commit-active': reply.identity_status.actor_revoked = False; reply.identity_status.active = True
                elif operation.HasField('barrier_read'):
                    if mode == 'barrier-target': reply.authority_barrier.target.id = b'x'*32
                    elif mode == 'barrier-failure':
                        peer.stream.shutdown(socket.SHUT_RDWR)
                        raise EOFError()
            peer.stream.sendall(frame(reply))

        return FaultPeer(self, response=respond), mutations

    def test_substituted_challenges_never_receive_a_mutation_proof(self):
        before = self.snapshot()
        for mode in ('challenge-target', 'challenge-owner', 'challenge-issuer', 'challenge-purpose'):
            with self.subTest(mode=mode):
                peer, mutations = self.fault_peer(mode)
                self.finish_command(self.start_command(self.command(), port=peer.port), pb.REVOCATION_REJECTED)
                self.assertEqual(mutations, [])
                self.assertEqual(peer.connections, 1)
                self.assertEqual(self.snapshot(), before)
                peer.close()

    def test_lost_commit_reply_is_uncertain_and_explicit_resume_reconciles(self):
        self.service_pending()
        peer, mutations = self.fault_peer('lost-commit')
        result = self.finish_command(self.start_command(self.command(), port=peer.port), pb.REVOCATION_COMMIT_UNCERTAIN)
        self.assertEqual(result.revision, 1)
        self.assertEqual(len(mutations), 1)
        self.assertEqual(peer.connections, 1)
        self.assertEqual(self.barrier().pending_count, 1)
        request = pb.RevocationCommand(protocol_version=1, wait_ms=200,
            resume=pb.RevocationCompletionRead(target=self.target, revision=1))
        self.finish_command(self.start_command(request), pb.REVOCATION_COMMITTED)
        self.assertTrue(self.applied(0, 1).HasField('authority_activation'))
        self.finish_command(self.start_command(request), pb.REVOCATION_COMPLETE)

    def test_invalid_commit_replies_never_claim_confirmation(self):
        for mode in ('commit-target', 'commit-revision', 'commit-active'):
            with self.subTest(mode=mode):
                signer, public = self.key('actor-' + mode)
                record = self.decide(self.peer, self.enroll(self.peer, self.intent(public), signer, public)).enrollment
                target = pb.IdentityReference(kind=pb.ACTOR_TARGET, id=self.claims(record, self.info).actor_id)
                peer, mutations = self.fault_peer(mode)
                head = self.peer.call(credential_check=self.record.credential).identity_status.security_revision
                command = self.command(revision=head, target=target)
                self.finish_command(self.start_command(command, port=peer.port), pb.REVOCATION_COMMIT_UNCERTAIN)
                self.assertEqual(len(mutations), 1)
                self.assertEqual(peer.connections, 1)
                peer.close()

    def test_failed_or_substituted_barrier_preserves_commit_without_completion(self):
        for mode in ('barrier-target', 'barrier-failure'):
            with self.subTest(mode=mode):
                signer, public = self.key('actor-' + mode)
                record = self.decide(self.peer, self.enroll(self.peer, self.intent(public), signer, public)).enrollment
                target = pb.IdentityReference(kind=pb.ACTOR_TARGET, id=self.claims(record, self.info).actor_id)
                head = self.peer.call(credential_check=self.record.credential).identity_status.security_revision
                peer, mutations = self.fault_peer(mode)
                result = self.finish_command(self.start_command(self.command(revision=head, target=target), port=peer.port), pb.REVOCATION_COMMITTED)
                self.assertFalse(result.HasField('barrier'))
                self.assertEqual(len(mutations), 1)
                self.assertEqual(peer.connections, 1)
                peer.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
