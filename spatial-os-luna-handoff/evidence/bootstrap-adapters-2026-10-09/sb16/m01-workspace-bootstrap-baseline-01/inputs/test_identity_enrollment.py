"""Real issuer enrollment, independent signatures, and durable credential checks."""
import sqlite3
import struct
import subprocess
import time
import unittest

import core_pb2 as core
import issuer_pb2 as pb
import key_pb2 as keys
from test_identity_issuer import IssuerFixture, BINARY, DOMAIN


class EnrollmentFixture(IssuerFixture):
    def setUp(self):
        super().setUp()
        self.actor_key, self.actor_public = self.key('actor')

    def intent(self, public=None, kind=pb.HUMAN, label='David test actor'):
        return pb.EnrollmentIntent(public_key=public or self.actor_public, kind=kind, label=label)

    def begin(self, client, intent=None):
        reply = client.call(enrollment_begin=intent or self.intent())
        self.assertTrue(reply.HasField('challenge'), reply)
        return reply.challenge

    def finish(self, client, challenge, key=None, public=None, proof=None):
        proof = proof or self.proof(challenge, key or self.actor_key, public or self.actor_public)
        return client.call(enrollment_complete=pb.EnrollmentComplete(nonce=challenge.nonce, proof=proof))

    def enroll(self, client, intent=None, key=None, public=None):
        reply = self.finish(client, self.begin(client, intent), key, public)
        self.assertTrue(reply.HasField('enrollment'), reply)
        return reply.enrollment

    def decide(self, client, record, choice=pb.APPROVE_NEW, target=b'', revision=1):
        operation = pb.IssuerOperation(enrollment_decision=pb.EnrollmentDecision(
            enrollment_id=record.enrollment_id, choice=choice, target_actor_id=target,
            expected_policy_revision=revision))
        return self.execute(client, self.challenge(client, operation))

    def inspect(self, client, record):
        operation = pb.IssuerOperation(enrollment_read=pb.EnrollmentRead(enrollment_id=record.enrollment_id))
        return self.execute(client, self.challenge(client, operation))

    def claims(self, record, info, profile='empty-v1', profile_revision=1):
        self.assertEqual(record.status, pb.APPROVED)
        signed = record.credential
        self.assertEqual(signed.content.purpose, keys.ISSUER_CREDENTIAL)
        self.assertEqual(signed.proof.purpose, keys.ISSUER_CREDENTIAL)
        self.assertEqual(signed.proof.public_key, info.public_key)
        public, data, signature = self.root/'issuer-public.der', self.root/'verify.bin', self.root/'verify.sig'
        public.write_bytes(bytes.fromhex('302a300506032b6570032100')+info.public_key)
        data.write_bytes(DOMAIN+struct.pack('!II', signed.content.purpose, len(signed.content.payload))+signed.content.payload)
        signature.write_bytes(signed.proof.signature)
        self.openssl('pkeyutl','-verify','-rawin','-pubin','-inkey',public,'-keyform','DER','-in',data,'-sigfile',signature)
        claims = pb.ActorCredential(); claims.ParseFromString(signed.content.payload)
        self.assertEqual(claims.protocol_version,1); self.assertEqual(claims.issuer_id,info.issuer_id)
        self.assertEqual(claims.enrollment_id,record.enrollment_id)
        self.assertEqual(claims.public_key,record.intent.public_key); self.assertEqual(claims.kind,record.intent.kind)
        self.assertEqual(claims.audience,'spatial/actor-credential/v1')
        self.assertEqual(claims.bootstrap_profile,profile); self.assertEqual(claims.bootstrap_profile_revision,profile_revision)
        self.assertEqual(len(claims.actor_id),32); self.assertEqual(len(claims.credential_id),32)
        return claims

    def counts(self):
        with sqlite3.connect(f'file:{self.state}/identity.db?mode=ro',uri=True) as db:
            return tuple(db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('enrollment','actor','credential'))

