"""Actor status from the production issuer and checked service client."""
from pathlib import Path
import sqlite3
import subprocess
import unittest
import core_pb2 as core
import issuer_pb2 as pb
from harness.wire import frame
from test_issuer_client import ClientFixture, FaultPeer


class ActorStatusTests(ClientFixture):
    def setUp(self):
        super().setUp()
        self.owner_peer = self.ready()
        self.record = self.decide(self.owner_peer, self.enroll(self.owner_peer)).enrollment
        self.identity = self.claims(self.record, self.info)

    def read(self, peer, ids=None):
        return peer.call(actor_status=pb.ActorStatusRead(actor_ids=ids if ids is not None else [self.identity.actor_id]))

    def revoke(self, kind, target, revision=0):
        operation = pb.IssuerOperation(identity_revoke=pb.IdentityRevoke(
            target=pb.IdentityReference(kind=kind, id=target), expected_security_revision=revision, reason='actor status regression'))
        reply = self.execute(self.owner_peer, self.challenge(self.owner_peer, operation))
        self.assertTrue(reply.HasField('identity_status'), reply)
        return reply.identity_status

    def batch(self, reply):
        self.assertTrue(reply.HasField('actor_status'), reply)
        self.assertEqual(reply.actor_status.issuer_id, self.info.issuer_id)
        return reply.actor_status

    def test_production_client_reads_current_enrolled_actor(self):
        batch = self.batch(self.read(self.client()))
        self.assertEqual(batch.security_revision, 0)
        self.assertEqual(list(batch.actors), [pb.ActorState(actor_id=self.identity.actor_id, known=True, active=True)])

    def test_unknown_actor_is_explicitly_inactive(self):
        batch = self.batch(self.read(self.owner_peer, [b'x'*32]))
        self.assertEqual(list(batch.actors), [pb.ActorState(actor_id=b'x'*32)])

    def test_order_and_mixed_known_unknown_statuses_are_preserved(self):
        ids = [b'y'*32, self.identity.actor_id, b'z'*32]
        batch = self.batch(self.read(self.client(), ids))
        self.assertEqual([x.actor_id for x in batch.actors], ids)
        self.assertEqual([x.active for x in batch.actors], [False, True, False])

    def test_credential_revocation_does_not_revoke_actor_grants(self):
        self.revoke(pb.CREDENTIAL_TARGET, self.identity.credential_id)
        batch = self.batch(self.read(self.client()))
        self.assertEqual(batch.security_revision, 1)
        self.assertTrue(batch.actors[0].active)
        self.assertFalse(batch.actors[0].revoked)
        self.assertFalse(self.owner_peer.call(credential_check=self.record.credential).identity_status.active)

    def test_actor_revocation_is_visible_without_a_new_credential(self):
        peer = self.client(); self.assertTrue(self.batch(self.read(peer)).actors[0].active)
        self.revoke(pb.ACTOR_TARGET, self.identity.actor_id)
        batch = self.batch(self.read(peer))
        self.assertEqual(batch.security_revision, 1)
        self.assertTrue(batch.actors[0].known); self.assertTrue(batch.actors[0].revoked)
        self.assertFalse(batch.actors[0].active)

    def test_revocation_status_survives_issuer_restart(self):
        self.revoke(pb.ACTOR_TARGET, self.identity.actor_id)
        self.stop_daemons(); self.owner_peer = self.ready()
        batch = self.batch(self.read(self.client()))
        self.assertEqual(batch.security_revision, 1)
        self.assertTrue(batch.actors[0].known); self.assertTrue(batch.actors[0].revoked)
        self.assertFalse(batch.actors[0].active)

    def test_failed_revocation_does_not_publish_inactive_status(self):
        with sqlite3.connect(self.state/'identity.db') as db:
            db.execute("CREATE TRIGGER fail_revocation BEFORE INSERT ON revocation BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
        operation = pb.IssuerOperation(identity_revoke=pb.IdentityRevoke(
            target=pb.IdentityReference(kind=pb.ACTOR_TARGET, id=self.identity.actor_id),
            expected_security_revision=0, reason='transaction failure'))
        self.denied(self.execute(self.owner_peer, self.challenge(self.owner_peer, operation)), core.STORAGE_ERROR)
        batch = self.batch(self.read(self.client()))
        self.assertEqual(batch.security_revision, 0)
        self.assertTrue(batch.actors[0].active)

    def test_reads_do_not_change_database_or_security_revision(self):
        with sqlite3.connect(self.state/'identity.db') as db: before = list(db.iterdump())
        self.read(self.client()); self.read(self.owner_peer, [b'q'*32])
        with sqlite3.connect(self.state/'identity.db') as db: after = list(db.iterdump())
        self.assertEqual(before, after)

    def test_unapproved_enrollment_is_not_an_actor(self):
        key, public = self.key('pending')
        pending = self.enroll(self.owner_peer, self.intent(public=public), key, public)
        batch = self.batch(self.read(self.client(), [pending.enrollment_id, public]))
        self.assertTrue(all(not row.known and not row.active for row in batch.actors))

    def test_human_and_agent_identities_use_the_same_status(self):
        key, public = self.key('agent')
        record = self.decide(self.owner_peer, self.enroll(self.owner_peer, self.intent(public=public, kind=pb.AGENT), key, public)).enrollment
        claims = self.claims(record, self.info)
        batch = self.batch(self.read(self.client(), [self.identity.actor_id, claims.actor_id]))
        self.assertTrue(all(row.known and row.active for row in batch.actors))

    def test_batch_accepts_exactly_128_distinct_identities(self):
        ids = [i.to_bytes(32, 'big') for i in range(128)]
        batch = self.batch(self.read(self.client(), ids))
        self.assertEqual([x.actor_id for x in batch.actors], ids)
        self.assertTrue(all(not row.active for row in batch.actors))

    def test_empty_duplicate_short_and_excessive_batches_fail(self):
        for ids in ([], [b'x'*32]*2, [b'x'], [i.to_bytes(32, 'big') for i in range(129)]):
            with self.subTest(size=len(ids)):
                self.denied(self.read(self.owner_peer, ids), core.INVALID_ARGUMENT)
        self.assertTrue(self.batch(self.read(self.owner_peer)).actors[0].active)

    def test_unknown_request_fields_fail_without_poisoning_service(self):
        request = pb.ActorStatusRead(actor_ids=[self.identity.actor_id]); request.MergeFromString(b'\xf8\x07\x01')
        self.denied(self.owner_peer.call(actor_status=request), core.INVALID_ARGUMENT)
        self.assertTrue(self.batch(self.read(self.owner_peer)).actors[0].active)

    def test_actor_status_discloses_no_labels_keys_or_credentials(self):
        batch = self.batch(self.read(self.owner_peer))
        self.assertEqual({f.name for f, _ in batch.ListFields()}, {'issuer_id', 'actors'})
        self.assertEqual({f.name for f, _ in batch.actors[0].ListFields()}, {'actor_id', 'known', 'active'})
        self.assertNotIn(self.actor_public, batch.SerializeToString())
        self.assertNotIn(self.identity.credential_id, batch.SerializeToString())

    def test_status_is_not_a_proof_or_an_administrator_credential(self):
        client = self.client(); self.read(client)
        operation = pb.IssuerOperation(policy_set=pb.PolicySet(expected_revision=1,
            policy=pb.EnrollmentPolicy(mode=pb.APPROVE_ALWAYS, bootstrap_profile='empty-v1')))
        challenge = self.challenge(client, operation)
        self.denied(self.execute(client, challenge, proof=self.proof(challenge, self.actor_key, self.actor_public)))
        self.assertTrue(self.batch(self.read(client)).actors[0].active)

    def forged(self, edit):
        def send(peer, request, reply):
            reply.ClearField('error')
            batch = pb.ActorStatusBatch(issuer_id=self.info.issuer_id, security_revision=0,
                actors=[pb.ActorState(actor_id=entry, known=True, active=True) for entry in request.actor_status.actor_ids])
            edit(batch); reply.actor_status.CopyFrom(batch); peer.stream.sendall(frame(reply))
        witness = FaultPeer(self, response=send); client = self.client(witness.port)
        output, _ = client.proc.communicate(frame(pb.IssuerRequest(protocol_version=1,
            actor_status=pb.ActorStatusRead(actor_ids=[self.identity.actor_id, b'x'*32]))), timeout=4)
        self.assertEqual(client.proc.returncode, 2)
        self.assertEqual(output, b'')
        self.assertEqual(witness.connections, 1)
        self.assertEqual(len(witness.requests), 3)

    def test_client_rejects_wrong_issuer_pin(self):
        self.forged(lambda b: setattr(b, 'issuer_id', b'x'*32))

    def test_client_rejects_missing_or_extra_subject(self):
        self.forged(lambda b: b.actors.pop())
        self.forged(lambda b: b.actors.add(actor_id=b'z'*32, known=True, active=True))

    def test_client_rejects_reordered_or_substituted_subjects(self):
        self.forged(lambda b: setattr(b.actors[0], 'actor_id', b'x'*32))
        self.forged(lambda b: b.actors.reverse())

    def test_client_rejects_inconsistent_actor_flags(self):
        self.forged(lambda b: setattr(b.actors[0], 'revoked', True))
        self.forged(lambda b: setattr(b.actors[0], 'known', False))
        self.forged(lambda b: setattr(b.actors[0], 'active', False))
        def invalid(b):
            b.actors[0].known = False; b.actors[0].revoked = True; b.actors[0].active = False
        self.forged(invalid)

    def test_client_rejects_unknown_fields_and_excessive_revision(self):
        self.forged(lambda b: b.MergeFromString(b'\xf8\x07\x01'))
        self.forged(lambda b: b.actors[0].MergeFromString(b'\xf8\x07\x01'))
        self.forged(lambda b: setattr(b, 'security_revision', 2**63))

    def test_checked_client_remains_usable_after_typed_request_error(self):
        client = self.client(); self.denied(self.read(client, []), core.INVALID_ARGUMENT)
        self.assertTrue(self.batch(self.read(client)).actors[0].active)


if __name__ == '__main__': unittest.main(verbosity=2)
