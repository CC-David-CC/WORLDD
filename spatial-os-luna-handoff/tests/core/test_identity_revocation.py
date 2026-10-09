"""Current issuer status, signed actor proofs, and durable independent revocation."""
import sqlite3
import subprocess
import time
import unittest

import core_pb2 as core
import issuer_pb2 as pb
import key_pb2 as keys
from test_identity_enrollment import EnrollmentFixture, BINARY


class RevocationTests(EnrollmentFixture):
    def setUp(self):
        super().setUp()
        self.info=self.init();self.client=self.start()
        self.record=self.decide(self.client,self.enroll(self.client)).enrollment
        self.actor=self.claims(self.record,self.info)

    def auth_begin(self, record=None, audience='world/test', binding=b'w'*32, client=None):
        return (client or self.client).call(authentication_begin=pb.AuthenticationBegin(
            credential=(record or self.record).credential,audience=audience,channel_binding=binding))

    def auth_challenge(self, **options):
        reply=self.auth_begin(**options);self.assertTrue(reply.HasField('challenge'),reply);return reply.challenge

    def auth_finish(self, challenge, key=None, public=None, proof=None, client=None):
        proof=proof or self.proof(challenge,key or self.actor_key,public or self.actor_public)
        return (client or self.client).call(authentication_complete=pb.AuthenticationComplete(nonce=challenge.nonce,proof=proof))

    def authenticate(self, record=None, key=None, public=None):
        return self.auth_finish(self.auth_challenge(record=record),key,public)

    def reference(self, actor=False, id=None):
        return pb.IdentityReference(kind=pb.ACTOR_TARGET if actor else pb.CREDENTIAL_TARGET,
                                    id=id or (self.actor.actor_id if actor else self.actor.credential_id))

    def read_status(self, target=None):
        operation=pb.IssuerOperation(identity_read=target or self.reference())
        return self.execute(self.client,self.challenge(self.client,operation))

    def revoke(self, actor=False, revision=0, id=None, reason='test revocation'):
        operation=pb.IssuerOperation(identity_revoke=pb.IdentityRevoke(
            target=self.reference(actor,id),expected_security_revision=revision,reason=reason))
        return self.execute(self.client,self.challenge(self.client,operation))

    def check(self, record=None):
        return self.client.call(credential_check=(record or self.record).credential)

    def another(self, name='second', same_actor=True):
        key,public=self.key(name)
        request=self.enroll(self.client,self.intent(public,label=name),key,public)
        reply=self.decide(self.client,request,pb.APPROVE_EXISTING if same_actor else pb.APPROVE_NEW,
                          self.actor.actor_id if same_actor else b'')
        self.assertTrue(reply.HasField('enrollment'),reply)
        return reply.enrollment,key,public

    def test_fresh_actor_proof_derives_identity_from_registered_credential(self):
        challenge=self.auth_challenge()
        payload=pb.AuthenticationChallenge();payload.ParseFromString(challenge.signing_request.payload)
        self.assertEqual(challenge.signing_request.purpose,keys.ACTOR_AUTHENTICATION)
        self.assertEqual(payload.protocol_version,1);self.assertEqual(payload.issuer_id,self.info.issuer_id)
        self.assertEqual(payload.nonce,challenge.nonce);self.assertEqual(len(payload.incarnation),32)
        self.assertEqual(payload.request.credential,self.record.credential)
        self.assertEqual(payload.request.audience,'world/test');self.assertEqual(payload.request.channel_binding,b'w'*32)
        result=self.auth_finish(challenge).authentication
        self.assertTrue(result.identity.active);self.assertEqual(result.identity.actor_id,self.actor.actor_id)
        self.assertEqual(result.identity.credential_id,self.actor.credential_id)
        self.assertEqual(result.identity.issuer_id,self.info.issuer_id);self.assertEqual(result.identity.kind,pb.HUMAN)
        self.assertEqual(result.audience,'world/test');self.assertEqual(result.channel_binding,b'w'*32)

    def test_another_key_cannot_authenticate_and_failed_attempt_consumes_challenge(self):
        challenge=self.auth_challenge()
        self.denied(self.auth_finish(challenge,self.owner,self.owner_public));self.denied(self.auth_finish(challenge))
        self.assertTrue(self.authenticate().authentication.identity.active)

    def test_changed_audience_binding_or_signature_purpose_is_rejected(self):
        for field,value in [('audience','other/world'),('channel_binding',b'x'*32)]:
            challenge=self.auth_challenge();payload=pb.AuthenticationChallenge()
            payload.ParseFromString(challenge.signing_request.payload);setattr(payload.request,field,value)
            proof=self.proof(challenge,self.actor_key,self.actor_public,payload=payload.SerializeToString())
            self.denied(self.auth_finish(challenge,proof=proof))
        challenge=self.auth_challenge();proof=self.proof(challenge,self.actor_key,self.actor_public,purpose=keys.ENROLLMENT_PROOF)
        proof.purpose=keys.ACTOR_AUTHENTICATION;self.denied(self.auth_finish(challenge,proof=proof))

    def test_successful_actor_proof_cannot_be_replayed(self):
        challenge=self.auth_challenge();proof=self.proof(challenge,self.actor_key,self.actor_public)
        self.assertTrue(self.auth_finish(challenge,proof=proof).authentication.identity.active)
        self.denied(self.auth_finish(challenge,proof=proof))

    def test_authentication_expiry_and_restart_reject_old_proofs(self):
        self.client.close();self.client=self.start(ttl=200)
        challenge=self.auth_challenge();proof=self.proof(challenge,self.actor_key,self.actor_public);time.sleep(.3)
        self.denied(self.auth_finish(challenge,proof=proof))
        challenge=self.auth_challenge();proof=self.proof(challenge,self.actor_key,self.actor_public)
        self.client.close(crash=True);self.client=self.start();self.denied(self.auth_finish(challenge,proof=proof))
        self.assertTrue(self.authenticate().authentication.identity.active)

    def test_credentials_and_proofs_do_not_cross_issuer_instances(self):
        other=self.root/'other';other.mkdir(mode=0o700);self.init(other);remote=self.start(other)
        self.denied(self.auth_begin(client=remote))
        challenge=self.auth_challenge();self.denied(self.auth_finish(challenge,client=remote))
        self.assertTrue(self.auth_finish(challenge).authentication.identity.active)

    def test_tampered_or_self_signed_credential_cannot_start_authentication(self):
        bad=pb.EnrollmentRecord();bad.CopyFrom(self.record)
        signature=bad.credential.proof.signature;bad.credential.proof.signature=bytes([signature[0]^1])+signature[1:]
        self.denied(self.auth_begin(record=bad))
        bad.CopyFrom(self.record)
        forged=pb.ChallengeReply(signing_request=bad.credential.content)
        bad.credential.proof.CopyFrom(self.proof(forged,self.actor_key,self.actor_public))
        self.denied(self.auth_begin(record=bad))

    def test_valid_issuer_signature_does_not_register_an_unissued_credential(self):
        bad=pb.EnrollmentRecord();bad.CopyFrom(self.record)
        claims=pb.ActorCredential();claims.CopyFrom(self.actor);claims.credential_id=b'x'*32
        bad.credential.content.payload=claims.SerializeToString()
        proof=self.proof(pb.ChallengeReply(signing_request=bad.credential.content),self.state/'issuer.pem',self.info.public_key)
        bad.credential.proof.CopyFrom(proof)
        self.denied(self.auth_begin(record=bad));self.denied(self.check(bad))

    def test_credential_revocation_preserves_other_credential_and_pending_proof(self):
        second,key,public=self.another();pending=self.auth_challenge(record=second)
        result=self.revoke().identity_status
        self.assertTrue(result.credential_revoked);self.assertFalse(result.actor_revoked);self.assertFalse(result.active)
        self.assertEqual(result.security_revision,1);self.denied(self.auth_begin())
        other=self.auth_finish(pending,key,public).authentication.identity
        self.assertTrue(other.active);self.assertEqual(other.actor_id,self.actor.actor_id)
        self.assertEqual(other.security_revision,1);self.assertEqual(self.policy(self.client).revision,1)

    def test_actor_revocation_disables_all_its_credentials_but_not_another_actor(self):
        second,key,public=self.another();other,other_key,other_public=self.another('other',False)
        result=self.revoke(actor=True).identity_status
        self.assertTrue(result.actor_revoked);self.assertFalse(result.active)
        self.denied(self.auth_begin());self.denied(self.auth_begin(record=second))
        self.assertTrue(self.authenticate(other,other_key,other_public).authentication.identity.active)
        with sqlite3.connect(self.state/'identity.db') as db:
            rows=db.execute('SELECT kind,target_id,revision,reason FROM revocation').fetchall()
        self.assertEqual(rows,[(pb.ACTOR_TARGET,self.actor.actor_id,1,b'test revocation')])

    def test_revocation_between_challenge_and_completion_denies_authentication(self):
        challenge=self.auth_challenge();proof=self.proof(challenge,self.actor_key,self.actor_public)
        self.revoke();self.denied(self.auth_finish(challenge,proof=proof))
        self.assertFalse(self.check().identity_status.active)

    def test_only_owner_can_read_identity_status_or_revoke(self):
        for operation in [pb.IssuerOperation(identity_read=self.reference()),
                          pb.IssuerOperation(identity_revoke=pb.IdentityRevoke(target=self.reference(),expected_security_revision=0,reason='attempt'))]:
            challenge=self.challenge(self.client,operation)
            self.denied(self.execute(self.client,challenge,self.proof(challenge,self.actor_key,self.actor_public)))
        self.assertTrue(self.authenticate().authentication.identity.active)

    def test_stale_security_revision_does_not_revoke_another_identity(self):
        second,key,public=self.another();second_claims=self.claims(second,self.info)
        self.revoke();self.denied(self.revoke(id=second_claims.credential_id),core.REVISION_CONFLICT)
        self.assertTrue(self.authenticate(second,key,public).authentication.identity.active)
        self.assertFalse(self.revoke(id=second_claims.credential_id,revision=1).identity_status.active)

    def test_fresh_duplicate_revocation_preserves_first_reason_and_revision(self):
        first=self.revoke().identity_status
        again=self.revoke(revision=1,reason='duplicate').identity_status;self.assertEqual(again,first)
        with sqlite3.connect(self.state/'identity.db') as db:
            self.assertEqual(db.execute('SELECT revision,reason FROM revocation').fetchall(),[(1,b'test revocation')])
        self.denied(self.revoke(),core.REVISION_CONFLICT)

    def test_revocation_and_security_revision_survive_process_death(self):
        self.revoke();self.client.close(crash=True);self.client=self.start()
        state=self.read_status().identity_status
        self.assertEqual(state.security_revision,1);self.assertTrue(state.credential_revoked)
        self.denied(self.auth_begin())

    def test_missing_targets_do_not_change_security_revision(self):
        for actor in (False,True):self.denied(self.revoke(actor=actor,id=b'x'*32),core.NOT_FOUND)
        self.assertEqual(self.check().identity_status.security_revision,0)

    def test_revoked_actor_cannot_receive_an_additional_credential(self):
        key,public=self.key('extra');request=self.enroll(self.client,self.intent(public),key,public)
        self.revoke(actor=True)
        self.denied(self.decide(self.client,request,pb.APPROVE_EXISTING,self.actor.actor_id))
        self.assertEqual(self.inspect(self.client,request).enrollment.status,pb.PENDING)
        self.assertEqual(self.counts(),(2,1,1))

    def test_fresh_enrollment_recovery_does_not_reactivate_a_revoked_credential(self):
        self.revoke();recovered=self.enroll(self.client)
        self.assertEqual(recovered,self.record);self.assertFalse(self.check(recovered).identity_status.active)
        self.denied(self.auth_begin(record=recovered));self.assertEqual(self.counts(),(1,1,1))

    def test_sqlite_abort_rolls_back_revocation_and_global_revision(self):
        challenge=self.auth_challenge()
        with sqlite3.connect(self.state/'identity.db') as db:
            db.execute("CREATE TRIGGER revocation_write_failure BEFORE INSERT ON revocation BEGIN SELECT RAISE(ABORT,'injected revocation failure'); END")
        self.denied(self.revoke(),core.STORAGE_ERROR)
        self.assertTrue(self.auth_finish(challenge).authentication.identity.active)
        self.assertEqual(self.check().identity_status.security_revision,0)
        with sqlite3.connect(self.state/'identity.db') as db:db.execute('DROP TRIGGER revocation_write_failure')
        self.assertEqual(self.revoke().identity_status.security_revision,1)

    def test_explicit_v2_migration_preserves_all_existing_credential_bytes(self):
        self.client.close();secret=(self.state/'issuer.pem').read_bytes();policy=self.snapshot()
        with sqlite3.connect(self.state/'identity.db') as db:
            before=db.execute('SELECT * FROM credential').fetchall()
            db.executescript('DROP TABLE authority_obligation; DROP TABLE authority_barrier; DROP TABLE authority_activation; DROP TABLE authority_participant_domain; DROP TABLE authority_participant; DROP TABLE authority_registry; DROP TABLE bootstrap_profile; DROP TABLE revocation; DROP TABLE security_state; PRAGMA user_version=2;')
        command=[str(BINARY),'--state-dir',str(self.state)]
        result=subprocess.run(command,input=b'',capture_output=True,timeout=4)
        self.assertNotEqual(result.returncode,0);self.assertIn(b'migration',result.stderr.lower())
        result=subprocess.run(command+['--migrate'],input=b'',capture_output=True,timeout=4)
        self.assertEqual(result.returncode,0,result.stderr)
        with sqlite3.connect(self.state/'identity.db') as db:
            self.assertEqual(db.execute('SELECT * FROM credential').fetchall(),before)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],6)
        self.assertEqual(self.snapshot(),policy);self.assertEqual((self.state/'issuer.pem').read_bytes(),secret)
        self.client=self.start();self.assertTrue(self.authenticate().authentication.identity.active)

    def test_invalid_revocation_requests_do_not_allocate_owner_challenges(self):
        invalid=[pb.IdentityRevoke(target=self.reference(),reason='missing revision'),
                 pb.IdentityRevoke(target=self.reference(),expected_security_revision=0,reason=''),
                 pb.IdentityRevoke(target=self.reference(),expected_security_revision=0,reason='x'*257),
                 pb.IdentityRevoke(target=self.reference(),expected_security_revision=0,reason='a\0b'),
                 pb.IdentityRevoke(target=pb.IdentityReference(kind=99,id=b'x'*32),expected_security_revision=0,reason='invalid')]
        for request in invalid:
            self.denied(self.client.call(challenge=pb.IssuerOperation(identity_revoke=request)),core.INVALID_ARGUMENT)
        self.assertEqual(self.check().identity_status.security_revision,0)

    def test_invalid_authentication_context_and_unknown_fields_are_rejected(self):
        for audience,binding in [('',b'w'*32),('x'*129,b'w'*32),('a\0b',b'w'*32),('world',b'x')]:
            self.denied(self.auth_begin(audience=audience,binding=binding),core.INVALID_ARGUMENT)
        bad=pb.EnrollmentRecord();bad.CopyFrom(self.record)
        bad.credential.ParseFromString(bad.credential.SerializeToString()+b'\xa0\x06\x01')
        self.denied(self.auth_begin(record=bad),core.INVALID_ARGUMENT)

    def test_authentication_uses_the_same_bounded_challenge_pool(self):
        for _ in range(128):self.challenge(self.client);self.auth_challenge()
        self.denied(self.auth_begin(),core.BUSY)
        self.denied(self.client.call(enrollment_begin=self.intent()),core.BUSY)

    def test_owner_and_authentication_challenges_cannot_cross_protocols(self):
        challenge=self.auth_challenge()
        self.denied(self.execute(self.client,challenge,self.proof(challenge,self.actor_key,self.actor_public)))
        self.denied(self.auth_finish(challenge))
        challenge=self.challenge(self.client)
        self.denied(self.auth_finish(challenge,self.owner,self.owner_public));self.denied(self.execute(self.client,challenge))


if __name__=='__main__':unittest.main(verbosity=2)