class EnrollmentTests(EnrollmentFixture):
    def test_proven_key_waits_for_owner_approval(self):
        self.init(); client=self.start(); record=self.enroll(client)
        self.assertEqual(record.status,pb.PENDING); self.assertEqual(len(record.enrollment_id),32)
        self.assertEqual(record.intent,self.intent()); self.assertFalse(record.HasField('credential'))
        self.assertEqual(self.counts(),(1,0,0)); self.assertEqual(self.inspect(client,record).enrollment,record)

    def test_owner_approval_issues_an_independently_verifiable_durable_credential(self):
        info=self.init();client=self.start();record=self.enroll(client)
        approved=self.decide(client,record).enrollment; claims=self.claims(approved,info)
        self.assertEqual(claims.policy_revision,1);self.assertEqual(self.counts(),(1,1,1))
        with sqlite3.connect(self.state/'identity.db') as db:
            row,=db.execute('SELECT id,actor_id,public_key,signed_credential FROM credential').fetchall()
            self.assertEqual(row[:3],(claims.credential_id,claims.actor_id,self.actor_public))
            stored=pb.SignedCredential();stored.ParseFromString(row[3]);self.assertEqual(stored,approved.credential)
        client.close(crash=True);client=self.start()
        self.assertEqual(self.enroll(client),approved);self.assertEqual(self.counts(),(1,1,1))

    def test_humans_and_agents_get_distinct_actor_and_credential_identities(self):
        info=self.init();client=self.start();human=self.decide(client,self.enroll(client)).enrollment
        key,public=self.key('agent');record=self.enroll(client,self.intent(public,pb.AGENT,'agent'),key,public)
        agent=self.decide(client,record).enrollment
        a,b=self.claims(human,info),self.claims(agent,info)
        self.assertNotEqual(a.actor_id,b.actor_id);self.assertNotEqual(a.credential_id,b.credential_id)
        self.assertEqual(self.counts(),(2,2,2))

    def test_enrollment_challenge_binds_the_exact_intent_and_service(self):
        info=self.init();client=self.start();challenge=self.begin(client)
        payload=pb.EnrollmentChallenge();payload.ParseFromString(challenge.signing_request.payload)
        self.assertEqual(challenge.signing_request.purpose,keys.ENROLLMENT_PROOF)
        self.assertEqual(payload.protocol_version,1);self.assertEqual(payload.issuer_id,info.issuer_id)
        self.assertEqual(payload.intent,self.intent());self.assertEqual(payload.nonce,challenge.nonce)
        self.assertEqual(len(payload.incarnation),32);self.assertEqual(payload.audience,'identityd/enroll/v1')
        self.assertEqual(payload.lifetime_ms,30000)

    def test_another_key_cannot_enroll_and_failed_completion_consumes_nonce(self):
        self.init();client=self.start();challenge=self.begin(client)
        self.denied(self.finish(client,challenge,self.owner,self.owner_public))
        self.denied(self.finish(client,challenge));self.assertEqual(self.counts(),(0,0,0))

    def test_changed_enrollment_intent_or_purpose_is_rejected(self):
        self.init();client=self.start();challenge=self.begin(client)
        content=pb.EnrollmentChallenge();content.ParseFromString(challenge.signing_request.payload)
        content.intent.label='changed'
        proof=self.proof(challenge,self.actor_key,self.actor_public,payload=content.SerializeToString())
        self.denied(self.finish(client,challenge,proof=proof))
        challenge=self.begin(client)
        proof=self.proof(challenge,self.actor_key,self.actor_public,purpose=keys.ACTOR_AUTHENTICATION)
        proof.purpose=keys.ENROLLMENT_PROOF
        self.denied(self.finish(client,challenge,proof=proof));self.assertEqual(self.counts(),(0,0,0))

    def test_replay_is_denied_but_fresh_proof_recovers_the_same_pending_request(self):
        self.init();client=self.start();challenge=self.begin(client)
        proof=self.proof(challenge,self.actor_key,self.actor_public)
        first=self.finish(client,challenge,proof=proof).enrollment
        self.denied(self.finish(client,challenge,proof=proof))
        self.assertEqual(self.enroll(client),first);self.assertEqual(self.counts(),(1,0,0))

    def test_restart_preserves_pending_requests_and_invalidates_old_challenges(self):
        self.init();client=self.start();first=self.enroll(client);challenge=self.begin(client)
        proof=self.proof(challenge,self.actor_key,self.actor_public);client.close(crash=True);client=self.start()
        self.denied(self.finish(client,challenge,proof=proof));self.assertEqual(self.enroll(client),first)
        self.assertEqual(self.decide(client,first).enrollment.status,pb.APPROVED)

    def test_enrollment_proof_expires_on_the_real_clock(self):
        self.init();client=self.start(ttl=200);challenge=self.begin(client)
        proof=self.proof(challenge,self.actor_key,self.actor_public);time.sleep(.3)
        self.denied(self.finish(client,challenge,proof=proof));self.assertEqual(self.counts(),(0,0,0))
        self.assertEqual(self.enroll(client).status,pb.PENDING)

    def test_owner_and_actor_challenges_cannot_be_used_in_the_other_protocol(self):
        self.init();client=self.start();challenge=self.begin(client)
        self.denied(self.execute(client,challenge,self.proof(challenge,self.actor_key,self.actor_public)))
        self.denied(self.finish(client,challenge))
        challenge=self.challenge(client)
        self.denied(self.finish(client,challenge,self.owner,self.owner_public));self.denied(self.execute(client,challenge))
        self.assertEqual(self.counts(),(0,0,0))

    def test_requester_cannot_approve_itself_or_read_another_request_as_owner(self):
        self.init();client=self.start();record=self.enroll(client)
        for operation in (
            pb.IssuerOperation(enrollment_read=pb.EnrollmentRead(enrollment_id=record.enrollment_id)),
            pb.IssuerOperation(enrollment_decision=pb.EnrollmentDecision(enrollment_id=record.enrollment_id,choice=pb.APPROVE_NEW,expected_policy_revision=1))):
            challenge=self.challenge(client,operation)
            self.denied(self.execute(client,challenge,self.proof(challenge,self.actor_key,self.actor_public)))
        self.assertEqual(self.counts(),(1,0,0))

    def test_rejection_is_durable_and_cannot_be_reapproved_or_reenrolled(self):
        self.init();client=self.start();record=self.enroll(client)
        rejected=self.decide(client,record,pb.REJECT).enrollment
        self.assertEqual(rejected.status,pb.REJECTED);self.assertFalse(rejected.HasField('credential'))
        self.denied(self.decide(client,record),core.REVISION_CONFLICT)
        client.close();client=self.start();self.assertEqual(self.enroll(client),rejected)
        self.assertEqual(self.counts(),(1,0,0))

    def test_duplicate_decisions_do_not_create_another_credential(self):
        self.init();client=self.start();record=self.enroll(client)
        self.assertEqual(self.decide(client,record).enrollment.status,pb.APPROVED)
        self.denied(self.decide(client,record),core.REVISION_CONFLICT);self.assertEqual(self.counts(),(1,1,1))

    def test_a_key_cannot_create_a_second_request_by_changing_its_intent(self):
        self.init();client=self.start();self.enroll(client)
        for intent in (self.intent(label='renamed'),self.intent(kind=pb.AGENT)):
            self.denied(self.finish(client,self.begin(client,intent)),core.ALREADY_EXISTS)
        self.assertEqual(self.counts(),(1,0,0))

    def test_owner_can_explicitly_attach_a_second_proven_key_to_one_actor(self):
        info=self.init();client=self.start();first=self.claims(self.decide(client,self.enroll(client)).enrollment,info)
        key,public=self.key('second');request=self.enroll(client,self.intent(public,label='second credential'),key,public)
        added=self.decide(client,request,pb.APPROVE_EXISTING,first.actor_id).enrollment
        second=self.claims(added,info)
        self.assertEqual(first.actor_id,second.actor_id);self.assertNotEqual(first.credential_id,second.credential_id)
        self.assertEqual(self.counts(),(2,1,2))

    def test_attach_rejects_missing_actor_or_incompatible_kind_without_partial_issuance(self):
        info=self.init();client=self.start();first=self.claims(self.decide(client,self.enroll(client)).enrollment,info)
        key,public=self.key('agent');request=self.enroll(client,self.intent(public,pb.AGENT),key,public)
        self.denied(self.decide(client,request,pb.APPROVE_EXISTING,b'x'*32),core.NOT_FOUND)
        self.denied(self.decide(client,request,pb.APPROVE_EXISTING,first.actor_id),core.INVALID_ARGUMENT)
        self.assertEqual(self.counts(),(2,1,1));self.assertEqual(self.inspect(client,request).enrollment.status,pb.PENDING)

    def test_policy_revision_change_prevents_stale_approval(self):
        self.init();client=self.start();request=self.enroll(client)
        self.assertEqual(self.execute(client,self.challenge(client,self.operation(pb.MANUAL,1,'empty-v1'))).policy.revision,2)
        self.denied(self.decide(client,request),core.REVISION_CONFLICT);self.assertEqual(self.counts(),(1,0,0))
        self.assertEqual(self.decide(client,request,revision=2).enrollment.status,pb.APPROVED)

    def test_unknown_profile_blocks_issuance_and_preserves_pending_request(self):
        self.init();client=self.start();request=self.enroll(client)
        self.execute(client,self.challenge(client,self.operation(pb.MANUAL,1,'unknown')))
        self.denied(self.decide(client,request,revision=2),core.NOT_FOUND)
        self.assertEqual(self.counts(),(1,0,0));self.assertEqual(self.inspect(client,request).enrollment.status,pb.PENDING)

    def test_approve_always_uses_the_same_credential_format_without_owner_or_workspace_grants(self):
        info=self.init();client=self.start()
        self.execute(client,self.challenge(client,self.operation(pb.APPROVE_ALWAYS,1,'empty-v1')))
        record=self.enroll(client);claims=self.claims(record,info)
        self.assertEqual(claims.policy_revision,2);self.assertEqual(self.counts(),(1,1,1))
        challenge=self.challenge(client)
        self.denied(self.execute(client,challenge,self.proof(challenge,self.actor_key,self.actor_public)))
        self.assertEqual(self.enroll(client),record)

    def test_auto_policy_rejects_unknown_profile_without_persisting_partial_enrollment(self):
        self.init();client=self.start()
        self.execute(client,self.challenge(client,self.operation(pb.APPROVE_ALWAYS,1,'missing')))
        self.denied(self.finish(client,self.begin(client)),core.NOT_FOUND);self.assertEqual(self.counts(),(0,0,0))

    def test_policy_switch_does_not_approve_old_pending_requests_implicitly(self):
        self.init();client=self.start();record=self.enroll(client)
        self.execute(client,self.challenge(client,self.operation(pb.APPROVE_ALWAYS,1,'empty-v1')))
        self.assertEqual(self.enroll(client),record);self.assertEqual(self.counts(),(1,0,0))
        self.assertEqual(self.decide(client,record,revision=2).enrollment.status,pb.APPROVED)

    def test_invalid_intents_and_reserved_keys_cannot_allocate_enrollments(self):
        info=self.init();client=self.start()
        invalid=[self.intent(kind=0),self.intent(kind=99),self.intent(public=b'x'),self.intent(label=''),
                 self.intent(label='x'*257),self.intent(label='x\0y'),self.intent(public=self.owner_public),self.intent(public=info.public_key)]
        for intent in invalid:self.denied(client.call(enrollment_begin=intent),core.INVALID_ARGUMENT)
        intent=self.intent();intent.ParseFromString(intent.SerializeToString()+b'\xa0\x06\x01')
        self.denied(client.call(enrollment_begin=intent),core.INVALID_ARGUMENT);self.assertEqual(self.counts(),(0,0,0))

    def test_enrollment_and_owner_challenges_share_one_bounded_pool(self):
        self.init();client=self.start()
        for _ in range(128):self.challenge(client);self.begin(client)
        self.denied(client.call(enrollment_begin=self.intent()),core.BUSY)
        self.denied(client.call(challenge=self.operation()),core.BUSY)

    def test_real_database_abort_rolls_back_actor_and_credential_creation(self):
        self.init();client=self.start();request=self.enroll(client)
        with sqlite3.connect(self.state/'identity.db') as db:
            db.execute("CREATE TRIGGER enrollment_write_failure BEFORE INSERT ON credential BEGIN SELECT RAISE(ABORT,'injected credential write failure'); END")
        self.denied(self.decide(client,request),core.STORAGE_ERROR)
        self.assertEqual(self.counts(),(1,0,0));self.assertEqual(self.inspect(client,request).enrollment.status,pb.PENDING)
        with sqlite3.connect(self.state/'identity.db') as db:db.execute('DROP TRIGGER enrollment_write_failure')
        self.assertEqual(self.decide(client,request).enrollment.status,pb.APPROVED);self.assertEqual(self.counts(),(1,1,1))

    def test_explicit_schema_migration_preserves_issuer_key_owner_and_policy(self):
        info=self.init();before=self.snapshot();secret=(self.state/'issuer.pem').read_bytes()
        with sqlite3.connect(self.state/'identity.db') as db:
            db.executescript('DROP TABLE IF EXISTS bootstrap_profile; DROP TABLE IF EXISTS revocation; DROP TABLE IF EXISTS security_state; '
                             'DROP TABLE credential; DROP TABLE enrollment; DROP TABLE actor; PRAGMA user_version=1;')
        command=[str(BINARY),'--state-dir',str(self.state)]
        result=subprocess.run(command,input=b'',capture_output=True,timeout=4)
        self.assertNotEqual(result.returncode,0);self.assertIn(b'migration',result.stderr.lower())
        result=subprocess.run(command+['--migrate'],input=b'',capture_output=True,timeout=4)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.snapshot(),before);self.assertEqual((self.state/'issuer.pem').read_bytes(),secret)
        client=self.start();self.assertEqual(client.call(info=pb.IssuerInfoRequest()).info,info)
        self.assertEqual(self.enroll(client).status,pb.PENDING)


if __name__=='__main__':unittest.main(verbosity=2)
