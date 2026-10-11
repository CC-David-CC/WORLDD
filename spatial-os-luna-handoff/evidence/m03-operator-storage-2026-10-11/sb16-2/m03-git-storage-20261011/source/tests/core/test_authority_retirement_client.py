"""Independent TLS peers cannot invent retirement or substitute cleanup identity."""
import unittest
import core_pb2 as core
import issuer_pb2 as pb
import key_pb2 as keys
import test_authority_retirement as retirement
from harness.wire import frame

FaultPeer = retirement.fencing.launch.registry.clients.FaultPeer


class RetirementClientTests(retirement.RetirementTests):
    def good_challenge(self, request):
        value = pb.AuthorityRetireChallenge(protocol_version=1, issuer_id=self.info.issuer_id,
            issuer_incarnation=b'i' * 32, nonce=b'n' * 32, connection_binding=b'c' * 32, request=request,
            supervisor_public_key=self.supervisor_public, lifetime_ms=30000, audience='identityd/authority-retirement/v1')
        return pb.ChallengeReply(nonce=value.nonce, signing_request=keys.SignRequest(
            purpose=keys.AUTHORITY_RETIREMENT, payload=value.SerializeToString()))

    def good_result(self):
        return pb.AuthorityRetirementState(issuer_id=self.info.issuer_id, request=self.retire_request(),
            registration=self.registration, supervisor_public_key=self.supervisor_public, retirement_id=b'r' * 32,
            security_revision=1, applied_revision=0)

    def test_substituted_retirement_challenge_is_rejected(self):
        original = self.retire_request()
        for mode in ('issuer', 'incarnation', 'nonce', 'binding', 'generation', 'digest', 'fence', 'key', 'lifetime', 'audience', 'purpose', 'unknown'):
            with self.subTest(mode=mode):
                def respond(peer, request, reply):
                    challenge = self.good_challenge(request.authority_retire_begin)
                    value = pb.AuthorityRetireChallenge.FromString(challenge.signing_request.payload)
                    if mode == 'issuer': value.issuer_id = b'x' * 32
                    elif mode == 'incarnation': value.issuer_incarnation = b'x'
                    elif mode == 'nonce': value.nonce = b'x' * 32
                    elif mode == 'binding': value.connection_binding = b'x'
                    elif mode == 'generation': value.request.owner.generation = 2
                    elif mode == 'digest': value.request.cleanup_sha256 = b'x' * 32
                    elif mode == 'fence': value.request.fence_id = b'x' * 32
                    elif mode == 'key': value.supervisor_public_key = self.info.public_key
                    elif mode == 'lifetime': value.lifetime_ms = 60001
                    elif mode == 'audience': value.audience = 'other'
                    elif mode == 'unknown': value.ParseFromString(value.SerializeToString() + b'\xf8\x07\x01')
                    challenge.signing_request.payload = value.SerializeToString()
                    if mode == 'purpose': challenge.signing_request.purpose = keys.AUTHORITY_FENCING
                    reply.challenge.CopyFrom(challenge); peer.stream.sendall(frame(reply))
                peer = FaultPeer(self, response=respond); client = self.client(peer.port)
                self.poisoned(client, pb.IssuerRequest(protocol_version=1, authority_retire_begin=original), peer, 3)

    def test_substituted_result_or_invalid_proof_is_rejected(self):
        self.retire_request()
        for mode in ('issuer', 'registration', 'generation', 'digest', 'fence', 'id', 'revision', 'applied', 'key', 'unknown', 'outcome', 'proof'):
            with self.subTest(mode=mode):
                def respond(peer, request, reply):
                    if request.HasField('authority_retire_begin'):
                        reply.challenge.CopyFrom(self.good_challenge(request.authority_retire_begin))
                    else:
                        value = self.good_result()
                        if mode == 'issuer': value.issuer_id = b'x' * 32
                        elif mode == 'registration': value.registration.registered_revision = 2
                        elif mode == 'generation': value.request.owner.generation = 2
                        elif mode == 'digest': value.request.cleanup_sha256 = b'x' * 32
                        elif mode == 'fence': value.request.fence_id = b'x' * 32
                        elif mode == 'id': value.retirement_id = b'x'
                        elif mode == 'revision': value.security_revision = 2**63
                        elif mode == 'applied': value.applied_revision = 2
                        elif mode == 'key': value.supervisor_public_key = b'x' * 32
                        elif mode == 'unknown': value.ParseFromString(value.SerializeToString() + b'\xf8\x07\x01')
                        if mode == 'outcome': reply.policy.CopyFrom(pb.PolicyState(revision=1))
                        else: reply.authority_retirement.CopyFrom(value)
                    peer.stream.sendall(frame(reply))
                peer = FaultPeer(self, response=respond); client = self.client(peer.port)
                challenge = self.retire_begin(peer=client); proof = self.proof(challenge, self.supervisor_key, self.supervisor_public)
                if mode == 'proof': proof.signature = b'x' * 64
                self.poisoned(client, pb.IssuerRequest(protocol_version=1, authority_retire_complete=pb.AuthorityRetireComplete(
                    nonce=challenge.nonce, proof=proof)), peer, 4)

    def test_typed_denial_consumes_pending_retirement_context(self):
        self.retire_request()
        def respond(peer, request, reply):
            if request.HasField('authority_retire_begin'):
                reply.challenge.CopyFrom(self.good_challenge(request.authority_retire_begin))
            elif len(peer.requests) > 4: reply.authority_retirement.CopyFrom(self.good_result())
            peer.stream.sendall(frame(reply))
        peer = FaultPeer(self, response=respond); client = self.client(peer.port)
        challenge = self.retire_begin(peer=client)
        complete = pb.AuthorityRetireComplete(nonce=challenge.nonce, proof=self.proof(challenge, self.supervisor_key, self.supervisor_public))
        self.error(client.call(authority_retire_complete=complete), core.FORBIDDEN)
        self.poisoned(client, pb.IssuerRequest(protocol_version=1, authority_retire_complete=complete), peer, 5)

    def test_unsolicited_retirement_success_is_rejected(self):
        challenge = self.good_challenge(self.retire_request())
        def respond(peer, request, reply):
            reply.authority_retirement.CopyFrom(self.good_result()); peer.stream.sendall(frame(reply))
        peer = FaultPeer(self, response=respond); client = self.client(peer.port)
        self.poisoned(client, pb.IssuerRequest(protocol_version=1, authority_retire_complete=pb.AuthorityRetireComplete(
            nonce=challenge.nonce, proof=self.proof(challenge, self.supervisor_key, self.supervisor_public))), peer, 3)

    def test_changed_connection_context_is_rejected(self):
        original = self.retire_request()
        def respond(peer, request, reply):
            challenge = self.good_challenge(request.authority_retire_begin)
            if len(peer.requests) > 3:
                value = pb.AuthorityRetireChallenge.FromString(challenge.signing_request.payload)
                value.nonce = b'z' * 32; value.connection_binding = b'x' * 32
                challenge.nonce = value.nonce; challenge.signing_request.payload = value.SerializeToString()
            reply.challenge.CopyFrom(challenge); peer.stream.sendall(frame(reply))
        peer = FaultPeer(self, response=respond); client = self.client(peer.port)
        self.retire_begin(peer=client)
        self.poisoned(client, pb.IssuerRequest(protocol_version=1, authority_retire_begin=original), peer, 4)

    def test_barrier_cannot_claim_retirement_for_another_generation_or_history(self):
        self.retire_request()
        operation = pb.IssuerOperation(barrier_read=pb.AuthorityBarrierRead(revision=1))
        for mode in ('generation', 'incarnation', 'registration', 'applied', 'revision', 'missing', 'incomplete'):
            with self.subTest(mode=mode):
                def respond(peer, request, reply):
                    if request.HasField('challenge'): reply.challenge.CopyFrom(self.owner_challenge(request.challenge))
                    else:
                        result = pb.AuthorityBarrierStatus(issuer_id=self.info.issuer_id, revision=1,
                            target=pb.IdentityReference(kind=pb.ACTOR_TARGET, id=b'a' * 32), participant_count=1,
                            pending_count=0, complete=True)
                        item = result.obligations.add(registration=self.registration, generation=1,
                            service_incarnation=self.service.service_incarnation, applied_revision=0, complete=True,
                            retirement=self.good_result())
                        if mode == 'generation': item.retirement.request.owner.generation = 2
                        elif mode == 'incarnation': item.retirement.request.owner.service_incarnation = b'x' * 32
                        elif mode == 'registration': item.retirement.registration.registered_revision = 2
                        elif mode == 'applied': item.retirement.applied_revision = 1
                        elif mode == 'revision': item.retirement.security_revision = 0
                        elif mode == 'missing': item.ClearField('retirement')
                        elif mode == 'incomplete': item.complete = False
                        reply.authority_barrier.CopyFrom(result)
                    peer.stream.sendall(frame(reply))
                peer = FaultPeer(self, response=respond); client = self.client(peer.port)
                challenge = self.challenge(client, operation)
                self.poisoned(client, pb.IssuerRequest(protocol_version=1, execute=pb.IssuerExecute(
                    nonce=challenge.nonce, proof=self.proof(challenge, self.owner, self.owner_public))), peer, 4)


if __name__ == '__main__':
    suite = unittest.TestSuite(RetirementClientTests(n) for n in RetirementClientTests.__dict__ if n.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
