"""Real checked participation and bounded owner queries; explicit peers test reply validation."""
import unittest
import core_pb2 as core
import grants_pb2 as grants
import issuer_pb2 as pb
import key_pb2 as keys
from harness.wire import frame
from test_issuer_client import ClientFixture, FaultPeer
import test_identity_participant_session_client as session_fixture


class BarrierClientTests(ClientFixture):
    service_request = session_fixture.SessionClientTests.service_request
    service_begin = session_fixture.SessionClientTests.service_begin
    completion = session_fixture.SessionClientTests.completion
    good_challenge = session_fixture.SessionClientTests.good_challenge
    good_result = session_fixture.SessionClientTests.good_result

    def setUp(self):
        super().setUp(); self.peer = self.ready(); self.signer,self.public = self.key('registered-service')
        value = pb.AuthorityParticipant(id=b'p'*32,public_key=self.public,
            domains=[grants.Domain(kind=grants.WORLD,id='park')])
        op = pb.IssuerOperation(participant_register=pb.AuthorityParticipantRegister(participant=value,expected_registry_revision=0))
        reply = self.execute(self.peer,self.challenge(self.peer,op))
        self.assertTrue(reply.HasField('participant'),reply); self.registration = reply.participant

    def authenticate_client(self, port=None):
        client = self.client(port); reply = client.call(authority_authentication_complete=self.completion(self.service_begin(client)))
        self.assertTrue(reply.HasField('authority_authentication'),reply); return client

    def activate_client(self, client, revision=0):
        try: reply = client.call(authority_activate=pb.AuthorityActivate(synchronized_revision=revision))
        except (EOFError,AssertionError):
            with open(client.stderr.name) as log:self.fail('Client closed before activation: '+log.read())
        self.assertTrue(reply.HasField('authority_activation'),reply); return reply.authority_activation

    def revoke(self):
        record = self.decide(self.peer,self.enroll(self.peer)).enrollment; claims = self.claims(record,self.info)
        op = pb.IssuerOperation(identity_revoke=pb.IdentityRevoke(target=pb.IdentityReference(kind=pb.ACTOR_TARGET,id=claims.actor_id),
            expected_security_revision=0,reason='client fixture'))
        self.assertTrue(self.execute(self.peer,self.challenge(self.peer,op)).HasField('identity_status'))

    def barrier_operation(self):return pb.IssuerOperation(barrier_read=pb.AuthorityBarrierRead(revision=1))

    def test_checked_client_completes_real_activation_application_and_release(self):
        client = self.authenticate_client(); state = self.activate_client(client); self.assertEqual(state.generation,1)
        self.assertEqual(client.call(authority_state=pb.AuthorityStateRead()).authority_activation,state)
        self.revoke()
        applied = pb.AuthorityApplied(generation=1,from_revision=0,through_revision=1)
        reply = client.call(authority_applied=applied); self.assertTrue(reply.HasField('authority_activation'),reply)
        self.assertEqual(reply.authority_activation.applied_revision,1)
        self.assertEqual(client.call(authority_applied=applied).authority_activation,reply.authority_activation)
        released = client.call(authority_release=pb.AuthorityRelease(generation=1,applied_revision=1))
        self.assertTrue(released.HasField('authority_activation'),released); self.assertFalse(released.authority_activation.active)

    def test_owner_checked_client_reads_pending_then_complete_barrier(self):
        service = self.authenticate_client(); self.activate_client(service); self.revoke(); owner = self.client()
        pending = self.execute(owner,self.challenge(owner,self.barrier_operation()))
        self.assertTrue(pending.HasField('authority_barrier'),pending); self.assertFalse(pending.authority_barrier.complete)
        self.assertEqual(pending.authority_barrier.pending_count,1)
        self.assertTrue(service.call(authority_applied=pb.AuthorityApplied(generation=1,from_revision=0,through_revision=1)).HasField('authority_activation'))
        done = self.execute(owner,self.challenge(owner,self.barrier_operation()))
        self.assertTrue(done.HasField('authority_barrier'),done); self.assertTrue(done.authority_barrier.complete)

    def good_activation(self, challenge):
        proof = self.good_result(challenge)
        return pb.AuthorityActivationState(issuer_id=proof.issuer_id,registration=proof.registration,generation=1,
            service_incarnation=proof.service_incarnation,issuer_incarnation=proof.issuer_incarnation,
            connection_binding=proof.connection_binding,applied_revision=0,head_revision=0,active=True)

    def service_peer(self, mutate):
        def respond(peer,request,reply):
            if request.HasField('authority_authentication_begin'):
                peer.challenge = self.good_challenge(request.authority_authentication_begin)
                reply.challenge.CopyFrom(peer.challenge)
            elif request.HasField('authority_authentication_complete'):
                reply.authority_authentication.CopyFrom(self.good_result(peer.challenge))
            else:
                state = self.good_activation(peer.challenge)
                if request.HasField('authority_applied'):state.applied_revision=1;state.head_revision=1
                if request.HasField('authority_release'):state.active=False
                mutate(request,state);reply.authority_activation.CopyFrom(state)
            peer.stream.sendall(frame(reply))
        return FaultPeer(self,response=respond)

    def poisoned(self, client, request, peer, count):
        output,_ = client.proc.communicate(frame(request)*2,timeout=3)
        self.assertEqual(client.proc.returncode,2);self.assertEqual(output,b'')
        self.assertEqual(len(peer.requests),count);self.assertEqual(peer.connections,1)

    def malformed_activation(self, mode):
        def mutate(request,state):
            if mode=='issuer':state.issuer_id=b'x'*32
            elif mode=='participant':state.registration.participant.id=b'x'*32
            elif mode=='domains':state.registration.participant.domains[0].id='other'
            elif mode=='key':state.registration.participant.public_key=b'x'*32
            elif mode=='revision':state.registration.registered_revision=2
            elif mode=='generation':state.generation=0
            elif mode=='incarnation':state.service_incarnation=b'x'*32
            elif mode=='issuer-incarnation':state.issuer_incarnation=b'x'*32
            elif mode=='binding':state.connection_binding=b'x'*32
            elif mode=='applied':state.applied_revision=1;state.head_revision=1
            elif mode=='head':state.head_revision=1
            elif mode=='inactive':state.active=False
            elif mode=='overflow':state.head_revision=2**63
            elif mode=='unknown':state.ParseFromString(state.SerializeToString()+b'\xf8\x07\x01')
        peer=self.service_peer(mutate);client=self.authenticate_client(peer.port)
        self.poisoned(client,pb.IssuerRequest(protocol_version=1,authority_activate=pb.AuthorityActivate(synchronized_revision=0)),peer,5)

    def test_activation_context_and_registration_cannot_be_substituted(self):
        for mode in ('issuer','participant','domains','key','revision','incarnation','issuer-incarnation','binding'):
            with self.subTest(mode=mode):self.malformed_activation(mode)

    def test_activation_must_match_synchronized_revision_and_valid_state(self):
        for mode in ('generation','applied','head','inactive','overflow','unknown'):
            with self.subTest(mode=mode):self.malformed_activation(mode)

    def test_applied_reply_cannot_claim_wrong_generation_revision_or_inactive_state(self):
        for mode in ('generation','applied','head','inactive','binding'):
            with self.subTest(mode=mode):
                def mutate(request,state):
                    if not request.HasField('authority_applied'):return
                    if mode=='generation':state.generation=2
                    elif mode=='applied':state.applied_revision=0
                    elif mode=='head':state.head_revision=0
                    elif mode=='inactive':state.active=False
                    else:state.connection_binding=b'x'*32
                peer=self.service_peer(mutate);client=self.authenticate_client(peer.port);self.activate_client(client)
                self.poisoned(client,pb.IssuerRequest(protocol_version=1,authority_applied=pb.AuthorityApplied(
                    generation=1,from_revision=0,through_revision=1)),peer,6)

    def test_release_cannot_claim_active_or_unsynchronized_state(self):
        for mode in ('active','head','applied','generation'):
            with self.subTest(mode=mode):
                def mutate(request,state):
                    if not request.HasField('authority_release'):return
                    if mode=='active':state.active=True
                    elif mode=='head':state.head_revision=1
                    elif mode=='applied':state.applied_revision=1;state.head_revision=1
                    else:state.generation=2
                peer=self.service_peer(mutate);client=self.authenticate_client(peer.port);self.activate_client(client)
                self.poisoned(client,pb.IssuerRequest(protocol_version=1,authority_release=pb.AuthorityRelease(generation=1,applied_revision=0)),peer,6)

    def test_state_read_cannot_roll_back_an_observed_applied_revision(self):
        peer=self.service_peer(lambda request,state:None);client=self.authenticate_client(peer.port);self.activate_client(client)
        reply=client.call(authority_applied=pb.AuthorityApplied(generation=1,from_revision=0,through_revision=1))
        self.assertTrue(reply.HasField('authority_activation'),reply)
        self.poisoned(client,pb.IssuerRequest(protocol_version=1,authority_state=pb.AuthorityStateRead()),peer,7)

    def owner_challenge(self, operation):
        value=pb.IssuerChallenge(protocol_version=1,issuer_id=self.info.issuer_id,incarnation=b'i'*32,nonce=b'n'*32,
            owner_public_key=self.owner_public,operation=operation,lifetime_ms=30000,audience='identityd/admin/v1')
        return pb.ChallengeReply(nonce=value.nonce,signing_request=keys.SignRequest(purpose=keys.ISSUER_ADMINISTRATION,payload=value.SerializeToString()))

    def good_barrier(self):
        return pb.AuthorityBarrierStatus(issuer_id=self.info.issuer_id,revision=1,target=pb.IdentityReference(kind=pb.ACTOR_TARGET,id=b'a'*32),
            participant_count=1,pending_count=1,complete=False,obligations=[pb.AuthorityBarrierObligation(registration=self.registration,
                generation=1,service_incarnation=b's'*32,applied_revision=0,complete=False)])

    def bad_barrier(self, mode):
        def respond(peer,request,reply):
            if request.HasField('challenge'):reply.challenge.CopyFrom(self.owner_challenge(request.challenge))
            else:
                result=self.good_barrier()
                if mode=='issuer':result.issuer_id=b'x'*32
                elif mode=='revision':result.revision=2
                elif mode=='target':result.target.id=b'x'
                elif mode=='kind':result.target.kind=0
                elif mode=='complete':result.complete=True
                elif mode=='pending':result.pending_count=2
                elif mode=='count':result.participant_count=0
                elif mode=='missing':result.ClearField('obligations')
                elif mode=='generation':result.obligations[0].generation=0
                elif mode=='incarnation':result.obligations[0].service_incarnation=b'x'
                elif mode=='applied':result.obligations[0].applied_revision=1
                elif mode=='record':result.obligations[0].registration.issuer_id=b'x'*32
                elif mode=='duplicate':result.obligations.add().CopyFrom(result.obligations[0]);result.participant_count=2;result.pending_count=2
                elif mode=='cursor':result.next_after=b'x'*32
                elif mode=='unknown':result.ParseFromString(result.SerializeToString()+b'\xf8\x07\x01')
                if mode=='outcome':reply.policy.CopyFrom(pb.PolicyState(revision=1))
                else:reply.authority_barrier.CopyFrom(result)
            peer.stream.sendall(frame(reply))
        peer=FaultPeer(self,response=respond);client=self.client(peer.port);challenge=self.challenge(client,self.barrier_operation())
        proof=self.proof(challenge)
        if mode=='proof':proof.signature=b'x'*64
        self.poisoned(client,pb.IssuerRequest(protocol_version=1,execute=pb.IssuerExecute(nonce=challenge.nonce,proof=proof)),peer,4)

    def test_barrier_identity_and_global_counts_are_checked(self):
        for mode in ('issuer','revision','target','kind','complete','pending','count','missing'):
            with self.subTest(mode=mode):self.bad_barrier(mode)

    def test_barrier_obligations_and_pagination_are_checked(self):
        for mode in ('generation','incarnation','applied','record','duplicate','cursor','unknown'):
            with self.subTest(mode=mode):self.bad_barrier(mode)

    def test_owner_result_requires_matching_outcome_and_valid_proof(self):
        for mode in ('outcome','proof'):
            with self.subTest(mode=mode):self.bad_barrier(mode)


if __name__=='__main__':unittest.main(verbosity=2)
