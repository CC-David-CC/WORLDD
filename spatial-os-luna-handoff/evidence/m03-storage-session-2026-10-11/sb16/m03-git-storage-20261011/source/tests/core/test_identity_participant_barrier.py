"""Real issuer persistence and service authority; external effect completion is tested separately."""
import sqlite3
import subprocess
import unittest
import core_pb2 as core
import grants_pb2 as grants
import issuer_pb2 as pb
from test_identity_tls import TlsFixture
from test_identity_issuer import BINARY


class BarrierTests(TlsFixture):
    def setUp(self):
        super().setUp(); self.peer = self.ready(); self.registry_revision = 0
        self.service = self.register_service()

    def register_service(self, identity=b'p'*32, incarnation=b'i'*32):
        signer, public = self.key('authority-'+identity.hex())
        definition = pb.AuthorityParticipant(id=identity, public_key=public,
            domains=[grants.Domain(kind=grants.WORLD, id=identity.hex())])
        op = pb.IssuerOperation(participant_register=pb.AuthorityParticipantRegister(
            participant=definition, expected_registry_revision=self.registry_revision))
        result = self.execute(self.peer, self.challenge(self.peer, op))
        self.assertTrue(result.HasField('participant'), result); self.registry_revision += 1
        return self.authenticate(identity, self.registry_revision, incarnation, signer, public)

    def authenticate(self, identity, revision, incarnation, signer, public):
        peer = self.connect()
        result = peer.call(authority_authentication_begin=pb.AuthorityAuthenticationBegin(
            participant_id=identity, registered_revision=revision, service_incarnation=incarnation))
        self.assertTrue(result.HasField('challenge'), result)
        challenge = result.challenge
        result = peer.call(authority_authentication_complete=pb.AuthorityAuthenticationComplete(
            nonce=challenge.nonce, proof=self.proof(challenge, signer, public)))
        self.assertTrue(result.HasField('authority_authentication'), result)
        return dict(peer=peer, proof=result.authority_authentication, signer=signer, public=public)

    def activate(self, service=None, revision=0):
        result = (service or self.service)['peer'].call(authority_activate=pb.AuthorityActivate(synchronized_revision=revision))
        self.assertTrue(result.HasField('authority_activation'), result)
        return result.authority_activation

    def applied(self, before, after, service=None, generation=1):
        return (service or self.service)['peer'].call(authority_applied=pb.AuthorityApplied(
            generation=generation, from_revision=before, through_revision=after))

    def revoke_actor(self, revision=0):
        self.actor_key, self.actor_public = self.key('revoke-actor-'+str(revision))
        record = self.decide(self.peer, self.enroll(self.peer)).enrollment
        claims = self.claims(record, self.info)
        target = pb.IdentityReference(kind=pb.ACTOR_TARGET, id=claims.actor_id)
        op = pb.IssuerOperation(identity_revoke=pb.IdentityRevoke(target=target,
            expected_security_revision=revision, reason='barrier fixture'))
        result = self.execute(self.peer, self.challenge(self.peer, op))
        self.assertTrue(result.HasField('identity_status'), result)
        self.assertEqual(result.identity_status.security_revision, revision+1)
        return target

    def barrier(self, revision=1, cursor=b'', limit=0):
        op = pb.IssuerOperation(barrier_read=pb.AuthorityBarrierRead(
            revision=revision, after_participant_id=cursor, limit=limit))
        result = self.execute(self.peer, self.challenge(self.peer, op))
        self.assertTrue(result.HasField('authority_barrier'), result)
        return result.authority_barrier

    def release(self, revision=0, generation=1, service=None):
        return (service or self.service)['peer'].call(authority_release=pb.AuthorityRelease(
            generation=generation, applied_revision=revision))

    def snapshot(self):
        with sqlite3.connect(self.state/'identity.db') as db:
            names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {n:db.execute('SELECT * FROM '+n).fetchall() for n in names}

    def test_activation_records_original_service_and_connection(self):
        state = self.activate(); proof = self.service['proof']
        self.assertEqual(state.issuer_id, self.info.issuer_id)
        self.assertEqual(state.registration, proof.registration)
        self.assertEqual(state.service_incarnation, proof.service_incarnation)
        self.assertEqual(state.issuer_incarnation, proof.issuer_incarnation)
        self.assertEqual(state.connection_binding, proof.connection_binding)
        self.assertEqual((state.generation,state.applied_revision,state.head_revision,state.active),(1,0,0,True))
        self.assertEqual(self.service['peer'].call(authority_state=pb.AuthorityStateRead()).authority_activation,state)
        self.assertEqual(self.counts(),(0,0,0))

    def test_transport_alone_cannot_activate(self):
        before = self.snapshot()
        self.denied(self.peer.call(authority_activate=pb.AuthorityActivate(synchronized_revision=0)),core.FORBIDDEN)
        self.assertEqual(self.snapshot(),before)

    def test_owner_reads_committed_barrier_with_no_active_participants(self):
        target = self.revoke_actor(); state = self.barrier()
        self.assertEqual(state.issuer_id,self.info.issuer_id); self.assertEqual(state.target,target)
        self.assertEqual((state.revision,state.participant_count,state.pending_count,state.complete),(1,0,0,True))
        self.assertFalse(state.obligations); self.assertFalse(state.next_after)

    def test_revocation_stays_pending_until_both_generations_apply(self):
        other = self.register_service(b'q'*32,b'j'*32)
        self.activate(); self.activate(other); self.revoke_actor()
        state = self.barrier(); self.assertEqual((state.participant_count,state.pending_count,state.complete),(2,2,False))
        self.assertEqual(len(state.obligations),2)
        self.assertTrue(self.applied(0,1).HasField('authority_activation'))
        state = self.barrier(); self.assertEqual((state.pending_count,state.complete),(1,False))
        self.assertTrue(self.applied(0,1,other).HasField('authority_activation'))
        state = self.barrier(); self.assertEqual((state.pending_count,state.complete),(0,True))

    def test_stale_activation_and_missing_revision_do_not_change_state(self):
        self.revoke_actor(); before = self.snapshot()
        self.denied(self.service['peer'].call(authority_activate=pb.AuthorityActivate(synchronized_revision=0)),core.REVISION_CONFLICT)
        self.denied(self.service['peer'].call(authority_activate=pb.AuthorityActivate()),core.INVALID_ARGUMENT)
        self.assertEqual(self.snapshot(),before); self.assertEqual(self.activate(revision=1).applied_revision,1)

    def test_application_rejects_gaps_future_revisions_and_foreign_generations(self):
        self.activate(); self.revoke_actor(); self.revoke_actor(1); before = self.snapshot()
        for start,end,generation in ((0,2,1),(1,2,1),(0,1,2),(2,3,1)):
            result = self.applied(start,end,generation=generation)
            self.assertTrue(result.HasField('error'),result)
        self.assertEqual(self.snapshot(),before)
        self.assertTrue(self.applied(0,1).HasField('authority_activation'))
        self.assertTrue(self.applied(1,2).HasField('authority_activation'))
        self.assertTrue(self.barrier(2).complete)

    def test_duplicate_application_is_idempotent(self):
        self.activate(); self.revoke_actor()
        first = self.applied(0,1); self.assertTrue(first.HasField('authority_activation'),first)
        before = self.snapshot(); self.assertEqual(self.applied(0,1).authority_activation,first.authority_activation)
        self.assertEqual(self.snapshot(),before)

    def test_clean_release_cannot_skip_pending_cancellation(self):
        self.activate(); self.revoke_actor(); before = self.snapshot()
        self.assertTrue(self.release().HasField('error')); self.assertEqual(self.snapshot(),before)
        self.assertTrue(self.applied(0,1).HasField('authority_activation'))
        result = self.release(1); self.assertTrue(result.HasField('authority_activation'),result)
        self.assertFalse(result.authority_activation.active)
        before = self.snapshot(); duplicate = self.release(1)
        self.assertEqual(duplicate.authority_activation,result.authority_activation); self.assertEqual(self.snapshot(),before)

    def test_another_connection_cannot_replace_acknowledge_or_release(self):
        self.activate(); proof = self.service['proof']; other = self.authenticate(b'p'*32,1,b'i'*32,
            self.service['signer'],self.service['public']); self.revoke_actor(); before = self.snapshot()
        self.denied(other['peer'].call(authority_activate=pb.AuthorityActivate(synchronized_revision=1)),core.BUSY)
        self.assertTrue(self.applied(0,1,other).HasField('error')); self.assertTrue(self.release(service=other).HasField('error'))
        self.assertEqual(self.snapshot(),before); self.assertFalse(self.barrier().complete)

    def test_disconnect_keeps_obligation_and_blocks_replacement(self):
        self.activate(); self.revoke_actor(); self.service['peer'].close()
        other = self.authenticate(b'p'*32,1,b'j'*32,self.service['signer'],self.service['public'])
        self.denied(other['peer'].call(authority_activate=pb.AuthorityActivate(synchronized_revision=1)),core.BUSY)
        self.assertEqual(self.barrier().pending_count,1)

    def test_issuer_restart_keeps_obligations_and_old_generation(self):
        self.activate(); self.revoke_actor(); before = self.snapshot()
        self.peer.close(); self.service['peer'].close(); self.stop_daemons(); self.peer = self.ready()
        self.assertEqual(self.snapshot(),before); self.assertEqual(self.barrier().pending_count,1)
        other = self.authenticate(b'p'*32,1,b'j'*32,self.service['signer'],self.service['public'])
        self.denied(other['peer'].call(authority_activate=pb.AuthorityActivate(synchronized_revision=1)),core.BUSY)
        self.assertTrue(self.applied(0,1,other).HasField('error'))

    def test_reactivation_requires_new_incarnation_and_generation(self):
        self.activate(); self.assertTrue(self.release().HasField('authority_activation'))
        self.assertTrue(self.service['peer'].call(authority_activate=pb.AuthorityActivate(synchronized_revision=0)).HasField('error'))
        other = self.authenticate(b'p'*32,1,b'j'*32,self.service['signer'],self.service['public'])
        state = self.activate(other); self.assertEqual(state.generation,2)
        self.revoke_actor(); barrier = self.barrier(); self.assertEqual(barrier.obligations[0].generation,2)
        self.assertTrue(self.applied(0,1).HasField('error'))
        self.assertTrue(self.applied(0,1,other,generation=2).HasField('authority_activation'))

    def test_later_activation_does_not_change_earlier_obligations(self):
        self.activate(); self.revoke_actor(); other = self.register_service(b'q'*32,b'j'*32)
        self.activate(other,1); state = self.barrier()
        self.assertEqual(state.participant_count,1); self.assertEqual(state.pending_count,1)
        self.assertTrue(self.applied(0,1).HasField('authority_activation')); self.assertTrue(self.barrier().complete)

    def test_pagination_preserves_global_completion_counts(self):
        other = self.register_service(b'q'*32,b'j'*32); self.activate(); self.activate(other); self.revoke_actor()
        self.assertTrue(self.applied(0,1).HasField('authority_activation'))
        first = self.barrier(limit=1); self.assertEqual(first.pending_count,1); self.assertFalse(first.complete)
        self.assertTrue(first.obligations[0].complete); self.assertEqual(first.next_after,b'p'*32)
        second = self.barrier(cursor=first.next_after,limit=1)
        self.assertEqual(second.pending_count,1); self.assertFalse(second.obligations[0].complete); self.assertFalse(second.next_after)

    def test_obligation_failure_rolls_back_revocation_and_revision(self):
        self.activate()
        record = self.decide(self.peer,self.enroll(self.peer)).enrollment; claims = self.claims(record,self.info)
        op = pb.IssuerOperation(identity_revoke=pb.IdentityRevoke(
            target=pb.IdentityReference(kind=pb.ACTOR_TARGET,id=claims.actor_id),expected_security_revision=0,reason='rollback fixture'))
        with sqlite3.connect(self.state/'identity.db') as db:
            db.execute("CREATE TRIGGER fail_obligation BEFORE INSERT ON authority_obligation BEGIN SELECT RAISE(ABORT,'fixture'); END")
        before = self.snapshot(); self.denied(self.execute(self.peer,self.challenge(self.peer,op)),core.STORAGE_ERROR)
        self.assertEqual(self.snapshot(),before)
        self.assertTrue(self.peer.call(credential_check=record.credential).identity_status.active)
        with sqlite3.connect(self.state/'identity.db') as db:db.execute('DROP TRIGGER fail_obligation')
        result = self.execute(self.peer,self.challenge(self.peer,op)); self.assertEqual(result.identity_status.security_revision,1)
        self.assertEqual(self.barrier().pending_count,1)

    def test_service_key_cannot_read_owner_barrier_details(self):
        self.activate(); self.revoke_actor(); before = self.snapshot()
        op = pb.IssuerOperation(barrier_read=pb.AuthorityBarrierRead(revision=1))
        challenge = self.challenge(self.service['peer'],op)
        proof = self.proof(challenge,self.service['signer'],self.service['public'])
        self.denied(self.execute(self.service['peer'],challenge,proof))
        self.denied(self.execute(self.service['peer'],challenge))
        self.assertEqual(self.snapshot(),before); self.assertFalse(self.barrier().complete)

    def test_malformed_queries_and_missing_application_fields_preserve_state(self):
        self.activate(); self.revoke_actor(); before = self.snapshot()
        for read in (pb.AuthorityBarrierRead(),pb.AuthorityBarrierRead(revision=2**63),
                     pb.AuthorityBarrierRead(revision=1,limit=17),pb.AuthorityBarrierRead(revision=1,after_participant_id=b'x')):
            self.denied(self.peer.call(challenge=pb.IssuerOperation(barrier_read=read)),core.INVALID_ARGUMENT)
        for value in (pb.AuthorityApplied(generation=1,through_revision=1),
                      pb.AuthorityApplied(generation=1,from_revision=0),
                      pb.AuthorityApplied(generation=0,from_revision=0,through_revision=1)):
            self.denied(self.service['peer'].call(authority_applied=value),core.INVALID_ARGUMENT)
        self.denied(self.service['peer'].call(authority_release=pb.AuthorityRelease(generation=1)),core.INVALID_ARGUMENT)
        self.assertEqual(self.snapshot(),before)

    def test_v5_migration_preserves_state_without_inventing_historical_completion(self):
        self.revoke_actor(); self.peer.close(); self.service['peer'].close(); self.stop_daemons()
        secret = (self.state/'issuer.pem').read_bytes()
        with sqlite3.connect(self.state/'identity.db') as db:
            db.executescript('DROP TABLE authority_retirement; DROP TABLE authority_fence; DROP TABLE authority_launch_authorization; DROP TABLE authority_supervisor; DROP TABLE authority_obligation; DROP TABLE authority_barrier; DROP TABLE authority_activation; PRAGMA user_version=5;')
        before = self.snapshot(); command = [str(BINARY),'--state-dir',str(self.state)]
        denied = subprocess.run(command,input=b'',capture_output=True,timeout=4)
        self.assertNotEqual(denied.returncode,0); self.assertIn(b'migration',denied.stderr.lower())
        for _ in range(2):
            migrated = subprocess.run(command+['--migrate'],input=b'',capture_output=True,timeout=4)
            self.assertEqual(migrated.returncode,0,migrated.stderr)
        after = self.snapshot(); self.assertEqual({n:after[n] for n in before},before)
        for name in ('authority_activation','authority_barrier','authority_obligation'):self.assertEqual(after[name],[])
        with sqlite3.connect(self.state/'identity.db') as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],10)
        self.assertEqual((self.state/'issuer.pem').read_bytes(),secret); self.peer = self.ready()
        op = pb.IssuerOperation(barrier_read=pb.AuthorityBarrierRead(revision=1))
        self.denied(self.execute(self.peer,self.challenge(self.peer,op)),core.NOT_FOUND)

    def test_failed_v5_migration_rolls_back_tables_and_version(self):
        self.peer.close(); self.service['peer'].close(); self.stop_daemons()
        with sqlite3.connect(self.state/'identity.db') as db:
            db.executescript('DROP TABLE authority_retirement; DROP TABLE authority_fence; DROP TABLE authority_launch_authorization; DROP TABLE authority_supervisor; DROP TABLE authority_obligation; DROP TABLE authority_barrier; DROP TABLE authority_activation; '
                'PRAGMA user_version=5; CREATE TABLE authority_obligation(marker INTEGER);')
        before = self.snapshot()
        result = subprocess.run([str(BINARY),'--state-dir',str(self.state),'--migrate'],input=b'',capture_output=True,timeout=4)
        self.assertNotEqual(result.returncode,0); self.assertEqual(self.snapshot(),before)
        with sqlite3.connect(self.state/'identity.db') as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],5)


if __name__ == '__main__': unittest.main(verbosity=2)
