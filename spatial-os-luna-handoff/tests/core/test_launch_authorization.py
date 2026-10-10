"""Supervisor approval is bound to one actual TLS service identity and launch."""
import sqlite3
import subprocess
import concurrent.futures
import unittest
import core_pb2 as core
import issuer_pb2 as pb
import key_pb2 as keys
import test_supervisor_registry as registry
from harness.wire import frame


class LaunchAuthorizationTests(registry.SupervisorRegistryTests):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.bind().HasField('supervision'))
        self.supervisor=self.client()
        self.service=self.authenticate(self.peer)

    def authenticate(self, peer, incarnation=b'i'*32):
        result=self.service_finish(self.service_begin(peer,self.request(incarnation=incarnation)),peer)
        self.assertTrue(result.HasField('authority_authentication'),result)
        return result.authority_authentication

    def launch_request(self, service=None, launch_id=b'l'*32, previous=0, revision=1):
        return pb.AuthorityLaunchBegin(service=self.service if service is None else service,
            supervision_revision=revision,launch_id=launch_id,previous_generation=previous)

    def launch_begin(self, request=None, peer=None):
        result=(peer or self.supervisor).call(authority_launch_begin=request or self.launch_request())
        self.assertTrue(result.HasField('challenge'),result)
        self.assertEqual(result.challenge.signing_request.purpose,keys.AUTHORITY_LAUNCH_AUTHORIZATION)
        return result.challenge

    def launch_complete(self, challenge, peer=None, proof=None):
        return (peer or self.supervisor).call(authority_launch_complete=pb.AuthorityLaunchComplete(
            nonce=challenge.nonce,proof=proof or self.proof(challenge,self.supervisor_key,self.supervisor_public)))

    def authorize(self, request=None):
        result=self.launch_complete(self.launch_begin(request))
        self.assertTrue(result.HasField('authority_launch_authorization'),result)
        return result.authority_launch_authorization

    def activate(self, peer=None):
        return (peer or self.peer).call(authority_activate=pb.AuthorityActivate(synchronized_revision=0))

    def test_supervisor_authorizes_exact_service_connection_through_production_client(self):
        self.denied(self.activate(),core.FORBIDDEN)
        request=self.launch_request();authorization=self.authorize(request)
        self.assertEqual(authorization.request,request);self.assertEqual(len(authorization.authorization_id),32)
        self.assertEqual(authorization.generation,0);self.assertEqual(authorization.supervisor_public_key,self.supervisor_public)
        result=self.activate();self.assertTrue(result.HasField('authority_activation'),result)
        self.assertEqual(result.authority_activation.generation,1)
        recovered=self.authorize(request)
        self.assertEqual(recovered.authorization_id,authorization.authorization_id);self.assertEqual(recovered.generation,1)

    def test_other_service_connection_cannot_consume_authorization(self):
        self.authorize();other=self.connect();self.authenticate(other)
        before=self.snapshot_all();self.denied(self.activate(other),core.FORBIDDEN)
        self.assertEqual(self.snapshot_all(),before)
        self.assertTrue(self.activate().HasField('authority_activation'))

    def test_wrong_signers_cannot_authorize_launch(self):
        before=self.snapshot_all()
        for key,public in [(self.owner,self.owner_public),(self.signer,self.public)]:
            challenge=self.launch_begin();self.denied(self.launch_complete(challenge,proof=self.proof(challenge,key,public)))
            self.assertEqual(self.snapshot_all(),before)
        self.denied(self.activate(),core.FORBIDDEN)

    def test_completion_cannot_cross_supervisor_connections(self):
        challenge=self.launch_begin();other=self.client();before=self.snapshot_all()
        self.denied(self.launch_complete(challenge,peer=other));self.assertEqual(self.snapshot_all(),before)
        self.denied(self.launch_complete(challenge));self.assertEqual(self.snapshot_all(),before)

    def test_replayed_nonce_cannot_repeat_authorization(self):
        challenge=self.launch_begin();result=self.launch_complete(challenge)
        self.assertTrue(result.HasField('authority_launch_authorization'),result);before=self.snapshot_all()
        self.denied(self.launch_complete(challenge));self.assertEqual(self.snapshot_all(),before)

    def test_supervisor_rotation_invalidates_unused_authorization(self):
        self.authorize();_,public=self.key('new-supervisor')
        self.assertTrue(self.bind(expected=1,public=public).HasField('supervision'));before=self.snapshot_all()
        self.denied(self.activate(),core.FORBIDDEN);self.assertEqual(self.snapshot_all(),before)

    def test_authorization_consumption_and_activation_roll_back_together(self):
        self.authorize()
        with sqlite3.connect(self.state/'identity.db') as db:
            db.execute("CREATE TRIGGER deny_consumption BEFORE UPDATE ON authority_launch_authorization BEGIN SELECT RAISE(ABORT,'launch fault'); END")
        before=self.snapshot_all();self.denied(self.activate(),core.STORAGE_ERROR);self.assertEqual(self.snapshot_all(),before)
        with sqlite3.connect(self.state/'identity.db') as db:db.execute('DROP TRIGGER deny_consumption')
        result=self.activate();self.assertTrue(result.HasField('authority_activation'),result)
        self.assertEqual(result.authority_activation.generation,1)

    def test_starting_one_generation_invalidates_other_prior_approvals(self):
        self.authorize();other=self.connect();service=self.authenticate(other,b'n'*32)
        self.authorize(self.launch_request(service,launch_id=b'm'*32))
        self.assertTrue(self.activate().HasField('authority_activation'))
        released=self.peer.call(authority_release=pb.AuthorityRelease(generation=1,applied_revision=0))
        self.assertTrue(released.HasField('authority_activation'),released);before=self.snapshot_all()
        self.denied(self.activate(other),core.REVISION_CONFLICT);self.assertEqual(self.snapshot_all(),before)

    def test_stale_supervision_or_registration_cannot_authorize(self):
        for mode in ('supervision','registration','domain','issuer-incarnation','future-security'):
            with self.subTest(mode=mode):
                request=self.launch_request()
                if mode=='supervision':request.supervision_revision=2
                elif mode=='registration':request.service.registration.registered_revision=2
                elif mode=='domain':request.service.registration.participant.domains[0].id='other'
                elif mode=='issuer-incarnation':request.service.issuer_incarnation=b'x'*32
                else:request.service.security_revision=1
                before=self.snapshot_all();reply=self.peer.call(authority_launch_begin=request)
                self.assertTrue(reply.HasField('error'),reply);self.assertEqual(self.snapshot_all(),before)

    def test_rotation_between_challenge_and_proof_denies_authorization(self):
        challenge=self.launch_begin();_,public=self.key('rotated-before-proof')
        self.assertTrue(self.bind(expected=1,public=public).HasField('supervision'));before=self.snapshot_all()
        self.denied(self.launch_complete(challenge),core.REVISION_CONFLICT);self.assertEqual(self.snapshot_all(),before)

    def test_new_authorization_cannot_bypass_an_active_generation(self):
        self.authorize();self.assertTrue(self.activate().HasField('authority_activation'))
        other=self.connect();service=self.authenticate(other,b'n'*32)
        challenge=self.launch_begin(self.launch_request(service,launch_id=b'm'*32,previous=1));before=self.snapshot_all()
        self.denied(self.launch_complete(challenge),core.BUSY);self.assertEqual(self.snapshot_all(),before)

    def test_launch_and_service_incarnation_reuse_cannot_change_approved_context(self):
        self.authorize();other=self.connect();service=self.authenticate(other,b'n'*32)
        for request in [self.launch_request(service),self.launch_request(launch_id=b'm'*32)]:
            challenge=self.launch_begin(request);before=self.snapshot_all()
            self.denied(self.launch_complete(challenge),core.ALREADY_EXISTS);self.assertEqual(self.snapshot_all(),before)

    def test_concurrent_identical_proofs_return_one_durable_authorization(self):
        other=self.client();first=self.launch_begin();second=self.launch_begin(peer=other)
        proofs=[self.proof(challenge,self.supervisor_key,self.supervisor_public) for challenge in (first,second)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            replies=list(pool.map(lambda item:self.launch_complete(item[0],peer=item[1],proof=item[2]),
                [(first,self.supervisor,proofs[0]),(second,other,proofs[1])]))
        for reply in replies:self.assertTrue(reply.HasField('authority_launch_authorization'),reply)
        self.assertEqual(replies[0].authority_launch_authorization,replies[1].authority_launch_authorization)
        self.assertEqual(len(self.snapshot_all()['authority_launch_authorization']),1)

    def test_failed_authorization_insert_preserves_state_and_fresh_proof_can_retry(self):
        with sqlite3.connect(self.state/'identity.db') as db:
            db.execute("CREATE TRIGGER deny_authorization BEFORE INSERT ON authority_launch_authorization BEGIN SELECT RAISE(ABORT,'authorization fault'); END")
        challenge=self.launch_begin();before=self.snapshot_all()
        self.denied(self.launch_complete(challenge),core.STORAGE_ERROR);self.assertEqual(self.snapshot_all(),before)
        with sqlite3.connect(self.state/'identity.db') as db:db.execute('DROP TRIGGER deny_authorization')
        self.assertEqual(self.authorize().generation,0)

    def test_restart_rejects_old_challenge_and_unused_service_authorization(self):
        self.authorize();challenge=self.launch_begin();proof=self.proof(challenge,self.supervisor_key,self.supervisor_public)
        request=self.launch_request();self.peer.close();self.supervisor.close();self.stop_daemons();self.peer=self.ready()
        before=self.snapshot_all()
        self.denied(self.peer.call(authority_launch_complete=pb.AuthorityLaunchComplete(nonce=challenge.nonce,proof=proof)))
        self.denied(self.peer.call(authority_launch_begin=request));self.assertEqual(self.snapshot_all(),before)
        self.authenticate(self.peer);self.denied(self.activate(),core.FORBIDDEN);self.assertEqual(self.snapshot_all(),before)

    def test_explicit_v7_migration_preserves_binding_without_granting_launch(self):
        self.peer.close();self.supervisor.close();self.stop_daemons();secret=(self.state/'issuer.pem').read_bytes()
        with sqlite3.connect(self.state/'identity.db') as db:db.executescript('DROP TABLE authority_retirement; DROP TABLE authority_fence; DROP TABLE authority_launch_authorization; PRAGMA user_version=7;')
        before=self.snapshot_all();command=[str(registry.sessions.BINARY),'--state-dir',str(self.state)]
        result=subprocess.run(command,input=b'',capture_output=True,timeout=4)
        self.assertNotEqual(result.returncode,0);self.assertIn(b'migration',result.stderr.lower())
        for _ in range(2):
            result=subprocess.run(command+['--migrate'],input=b'',capture_output=True,timeout=4);self.assertEqual(result.returncode,0,result.stderr)
        after=self.snapshot_all();self.assertEqual({n:after[n] for n in before},before);self.assertEqual(after['authority_launch_authorization'],[])
        self.assertEqual((self.state/'issuer.pem').read_bytes(),secret)
        with sqlite3.connect(self.state/'identity.db') as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],10)
        self.peer=self.ready();self.authenticate(self.peer);self.denied(self.activate(),core.FORBIDDEN)

    def test_failed_v7_migration_rolls_back(self):
        self.peer.close();self.supervisor.close();self.stop_daemons()
        with sqlite3.connect(self.state/'identity.db') as db:
            db.executescript('DROP TABLE authority_retirement; DROP TABLE authority_fence; DROP TABLE authority_launch_authorization; PRAGMA user_version=7; CREATE TABLE authority_launch_authorization(marker INTEGER);')
        before=self.snapshot_all()
        result=subprocess.run([str(registry.sessions.BINARY),'--state-dir',str(self.state),'--migrate'],input=b'',capture_output=True,timeout=4)
        self.assertNotEqual(result.returncode,0);self.assertEqual(self.snapshot_all(),before)
        with sqlite3.connect(self.state/'identity.db') as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],7)

    def test_authorization_retry_does_not_complete_pending_revocation(self):
        authorization=self.authorize();self.assertTrue(self.activate().HasField('authority_activation'))
        record=self.decide(self.peer,self.enroll(self.peer)).enrollment;claims=self.claims(record,self.info)
        operation=pb.IssuerOperation(identity_revoke=pb.IdentityRevoke(
            target=pb.IdentityReference(kind=pb.ACTOR_TARGET,id=claims.actor_id),expected_security_revision=0,reason='authorization witness'))
        result=self.execute(self.peer,self.challenge(self.peer,operation));self.assertTrue(result.HasField('identity_status'),result)
        before=self.snapshot_all();self.assertEqual(len(before['authority_obligation']),1)
        recovered=self.authorize();self.assertEqual(recovered.authorization_id,authorization.authorization_id)
        self.assertEqual(recovered.generation,1);self.assertEqual(self.snapshot_all(),before)
        operation=pb.IssuerOperation(barrier_read=pb.AuthorityBarrierRead(revision=1))
        result=self.execute(self.peer,self.challenge(self.peer,operation))
        self.assertTrue(result.HasField('authority_barrier'),result);self.assertEqual(result.authority_barrier.pending_count,1)

    def good_launch_challenge(self, request):
        value=pb.AuthorityLaunchChallenge(protocol_version=1,issuer_id=self.info.issuer_id,
            issuer_incarnation=self.service.issuer_incarnation,nonce=b'n'*32,connection_binding=b'c'*32,
            request=request,supervisor_public_key=self.supervisor_public,lifetime_ms=30000,audience='identityd/authority-launch/v1')
        return pb.ChallengeReply(nonce=value.nonce,signing_request=keys.SignRequest(
            purpose=keys.AUTHORITY_LAUNCH_AUTHORIZATION,payload=value.SerializeToString()))

    def good_authorization(self):
        return pb.AuthorityLaunchAuthorization(request=self.launch_request(),authorization_id=b'a'*32,
            supervisor_public_key=self.supervisor_public,generation=0)

    def test_client_rejects_substituted_launch_challenge(self):
        for mode in ('issuer','incarnation','nonce','binding','request','supervision','generation','key','lifetime','audience','purpose','unknown'):
            with self.subTest(mode=mode):
                def respond(peer,request,reply):
                    challenge=self.good_launch_challenge(request.authority_launch_begin)
                    value=pb.AuthorityLaunchChallenge.FromString(challenge.signing_request.payload)
                    if mode=='issuer':value.issuer_id=b'x'*32
                    elif mode=='incarnation':value.issuer_incarnation=b'x'*32
                    elif mode=='nonce':value.nonce=b'x'*32
                    elif mode=='binding':value.connection_binding=b'x'
                    elif mode=='request':value.request.launch_id=b'x'*32
                    elif mode=='supervision':value.request.supervision_revision=2
                    elif mode=='generation':value.request.previous_generation=1
                    elif mode=='key':value.supervisor_public_key=self.public
                    elif mode=='lifetime':value.lifetime_ms=60001
                    elif mode=='audience':value.audience='other'
                    elif mode=='unknown':value.ParseFromString(value.SerializeToString()+b'\xf8\x07\x01')
                    challenge.signing_request.payload=value.SerializeToString()
                    if mode=='purpose':challenge.signing_request.purpose=keys.AUTHORITY_PARTICIPATION
                    reply.challenge.CopyFrom(challenge);peer.stream.sendall(frame(reply))
                peer=registry.clients.FaultPeer(self,response=respond);client=self.client(peer.port)
                self.poisoned(client,pb.IssuerRequest(protocol_version=1,authority_launch_begin=self.launch_request()),peer,3)

    def test_client_rejects_substituted_authorization_or_invalid_proof(self):
        for mode in ('key','issuer','id','launch','binding','previous','generation','overflow','unknown','outcome','proof'):
            with self.subTest(mode=mode):
                def respond(peer,request,reply):
                    if request.HasField('authority_launch_begin'):reply.challenge.CopyFrom(self.good_launch_challenge(request.authority_launch_begin))
                    else:
                        result=self.good_authorization()
                        if mode=='key':result.supervisor_public_key=b'x'*32
                        elif mode=='issuer':result.request.service.issuer_id=b'x'*32
                        elif mode=='id':result.authorization_id=b'x'
                        elif mode=='launch':result.request.launch_id=b'x'*32
                        elif mode=='binding':result.request.service.connection_binding=b'x'*32
                        elif mode=='previous':result.request.previous_generation=1
                        elif mode=='generation':result.generation=2
                        elif mode=='overflow':result.generation=2**63
                        elif mode=='unknown':result.ParseFromString(result.SerializeToString()+b'\xf8\x07\x01')
                        if mode=='outcome':reply.policy.CopyFrom(pb.PolicyState(revision=1))
                        else:reply.authority_launch_authorization.CopyFrom(result)
                    peer.stream.sendall(frame(reply))
                peer=registry.clients.FaultPeer(self,response=respond);client=self.client(peer.port);challenge=self.launch_begin(peer=client)
                proof=self.proof(challenge,self.supervisor_key,self.supervisor_public)
                if mode=='proof':proof.signature=b'x'*64
                self.poisoned(client,pb.IssuerRequest(protocol_version=1,authority_launch_complete=pb.AuthorityLaunchComplete(
                    nonce=challenge.nonce,proof=proof)),peer,4)

    def test_client_denial_consumes_launch_context(self):
        def respond(peer,request,reply):
            if request.HasField('authority_launch_begin'):reply.challenge.CopyFrom(self.good_launch_challenge(request.authority_launch_begin))
            elif len(peer.requests)>4:reply.authority_launch_authorization.CopyFrom(self.good_authorization())
            peer.stream.sendall(frame(reply))
        peer=registry.clients.FaultPeer(self,response=respond);client=self.client(peer.port);challenge=self.launch_begin(peer=client)
        completion=pb.AuthorityLaunchComplete(nonce=challenge.nonce,proof=self.proof(challenge,self.supervisor_key,self.supervisor_public))
        self.denied(client.call(authority_launch_complete=completion),core.FORBIDDEN)
        self.poisoned(client,pb.IssuerRequest(protocol_version=1,authority_launch_complete=completion),peer,5)

    def test_client_unsolicited_authorization_is_rejected(self):
        challenge=self.good_launch_challenge(self.launch_request())
        def respond(peer,request,reply):
            reply.authority_launch_authorization.CopyFrom(self.good_authorization());peer.stream.sendall(frame(reply))
        peer=registry.clients.FaultPeer(self,response=respond);client=self.client(peer.port)
        self.poisoned(client,pb.IssuerRequest(protocol_version=1,authority_launch_complete=pb.AuthorityLaunchComplete(
            nonce=challenge.nonce,proof=self.proof(challenge,self.supervisor_key,self.supervisor_public))),peer,3)

    def test_client_configuration_remains_private_and_unchanged_for_delayed_readers(self):
        first=self.config_file(1);original=first.read_bytes();second=self.config_file(2)
        self.assertNotEqual(first,second);self.assertEqual(first.read_bytes(),original)
        self.assertNotEqual(first.read_bytes(),second.read_bytes())
        self.assertEqual(first.stat().st_mode & 0o077,0);self.assertEqual(second.stat().st_mode & 0o077,0)


if __name__=='__main__':
    suite=unittest.TestSuite(LaunchAuthorizationTests(n) for n in LaunchAuthorizationTests.__dict__ if n.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
