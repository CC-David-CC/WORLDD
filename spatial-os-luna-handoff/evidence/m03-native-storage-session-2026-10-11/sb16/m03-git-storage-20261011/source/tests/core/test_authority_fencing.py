"""Durable fencing requires the exact supervisor proof and preserves cleanup obligations."""
import concurrent.futures
import sqlite3
import subprocess
import time
import unittest
import core_pb2 as core
import issuer_pb2 as pb
import key_pb2 as keys
import test_launch_authorization as launch
from harness.wire import frame


class AuthorityFencingTests(launch.LaunchAuthorizationTests):
    def setUp(self):
        super().setUp()
        self.authorization = self.authorize()
        reply = self.activate()
        self.assertTrue(reply.HasField('authority_activation'), reply)
        self.active = reply.authority_activation

    def fence_request(self, **changes):
        value = pb.AuthorityFenceBegin(participant_id=self.registration.participant.id,
            registered_revision=1, generation=1, service_incarnation=self.service.service_incarnation,
            launch_id=self.authorization.request.launch_id, supervision_revision=1,
            authorization_id=self.authorization.authorization_id)
        for name, item in changes.items():
            setattr(value, name, item)
        return value

    def fence_begin(self, request=None, peer=None):
        reply = (peer or self.supervisor).call(authority_fence_begin=request or self.fence_request())
        self.assertTrue(reply.HasField('challenge'), reply)
        self.assertEqual(reply.challenge.signing_request.purpose, keys.AUTHORITY_FENCING)
        return reply.challenge

    def fence_complete(self, challenge, peer=None, proof=None):
        return (peer or self.supervisor).call(authority_fence_complete=pb.AuthorityFenceComplete(
            nonce=challenge.nonce, proof=proof or self.proof(challenge, self.supervisor_key, self.supervisor_public)))

    def fenced(self):
        reply = self.fence_complete(self.fence_begin())
        self.assertTrue(reply.HasField('authority_fence'), reply)
        return reply.authority_fence

    def error(self, reply, code=None):
        self.assertTrue(reply.HasField('error'), reply)
        if code is not None:
            self.assertEqual(reply.error.code, code)

    def revoke_actor_for_test(self, revision=0):
        record = self.decide(self.peer, self.enroll(self.peer)).enrollment
        claims = self.claims(record, self.info)
        operation = pb.IssuerOperation(identity_revoke=pb.IdentityRevoke(target=pb.IdentityReference(
            kind=pb.ACTOR_TARGET, id=claims.actor_id), expected_security_revision=revision, reason='fencing witness'))
        reply = self.execute(self.peer, self.challenge(self.peer, operation))
        self.assertTrue(reply.HasField('identity_status'), reply)

    def barrier_for_test(self, revision=1):
        operation = pb.IssuerOperation(barrier_read=pb.AuthorityBarrierRead(revision=revision))
        result = self.execute(self.peer, self.challenge(self.peer, operation))
        self.assertTrue(result.HasField('authority_barrier'), result)
        return result.authority_barrier

    def restart(self):
        self.peer.close(); self.supervisor.close(); self.stop_daemons()
        self.peer = self.ready(); self.supervisor = self.client()

    def test_supervisor_fences_exact_active_launch_without_changing_acknowledgement_history(self):
        before = self.snapshot_all()
        value = self.fenced()
        self.assertEqual(value.issuer_id, self.info.issuer_id)
        self.assertEqual(value.registration, self.registration)
        self.assertEqual(value.generation, 1)
        self.assertEqual(value.service_incarnation, self.service.service_incarnation)
        self.assertEqual(value.authorization_id, self.authorization.authorization_id)
        self.assertEqual(value.launch_id, self.authorization.request.launch_id)
        self.assertEqual(value.supervisor_public_key, self.supervisor_public)
        self.assertEqual(value.supervision_revision, 1)
        self.assertEqual(value.security_revision, 0)
        self.assertEqual(len(value.fence_id), 32)
        after = self.snapshot_all()
        self.assertEqual(len(after['authority_fence']), 1)
        self.assertEqual({n:v for n,v in before.items() if n != 'authority_fence'},
                         {n:v for n,v in after.items() if n != 'authority_fence'})
        reply = self.peer.call(authority_state=pb.AuthorityStateRead())
        self.assertTrue(reply.HasField('authority_activation'), reply)
        self.assertEqual(reply.authority_activation, self.active)

    def test_owner_service_and_unknown_keys_cannot_fence(self):
        other_key, other_public = self.key('unregistered-fencer')
        before = self.snapshot_all()
        for key, public in [(self.owner, self.owner_public), (self.signer, self.public), (other_key, other_public)]:
            challenge = self.fence_begin()
            self.error(self.fence_complete(challenge, proof=self.proof(challenge, key, public)), core.UNAUTHORIZED)
            self.assertEqual(self.snapshot_all(), before)

    def test_fence_proof_cannot_use_launch_signature_purpose(self):
        challenge = self.fence_begin()
        proof = self.proof(challenge, self.supervisor_key, self.supervisor_public)
        proof.purpose = keys.AUTHORITY_LAUNCH_AUTHORIZATION
        before = self.snapshot_all()
        self.error(self.fence_complete(challenge, proof=proof), core.UNAUTHORIZED)
        self.assertEqual(self.snapshot_all(), before)

    def test_fence_proof_cannot_cross_connections_and_consumes_nonce(self):
        challenge = self.fence_begin(); other = self.client()
        before = self.snapshot_all()
        self.error(self.fence_complete(challenge, peer=other), core.UNAUTHORIZED)
        self.error(self.fence_complete(challenge), core.UNAUTHORIZED)
        self.assertEqual(self.snapshot_all(), before)

    def test_replayed_nonce_fails_but_fresh_identical_proof_returns_same_fence(self):
        challenge = self.fence_begin()
        first = self.fence_complete(challenge)
        self.assertTrue(first.HasField('authority_fence'), first)
        before = self.snapshot_all()
        self.error(self.fence_complete(challenge), core.UNAUTHORIZED)
        self.assertEqual(self.fenced(), first.authority_fence)
        self.assertEqual(self.snapshot_all(), before)

    def test_mismatched_target_fields_do_not_create_fences(self):
        changes = dict(participant_id=b'x'*32, registered_revision=2, generation=2,
            service_incarnation=b'x'*32, launch_id=b'x'*32, supervision_revision=2, authorization_id=b'x'*32)
        before = self.snapshot_all()
        for name, value in changes.items():
            with self.subTest(field=name):
                self.error(self.supervisor.call(authority_fence_begin=self.fence_request(**{name:value})))
                self.assertEqual(self.snapshot_all(), before)

    def test_malformed_and_overflowed_target_fields_are_rejected(self):
        before = self.snapshot_all()
        changes = [(n, b'x') for n in ('participant_id','service_incarnation','launch_id','authorization_id')]
        changes += [(n,v) for n in ('registered_revision','generation','supervision_revision') for v in (0,2**63)]
        for name, value in changes:
            with self.subTest(field=name, value=value):
                self.error(self.supervisor.call(authority_fence_begin=self.fence_request(**{name:value})), core.INVALID_ARGUMENT)
                self.assertEqual(self.snapshot_all(), before)

    def test_release_between_challenge_and_completion_prevents_fencing(self):
        challenge = self.fence_begin()
        reply = self.peer.call(authority_release=pb.AuthorityRelease(generation=1, applied_revision=0))
        self.assertTrue(reply.HasField('authority_activation'), reply)
        before = self.snapshot_all()
        self.error(self.fence_complete(challenge), core.UNAUTHORIZED)
        self.assertEqual(self.snapshot_all(), before)

    def test_fenced_service_cannot_apply_or_release_and_barrier_stays_pending(self):
        self.revoke_actor_for_test(); self.fenced(); before = self.snapshot_all()
        self.error(self.peer.call(authority_applied=pb.AuthorityApplied(generation=1, from_revision=0, through_revision=1)), core.UNAUTHORIZED)
        self.error(self.peer.call(authority_release=pb.AuthorityRelease(generation=1, applied_revision=0)), core.UNAUTHORIZED)
        status = self.barrier_for_test()
        self.assertEqual((status.participant_count,status.pending_count,status.complete), (1,1,False))
        self.assertEqual(self.snapshot_all(), before)

    def test_previously_valid_acknowledgement_retry_is_rejected_after_fencing(self):
        self.revoke_actor_for_test()
        applied = pb.AuthorityApplied(generation=1, from_revision=0, through_revision=1)
        self.assertTrue(self.peer.call(authority_applied=applied).HasField('authority_activation'))
        self.fenced(); before = self.snapshot_all()
        self.error(self.peer.call(authority_applied=applied), core.UNAUTHORIZED)
        self.assertEqual(self.snapshot_all(), before)
        self.assertTrue(self.barrier_for_test().complete, 'Fencing must preserve a prior real acknowledgement')

    def test_new_revocation_still_obligates_fenced_generation(self):
        fence = self.fenced(); self.revoke_actor_for_test()
        status = self.barrier_for_test()
        self.assertEqual((status.participant_count,status.pending_count,status.complete), (1,1,False))
        self.assertEqual(self.fenced(), fence)

    def test_concurrent_revocation_and_fencing_preserve_pending_obligation(self):
        record = self.decide(self.peer, self.enroll(self.peer)).enrollment
        claims = self.claims(record, self.info)
        operation = pb.IssuerOperation(identity_revoke=pb.IdentityRevoke(target=pb.IdentityReference(
            kind=pb.ACTOR_TARGET,id=claims.actor_id),expected_security_revision=0,reason='concurrent fence'))
        revocation = self.challenge(self.peer,operation)
        revoke_proof = self.proof(revocation,self.owner,self.owner_public)
        fence = self.fence_begin(); fence_proof = self.proof(fence,self.supervisor_key,self.supervisor_public)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(self.execute,self.peer,revocation,revoke_proof),
                     pool.submit(self.fence_complete,fence,proof=fence_proof)]
            revoked, fenced = [task.result() for task in tasks]
        self.assertTrue(revoked.HasField('identity_status'),revoked)
        self.assertTrue(fenced.HasField('authority_fence'),fenced)
        self.assertEqual(self.barrier_for_test().pending_count,1)
        state = self.peer.call(authority_state=pb.AuthorityStateRead())
        self.assertTrue(state.authority_activation.active)
        self.assertEqual(state.authority_activation.applied_revision,0)

    def test_second_authenticated_service_socket_and_new_launch_cannot_bypass_fence(self):
        other = self.connect(); service = self.authenticate(other, b'n'*32)
        self.fenced(); before = self.snapshot_all()
        challenge = self.launch_begin(self.launch_request(service, launch_id=b'm'*32, previous=1))
        self.error(self.launch_complete(challenge), core.BUSY)
        self.error(self.activate(other), core.FORBIDDEN)
        self.error(other.call(authority_applied=pb.AuthorityApplied(generation=1,from_revision=0,through_revision=1)), core.UNAUTHORIZED)
        self.assertEqual(self.snapshot_all(), before)

    def test_failed_fence_insert_rolls_back_and_fresh_proof_can_retry(self):
        with sqlite3.connect(self.state/'identity.db') as db:
            db.execute("CREATE TRIGGER reject_fence BEFORE INSERT ON authority_fence BEGIN SELECT RAISE(ABORT,'fence fault'); END")
        before = self.snapshot_all()
        self.error(self.fence_complete(self.fence_begin()), core.STORAGE_ERROR)
        self.assertEqual(self.snapshot_all(), before)
        with sqlite3.connect(self.state/'identity.db') as db: db.execute('DROP TRIGGER reject_fence')
        self.assertEqual(self.fenced().generation, 1)

    def test_concurrent_identical_completions_return_one_fence(self):
        other = self.client()
        challenges = [self.fence_begin(), self.fence_begin(peer=other)]
        proofs = [self.proof(c,self.supervisor_key,self.supervisor_public) for c in challenges]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(lambda item:self.fence_complete(item[0],peer=item[1],proof=item[2]),
                zip(challenges,[self.supervisor,other],proofs)))
        for reply in replies:self.assertTrue(reply.HasField('authority_fence'),reply)
        self.assertEqual(replies[0].authority_fence,replies[1].authority_fence)
        self.assertEqual(len(self.snapshot_all()['authority_fence']),1)

    def test_restart_allows_fencing_original_active_generation(self):
        before = self.snapshot_all(); self.restart()
        self.assertEqual(self.snapshot_all(),before)
        self.assertEqual(self.fenced().generation,1)

    def test_committed_fence_survives_restart_and_remains_pending(self):
        original = self.fenced(); self.revoke_actor_for_test(); self.restart()
        self.assertEqual(self.fenced(),original)
        self.assertEqual(self.barrier_for_test().pending_count,1)

    def test_restart_invalidates_outstanding_fence_challenge(self):
        challenge = self.fence_begin(); proof = self.proof(challenge,self.supervisor_key,self.supervisor_public)
        self.restart(); before=self.snapshot_all()
        self.error(self.fence_complete(challenge,proof=proof),core.UNAUTHORIZED)
        self.assertEqual(self.snapshot_all(),before)
        self.assertEqual(self.fenced().generation,1)

    def test_expired_fence_challenge_cannot_commit(self):
        self.challenge_ttl=100; self.restart()
        challenge=self.fence_begin(); proof=self.proof(challenge,self.supervisor_key,self.supervisor_public)
        before=self.snapshot_all();time.sleep(.15)
        self.error(self.fence_complete(challenge,proof=proof),core.UNAUTHORIZED)
        self.assertEqual(self.snapshot_all(),before)

    def test_inconsistent_persisted_fence_is_not_returned_as_success(self):
        self.fenced()
        with sqlite3.connect(self.state/'identity.db') as db:db.execute('UPDATE authority_fence SET fence_id=?',(b'x'*32,))
        before=self.snapshot_all()
        self.error(self.fence_complete(self.fence_begin()),core.STORAGE_ERROR)
        self.assertEqual(self.snapshot_all(),before)

    def test_explicit_v8_migration_preserves_active_launch_and_issuer_key(self):
        self.peer.close();self.supervisor.close();self.stop_daemons()
        secret=(self.state/'issuer.pem').read_bytes()
        with sqlite3.connect(self.state/'identity.db') as db:db.executescript('DROP TABLE authority_retirement; DROP TABLE authority_fence; PRAGMA user_version=8;')
        before=self.snapshot_all();command=[str(launch.registry.sessions.BINARY),'--state-dir',str(self.state)]
        result=subprocess.run(command,input=b'',capture_output=True,timeout=4)
        self.assertNotEqual(result.returncode,0);self.assertIn(b'migration',result.stderr.lower())
        self.assertEqual(self.snapshot_all(),before)
        for _ in range(2):
            result=subprocess.run(command+['--migrate'],input=b'',capture_output=True,timeout=4)
            self.assertEqual(result.returncode,0,result.stderr)
        after=self.snapshot_all();self.assertEqual({n:after[n] for n in before},before)
        self.assertEqual(after['authority_fence'],[])
        self.assertEqual((self.state/'issuer.pem').read_bytes(),secret)
        with sqlite3.connect(self.state/'identity.db') as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],10)
        self.peer=self.ready();self.supervisor=self.client()
        self.assertEqual(self.fenced().generation,1)

    def test_failed_v8_migration_rolls_back(self):
        self.peer.close();self.supervisor.close();self.stop_daemons()
        with sqlite3.connect(self.state/'identity.db') as db:
            db.executescript('DROP TABLE authority_retirement; DROP TABLE authority_fence; PRAGMA user_version=8; CREATE TABLE authority_fence(marker INTEGER);')
        before=self.snapshot_all()
        result=subprocess.run([str(launch.registry.sessions.BINARY),'--state-dir',str(self.state),'--migrate'],input=b'',capture_output=True,timeout=4)
        self.assertNotEqual(result.returncode,0);self.assertEqual(self.snapshot_all(),before)
        with sqlite3.connect(self.state/'identity.db') as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],8)


if __name__=='__main__':
    suite=unittest.TestSuite(AuthorityFencingTests(n) for n in AuthorityFencingTests.__dict__ if n.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
