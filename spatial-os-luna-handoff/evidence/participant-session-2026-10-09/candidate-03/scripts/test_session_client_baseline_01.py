"""Checked service proofs with real issuer authority and explicit TLS fault peers."""
import unittest
import core_pb2 as core
import grants_pb2 as grants
import issuer_pb2 as pb
import key_pb2 as keys
from harness.wire import frame
from test_issuer_client import ClientFixture, FaultPeer


class SessionClientTests(ClientFixture):
    def setUp(self):
        super().setUp();self.peer=self.ready();self.signer,self.public=self.key('registered-service')
        value=pb.AuthorityParticipant(id=b'p'*32,public_key=self.public,
            domains=[grants.Domain(kind=grants.WORLD,id='park')])
        operation=pb.IssuerOperation(participant_register=pb.AuthorityParticipantRegister(participant=value,expected_registry_revision=0))
        reply=self.execute(self.peer,self.challenge(self.peer,operation))
        self.assertTrue(reply.HasField('participant'),reply);self.registration=reply.participant

    def service_request(self):
        return pb.AuthorityAuthenticationBegin(participant_id=b'p'*32,registered_revision=1,service_incarnation=b's'*32)

    def service_begin(self,client):
        try:
            reply=client.call(authority_authentication_begin=self.service_request())
        except EOFError:
            with open(client.stderr.name) as log:self.fail('Checked client closed before service challenge: '+log.read())
        self.assertTrue(reply.HasField('challenge'),reply);return reply.challenge

    def completion(self,challenge):
        return pb.AuthorityAuthenticationComplete(nonce=challenge.nonce,proof=self.proof(challenge,self.signer,self.public))

    def good_challenge(self,request,number=3):
        payload=pb.AuthorityAuthenticationChallenge(protocol_version=1,issuer_id=self.info.issuer_id,
            issuer_incarnation=b'i'*32,nonce=b'n'*31+bytes([number]),request=request,
            connection_binding=b'c'*32,lifetime_ms=30000,audience='identityd/authority-participation/v1')
        return pb.ChallengeReply(nonce=payload.nonce,signing_request=keys.SignRequest(
            purpose=keys.AUTHORITY_PARTICIPATION,payload=payload.SerializeToString()))

    def good_result(self,challenge):
        payload=pb.AuthorityAuthenticationChallenge.FromString(challenge.signing_request.payload)
        return pb.AuthorityAuthenticationResult(issuer_id=self.info.issuer_id,issuer_incarnation=payload.issuer_incarnation,
            registration=self.registration,service_incarnation=payload.request.service_incarnation,
            connection_binding=payload.connection_binding,security_revision=0)

    def test_checked_client_completes_real_registered_service_proof(self):
        client=self.client();challenge=self.service_begin(client)
        reply=client.call(authority_authentication_complete=self.completion(challenge))
        self.assertTrue(reply.HasField('authority_authentication'),reply)
        result=reply.authority_authentication;payload=pb.AuthorityAuthenticationChallenge.FromString(challenge.signing_request.payload)
        self.assertEqual(result.registration,self.registration)
        self.assertEqual(result.connection_binding,payload.connection_binding)
        self.assertEqual(result.issuer_incarnation,payload.issuer_incarnation)
        self.assertEqual(result.service_incarnation,self.service_request().service_incarnation)
        self.assertEqual(result.security_revision,0);self.assertEqual(self.counts(),(0,0,0))

    def test_real_denial_consumes_proof_but_preserves_checked_connection(self):
        client=self.client();challenge=self.service_begin(client)
        bad=self.completion(challenge);bad.proof.CopyFrom(self.proof(challenge,self.actor_key,self.actor_public))
        self.denied(client.call(authority_authentication_complete=bad))
        self.denied(client.call(authority_authentication_complete=self.completion(challenge)))
        self.assertTrue(client.call(authority_authentication_complete=self.completion(self.service_begin(client))).HasField('authority_authentication'))
        self.assertEqual(client.call(info=pb.IssuerInfoRequest()).info,self.info)

    def test_real_authenticated_connection_cannot_replace_service_identity(self):
        client=self.client();challenge=self.service_begin(client);completion=self.completion(challenge)
        self.assertTrue(client.call(authority_authentication_complete=completion).HasField('authority_authentication'))
        self.denied(client.call(authority_authentication_complete=completion))
        self.denied(client.call(authority_authentication_begin=self.service_request()),core.BUSY)
        self.assertEqual(client.call(info=pb.IssuerInfoRequest()).info,self.info)

    def malformed_challenge(self,mode):
        def respond(peer,request,reply):
            challenge=self.good_challenge(request.authority_authentication_begin)
            payload=pb.AuthorityAuthenticationChallenge.FromString(challenge.signing_request.payload)
            if mode=='version':payload.protocol_version=2
            elif mode=='issuer':payload.issuer_id=b'x'*32
            elif mode=='incarnation':payload.issuer_incarnation=b'x'
            elif mode=='nonce':payload.nonce=b'x'*32
            elif mode=='binding':payload.connection_binding=b'x'
            elif mode=='id':payload.request.participant_id=b'x'*32
            elif mode=='revision':payload.request.registered_revision=2
            elif mode=='service':payload.request.service_incarnation=b'x'*32
            elif mode=='short-ttl':payload.lifetime_ms=49
            elif mode=='long-ttl':payload.lifetime_ms=60001
            elif mode=='audience':payload.audience='world/actor'
            elif mode=='unknown':payload.ParseFromString(payload.SerializeToString()+b'\xf8\x07\x01')
            challenge.signing_request.payload=payload.SerializeToString()
            if mode=='purpose':challenge.signing_request.purpose=keys.ACTOR_AUTHENTICATION
            elif mode=='missing':challenge.ClearField('signing_request')
            elif mode=='payload':challenge.signing_request.payload=b'broken'
            elif mode=='outer-nonce':challenge.nonce=b'x'
            reply.challenge.CopyFrom(challenge);peer.stream.sendall(frame(reply))
        peer=FaultPeer(self,response=respond);client=self.client(peer.port)
        request=pb.IssuerRequest(protocol_version=1,authority_authentication_begin=self.service_request())
        output,_=client.proc.communicate(frame(request)*2,timeout=3)
        self.assertEqual(client.proc.returncode,2,mode);self.assertEqual(output,b'',mode)
        self.assertEqual(len(peer.requests),3);self.assertEqual(peer.connections,1)

    def test_invalid_challenge_envelope_poison_client(self):
        for mode in ('purpose','missing','payload','outer-nonce','version','unknown'):
            with self.subTest(mode=mode):self.malformed_challenge(mode)

    def test_invalid_challenge_context_poison_client(self):
        for mode in ('issuer','incarnation','nonce','binding','audience'):
            with self.subTest(mode=mode):self.malformed_challenge(mode)

    def test_substituted_request_or_invalid_lifetime_poison_client(self):
        for mode in ('id','revision','service','short-ttl','long-ttl'):
            with self.subTest(mode=mode):self.malformed_challenge(mode)

    def malformed_result(self,mode):
        def respond(peer,request,reply):
            if request.HasField('authority_authentication_begin'):
                peer.challenge=self.good_challenge(request.authority_authentication_begin)
                reply.challenge.CopyFrom(peer.challenge)
            else:
                result=self.good_result(peer.challenge)
                if mode=='issuer':result.issuer_id=b'x'*32
                elif mode=='incarnation':result.issuer_incarnation=b'x'*32
                elif mode=='binding':result.connection_binding=b'x'*32
                elif mode=='service':result.service_incarnation=b'x'*32
                elif mode=='missing':result.ClearField('registration')
                elif mode=='record-issuer':result.registration.issuer_id=b'x'*32
                elif mode=='id':result.registration.participant.id=b'x'*32
                elif mode=='revision':result.registration.registered_revision=2
                elif mode=='key':result.registration.participant.public_key=b'x'*32
                elif mode=='domains':result.registration.participant.ClearField('domains')
                elif mode=='overflow':result.security_revision=2**63
                elif mode=='unknown':result.ParseFromString(result.SerializeToString()+b'\xf8\x07\x01')
                reply.authority_authentication.CopyFrom(result)
            peer.stream.sendall(frame(reply))
        peer=FaultPeer(self,response=respond);client=self.client(peer.port)
        completion=self.completion(self.service_begin(client))
        if mode=='proof':completion.proof.signature=b'x'*64
        if mode=='proof-purpose':completion.proof.purpose=keys.ACTOR_AUTHENTICATION
        request=pb.IssuerRequest(protocol_version=1,authority_authentication_complete=completion)
        output,_=client.proc.communicate(frame(request)*2,timeout=3)
        self.assertEqual(client.proc.returncode,2,mode);self.assertEqual(output,b'',mode)
        self.assertEqual(len(peer.requests),4);self.assertEqual(peer.connections,1)

    def test_invalid_result_context_poison_client(self):
        for mode in ('issuer','incarnation','binding','service','overflow','unknown'):
            with self.subTest(mode=mode):self.malformed_result(mode)

    def test_substituted_registration_poison_client(self):
        for mode in ('missing','record-issuer','id','revision','key','domains'):
            with self.subTest(mode=mode):self.malformed_result(mode)

    def test_claimed_success_for_invalid_proof_poison_client(self):
        for mode in ('proof','proof-purpose'):
            with self.subTest(mode=mode):self.malformed_result(mode)

    def test_denial_cannot_leave_a_reusable_expected_challenge(self):
        def respond(peer,request,reply):
            if request.HasField('authority_authentication_begin'):
                peer.challenge=self.good_challenge(request.authority_authentication_begin);reply.challenge.CopyFrom(peer.challenge)
            elif len(peer.requests)>4:reply.authority_authentication.CopyFrom(self.good_result(peer.challenge))
            peer.stream.sendall(frame(reply))
        peer=FaultPeer(self,response=respond);client=self.client(peer.port);completion=self.completion(self.service_begin(client))
        self.denied(client.call(authority_authentication_complete=completion),core.FORBIDDEN)
        request=pb.IssuerRequest(protocol_version=1,authority_authentication_complete=completion)
        output,_=client.proc.communicate(frame(request)*2,timeout=3)
        self.assertEqual(client.proc.returncode,2);self.assertEqual(output,b'');self.assertEqual(len(peer.requests),5)

    def test_unrequested_authentication_success_poison_client(self):
        challenge=self.good_challenge(self.service_request())
        def respond(peer,request,reply):
            reply.authority_authentication.CopyFrom(self.good_result(challenge));peer.stream.sendall(frame(reply))
        peer=FaultPeer(self,response=respond);client=self.client(peer.port)
        request=pb.IssuerRequest(protocol_version=1,authority_authentication_complete=self.completion(challenge))
        output,_=client.proc.communicate(frame(request)*2,timeout=3)
        self.assertEqual(client.proc.returncode,2);self.assertEqual(output,b'');self.assertEqual(len(peer.requests),3)

    def inconsistent_second_challenge(self,mode):
        def respond(peer,request,reply):
            challenge=self.good_challenge(request.authority_authentication_begin,len(peer.requests))
            if len(peer.requests)>3:
                payload=pb.AuthorityAuthenticationChallenge.FromString(challenge.signing_request.payload)
                if mode=='duplicate':challenge.nonce=b'n'*31+b'\x03';payload.nonce=challenge.nonce
                elif mode=='binding':payload.connection_binding=b'x'*32
                else:payload.issuer_incarnation=b'x'*32
                challenge.signing_request.payload=payload.SerializeToString()
            reply.challenge.CopyFrom(challenge);peer.stream.sendall(frame(reply))
        peer=FaultPeer(self,response=respond);client=self.client(peer.port);self.service_begin(client)
        request=pb.IssuerRequest(protocol_version=1,authority_authentication_begin=self.service_request())
        output,_=client.proc.communicate(frame(request)*2,timeout=3)
        self.assertEqual(client.proc.returncode,2,mode);self.assertEqual(output,b'',mode);self.assertEqual(len(peer.requests),4)

    def test_connection_context_and_fresh_nonce_remain_consistent(self):
        for mode in ('duplicate','binding','incarnation'):
            with self.subTest(mode=mode):self.inconsistent_second_challenge(mode)


if __name__=='__main__':unittest.main(verbosity=2)
