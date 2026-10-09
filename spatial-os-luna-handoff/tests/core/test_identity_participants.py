"""Owner-signed, durable authority service registration without actor grants."""
import sqlite3
import subprocess
import unittest
import core_pb2 as core
import grants_pb2 as grants
import issuer_pb2 as pb
from test_identity_enrollment import EnrollmentFixture,BINARY

DROP_REGISTRY='DROP TABLE authority_launch_authorization; DROP TABLE authority_supervisor; DROP TABLE authority_obligation; DROP TABLE authority_barrier; DROP TABLE authority_activation; DROP TABLE authority_participant_domain; DROP TABLE authority_participant; DROP TABLE authority_registry; '

class ParticipantTests(EnrollmentFixture):
    def setUp(self):
        super().setUp();self.info=self.init();self.client=self.start()
        self.service,self.service_public=self.key('authority-service')
    def participant(self,identity=b'a'*32,public=None,domains=None):
        return pb.AuthorityParticipant(id=identity,public_key=public or self.service_public,
            domains=domains if domains is not None else [grants.Domain(kind=grants.WORLD,id='park')])
    def registration_operation(self,value=None,revision=0):
        return pb.IssuerOperation(participant_register=pb.AuthorityParticipantRegister(
            participant=value or self.participant(),expected_registry_revision=revision))
    def register(self,value=None,revision=0):
        return self.execute(self.client,self.challenge(self.client,self.registration_operation(value,revision)))
    def registry(self,after=b'',limit=0):
        operation=pb.IssuerOperation(registry_read=pb.AuthorityRegistryRead(after_id=after,limit=limit))
        reply=self.execute(self.client,self.challenge(self.client,operation))
        self.assertTrue(reply.HasField('registry'),reply);return reply.registry
    def test_owner_registers_distinct_service_without_actor_grants(self):
        before=self.policy(self.client);record=self.register()
        self.assertTrue(record.HasField('participant'),record)
        self.assertEqual(record.participant.participant,self.participant())
        self.assertEqual(record.participant.issuer_id,self.info.issuer_id)
        self.assertEqual(record.participant.registered_revision,1)
        page=self.registry();self.assertEqual(page.registry_revision,1)
        self.assertEqual(list(page.participants),[record.participant]);self.assertFalse(page.next_after)
        self.assertEqual(self.policy(self.client),before);self.assertEqual(self.counts(),(0,0,0))
        with sqlite3.connect(self.state/'identity.db') as db:
            self.assertEqual(db.execute('SELECT revision FROM security_state').fetchone()[0],0)
    def test_empty_registry_is_explicit_and_owner_protected(self):
        page=self.registry();self.assertEqual(page.issuer_id,self.info.issuer_id)
        self.assertEqual(page.registry_revision,0);self.assertFalse(page.participants);self.assertFalse(page.next_after)
    def test_registry_requires_owner_proof(self):
        challenge=self.challenge(self.client,pb.IssuerOperation(registry_read=pb.AuthorityRegistryRead()))
        forged=self.proof(challenge,self.actor_key,self.actor_public)
        self.denied(self.execute(self.client,challenge,proof=forged))
        self.denied(self.execute(self.client,challenge))
        self.assertEqual(self.registry().registry_revision,0)
    def test_actor_cannot_register_and_bad_proof_consumes_challenge(self):
        challenge=self.challenge(self.client,self.registration_operation())
        self.denied(self.execute(self.client,challenge,proof=self.proof(challenge,self.actor_key,self.actor_public)))
        self.denied(self.execute(self.client,challenge));self.assertFalse(self.registry().participants)
    def test_stale_registry_revision_cannot_add_second_service(self):
        self.assertTrue(self.register().HasField('participant'));_,key=self.key('second')
        second=self.participant(b'b'*32,key,[grants.Domain(kind=grants.WORLD,id='home')])
        self.denied(self.register(second,0),core.REVISION_CONFLICT)
        self.assertEqual(self.registry().registry_revision,1)
        self.assertTrue(self.register(second,1).HasField('participant'))
    def test_duplicate_identity_key_and_domain_do_not_replace_record(self):
        original=self.register().participant;_,key=self.key('other')
        examples=[(self.participant(public=key),core.ALREADY_EXISTS),
            (self.participant(b'b'*32,domains=[grants.Domain(kind=grants.WORLD,id='other')]),core.ALREADY_EXISTS),
            (self.participant(b'c'*32,key),core.IN_USE)]
        for value,code in examples:self.denied(self.register(value,1),code)
        self.assertEqual(list(self.registry().participants),[original])
    def test_same_domain_text_with_different_kind_is_distinct(self):
        self.assertTrue(self.register().HasField('participant'));_,key=self.key('workspace')
        value=self.participant(b'b'*32,key,[grants.Domain(kind=grants.WORKSPACE,id='park')])
        self.assertTrue(self.register(value,1).HasField('participant'))
    def test_domains_are_canonical_and_support_multiple_workspaces(self):
        domains=[grants.Domain(kind=grants.WORKSPACE,id='z'),grants.Domain(kind=grants.WORLD,id='b'),
            grants.Domain(kind=grants.WORKSPACE,id='a')]
        record=self.register(self.participant(domains=domains)).participant
        self.assertEqual([(d.kind,d.id) for d in record.participant.domains],[(grants.WORLD,'b'),(grants.WORKSPACE,'a'),(grants.WORKSPACE,'z')])
    def test_malformed_registration_is_rejected_before_owner_challenge(self):
        examples=[self.participant(identity=b'x'),self.participant(public=b'x'),self.participant(domains=[]),
            self.participant(domains=[grants.Domain(kind=grants.DOMAIN_UNSPECIFIED,id='x')]),
            self.participant(domains=[grants.Domain(kind=grants.WORLD,id='')]),
            self.participant(domains=[grants.Domain(kind=grants.WORLD,id='x\0y')]),
            self.participant(domains=[grants.Domain(kind=grants.WORLD,id='x'*257)]),
            self.participant(domains=[grants.Domain(kind=grants.WORLD,id='x')]*2),
            self.participant(domains=[grants.Domain(kind=grants.WORLD,id=str(i)) for i in range(65)])]
        for value in examples:self.denied(self.client.call(challenge=self.registration_operation(value)),core.INVALID_ARGUMENT)
        missing=self.registration_operation();missing.participant_register.ClearField('expected_registry_revision')
        self.denied(self.client.call(challenge=missing),core.INVALID_ARGUMENT)
        too_large=self.registration_operation(revision=2**63);self.denied(self.client.call(challenge=too_large),core.INVALID_ARGUMENT)
        self.assertEqual(self.registry().registry_revision,0)
    def test_all_64_domains_are_preserved(self):
        value=self.participant(domains=[grants.Domain(kind=grants.WORKSPACE,id=f'project-{i:02}') for i in range(64)])
        self.assertEqual(self.register(value).participant.participant,value)
    def test_reserved_owner_issuer_and_actor_keys_cannot_be_services(self):
        self.enroll(self.client)
        for public in (self.owner_public,self.info.public_key,self.actor_public):
            self.denied(self.register(self.participant(public=public)),core.FORBIDDEN)
        self.assertFalse(self.registry().participants)
    def test_service_key_cannot_enroll_as_actor(self):
        self.assertTrue(self.register().HasField('participant'))
        self.denied(self.client.call(enrollment_begin=self.intent(self.service_public)),core.FORBIDDEN)
        self.assertEqual(self.counts(),(0,0,0))
    def test_registration_invalidates_pending_actor_enrollment_for_same_key(self):
        challenge=self.begin(self.client,self.intent(self.service_public))
        self.assertTrue(self.register().HasField('participant'))
        self.denied(self.finish(self.client,challenge,self.service,self.service_public),core.FORBIDDEN)
        self.assertEqual(self.counts(),(0,0,0));self.assertEqual(self.registry().registry_revision,1)
    def test_restart_preserves_registration(self):
        record=self.register().participant;self.client.close();self.client=self.start()
        self.assertEqual(list(self.registry().participants),[record])
        self.denied(self.register(revision=1),core.ALREADY_EXISTS)
    def test_owner_challenge_cannot_replay_after_restart(self):
        challenge=self.challenge(self.client,self.registration_operation());proof=self.proof(challenge)
        self.client.close();self.client=self.start();self.denied(self.execute(self.client,challenge,proof=proof))
        self.assertFalse(self.registry().participants)
    def test_pagination_is_ordered_and_bounded(self):
        for revision,letter in enumerate((b'c',b'a',b'b')):
            _,public=self.key('page-'+letter.decode())
            value=self.participant(letter*32,public,[grants.Domain(kind=grants.WORLD,id=letter.decode())])
            self.assertTrue(self.register(value,revision).HasField('participant'))
        first=self.registry(limit=2);self.assertEqual([r.participant.id for r in first.participants],[b'a'*32,b'b'*32])
        self.assertEqual(first.next_after,b'b'*32);second=self.registry(first.next_after,2)
        self.assertEqual([r.participant.id for r in second.participants],[b'c'*32]);self.assertFalse(second.next_after)
        self.assertEqual(first.registry_revision,second.registry_revision)
        for query in (pb.AuthorityRegistryRead(limit=17),pb.AuthorityRegistryRead(after_id=b'x')):
            self.denied(self.client.call(challenge=pb.IssuerOperation(registry_read=query)),core.INVALID_ARGUMENT)
    def test_domain_insert_failure_rolls_back_registration_and_revision(self):
        with sqlite3.connect(self.state/'identity.db') as db:
            db.execute("CREATE TRIGGER registry_failure BEFORE INSERT ON authority_participant_domain BEGIN SELECT RAISE(ABORT,'fault'); END")
        self.denied(self.register(),core.STORAGE_ERROR)
        self.assertEqual(self.registry().registry_revision,0);self.assertFalse(self.registry().participants)
        with sqlite3.connect(self.state/'identity.db') as db:db.execute('DROP TRIGGER registry_failure')
        self.assertEqual(self.register().participant.registered_revision,1)
    def test_revision_update_failure_rolls_back_all_domain_claims(self):
        with sqlite3.connect(self.state/'identity.db') as db:
            db.execute("CREATE TRIGGER registry_failure BEFORE UPDATE ON authority_registry BEGIN SELECT RAISE(ABORT,'fault'); END")
        value=self.participant(domains=[grants.Domain(kind=grants.WORLD,id='park'),grants.Domain(kind=grants.WORKSPACE,id='project')])
        self.denied(self.register(value),core.STORAGE_ERROR)
        self.assertEqual(self.registry().registry_revision,0);self.assertFalse(self.registry().participants)
        with sqlite3.connect(self.state/'identity.db') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM authority_participant_domain').fetchone()[0],0)
            db.execute('DROP TRIGGER registry_failure')
        self.assertEqual(self.register(value).participant.registered_revision,1)
    def test_binary_participant_ids_have_stable_pagination_order(self):
        for revision,value in enumerate((255,0,128)):
            _,public=self.key('binary-'+str(value))
            participant=self.participant(bytes([value])*32,public,[grants.Domain(kind=grants.WORLD,id=str(value))])
            self.assertTrue(self.register(participant,revision).HasField('participant'))
        cursor=b'';found=[]
        for _ in range(3):
            page=self.registry(cursor,1);self.assertEqual(len(page.participants),1)
            found.append(page.participants[0].participant.id);cursor=page.next_after
        self.assertEqual(found,[bytes([i])*32 for i in (0,128,255)]);self.assertFalse(cursor)
    def test_corrupt_record_and_domain_index_fail_closed(self):
        self.assertTrue(self.register().HasField('participant'))
        with sqlite3.connect(self.state/'identity.db') as db:
            original=db.execute('SELECT definition FROM authority_participant').fetchone()[0]
            db.execute('UPDATE authority_participant SET definition=?',(b'broken',))
        operation=pb.IssuerOperation(registry_read=pb.AuthorityRegistryRead())
        self.denied(self.execute(self.client,self.challenge(self.client,operation)),core.STORAGE_ERROR)
        with sqlite3.connect(self.state/'identity.db') as db:
            db.execute('UPDATE authority_participant SET definition=?',(original,));db.execute('DELETE FROM authority_participant_domain')
        self.denied(self.execute(self.client,self.challenge(self.client,operation)),core.STORAGE_ERROR)
    def test_v4_migration_preserves_credential_and_issuer_state(self):
        enrolled=self.decide(self.client,self.enroll(self.client)).enrollment
        claims=self.claims(enrolled,self.info)
        revoke=pb.IssuerOperation(identity_revoke=pb.IdentityRevoke(
            target=pb.IdentityReference(kind=pb.CREDENTIAL_TARGET,id=claims.credential_id),
            expected_security_revision=0,reason='preserve migration history'))
        self.assertFalse(self.execute(self.client,self.challenge(self.client,revoke)).identity_status.active)
        profile=pb.BootstrapProfile(id='preserved',grants=[pb.BootstrapGrantTemplate(id='enter',
            terms=grants.GrantTerms(scope=grants.Scope(domain=grants.Domain(kind=grants.WORLD,id='park')),
                rights=[grants.WORLD_ENTER]))])
        publish=pb.IssuerOperation(profile_put=pb.BootstrapProfilePut(profile=profile,expected_revision=0))
        self.assertTrue(self.execute(self.client,self.challenge(self.client,publish)).HasField('profile'))
        self.client.close();secret=(self.state/'issuer.pem').read_bytes()
        with sqlite3.connect(self.state/'identity.db') as db:
            before={table:db.execute('SELECT * FROM '+table).fetchall() for table in ('issuer','actor','credential','enrollment','security_state','revocation','bootstrap_profile')}
            db.executescript(DROP_REGISTRY+'PRAGMA user_version=4;')
        command=[str(BINARY),'--state-dir',str(self.state)]
        denied=subprocess.run(command,input=b'',capture_output=True,timeout=4)
        self.assertNotEqual(denied.returncode,0);self.assertIn(b'migration',denied.stderr.lower())
        for _ in range(2):
            result=subprocess.run(command+['--migrate'],input=b'',capture_output=True,timeout=4);self.assertEqual(result.returncode,0,result.stderr)
        with sqlite3.connect(self.state/'identity.db') as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],8)
            self.assertEqual({table:db.execute('SELECT * FROM '+table).fetchall() for table in before},before)
        self.assertEqual((self.state/'issuer.pem').read_bytes(),secret)
        self.client=self.start();self.assertFalse(self.client.call(credential_check=enrolled.credential).identity_status.active)
        self.assertEqual(self.client.call(profile_read=pb.BootstrapProfileRead(id='preserved',revision=1)).profile.profile,profile)
        self.assertEqual(self.registry().registry_revision,0)
    def test_failed_migration_rolls_back_new_tables_and_version(self):
        self.client.close()
        with sqlite3.connect(self.state/'identity.db') as db:
            db.executescript(DROP_REGISTRY+'PRAGMA user_version=4; CREATE TABLE authority_participant_domain(marker INTEGER);')
        result=subprocess.run([str(BINARY),'--state-dir',str(self.state),'--migrate'],input=b'',capture_output=True,timeout=4)
        self.assertNotEqual(result.returncode,0)
        with sqlite3.connect(self.state/'identity.db') as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],4)
            names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn('authority_registry',names);self.assertNotIn('authority_participant',names)

if __name__=='__main__':unittest.main(verbosity=2)
