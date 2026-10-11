"""Exact supervisor retirement preserves history and resolves only its generation."""
import concurrent.futures
import sqlite3
import subprocess
import time
import unittest
import core_pb2 as core
import issuer_pb2 as pb
import key_pb2 as keys
import test_authority_fencing as fencing
from harness.wire import frame


class RetirementTests(fencing.AuthorityFencingTests):
    def retire_request(self, digest=b'd' * 32):
        if not hasattr(self, 'retirement_fence'):
            self.retirement_fence = self.fenced()
        return pb.AuthorityRetireBegin(owner=self.fence_request(), fence_id=self.retirement_fence.fence_id,
            cleanup_sha256=digest)

    def retire_begin(self, request=None, peer=None):
        result = (peer or self.supervisor).call(authority_retire_begin=request or self.retire_request())
        self.assertTrue(result.HasField('challenge'), result)
        self.assertEqual(result.challenge.signing_request.purpose, keys.AUTHORITY_RETIREMENT)
        return result.challenge

    def retire_complete(self, challenge, peer=None, proof=None):
        return (peer or self.supervisor).call(authority_retire_complete=pb.AuthorityRetireComplete(nonce=challenge.nonce,
            proof=proof or self.proof(challenge, self.supervisor_key, self.supervisor_public)))

    def retired(self, request=None, peer=None):
        reply = self.retire_complete(self.retire_begin(request, peer), peer)
        self.assertTrue(reply.HasField('authority_retirement'), reply)
        return reply.authority_retirement

    def test_retirement_completes_barrier_without_fabricating_applied_history(self):
        self.revoke_actor_for_test(); request = self.retire_request(); before = self.snapshot_all()
        result = self.retired(request)
        self.assertEqual(result.issuer_id, self.info.issuer_id)
        self.assertEqual(result.request, request); self.assertEqual(result.registration, self.registration)
        self.assertEqual(result.supervisor_public_key, self.supervisor_public)
        self.assertEqual((result.security_revision, result.applied_revision), (1, 0))
        self.assertEqual(len(result.retirement_id), 32)
        after = self.snapshot_all()
        for name, rows in before.items():
            if name not in ('authority_activation', 'authority_retirement'): self.assertEqual(after[name], rows, name)
        self.assertEqual(after['authority_activation'], [row[:-1] + (0,) for row in before['authority_activation']])
        self.assertEqual(len(after['authority_retirement']), 1)
        barrier = self.barrier_for_test()
        self.assertEqual((barrier.participant_count, barrier.pending_count, barrier.complete), (1, 0, True))
        self.assertEqual(barrier.obligations[0].retirement, result)
        self.assertEqual(barrier.obligations[0].applied_revision, 0)
        # This barrier is also checked by the production TLS client.
        operation = pb.IssuerOperation(barrier_read=pb.AuthorityBarrierRead(revision=1))
        checked = self.execute(self.supervisor, self.challenge(self.supervisor, operation))
        self.assertEqual(checked.authority_barrier, barrier)

    def test_other_participant_keeps_obligations_and_active_generation(self):
        other_key, other_public = self.key('other-service')
        registration = self.register(identity=b'q' * 32, public=other_public, domain='other', revision=1)
        other = self.connect()
        challenge = self.service_begin(other, self.request(identity=b'q' * 32, revision=2, incarnation=b'j' * 32))
        reply = self.service_finish(challenge, other, self.proof(challenge, other_key, other_public))
        self.assertTrue(reply.HasField('authority_authentication'), reply)
        self.assertTrue(self.activate(other).HasField('authority_activation'))
        self.revoke_actor_for_test(); self.retired()
        barrier = self.barrier_for_test()
        self.assertEqual((barrier.participant_count, barrier.pending_count), (2, 1))
        remaining = next(x for x in barrier.obligations if x.registration == registration)
        self.assertFalse(remaining.complete); self.assertFalse(remaining.HasField('retirement'))
        self.assertTrue(other.call(authority_state=pb.AuthorityStateRead()).authority_activation.active)

    def test_revocation_between_fence_challenge_and_completion_is_covered(self):
        challenge = self.retire_begin(); self.revoke_actor_for_test()
        result = self.retire_complete(challenge)
        self.assertTrue(result.HasField('authority_retirement'), result)
        self.assertEqual(result.authority_retirement.security_revision, 1)
        self.assertTrue(self.barrier_for_test().complete)

    def test_retired_generation_gets_no_new_revocation_obligations(self):
        original = self.retired(); self.revoke_actor_for_test()
        barrier = self.barrier_for_test()
        self.assertEqual((barrier.participant_count, barrier.pending_count, barrier.complete), (0, 0, True))
        self.assertFalse(barrier.obligations)
        self.assertEqual(self.retired(), original)

    def test_fresh_exact_retry_and_nonce_replay_preserve_original_record(self):
        challenge = self.retire_begin(); original = self.retire_complete(challenge).authority_retirement
        self.assertEqual(len(original.retirement_id), 32); before = self.snapshot_all()
        self.error(self.retire_complete(challenge), core.UNAUTHORIZED)
        self.assertEqual(self.retired(), original); self.assertEqual(self.snapshot_all(), before)

    def test_unread_completion_reply_can_retry_on_a_fresh_connection(self):
        request = self.retire_request(); raw = self.connect()
        challenge = self.retire_begin(request, raw)
        complete = pb.IssuerRequest(protocol_version=1, request_id=raw.sequence + 1,
            authority_retire_complete=pb.AuthorityRetireComplete(nonce=challenge.nonce,
                proof=self.proof(challenge, self.supervisor_key, self.supervisor_public)))
        raw.stream.sendall(frame(complete))
        deadline = time.monotonic() + 3
        while not self.snapshot_all()['authority_retirement'] and time.monotonic() < deadline: time.sleep(.01)
        self.assertEqual(len(self.snapshot_all()['authority_retirement']), 1)
        raw.close()  # Discard the reply after observing the independent database commit.
        before = self.snapshot_all(); result = self.retired(request, self.client())
        self.assertEqual(result.request, request); self.assertEqual(self.snapshot_all(), before)

    def test_management_pipe_cannot_retire(self):
        request = self.retire_request(); self.peer.close(); self.supervisor.close(); self.stop_daemons()
        pipe = self.start(); before = self.snapshot_all()
        self.error(pipe.call(authority_retire_begin=request), core.FORBIDDEN)
        self.error(pipe.call(authority_retire_complete=pb.AuthorityRetireComplete(nonce=b'n' * 32,
            proof=keys.KeyProof(purpose=keys.AUTHORITY_RETIREMENT, public_key=self.supervisor_public, signature=b's' * 64))), core.FORBIDDEN)
        self.assertEqual(self.snapshot_all(), before)

    def test_changed_cleanup_or_fence_cannot_replace_retirement(self):
        self.retired(); before = self.snapshot_all()
        for field in ('cleanup_sha256', 'fence_id'):
            request = self.retire_request(); setattr(request, field, b'x' * 32)
            self.error(self.supervisor.call(authority_retire_begin=request))
            self.assertEqual(self.snapshot_all(), before)

    def test_wrong_keys_and_other_signature_purposes_cannot_retire(self):
        self.retire_request(); before = self.snapshot_all(); other_key, other_public = self.key('other-retirer')
        for key, public in ((self.owner, self.owner_public), (self.signer, self.public), (other_key, other_public)):
            challenge = self.retire_begin()
            self.error(self.retire_complete(challenge, proof=self.proof(challenge, key, public)), core.UNAUTHORIZED)
        for purpose in (keys.AUTHORITY_FENCING, keys.AUTHORITY_LAUNCH_AUTHORIZATION, keys.ISSUER_ADMINISTRATION):
            challenge = self.retire_begin(); proof = self.proof(challenge, self.supervisor_key, self.supervisor_public)
            proof.purpose = purpose
            self.error(self.retire_complete(challenge, proof=proof), core.UNAUTHORIZED)
        self.assertEqual(self.snapshot_all(), before)

    def test_proof_cannot_cross_connections_and_nonce_is_consumed(self):
        challenge = self.retire_begin(); other = self.client(); before = self.snapshot_all()
        self.error(self.retire_complete(challenge, other), core.UNAUTHORIZED)
        self.error(self.retire_complete(challenge), core.UNAUTHORIZED)
        self.assertEqual(self.snapshot_all(), before)

    def test_missing_fence_cannot_retire(self):
        request = pb.AuthorityRetireBegin(owner=self.fence_request(), fence_id=b'f' * 32, cleanup_sha256=b'd' * 32)
        before = self.snapshot_all()
        self.error(self.supervisor.call(authority_retire_begin=request), core.FORBIDDEN)
        self.assertEqual(self.snapshot_all(), before)

    def test_malformed_or_substituted_owner_is_rejected_without_mutation(self):
        original = self.retire_request(); before = self.snapshot_all()
        for field in ('fence_id', 'cleanup_sha256'):
            for value in (b'', b'x', b'x' * 33):
                request = pb.AuthorityRetireBegin(); request.CopyFrom(original); setattr(request, field, value)
                self.error(self.supervisor.call(authority_retire_begin=request), core.INVALID_ARGUMENT)
        changes = dict(participant_id=b'x' * 32, registered_revision=2, generation=2, service_incarnation=b'x' * 32,
            launch_id=b'x' * 32, supervision_revision=2, authorization_id=b'x' * 32)
        for field, value in changes.items():
            request = pb.AuthorityRetireBegin(); request.CopyFrom(original); setattr(request.owner, field, value)
            self.error(self.supervisor.call(authority_retire_begin=request))
        self.assertEqual(self.snapshot_all(), before)

    def test_insert_or_deactivation_failure_rolls_back_before_retry(self):
        self.retire_request()
        for target, event in (('authority_retirement', 'INSERT'), ('authority_activation', 'UPDATE')):
            with sqlite3.connect(self.state / 'identity.db') as db:
                db.execute(f"CREATE TRIGGER reject_retirement BEFORE {event} ON {target} BEGIN SELECT RAISE(ABORT,'retirement fault'); END")
            before = self.snapshot_all()
            self.error(self.retire_complete(self.retire_begin()), core.STORAGE_ERROR)
            self.assertEqual(self.snapshot_all(), before)
            with sqlite3.connect(self.state / 'identity.db') as db: db.execute('DROP TRIGGER reject_retirement')
        self.assertEqual(self.retired().request, self.retire_request())

    def test_completed_retirement_survives_restart_and_exact_retry(self):
        self.revoke_actor_for_test(); original = self.retired(); before = self.snapshot_all()
        self.restart(); self.assertEqual(self.retired(), original); self.assertEqual(self.snapshot_all(), before)
        self.assertTrue(self.barrier_for_test().complete)

    def test_old_service_stays_fenced_and_replacement_requires_fresh_authorization(self):
        original = self.retired(); other = self.connect(); service = self.authenticate(other, b'n' * 32)
        self.error(self.activate(other), core.FORBIDDEN)
        self.authorize(self.launch_request(service, launch_id=b'm' * 32, previous=1))
        result = self.activate(other)
        self.assertEqual(result.authority_activation.generation, 2); self.assertTrue(result.authority_activation.active)
        before = self.snapshot_all()
        self.assertEqual(self.retired(), original)
        self.error(self.activate(), core.ALREADY_EXISTS)
        self.error(self.peer.call(authority_release=pb.AuthorityRelease(generation=1, applied_revision=0)), core.UNAUTHORIZED)
        self.assertEqual(self.snapshot_all(), before)

    def test_concurrent_identical_completions_return_one_retirement(self):
        other = self.client(); challenges = [self.retire_begin(), self.retire_begin(peer=other)]
        proofs = [self.proof(c, self.supervisor_key, self.supervisor_public) for c in challenges]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(lambda x: self.retire_complete(x[0], x[1], x[2]), zip(challenges, [self.supervisor, other], proofs)))
        for reply in replies: self.assertTrue(reply.HasField('authority_retirement'), reply)
        self.assertEqual(replies[0].authority_retirement, replies[1].authority_retirement)
        self.assertEqual(len(self.snapshot_all()['authority_retirement']), 1)

    def test_expired_retirement_proof_cannot_commit(self):
        self.retire_request(); self.challenge_ttl = 100; self.restart()
        challenge = self.retire_begin(); proof = self.proof(challenge, self.supervisor_key, self.supervisor_public)
        before = self.snapshot_all(); time.sleep(.15)
        self.error(self.retire_complete(challenge, proof=proof), core.UNAUTHORIZED)
        self.assertEqual(self.snapshot_all(), before)

    def test_restart_invalidates_uncompleted_retirement_proof(self):
        challenge = self.retire_begin(); proof = self.proof(challenge, self.supervisor_key, self.supervisor_public)
        self.restart(); before = self.snapshot_all()
        self.error(self.retire_complete(challenge, proof=proof), core.UNAUTHORIZED)
        self.assertEqual(self.snapshot_all(), before); self.retired()

    def test_corrupted_retirement_is_not_returned_or_overwritten(self):
        self.retired()
        with sqlite3.connect(self.state / 'identity.db') as db: db.execute('UPDATE authority_retirement SET retirement_id=?', (b'x' * 32,))
        before = self.snapshot_all()
        self.error(self.supervisor.call(authority_retire_begin=self.retire_request()), core.STORAGE_ERROR)
        self.assertEqual(self.snapshot_all(), before)

    def test_explicit_v9_migration_preserves_fence_active_generation_and_key(self):
        self.retire_request(); self.peer.close(); self.supervisor.close(); self.stop_daemons()
        secret = (self.state / 'issuer.pem').read_bytes()
        with sqlite3.connect(self.state / 'identity.db') as db:
            db.executescript('DROP TABLE authority_retirement; PRAGMA user_version=9;')
        before = self.snapshot_all(); command = [str(fencing.launch.registry.sessions.BINARY), '--state-dir', str(self.state)]
        result = subprocess.run(command, input=b'', capture_output=True, timeout=4)
        self.assertNotEqual(result.returncode, 0); self.assertIn(b'migration', result.stderr.lower())
        self.assertEqual(self.snapshot_all(), before)
        for _ in range(2):
            result = subprocess.run(command + ['--migrate'], input=b'', capture_output=True, timeout=4)
            self.assertEqual(result.returncode, 0, result.stderr)
        after = self.snapshot_all(); self.assertEqual({name: after[name] for name in before}, before)
        self.assertEqual(after['authority_retirement'], [])
        self.assertEqual((self.state / 'issuer.pem').read_bytes(), secret)
        with sqlite3.connect(self.state / 'identity.db') as db: self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 10)
        self.peer = self.ready(); self.supervisor = self.client(); self.retired()

    def test_failed_v9_migration_preserves_original_schema(self):
        self.retire_request(); self.peer.close(); self.supervisor.close(); self.stop_daemons()
        with sqlite3.connect(self.state / 'identity.db') as db:
            db.executescript('DROP TABLE authority_retirement; PRAGMA user_version=9; CREATE TABLE authority_retirement(marker INTEGER);')
        before = self.snapshot_all()
        command = [str(fencing.launch.registry.sessions.BINARY), '--state-dir', str(self.state), '--migrate']
        result = subprocess.run(command, input=b'', capture_output=True, timeout=4)
        self.assertNotEqual(result.returncode, 0); self.assertEqual(self.snapshot_all(), before)
        with sqlite3.connect(self.state / 'identity.db') as db: self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 9)


if __name__ == '__main__':
    suite = unittest.TestSuite(RetirementTests(n) for n in RetirementTests.__dict__ if n.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
