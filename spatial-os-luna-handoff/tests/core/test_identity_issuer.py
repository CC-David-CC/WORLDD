"""Production issuer tests with independent signatures and SQLite inspection."""
import io
import os
from pathlib import Path
import select
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import unittest

import core_pb2 as core
import issuer_pb2 as pb
import key_pb2 as keys
from harness.wire import frame, read_message

BINARY = Path(sys.argv.pop(1))
DOMAIN = b'spatial.identity.key-proof/v1\0'


class Connection:
    def __init__(self, state, log, *extra):
        self.stderr = log.open('wb')
        self.proc = subprocess.Popen([str(BINARY), '--state-dir', str(state), *extra], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=self.stderr, start_new_session=True)
        self.sequence = 0

    def close(self, crash=False):
        if self.proc.poll() is None:
            if crash: self.proc.kill()
            else: self.proc.stdin.close()
            try: self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired: self.proc.kill(); self.proc.wait(timeout=3); raise
        for stream in (self.proc.stdin, self.proc.stdout, self.stderr):
            if not stream.closed: stream.close()

    def receive(self):
        def exact(size):
            result = b''; deadline = time.monotonic()+4
            while len(result) < size:
                if not select.select([self.proc.stdout], [], [], max(0, deadline-time.monotonic()))[0]:
                    raise AssertionError('Issuer response deadline expired')
                data = os.read(self.proc.stdout.fileno(), size-len(result))
                if not data: raise AssertionError('Issuer transport closed')
                result += data
            return result
        size = struct.unpack('!I', exact(4))[0]
        if size > 1048576: raise AssertionError('Excessive issuer response')
        reply = pb.IssuerReply(); reply.ParseFromString(exact(size)); return reply

    def request(self, request):
        self.sequence += 1; request.request_id = self.sequence
        self.proc.stdin.write(frame(request)); self.proc.stdin.flush()
        reply = self.receive()
        if reply.request_id != self.sequence or reply.protocol_version != 1: raise AssertionError('Wrong reply identity')
        return reply

    def call(self, **operation):
        return self.request(pb.IssuerRequest(protocol_version=1, **operation))


class IssuerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='spatial-issuer-')
        self.addCleanup(self.temp.cleanup); self.root = Path(self.temp.name)
        self.state = self.root/'issuer'; self.state.mkdir(mode=0o700)
        self.owner, self.owner_public = self.key('owner')
        self.public_file = self.root/'owner.pb'
        self.public_file.write_bytes(frame(keys.KeyReply(protocol_version=1, public_key=keys.PublicKey(ed25519=self.owner_public))))
        self.connections = []
        self.addCleanup(self.close_all)

    def openssl(self, *args):
        p = subprocess.run(['openssl', *map(str, args)], capture_output=True, timeout=5)
        self.assertEqual(p.returncode, 0, p.stderr); return p.stdout

    def key(self, name):
        path = self.root/(name+'.pem')
        self.openssl('genpkey', '-algorithm', 'ED25519', '-out', path); path.chmod(0o600)
        public = self.openssl('pkey', '-in', path, '-pubout', '-outform', 'DER')
        self.assertEqual(public[:12], bytes.fromhex('302a300506032b6570032100'))
        return path, public[12:]

    def initialize(self, state=None, public=None):
        return subprocess.run([str(BINARY), '--init', '--state-dir', str(state or self.state),
                               '--owner-public', str(public or self.public_file)], capture_output=True, timeout=5)

    def init(self, state=None):
        result = self.initialize(state)
        self.assertEqual(result.returncode, 0, result.stderr)
        reply = read_message(io.BytesIO(result.stdout), pb.IssuerReply)
        self.assertEqual(reply.protocol_version, 1); self.assertTrue(reply.HasField('info'))
        self.assertNotIn(b'PRIVATE KEY', result.stdout+result.stderr)
        return reply.info

    def start(self, state=None, ttl=None):
        extra = [] if ttl is None else ['--challenge-ttl-ms', str(ttl)]
        client = Connection(state or self.state, self.root/f'daemon-{len(self.connections)}.log', *extra)
        self.connections.append(client)
        hello = client.call(hello=core.HelloRequest())
        self.assertEqual(hello.hello.implementation, 'production')
        self.assertEqual(hello.hello.protocol, 'spatial-identity-protobuf/1')
        self.assertIn('identity.issuer-control/v1', hello.hello.capabilities)
        return client

    def close_all(self):
        for client in self.connections: client.close()

    def operation(self, mode=None, revision=1, profile='evaluation'):
        if mode is None: return pb.IssuerOperation(policy_read=pb.PolicyRead())
        return pb.IssuerOperation(policy_set=pb.PolicySet(policy=pb.EnrollmentPolicy(mode=mode, bootstrap_profile=profile), expected_revision=revision))

    def challenge(self, client, operation=None):
        reply = client.call(challenge=operation or self.operation())
        self.assertTrue(reply.HasField('challenge'), reply)
        return reply.challenge

    def proof(self, challenge, key=None, public=None, purpose=None, payload=None):
        content = challenge.signing_request
        purpose = content.purpose if purpose is None else purpose
        payload = content.payload if payload is None else payload
        data, signature = self.root/'signing.bin', self.root/'signature.bin'
        data.write_bytes(DOMAIN+struct.pack('!II', purpose, len(payload))+payload)
        self.openssl('pkeyutl', '-sign', '-rawin', '-inkey', key or self.owner, '-in', data, '-out', signature)
        return keys.KeyProof(purpose=purpose, public_key=public or self.owner_public, signature=signature.read_bytes())

    def execute(self, client, challenge, proof=None):
        return client.call(execute=pb.IssuerExecute(nonce=challenge.nonce, proof=proof or self.proof(challenge)))

    def policy(self, client):
        reply = self.execute(client, self.challenge(client))
        self.assertTrue(reply.HasField('policy'), reply)
        return reply.policy

    def denied(self, reply, code=core.UNAUTHORIZED):
        self.assertEqual(reply.error.code, code, reply)

    def snapshot(self):
        with sqlite3.connect(f'file:{self.state}/identity.db?mode=ro', uri=True) as db:
            return db.execute('SELECT issuer_id,public_key,owner_public_key,revision,policy FROM issuer').fetchall()

    def test_explicit_initialization_creates_distinct_durable_issuer_and_manual_policy(self):
        info = self.init(); client = self.start()
        self.assertEqual(client.call(info=pb.IssuerInfoRequest()).info, info)
        self.assertEqual(len(info.issuer_id), 32); self.assertEqual(len(info.public_key), 32)
        self.assertNotEqual(info.public_key, self.owner_public)
        public = self.openssl('pkey', '-in', self.state/'issuer.pem', '-pubout', '-outform', 'DER')
        self.assertEqual(public[12:], info.public_key)
        policy = self.policy(client)
        self.assertEqual(policy.policy.mode, pb.MANUAL)
        self.assertEqual(policy.policy.bootstrap_profile, 'empty-v1'); self.assertEqual(policy.revision, 1)
        row, = self.snapshot()
        self.assertEqual(row[:4], (info.issuer_id, info.public_key, self.owner_public, 1))
        for name in ('issuer.pem','identity.db','identity.lock'):
            self.assertEqual((self.state/name).stat().st_mode & 0o777, 0o600)
        client.close(); again = self.start()
        self.assertEqual(again.call(info=pb.IssuerInfoRequest()).info, info)

    def test_initialization_and_startup_never_replace_missing_or_existing_identity(self):
        result = subprocess.run([str(BINARY),'--state-dir',str(self.state)], input=b'', capture_output=True, timeout=4)
        self.assertNotEqual(result.returncode, 0); self.assertFalse((self.state/'issuer.pem').exists())
        self.init(); before = self.snapshot(); secret = (self.state/'issuer.pem').read_bytes()
        self.assertNotEqual(self.initialize().returncode, 0)
        self.assertEqual(self.snapshot(), before); self.assertEqual((self.state/'issuer.pem').read_bytes(), secret)
        (self.state/'identity.db').rename(self.state/'saved.db')
        result = subprocess.run([str(BINARY),'--state-dir',str(self.state)], input=b'', capture_output=True, timeout=4)
        self.assertNotEqual(result.returncode, 0); self.assertFalse((self.state/'identity.db').exists())

    def test_challenge_binds_issuer_incarnation_owner_operation_and_audience(self):
        info = self.init(); client = self.start(); operation = self.operation(pb.APPROVE_ALWAYS)
        challenge = self.challenge(client, operation)
        payload = pb.IssuerChallenge(); payload.ParseFromString(challenge.signing_request.payload)
        self.assertEqual(challenge.signing_request.purpose, keys.ISSUER_ADMINISTRATION)
        self.assertEqual(payload.protocol_version, 1); self.assertEqual(payload.issuer_id, info.issuer_id)
        self.assertEqual(payload.owner_public_key, self.owner_public); self.assertEqual(payload.operation, operation)
        self.assertEqual(payload.nonce, challenge.nonce); self.assertEqual(len(payload.nonce),32)
        self.assertEqual(len(payload.incarnation),32); self.assertEqual(payload.lifetime_ms,30000)
        self.assertEqual(payload.audience,'identityd/admin/v1')
        second = self.challenge(client); self.assertNotEqual(second.nonce, challenge.nonce)

    def test_owner_policy_change_is_durable_and_observed_through_real_sqlite(self):
        self.init(); client = self.start()
        reply = self.execute(client, self.challenge(client, self.operation(pb.APPROVE_ALWAYS)))
        self.assertEqual(reply.policy.revision,2); self.assertEqual(reply.policy.policy.bootstrap_profile,'evaluation')
        row, = self.snapshot(); record = pb.EnrollmentPolicy(); record.ParseFromString(row[4])
        self.assertEqual(row[3],2); self.assertEqual(record,reply.policy.policy)
        client.close(crash=True); client = self.start()
        self.assertEqual(self.policy(client),reply.policy)
        changed = self.execute(client,self.challenge(client,self.operation(pb.MANUAL,2,'empty-v1')))
        self.assertEqual(changed.policy.revision,3); self.assertEqual(changed.policy.policy.mode,pb.MANUAL)

    def test_another_key_cannot_read_or_change_policy_and_attempt_is_consumed(self):
        self.init(); client=self.start(); key,public=self.key('outsider'); before=self.snapshot()
        for operation in (self.operation(),self.operation(pb.APPROVE_ALWAYS)):
            challenge=self.challenge(client,operation)
            self.denied(self.execute(client,challenge,self.proof(challenge,key,public)))
            self.denied(self.execute(client,challenge))
        self.assertEqual(self.snapshot(),before)

    def test_successful_proof_cannot_be_replayed(self):
        self.init(); client=self.start(); challenge=self.challenge(client,self.operation(pb.APPROVE_ALWAYS))
        proof=self.proof(challenge); first=self.execute(client,challenge,proof)
        self.assertEqual(first.policy.revision,2)
        self.denied(self.execute(client,challenge,proof)); self.assertEqual(self.policy(client).revision,2)

    def test_changed_operation_payload_and_purpose_do_not_authorize_stored_operation(self):
        self.init(); client=self.start(); before=self.snapshot()
        challenge=self.challenge(client,self.operation(pb.APPROVE_ALWAYS))
        altered=pb.IssuerChallenge(); altered.ParseFromString(challenge.signing_request.payload)
        altered.operation.policy_set.policy.bootstrap_profile='different'
        self.denied(self.execute(client,challenge,self.proof(challenge,payload=altered.SerializeToString())))
        challenge=self.challenge(client,self.operation(pb.APPROVE_ALWAYS))
        proof=self.proof(challenge,purpose=keys.ENROLLMENT_PROOF); proof.purpose=keys.ISSUER_ADMINISTRATION
        self.denied(self.execute(client,challenge,proof)); self.assertEqual(self.snapshot(),before)

    def test_proof_for_one_challenge_does_not_complete_another(self):
        self.init(); client=self.start(); first=self.challenge(client); second=self.challenge(client)
        self.denied(self.execute(client,second,self.proof(first)))
        self.assertTrue(self.execute(client,first).HasField('policy'))

    def test_real_monotonic_expiry_rejects_old_proof_and_new_challenge_works(self):
        self.init(); client=self.start(ttl=200); challenge=self.challenge(client); proof=self.proof(challenge)
        time.sleep(.3)
        self.denied(self.execute(client,challenge,proof))
        self.assertEqual(self.policy(client).revision,1)

    def test_restart_invalidates_pending_challenges_without_changing_policy(self):
        self.init(); client=self.start(); challenge=self.challenge(client,self.operation(pb.APPROVE_ALWAYS)); proof=self.proof(challenge)
        old=pb.IssuerChallenge(); old.ParseFromString(challenge.signing_request.payload)
        client.close(); client=self.start(); self.denied(self.execute(client,challenge,proof))
        new=pb.IssuerChallenge(); new.ParseFromString(self.challenge(client).signing_request.payload)
        self.assertNotEqual(new.incarnation,old.incarnation); self.assertEqual(self.policy(client).revision,1)

    def test_issuer_instances_do_not_accept_each_others_challenges(self):
        first=self.init(); client=self.start(); other=self.root/'other';other.mkdir(mode=0o700)
        second=self.init(other); remote=self.start(other); self.assertNotEqual(first.issuer_id,second.issuer_id)
        challenge=self.challenge(client,self.operation(pb.APPROVE_ALWAYS))
        self.denied(self.execute(remote,challenge)); self.assertEqual(self.policy(remote).revision,1)
        self.assertEqual(self.execute(client,challenge).policy.revision,2)

    def test_competing_revision_updates_have_one_winner(self):
        self.init();client=self.start()
        first=self.challenge(client,self.operation(pb.APPROVE_ALWAYS,1,'one'))
        second=self.challenge(client,self.operation(pb.MANUAL,1,'two'))
        self.assertEqual(self.execute(client,first).policy.revision,2)
        self.denied(self.execute(client,second),core.REVISION_CONFLICT)
        policy=self.policy(client);self.assertEqual(policy.revision,2);self.assertEqual(policy.policy.bootstrap_profile,'one')

    def test_invalid_policy_and_unknown_fields_do_not_change_state(self):
        self.init();client=self.start();before=self.snapshot()
        invalid=[self.operation(99),self.operation(pb.APPROVE_ALWAYS,0),self.operation(pb.MANUAL,1,''),
                 self.operation(pb.MANUAL,1,'x'*129),self.operation(pb.MANUAL,1,'a\0b'),pb.IssuerOperation()]
        for operation in invalid:self.denied(client.call(challenge=operation),core.INVALID_ARGUMENT)
        operation=self.operation(pb.APPROVE_ALWAYS)
        operation.policy_set.policy.ParseFromString(operation.policy_set.policy.SerializeToString()+b'\xa0\x06\x01')
        self.denied(client.call(challenge=operation),core.INVALID_ARGUMENT);self.assertEqual(self.snapshot(),before)

    def test_pending_challenges_are_bounded_and_restart_clears_them(self):
        self.init();client=self.start();nonces=set()
        for _ in range(256):nonces.add(self.challenge(client).nonce)
        self.assertEqual(len(nonces),256)
        self.denied(client.call(challenge=self.operation()),core.BUSY)
        self.assertEqual(client.call(info=pb.IssuerInfoRequest()).info.policy_revision,1)
        client.close();client=self.start();self.assertEqual(self.policy(client).revision,1)

    def test_only_one_process_can_own_one_issuer_directory(self):
        self.init();client=self.start()
        second=subprocess.run([str(BINARY),'--state-dir',str(self.state)],input=b'',capture_output=True,timeout=4)
        self.assertNotEqual(second.returncode,0)
        self.assertIn(b'authority',second.stderr);self.assertEqual(self.policy(client).revision,1)

    def test_unsafe_state_files_and_directory_are_rejected(self):
        self.state.chmod(0o755);self.assertNotEqual(self.initialize().returncode,0);self.state.chmod(0o700)
        self.init();db=self.state/'identity.db';db.chmod(0o644)
        result=subprocess.run([str(BINARY),'--state-dir',str(self.state)],input=b'',capture_output=True,timeout=4)
        self.assertNotEqual(result.returncode,0);db.chmod(0o600)
        db.rename(self.state/'saved.db');db.symlink_to(self.state/'saved.db')
        result=subprocess.run([str(BINARY),'--state-dir',str(self.state)],input=b'',capture_output=True,timeout=4)
        self.assertNotEqual(result.returncode,0)

    def test_unknown_database_or_mismatched_issuer_key_is_never_repaired_implicitly(self):
        self.init();db=self.state/'identity.db'
        with sqlite3.connect(db) as connection:connection.execute('PRAGMA user_version=99')
        before=db.read_bytes()
        result=subprocess.run([str(BINARY),'--state-dir',str(self.state)],input=b'',capture_output=True,timeout=4)
        self.assertNotEqual(result.returncode,0);self.assertEqual(db.read_bytes(),before)
        with sqlite3.connect(db) as connection:connection.execute('PRAGMA user_version=1')
        key=self.state/'issuer.pem';key.write_bytes(self.owner.read_bytes())
        result=subprocess.run([str(BINARY),'--state-dir',str(self.state)],input=b'',capture_output=True,timeout=4)
        self.assertNotEqual(result.returncode,0);self.assertEqual(key.read_bytes(),self.owner.read_bytes())

    def test_protocol_errors_preserve_request_identity_and_connection_usability(self):
        self.init();client=self.start()
        self.denied(client.request(pb.IssuerRequest(protocol_version=9,hello=core.HelloRequest())),core.UNSUPPORTED_VERSION)
        self.denied(client.call(),core.UNKNOWN_METHOD)
        client.proc.stdin.write(b'\0\0\0\x01\xff');client.proc.stdin.flush();reply=client.receive()
        self.assertEqual(reply.error.code,core.INVALID_ARGUMENT);self.assertEqual(reply.request_id,0)
        self.assertEqual(client.call(info=pb.IssuerInfoRequest()).info.policy_revision,1)
        client.proc.stdin.write(struct.pack('!I',1048577));client.proc.stdin.flush()
        self.assertNotEqual(client.proc.wait(timeout=3),0)


if __name__=='__main__':unittest.main(verbosity=2)
