"""Negative controls for the supervised launch denial trace verifier."""
import unittest
import core_pb2 as core
import issuer_pb2 as identity
import key_pb2 as keys
from harness.issuer_launch_denial import (
    ACTIVE_GENERATION_DENIAL,
    describe_issuer_trace,
    require_active_generation_denial_trace,
)


class IssuerLaunchDenialTraceTests(unittest.TestCase):
    def setUp(self):
        self.issuer_id = b"i" * 32
        self.issuer_incarnation = b"j" * 32
        self.participant = b"p" * 32
        self.incarnation = b"c" * 32
        self.launch_id = b"l" * 32
        self.public_key = b"s" * 32
        self.revision = 7

        hello_request = identity.IssuerRequest(protocol_version=1, request_id=1, hello=core.HelloRequest())
        hello_reply = identity.IssuerReply(protocol_version=1, request_id=1)
        hello_reply.hello.SetInParent()
        info_request = identity.IssuerRequest(protocol_version=1, request_id=2, info=identity.IssuerInfoRequest())
        info_reply = identity.IssuerReply(protocol_version=1, request_id=2)
        info_reply.info.issuer_id = self.issuer_id
        info_reply.info.public_key = b"k" * 32
        info_reply.info.policy_revision = 1

        self.begin = identity.IssuerRequest(protocol_version=1, request_id=3,
            authority_launch_begin=identity.AuthorityLaunchBegin(
                service=identity.AuthorityAuthenticationResult(issuer_id=self.issuer_id,
                    issuer_incarnation=self.issuer_incarnation,
                    registration=identity.AuthorityParticipantRecord(participant=identity.AuthorityParticipant(
                        id=self.participant, public_key=b"a" * 32)), service_incarnation=self.incarnation),
                supervision_revision=self.revision, launch_id=self.launch_id))
        challenge = identity.AuthorityLaunchChallenge(protocol_version=1, issuer_id=self.issuer_id,
            issuer_incarnation=self.issuer_incarnation, nonce=b"n" * 32, connection_binding=b"b" * 32,
            request=self.begin.authority_launch_begin, supervisor_public_key=self.public_key,
            lifetime_ms=5000, audience="identityd/authority-launch/v1")
        self.begin_reply = identity.IssuerReply(protocol_version=1, request_id=3)
        self.begin_reply.challenge.nonce = challenge.nonce
        self.begin_reply.challenge.signing_request.purpose = keys.AUTHORITY_LAUNCH_AUTHORIZATION
        self.begin_reply.challenge.signing_request.payload = challenge.SerializeToString()
        self.complete = identity.IssuerRequest(protocol_version=1, request_id=4,
            authority_launch_complete=identity.AuthorityLaunchComplete(nonce=challenge.nonce))
        proof = self.complete.authority_launch_complete.proof
        proof.purpose = keys.AUTHORITY_LAUNCH_AUTHORIZATION
        proof.public_key = self.public_key
        proof.signature = b"x" * 64
        self.complete_reply = identity.IssuerReply(protocol_version=1, request_id=4)
        self.complete_reply.error.code = core.BUSY
        self.complete_reply.error.message = ACTIVE_GENERATION_DENIAL
        self.trace = [
            (hello_request, hello_reply),
            (info_request, info_reply),
            (self.begin, self.begin_reply),
            (self.complete, self.complete_reply),
        ]

    def check(self, trace=None, **overrides):
        values = dict(participant_id=self.participant, service_incarnation=self.incarnation,
                      launch_id=self.launch_id, supervision_revision=self.revision,
                      supervisor_public_key=self.public_key)
        values.update(overrides)
        values["issuer_id"] = self.issuer_id
        require_active_generation_denial_trace(self.trace if trace is None else trace, **values)

    def copied_pair(self, index):
        request = identity.IssuerRequest()
        request.CopyFrom(self.trace[index][0])
        reply = identity.IssuerReply()
        reply.CopyFrom(self.trace[index][1])
        return request, reply

    def test_complete_four_message_trace_is_accepted(self):
        self.check()

    def test_missing_reordered_and_extra_messages_are_rejected(self):
        self.assertRaises(AssertionError, self.check, self.trace[1:])
        self.assertRaises(AssertionError, self.check,
                          [self.trace[1], self.trace[0], *self.trace[2:]])
        self.assertRaises(AssertionError, self.check,
                          [self.trace[0], self.trace[1], self.trace[1], *self.trace[2:]])
        sensitive_request = identity.IssuerRequest()
        sensitive_request.CopyFrom(self.complete)
        sensitive_request.authority_launch_complete.proof.signature = b"SIGNATURE-SENTINEL"
        sensitive_reply = identity.IssuerReply()
        sensitive_reply.CopyFrom(self.complete_reply)
        sensitive_reply.error.message = "PAYLOAD-SENTINEL"
        summary = describe_issuer_trace([*self.trace[:3],
            (sensitive_request, sensitive_reply), *self.trace] * 2)
        self.assertIn("'count': 16", summary)
        self.assertIn("'omitted': 8", summary)
        self.assertIn("authority_launch_complete", summary)
        self.assertNotIn("SIGNATURE-SENTINEL", summary)
        self.assertNotIn("PAYLOAD-SENTINEL", summary)

    def test_wrong_prerequisite_ids_and_reply_variants_are_rejected(self):
        request, _ = self.copied_pair(0)
        request.request_id = 99
        self.assertRaises(AssertionError, self.check,
                          [(request, self.trace[0][1]), *self.trace[1:]])
        hello_reply = identity.IssuerReply(protocol_version=1, request_id=1)
        hello_reply.error.code = core.BUSY
        self.assertRaises(AssertionError, self.check,
                          [(self.trace[0][0], hello_reply), *self.trace[1:]])
        info_reply = identity.IssuerReply(protocol_version=1, request_id=2)
        info_reply.error.code = core.BUSY
        self.assertRaises(AssertionError, self.check,
                          [self.trace[0], (self.trace[1][0], info_reply), *self.trace[2:]])
        wrong_info_id = identity.IssuerReply()
        wrong_info_id.CopyFrom(self.trace[1][1])
        wrong_info_id.info.issuer_id = b"wrong-issuer"
        self.assertRaises(AssertionError, self.check,
                          [self.trace[0], (self.trace[1][0], wrong_info_id), *self.trace[2:]])
        wrong_protocol = identity.IssuerReply()
        wrong_protocol.CopyFrom(self.trace[1][1])
        wrong_protocol.protocol_version = 2
        self.assertRaises(AssertionError, self.check,
                          [self.trace[0], (self.trace[1][0], wrong_protocol), *self.trace[2:]])

    def test_launch_request_reply_ids_must_correlate(self):
        wrong_begin = identity.IssuerReply()
        wrong_begin.CopyFrom(self.begin_reply)
        wrong_begin.request_id = 99
        self.assertRaises(AssertionError, self.check,
                          [*self.trace[:2], (self.begin, wrong_begin), self.trace[3]])
        wrong_complete = identity.IssuerReply()
        wrong_complete.CopyFrom(self.complete_reply)
        wrong_complete.request_id = 99
        self.assertRaises(AssertionError, self.check,
                          [*self.trace[:3], (self.complete, wrong_complete)])

    def test_wrong_code_and_message_are_rejected(self):
        code = identity.IssuerReply()
        code.CopyFrom(self.complete_reply)
        code.error.code = core.FORBIDDEN
        self.assertRaises(AssertionError, self.check,
                          [*self.trace[:3], (self.complete, code)])
        message = identity.IssuerReply()
        message.CopyFrom(self.complete_reply)
        message.error.message = "other denial"
        self.assertRaises(AssertionError, self.check,
                          [*self.trace[:3], (self.complete, message)])

    def test_different_candidate_and_wrong_proof_are_rejected(self):
        self.assertRaises(AssertionError, self.check, launch_id=b"other-launch")
        self.assertRaises(AssertionError, self.check, service_incarnation=b"other-incarnation")
        request = identity.IssuerRequest()
        request.CopyFrom(self.complete)
        request.authority_launch_complete.nonce = b"other-nonce"
        self.assertRaises(AssertionError, self.check,
                          [*self.trace[:3], (request, self.complete_reply)])
        envelope = identity.IssuerReply()
        envelope.CopyFrom(self.begin_reply)
        envelope.challenge.nonce = b"z" * 32
        self.assertRaises(AssertionError, self.check,
                          [self.trace[0], self.trace[1], (self.begin, envelope), self.trace[3]])
        purpose = identity.IssuerReply()
        purpose.CopyFrom(self.begin_reply)
        purpose.challenge.signing_request.purpose = keys.ACTOR_AUTHENTICATION
        self.assertRaises(AssertionError, self.check,
                          [self.trace[0], self.trace[1], (self.begin, purpose), self.trace[3]])
        proof_request = identity.IssuerRequest()
        proof_request.CopyFrom(self.complete)
        proof_request.authority_launch_complete.proof.signature = b"bad"
        self.assertRaises(AssertionError, self.check,
                          [*self.trace[:3], (proof_request, self.complete_reply)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
