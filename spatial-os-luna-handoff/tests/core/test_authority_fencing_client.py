"""Independent TLS peers must not substitute fencing proof context or results."""
import unittest
import issuer_pb2 as pb
import key_pb2 as keys
import core_pb2 as core
import test_authority_fencing as fencing
from harness.wire import frame


class FencingClientTests(fencing.AuthorityFencingTests):
    def good_challenge(self, request):
        value=pb.AuthorityFenceChallenge(protocol_version=1,issuer_id=self.info.issuer_id,
            issuer_incarnation=b'i'*32,nonce=b'n'*32,connection_binding=b'c'*32,
            request=request,supervisor_public_key=self.supervisor_public,lifetime_ms=30000,
            audience='identityd/authority-fence/v1')
        return pb.ChallengeReply(nonce=value.nonce,signing_request=keys.SignRequest(
            purpose=keys.AUTHORITY_FENCING,payload=value.SerializeToString()))

    def good_result(self):
        request=self.fence_request()
        return pb.AuthorityFenceState(issuer_id=self.info.issuer_id,registration=self.registration,
            generation=request.generation,service_incarnation=request.service_incarnation,
            authorization_id=request.authorization_id,launch_id=request.launch_id,
            supervision_revision=request.supervision_revision,fence_id=b'f'*32,security_revision=0,
            supervisor_public_key=self.supervisor_public)

    def test_client_rejects_substituted_fence_challenge(self):
        for mode in ('issuer','incarnation','nonce','binding','target','generation','key','lifetime','audience','purpose','unknown'):
            with self.subTest(mode=mode):
                def respond(peer,request,reply):
                    challenge=self.good_challenge(request.authority_fence_begin)
                    value=pb.AuthorityFenceChallenge.FromString(challenge.signing_request.payload)
                    if mode=='issuer':value.issuer_id=b'x'*32
                    elif mode=='incarnation':value.issuer_incarnation=b'x'
                    elif mode=='nonce':value.nonce=b'x'*32
                    elif mode=='binding':value.connection_binding=b'x'
                    elif mode=='target':value.request.authorization_id=b'x'*32
                    elif mode=='generation':value.request.generation=2
                    elif mode=='key':value.supervisor_public_key=self.info.public_key
                    elif mode=='lifetime':value.lifetime_ms=60001
                    elif mode=='audience':value.audience='other'
                    elif mode=='unknown':value.ParseFromString(value.SerializeToString()+b'\xf8\x07\x01')
                    challenge.signing_request.payload=value.SerializeToString()
                    if mode=='purpose':challenge.signing_request.purpose=keys.AUTHORITY_LAUNCH_AUTHORIZATION
                    reply.challenge.CopyFrom(challenge);peer.stream.sendall(frame(reply))
                peer=fencing.launch.registry.clients.FaultPeer(self,response=respond);client=self.client(peer.port)
                self.poisoned(client,pb.IssuerRequest(protocol_version=1,authority_fence_begin=self.fence_request()),peer,3)

    def test_client_rejects_substituted_fence_result_or_invalid_proof(self):
        for mode in ('issuer','registration','generation','incarnation','authorization','launch','supervision','id','revision','key','unknown','outcome','proof'):
            with self.subTest(mode=mode):
                def respond(peer,request,reply):
                    if request.HasField('authority_fence_begin'):
                        reply.challenge.CopyFrom(self.good_challenge(request.authority_fence_begin))
                    else:
                        value=self.good_result()
                        if mode=='issuer':value.issuer_id=b'x'*32
                        elif mode=='registration':value.registration.registered_revision=2
                        elif mode=='generation':value.generation=2
                        elif mode=='incarnation':value.service_incarnation=b'x'*32
                        elif mode=='authorization':value.authorization_id=b'x'*32
                        elif mode=='launch':value.launch_id=b'x'*32
                        elif mode=='supervision':value.supervision_revision=2
                        elif mode=='id':value.fence_id=b'x'
                        elif mode=='revision':value.security_revision=2**63
                        elif mode=='key':value.supervisor_public_key=b'x'*32
                        elif mode=='unknown':value.ParseFromString(value.SerializeToString()+b'\xf8\x07\x01')
                        if mode=='outcome':reply.policy.CopyFrom(pb.PolicyState(revision=1))
                        else:reply.authority_fence.CopyFrom(value)
                    peer.stream.sendall(frame(reply))
                peer=fencing.launch.registry.clients.FaultPeer(self,response=respond);client=self.client(peer.port)
                challenge=self.fence_begin(peer=client)
                proof=self.proof(challenge,self.supervisor_key,self.supervisor_public)
                if mode=='proof':proof.signature=b'x'*64
                self.poisoned(client,pb.IssuerRequest(protocol_version=1,authority_fence_complete=pb.AuthorityFenceComplete(
                    nonce=challenge.nonce,proof=proof)),peer,4)

    def test_typed_denial_consumes_client_fence_context(self):
        def respond(peer,request,reply):
            if request.HasField('authority_fence_begin'):
                reply.challenge.CopyFrom(self.good_challenge(request.authority_fence_begin))
            elif len(peer.requests)>4:reply.authority_fence.CopyFrom(self.good_result())
            peer.stream.sendall(frame(reply))
        peer=fencing.launch.registry.clients.FaultPeer(self,response=respond);client=self.client(peer.port)
        challenge=self.fence_begin(peer=client)
        complete=pb.AuthorityFenceComplete(nonce=challenge.nonce,proof=self.proof(challenge,self.supervisor_key,self.supervisor_public))
        self.denied(client.call(authority_fence_complete=complete),core.FORBIDDEN)
        self.poisoned(client,pb.IssuerRequest(protocol_version=1,authority_fence_complete=complete),peer,5)

    def test_client_rejects_unsolicited_fence_success(self):
        challenge=self.good_challenge(self.fence_request())
        def respond(peer,request,reply):
            reply.authority_fence.CopyFrom(self.good_result());peer.stream.sendall(frame(reply))
        peer=fencing.launch.registry.clients.FaultPeer(self,response=respond);client=self.client(peer.port)
        self.poisoned(client,pb.IssuerRequest(protocol_version=1,authority_fence_complete=pb.AuthorityFenceComplete(
            nonce=challenge.nonce,proof=self.proof(challenge,self.supervisor_key,self.supervisor_public))),peer,3)


if __name__=='__main__':
    suite=unittest.TestSuite(FencingClientTests(n) for n in FencingClientTests.__dict__ if n.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
