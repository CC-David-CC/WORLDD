"""Owner proofs bind supervisor keys; real service connections cannot adopt active generations."""
import sqlite3
import subprocess
import unittest
import core_pb2 as core
import grants_pb2 as grants
import issuer_pb2 as pb
import key_pb2 as keys
from harness.wire import frame
import test_identity_participant_session as sessions
import test_issuer_client as clients

class SupervisorRegistryTests(sessions.ParticipantSessionTests,clients.ClientFixture):
    def setUp(self):
        super().setUp();self.supervisor_key,self.supervisor_public=self.key('supervisor')

    def binding_operation(self, expected=0, public=None, registration=1, participant=None):
        return pb.IssuerOperation(supervisor_bind=pb.AuthoritySupervisorBind(
            participant_id=self.registration.participant.id if participant is None else participant,
            registered_revision=registration,public_key=self.supervisor_public if public is None else public,expected_revision=expected))

    def bind(self,peer=None,**changes):
        peer=peer or self.peer;op=self.binding_operation(**changes)
        return self.execute(peer,self.challenge(peer,op))

    def read_binding(self,peer=None):
        peer=peer or self.peer
        op=pb.IssuerOperation(supervisor_read=pb.AuthoritySupervisorRead(participant_id=self.registration.participant.id))
        return self.execute(peer,self.challenge(peer,op))

    def test_owner_binds_exact_registration_through_production_client(self):
        client=self.client();before=self.snapshot_all();reply=self.bind(client)
        self.assertTrue(reply.HasField('supervision'),reply);binding=reply.supervision
        self.assertEqual(binding.registration,self.registration);self.assertEqual(binding.public_key,self.supervisor_public)
        self.assertEqual(binding.revision,1);self.assertEqual(self.read_binding(client).supervision,binding)
        after=self.snapshot_all();self.assertEqual(len(after['authority_supervisor']),1)
        for name in before:
            if name!='authority_supervisor':self.assertEqual(after[name],before[name],name)

    def test_stale_revision_cannot_replace_supervisor(self):
        self.assertTrue(self.bind().HasField('supervision'));before=self.snapshot_all()
        _,public=self.key('replacement');self.denied(self.bind(public=public),core.REVISION_CONFLICT)
        self.assertEqual(self.snapshot_all(),before)
        result=self.bind(expected=1,public=public);self.assertEqual(result.supervision.revision,2)
        self.assertEqual(result.supervision.public_key,public)

    def test_service_and_supervisor_keys_cannot_bind_themselves(self):
        before=self.snapshot_all()
        for key,public in [(self.signer,self.public),(self.supervisor_key,self.supervisor_public)]:
            challenge=self.challenge(self.peer,self.binding_operation())
            self.denied(self.execute(self.peer,challenge,self.proof(challenge,key,public)))
            self.assertEqual(self.snapshot_all(),before)

    def test_active_generation_cannot_be_adopted_or_cleared(self):
        self.assertTrue(self.service_finish(self.service_begin()).HasField('authority_authentication'))
        reply=self.peer.call(authority_activate=pb.AuthorityActivate(synchronized_revision=0))
        self.assertTrue(reply.HasField('authority_activation'),reply);before=self.snapshot_all()
        self.denied(self.bind(),core.BUSY);self.assertEqual(self.snapshot_all(),before)

    def test_binding_requires_launch_authorization_on_old_and_new_service_connections(self):
        self.assertTrue(self.service_finish(self.service_begin()).HasField('authority_authentication'))
        self.assertTrue(self.bind().HasField('supervision'));before=self.snapshot_all()
        self.denied(self.peer.call(authority_activate=pb.AuthorityActivate(synchronized_revision=0)),core.FORBIDDEN)
        peer=self.connect();challenge=self.service_begin(peer,self.request(incarnation=b'n'*32))
        self.assertTrue(self.service_finish(challenge,peer).HasField('authority_authentication'))
        self.denied(peer.call(authority_activate=pb.AuthorityActivate(synchronized_revision=0)),core.FORBIDDEN)
        self.assertEqual(self.snapshot_all(),before)

    def test_key_roles_stay_separate_in_both_directions(self):
        self.enroll(self.peer)
        for public in [self.owner_public,self.info.public_key,self.public,self.actor_public]:
            before=self.snapshot_all();self.denied(self.bind(public=public),core.FORBIDDEN);self.assertEqual(self.snapshot_all(),before)
        self.assertTrue(self.bind().HasField('supervision'))
        intent=pb.EnrollmentIntent(public_key=self.supervisor_public,kind=pb.AGENT,label='invalid role reuse')
        self.denied(self.peer.call(enrollment_begin=intent),core.FORBIDDEN)
        participant=pb.AuthorityParticipant(id=b'q'*32,public_key=self.supervisor_public,domains=[grants.Domain(kind=grants.WORLD,id='other')])
        op=pb.IssuerOperation(participant_register=pb.AuthorityParticipantRegister(participant=participant,expected_registry_revision=1))
        self.denied(self.execute(self.peer,self.challenge(self.peer,op)),core.FORBIDDEN)

    def test_pending_actor_enrollment_cannot_reuse_new_supervisor_key(self):
        intent=pb.EnrollmentIntent(public_key=self.supervisor_public,kind=pb.AGENT,label='pending role race')
        reply=self.peer.call(enrollment_begin=intent);self.assertTrue(reply.HasField('challenge'),reply)
        proof=self.proof(reply.challenge,self.supervisor_key,self.supervisor_public)
        self.assertTrue(self.bind().HasField('supervision'));before=self.snapshot_all()
        complete=pb.EnrollmentComplete(nonce=reply.challenge.nonce,proof=proof)
        self.denied(self.peer.call(enrollment_complete=complete),core.FORBIDDEN);self.assertEqual(self.snapshot_all(),before)

    def test_wrong_registration_and_unknown_participant_cannot_bind(self):
        before=self.snapshot_all();self.denied(self.bind(registration=2),core.UNAUTHORIZED)
        self.denied(self.bind(participant=b'x'*32),core.NOT_FOUND);self.assertEqual(self.snapshot_all(),before)

    def test_binding_survives_restart(self):
        binding=self.bind().supervision;self.peer.close();self.stop_daemons();self.peer=self.ready()
        self.assertEqual(self.read_binding().supervision,binding)

    def test_failed_binding_transaction_leaves_revision_and_key_unchanged(self):
        self.assertTrue(self.bind().HasField('supervision'))
        with sqlite3.connect(self.state/'identity.db') as db:
            db.execute("CREATE TRIGGER deny_binding BEFORE UPDATE ON authority_supervisor BEGIN SELECT RAISE(ABORT,'binding fault'); END")
        before=self.snapshot_all();self.denied(self.bind(expected=1),core.STORAGE_ERROR);self.assertEqual(self.snapshot_all(),before)
        with sqlite3.connect(self.state/'identity.db') as db:db.execute('DROP TRIGGER deny_binding')
        self.assertEqual(self.bind(expected=1).supervision.revision,2)

    def owner_challenge(self, operation):
        value=pb.IssuerChallenge(protocol_version=1,issuer_id=self.info.issuer_id,incarnation=b'i'*32,nonce=b'n'*32,
            owner_public_key=self.owner_public,operation=operation,lifetime_ms=30000,audience='identityd/admin/v1')
        return pb.ChallengeReply(nonce=value.nonce,signing_request=keys.SignRequest(
            purpose=keys.ISSUER_ADMINISTRATION,payload=value.SerializeToString()))

    def good_binding(self):
        return pb.AuthoritySupervisorBinding(registration=self.registration,public_key=self.supervisor_public,revision=1)

    def poisoned(self, client, request, peer, count):
        output,_=client.proc.communicate(frame(request)*2,timeout=3)
        self.assertEqual(client.proc.returncode,2);self.assertEqual(output,b'')
        self.assertEqual(len(peer.requests),count);self.assertEqual(peer.connections,1)

    def test_client_rejects_substituted_binding_and_invalid_owner_proof(self):
        for mode in ('issuer','participant','registration','key','revision','overflow','domain','unknown','outcome','proof'):
            with self.subTest(mode=mode):
                def respond(peer,request,reply):
                    if request.HasField('challenge'):reply.challenge.CopyFrom(self.owner_challenge(request.challenge))
                    else:
                        result=self.good_binding()
                        if mode=='issuer':result.registration.issuer_id=b'x'*32
                        elif mode=='participant':result.registration.participant.id=b'x'*32
                        elif mode=='registration':result.registration.registered_revision=2
                        elif mode=='key':result.public_key=b'x'*32
                        elif mode=='revision':result.revision=2
                        elif mode=='overflow':result.revision=2**63
                        elif mode=='domain':result.registration.participant.domains[0].kind=0
                        elif mode=='unknown':result.ParseFromString(result.SerializeToString()+b'\xf8\x07\x01')
                        if mode=='outcome':reply.policy.CopyFrom(pb.PolicyState(revision=1))
                        else:reply.supervision.CopyFrom(result)
                    peer.stream.sendall(frame(reply))
                peer=clients.FaultPeer(self,response=respond);client=self.client(peer.port)
                challenge=self.challenge(client,self.binding_operation());proof=self.proof(challenge)
                if mode=='proof':proof.signature=b'x'*64
                self.poisoned(client,pb.IssuerRequest(protocol_version=1,execute=pb.IssuerExecute(
                    nonce=challenge.nonce,proof=proof)),peer,4)

    def test_client_rejects_substituted_challenge_context(self):
        for mode in ('issuer','participant','revision','key','audience','nonce','purpose','unknown','lifetime'):
            with self.subTest(mode=mode):
                def respond(peer,request,reply):
                    challenge=self.owner_challenge(request.challenge);value=pb.IssuerChallenge.FromString(challenge.signing_request.payload)
                    if mode=='issuer':value.issuer_id=b'x'*32
                    elif mode=='participant':value.operation.supervisor_bind.participant_id=b'x'*32
                    elif mode=='revision':value.operation.supervisor_bind.expected_revision=1
                    elif mode=='key':value.operation.supervisor_bind.public_key=b'x'*32
                    elif mode=='audience':value.audience='other'
                    elif mode=='nonce':value.nonce=b'x'*32
                    elif mode=='lifetime':value.lifetime_ms=60001
                    elif mode=='unknown':value.ParseFromString(value.SerializeToString()+b'\xf8\x07\x01')
                    challenge.signing_request.payload=value.SerializeToString()
                    if mode=='purpose':challenge.signing_request.purpose=keys.AUTHORITY_PARTICIPATION
                    reply.challenge.CopyFrom(challenge);peer.stream.sendall(frame(reply))
                peer=clients.FaultPeer(self,response=respond);client=self.client(peer.port)
                self.poisoned(client,pb.IssuerRequest(protocol_version=1,challenge=self.binding_operation()),peer,3)

    def test_client_denial_consumes_context_before_replay_success(self):
        def respond(peer,request,reply):
            if request.HasField('challenge'):reply.challenge.CopyFrom(self.owner_challenge(request.challenge))
            elif len(peer.requests)>4:reply.supervision.CopyFrom(self.good_binding())
            peer.stream.sendall(frame(reply))
        peer=clients.FaultPeer(self,response=respond);client=self.client(peer.port)
        challenge=self.challenge(client,self.binding_operation());complete=pb.IssuerExecute(nonce=challenge.nonce,proof=self.proof(challenge))
        self.denied(client.call(execute=complete),core.FORBIDDEN)
        self.poisoned(client,pb.IssuerRequest(protocol_version=1,execute=complete),peer,5)

    def test_client_unsolicited_binding_is_not_authority(self):
        challenge=self.owner_challenge(self.binding_operation())
        def respond(peer,request,reply):
            reply.supervision.CopyFrom(self.good_binding());peer.stream.sendall(frame(reply))
        peer=clients.FaultPeer(self,response=respond);client=self.client(peer.port)
        self.poisoned(client,pb.IssuerRequest(protocol_version=1,execute=pb.IssuerExecute(
            nonce=challenge.nonce,proof=self.proof(challenge))),peer,3)

    def test_client_read_cannot_return_another_participant(self):
        def respond(peer,request,reply):
            if request.HasField('challenge'):reply.challenge.CopyFrom(self.owner_challenge(request.challenge))
            else:
                reply.supervision.CopyFrom(self.good_binding());reply.supervision.registration.participant.id=b'x'*32
            peer.stream.sendall(frame(reply))
        peer=clients.FaultPeer(self,response=respond);client=self.client(peer.port)
        operation=pb.IssuerOperation(supervisor_read=pb.AuthoritySupervisorRead(participant_id=self.registration.participant.id))
        challenge=self.challenge(client,operation)
        self.poisoned(client,pb.IssuerRequest(protocol_version=1,execute=pb.IssuerExecute(
            nonce=challenge.nonce,proof=self.proof(challenge))),peer,4)

    def test_malformed_binding_is_rejected_without_mutation(self):
        before=self.snapshot_all()
        for mode in ('participant','key','registration','overflow','missing-revision','revision-overflow'):
            with self.subTest(mode=mode):
                operation=self.binding_operation();bind=operation.supervisor_bind
                if mode=='participant':bind.participant_id=b'x'
                elif mode=='key':bind.public_key=b'x'
                elif mode=='registration':bind.registered_revision=0
                elif mode=='overflow':bind.registered_revision=2**63
                elif mode=='missing-revision':bind.ClearField('expected_revision')
                else:bind.expected_revision=2**63
                self.denied(self.peer.call(challenge=operation),core.INVALID_ARGUMENT)
                self.assertEqual(self.snapshot_all(),before)

    def test_explicit_v6_migration_preserves_existing_activation(self):
        self.assertTrue(self.service_finish(self.service_begin()).HasField('authority_authentication'))
        self.assertTrue(self.peer.call(authority_activate=pb.AuthorityActivate(synchronized_revision=0)).HasField('authority_activation'))
        self.peer.close();self.stop_daemons();secret=(self.state/'issuer.pem').read_bytes()
        with sqlite3.connect(self.state/'identity.db') as db:db.executescript('DROP TABLE authority_fence; DROP TABLE authority_launch_authorization; DROP TABLE authority_supervisor; PRAGMA user_version=6;')
        before=self.snapshot_all();command=[str(sessions.BINARY),'--state-dir',str(self.state)]
        denied=subprocess.run(command,input=b'',capture_output=True,timeout=4);self.assertNotEqual(denied.returncode,0)
        self.assertIn(b'migration',denied.stderr.lower())
        for _ in range(2):
            result=subprocess.run(command+['--migrate'],input=b'',capture_output=True,timeout=4);self.assertEqual(result.returncode,0,result.stderr)
        after=self.snapshot_all();self.assertEqual({n:after[n] for n in before},before);self.assertEqual(after['authority_supervisor'],[])
        self.assertEqual((self.state/'issuer.pem').read_bytes(),secret)
        with sqlite3.connect(self.state/'identity.db') as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],9)
        self.peer=self.ready();self.denied(self.bind(),core.BUSY)

    def test_failed_v6_migration_rolls_back_without_adopting_services(self):
        self.peer.close();self.stop_daemons()
        with sqlite3.connect(self.state/'identity.db') as db:
            db.executescript('DROP TABLE authority_fence; DROP TABLE authority_launch_authorization; DROP TABLE authority_supervisor; PRAGMA user_version=6; CREATE TABLE authority_supervisor(marker INTEGER);')
        before=self.snapshot_all();result=subprocess.run([str(sessions.BINARY),'--state-dir',str(self.state),'--migrate'],input=b'',capture_output=True,timeout=4)
        self.assertNotEqual(result.returncode,0);self.assertEqual(self.snapshot_all(),before)
        with sqlite3.connect(self.state/'identity.db') as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],6)

if __name__=='__main__':
    suite=unittest.TestSuite(SupervisorRegistryTests(n) for n in SupervisorRegistryTests.__dict__ if n.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
