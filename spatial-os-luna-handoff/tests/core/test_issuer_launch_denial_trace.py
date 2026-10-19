"""Negative controls for the supervised launch denial trace verifier."""
import unittest
import core_pb2 as core
import issuer_pb2 as identity
import key_pb2 as keys
from harness.issuer_launch_denial import require_active_generation_denial_trace, ACTIVE_GENERATION_DENIAL

class IssuerLaunchDenialTraceTests(unittest.TestCase):
    def setUp(self):
        self.issuer_id = b"i" * 32
        self.issuer_incarnation = b"j" * 32
        self.participant = b"p" * 32
        self.incarnation = b"c" * 32
        self.launch_id = b"l" * 32
        self.public_key = b"s" * 32
        self.revision = 7
        self.begin = identity.IssuerRequest(request_id=1, authority_launch_begin=identity.AuthorityLaunchBegin(
            service=identity.AuthorityAuthenticationResult(issuer_id=self.issuer_id,
                issuer_incarnation=self.issuer_incarnation,
                registration=identity.AuthorityParticipantRecord(participant=identity.AuthorityParticipant(
                    id=self.participant, public_key=b"a" * 32)), service_incarnation=self.incarnation),
            supervision_revision=self.revision, launch_id=self.launch_id))
        challenge = identity.AuthorityLaunchChallenge(protocol_version=1, issuer_id=self.issuer_id,
            issuer_incarnation=self.issuer_incarnation, nonce=b"n" * 32, connection_binding=b"b" * 32,
            request=self.begin.authority_launch_begin, supervisor_public_key=self.public_key,
            lifetime_ms=5000, audience="identityd/authority-launch/v1")
        self.begin_reply = identity.IssuerReply(request_id=1)
        self.begin_reply.challenge.nonce = challenge.nonce
        self.begin_reply.challenge.signing_request.purpose = keys.AUTHORITY_LAUNCH_AUTHORIZATION
        self.begin_reply.challenge.signing_request.payload = challenge.SerializeToString()
        self.complete = identity.IssuerRequest(request_id=2,
            authority_launch_complete=identity.AuthorityLaunchComplete(nonce=challenge.nonce))
        proof = self.complete.authority_launch_complete.proof
        proof.purpose = keys.AUTHORITY_LAUNCH_AUTHORIZATION
        proof.public_key = self.public_key
        proof.signature = b"x" * 64
        self.complete_reply = identity.IssuerReply(request_id=2)
        self.complete_reply.error.code = core.BUSY
        self.complete_reply.error.message = ACTIVE_GENERATION_DENIAL
        self.trace = [(self.begin, self.begin_reply), (self.complete, self.complete_reply)]

    def check(self, trace=None, **overrides):
        values=dict(participant_id=self.participant, service_incarnation=self.incarnation, launch_id=self.launch_id,
                    supervision_revision=self.revision, supervisor_public_key=self.public_key)
        values.update(overrides)
        values['issuer_id'] = self.issuer_id
        require_active_generation_denial_trace(self.trace if trace is None else trace, **values)

    def test_complete_authentic_trace_is_accepted(self):
        self.check()

    def test_wrong_phase_is_rejected(self):
        self.assertRaises(AssertionError, self.check, list(reversed(self.trace)))

    def test_wrong_code_and_wrong_message_are_rejected(self):
        code = identity.IssuerReply(); code.CopyFrom(self.complete_reply); code.error.code = core.FORBIDDEN
        self.assertRaises(AssertionError, self.check, [(self.begin, self.begin_reply), (self.complete, code)])
        message = identity.IssuerReply(); message.CopyFrom(self.complete_reply); message.error.message = "other denial"
        self.assertRaises(AssertionError, self.check, [(self.begin, self.begin_reply), (self.complete, message)])

    def test_different_candidate_is_rejected(self):
        self.assertRaises(AssertionError, self.check, launch_id=b"other-launch")
        self.assertRaises(AssertionError, self.check, service_incarnation=b"other-incarnation")

    def test_wrong_nonce_and_missing_observed_reply_are_rejected(self):
        request = identity.IssuerRequest(); request.CopyFrom(self.complete)
        request.authority_launch_complete.nonce = b"other-nonce"
        self.assertRaises(AssertionError, self.check, [(self.begin, self.begin_reply), (request, self.complete_reply)])
        self.assertRaises(AssertionError, self.check, self.trace[:1])
        envelope = identity.IssuerReply(); envelope.CopyFrom(self.begin_reply)
        envelope.challenge.nonce = b"z" * 32
        self.assertRaises(AssertionError, self.check, [(self.begin, envelope), (self.complete, self.complete_reply)])
        purpose = identity.IssuerReply(); purpose.CopyFrom(self.begin_reply)
        purpose.challenge.signing_request.purpose = keys.ACTOR_AUTHENTICATION
        self.assertRaises(AssertionError, self.check, [(self.begin, purpose), (self.complete, self.complete_reply)])

if __name__ == "__main__":
    unittest.main(verbosity=2)
