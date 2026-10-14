"""Registered service proof on actual TLS connections; no execution activation."""
import sqlite3
import socket
import subprocess
import time
import unittest
from google.protobuf import text_format
import core_pb2 as core
import grants_pb2 as grants
import issuer_pb2 as pb
import key_pb2 as keys
from test_identity_tls import TlsFixture
from test_identity_issuer import BINARY


class ParticipantSessionTests(TlsFixture):
    def launch(self, address='127.0.0.1'):
        if not hasattr(self, 'challenge_ttl'):
            return super().launch(address)
        self.config_path.write_text(text_format.MessageToString(self.config));self.config_path.chmod(0o600)
        listener=socket.socket();listener.bind((address,0));listener.listen(8)
        self.port=listener.getsockname()[1]
        log=(self.root/f'tls-{len(self.daemons)}.log').open('wb')
        try:
            proc=subprocess.Popen([str(BINARY),'--state-dir',str(self.state),
                '--listen-fd',str(listener.fileno()),'--tls-config',str(self.config_path),
                '--challenge-ttl-ms',str(self.challenge_ttl)],pass_fds=(listener.fileno(),),
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log)
        finally:
            listener.close()
        self.daemons.append((proc,log));return proc

    def setUp(self):
        super().setUp()
        self.peer = self.ready()
        self.signer, self.public = self.key('registered-authority')
        self.registration = self.register()

    def register(self, identity=b'p'*32, public=None, domain='park', revision=0):
        value = pb.AuthorityParticipant(id=identity, public_key=public or self.public,
            domains=[grants.Domain(kind=grants.WORLD, id=domain)])
        operation = pb.IssuerOperation(participant_register=pb.AuthorityParticipantRegister(
            participant=value, expected_registry_revision=revision))
        reply = self.execute(self.peer, self.challenge(self.peer, operation))
        self.assertTrue(reply.HasField('participant'), reply)
        return reply.participant

    def request(self, identity=b'p'*32, revision=1, incarnation=b'i'*32):
        return pb.AuthorityAuthenticationBegin(participant_id=identity,
            registered_revision=revision, service_incarnation=incarnation)

    def service_begin(self, peer=None, request=None):
        reply = (peer or self.peer).call(authority_authentication_begin=request or self.request())
        self.assertTrue(reply.HasField('challenge'), reply)
        return reply.challenge

    def service_finish(self, challenge, peer=None, proof=None):
        return (peer or self.peer).call(authority_authentication_complete=pb.AuthorityAuthenticationComplete(
            nonce=challenge.nonce, proof=proof or self.proof(challenge, self.signer, self.public)))

    def payload(self, challenge):
        return pb.AuthorityAuthenticationChallenge.FromString(challenge.signing_request.payload)

    def snapshot_all(self):
        with sqlite3.connect(self.state/'identity.db') as db:
            names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {name:db.execute('SELECT * FROM '+name).fetchall() for name in names}

    def test_registered_service_proof_binds_issuer_process_and_tls_connection(self):
        before=self.snapshot_all();challenge=self.service_begin();payload=self.payload(challenge)
        self.assertEqual(challenge.signing_request.purpose,keys.AUTHORITY_PARTICIPATION)
        self.assertEqual(payload.protocol_version,1);self.assertEqual(payload.issuer_id,self.info.issuer_id)
        self.assertEqual(payload.nonce,challenge.nonce);self.assertEqual(payload.request,self.request())
        self.assertEqual(len(payload.issuer_incarnation),32);self.assertEqual(len(payload.connection_binding),32)
        self.assertGreaterEqual(payload.lifetime_ms,50);self.assertLessEqual(payload.lifetime_ms,60000)
        self.assertEqual(payload.audience,'identityd/authority-participation/v1')
        result=self.service_finish(challenge);self.assertTrue(result.HasField('authority_authentication'),result)
        result=result.authority_authentication
        self.assertEqual(result.issuer_id,self.info.issuer_id)
        self.assertEqual(result.issuer_incarnation,payload.issuer_incarnation)
        self.assertEqual(result.registration,self.registration)
        self.assertEqual(result.connection_binding,payload.connection_binding)
        self.assertEqual(result.service_incarnation,self.request().service_incarnation)
        self.assertEqual(result.security_revision,0)
        self.assertEqual(self.snapshot_all(),before);self.assertEqual(self.counts(),(0,0,0))

    def test_proof_from_one_connection_cannot_authenticate_another(self):
        challenge=self.service_begin();proof=self.proof(challenge,self.signer,self.public);other=self.connect()
        self.denied(self.service_finish(challenge,other,proof))
        self.denied(self.service_finish(challenge,self.peer,proof))
        fresh=self.service_begin(other)
        self.assertNotEqual(self.payload(challenge).connection_binding,self.payload(fresh).connection_binding)
        self.assertTrue(self.service_finish(fresh,other).HasField('authority_authentication'))

    def test_management_pipe_cannot_create_service_session(self):
        self.peer.close();self.stop_daemons();pipe=self.start()
        self.denied(pipe.call(authority_authentication_begin=self.request()),core.FORBIDDEN)
        self.denied(pipe.call(authority_authentication_complete=pb.AuthorityAuthenticationComplete(
            nonce=b'n'*32,proof=keys.KeyProof(purpose=keys.AUTHORITY_PARTICIPATION,
                public_key=self.public,signature=b's'*64))),core.FORBIDDEN)

    def test_actor_owner_and_transport_keys_do_not_prove_registered_service(self):
        transport_public=self.openssl('pkey','-in',self.service_key,'-pubout','-outform','DER')[12:]
        for key,public in ((self.actor_key,self.actor_public),(self.owner,self.owner_public),
                           (self.service_key,transport_public)):
            with self.subTest(public=public.hex()):
                challenge=self.service_begin();self.denied(self.service_finish(challenge,proof=self.proof(challenge,key,public)))
                self.denied(self.service_finish(challenge))

    def test_wrong_signature_purpose_cannot_complete_or_replay(self):
        for purpose in (keys.ACTOR_AUTHENTICATION,keys.ISSUER_ADMINISTRATION,keys.ENROLLMENT_PROOF):
            challenge=self.service_begin()
            self.denied(self.service_finish(challenge,proof=self.proof(challenge,self.signer,self.public,purpose=purpose)))
            self.denied(self.service_finish(challenge))

    def test_invalid_signature_consumes_nonce(self):
        challenge=self.service_begin();proof=self.proof(challenge,self.signer,self.public)
        proof.signature=bytes([proof.signature[0]^1])+proof.signature[1:]
        self.denied(self.service_finish(challenge,proof=proof));self.denied(self.service_finish(challenge))

    def test_completed_proof_cannot_replay(self):
        challenge=self.service_begin();proof=self.proof(challenge,self.signer,self.public)
        self.assertTrue(self.service_finish(challenge,proof=proof).HasField('authority_authentication'))
        self.denied(self.service_finish(challenge,proof=proof))

    def test_unknown_participant_and_wrong_registration_revision_fail(self):
        for request in (self.request(identity=b'x'*32),self.request(revision=2)):
            self.denied(self.peer.call(authority_authentication_begin=request))

    def test_malformed_begin_and_unknown_fields_fail_before_challenge(self):
        examples=[self.request(identity=b''),self.request(identity=b'x'),self.request(revision=0),
            self.request(revision=2**63),self.request(incarnation=b''),self.request(incarnation=b'x')]
        unknown=self.request();unknown.ParseFromString(unknown.SerializeToString()+b'\xf8\x07\x01');examples.append(unknown)
        for request in examples:self.denied(self.peer.call(authority_authentication_begin=request),core.INVALID_ARGUMENT)
        self.assertTrue(self.service_finish(self.service_begin()).HasField('authority_authentication'))

    def test_altered_challenge_binding_is_not_an_authority_proof(self):
        for field in ('issuer_id','issuer_incarnation','connection_binding','nonce'):
            challenge=self.service_begin();payload=self.payload(challenge);setattr(payload,field,b'x'*32)
            proof=self.proof(challenge,self.signer,self.public,payload=payload.SerializeToString())
            self.denied(self.service_finish(challenge,proof=proof))

    def test_altered_service_incarnation_or_audience_is_rejected(self):
        for field in ('incarnation','audience'):
            challenge=self.service_begin();payload=self.payload(challenge)
            if field=='incarnation':payload.request.service_incarnation=b'x'*32
            else:payload.audience='world/actor'
            self.denied(self.service_finish(challenge,proof=self.proof(challenge,self.signer,self.public,payload=payload.SerializeToString())))

    def test_disconnect_requires_fresh_connection_proof(self):
        challenge=self.service_begin();proof=self.proof(challenge,self.signer,self.public);self.peer.close()
        other=self.connect();self.denied(self.service_finish(challenge,other,proof))
        self.assertTrue(self.service_finish(self.service_begin(other),other).HasField('authority_authentication'))

    def test_restart_rejects_old_proof_and_changes_issuer_incarnation(self):
        challenge=self.service_begin();proof=self.proof(challenge,self.signer,self.public)
        self.peer.close();self.stop_daemons();self.peer=self.ready()
        self.denied(self.service_finish(challenge,proof=proof));fresh=self.service_begin()
        self.assertNotEqual(self.payload(fresh).issuer_incarnation,self.payload(challenge).issuer_incarnation)
        self.assertTrue(self.service_finish(fresh).HasField('authority_authentication'))

    def test_authenticated_connection_cannot_switch_service_identity(self):
        self.assertTrue(self.service_finish(self.service_begin()).HasField('authority_authentication'))
        _,public=self.key('another-service');self.register(b'q'*32,public,'home',1)
        self.denied(self.peer.call(authority_authentication_begin=self.request(b'q'*32,2)),core.BUSY)

    def test_earlier_pending_challenge_cannot_replace_authenticated_context(self):
        first=self.service_begin();second=self.service_begin(request=self.request(incarnation=b'j'*32))
        self.assertTrue(self.service_finish(first).HasField('authority_authentication'))
        self.denied(self.service_finish(second),core.BUSY)
        self.denied(self.service_finish(second))

    def test_new_connection_requires_its_own_proof(self):
        first=self.service_begin();self.assertTrue(self.service_finish(first).HasField('authority_authentication'))
        other=self.connect();self.denied(self.service_finish(first,other))
        second=self.service_begin(other)
        self.assertNotEqual(self.payload(first).connection_binding,self.payload(second).connection_binding)
        self.assertTrue(self.service_finish(second,other).HasField('authority_authentication'))

    def test_completion_reports_revocation_committed_after_challenge(self):
        actor=self.decide(self.peer,self.enroll(self.peer)).enrollment;claims=self.claims(actor,self.info)
        challenge=self.service_begin()
        operation=pb.IssuerOperation(identity_revoke=pb.IdentityRevoke(
            target=pb.IdentityReference(kind=pb.CREDENTIAL_TARGET,id=claims.credential_id),
            expected_security_revision=0,reason='service proof race'))
        revoked=self.execute(self.peer,self.challenge(self.peer,operation));self.assertFalse(revoked.identity_status.active)
        result=self.service_finish(challenge);self.assertTrue(result.HasField('authority_authentication'),result)
        self.assertEqual(result.authority_authentication.security_revision,1)

    def test_expired_service_proof_is_rejected_and_fresh_proof_succeeds(self):
        self.peer.close();self.stop_daemons();self.challenge_ttl=200;self.peer=self.ready()
        challenge=self.service_begin();proof=self.proof(challenge,self.signer,self.public)
        self.assertEqual(self.payload(challenge).lifetime_ms,200)
        time.sleep(.25)
        self.denied(self.service_finish(challenge,proof=proof))
        self.assertTrue(self.service_finish(self.service_begin()).HasField('authority_authentication'))

    def test_fresh_challenges_keep_connection_binding_and_change_nonce(self):
        first=self.service_begin();second=self.service_begin()
        self.assertNotEqual(first.nonce,second.nonce)
        self.assertEqual(self.payload(first).connection_binding,self.payload(second).connection_binding)
        self.assertEqual(self.payload(first).issuer_incarnation,self.payload(second).issuer_incarnation)

    def test_pending_service_challenges_are_bounded(self):
        challenges=[self.service_begin() for _ in range(256)]
        self.assertEqual(len({c.nonce for c in challenges}),256)
        self.denied(self.peer.call(authority_authentication_begin=self.request()),core.BUSY)
        wrong=self.proof(challenges[0],self.actor_key,self.actor_public)
        self.denied(self.service_finish(challenges[0],proof=wrong))
        self.assertTrue(self.service_finish(self.service_begin()).HasField('authority_authentication'))


if __name__=='__main__':unittest.main(verbosity=2)
