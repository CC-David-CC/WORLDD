"""Independent filesystem and OpenSSL witnesses for production identity keys."""
from concurrent.futures import ThreadPoolExecutor
import io
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import unittest

import key_pb2 as pb
from harness.wire import frame, read_message

BINARY = Path(sys.argv.pop(1))
PURPOSES = (pb.ENROLLMENT_PROOF, pb.ACTOR_AUTHENTICATION, pb.ISSUER_ADMINISTRATION, pb.ISSUER_CREDENTIAL)
DOMAIN = b'spatial.identity.key-proof/v1\0'


class IdentityKeyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='spatial-identity-keys-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = self.root/'actor.pem'

    def cli(self, command, key=None, message=None, raw=None, extra=()):
        argv = [str(BINARY), command]
        if key is not None: argv += ['--key', str(key)]
        process = subprocess.run(argv+list(extra), input=frame(message) if message is not None else raw or b'',
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        stream = io.BytesIO(process.stdout)
        reply = read_message(stream, pb.KeyReply)
        self.assertEqual(stream.read(), b'')
        self.assertEqual(reply.protocol_version, 1)
        self.assertNotIn(b'PRIVATE KEY', process.stdout+process.stderr)
        return process, reply

    def ok(self, command, key=None, message=None):
        process, reply = self.cli(command, key, message)
        self.assertEqual(process.returncode, 0, (process.stderr, reply))
        self.assertFalse(reply.HasField('error'))
        return reply

    def error(self, code, command, key=None, message=None, raw=None):
        process, reply = self.cli(command, key, message, raw)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(reply.error.code, code, reply)
        return reply

    def generate(self, key=None):
        return self.ok('generate', key or self.key).public_key.ed25519

    def content(self, purpose=pb.ACTOR_AUTHENTICATION, payload=b'challenge with \0 exact bytes'):
        return pb.SignRequest(purpose=purpose, payload=payload)

    def verify(self, content, proof, expected):
        result = self.ok('verify', message=pb.VerifyRequest(content=content, proof=proof, expected_public_key=expected))
        self.assertTrue(result.verification.HasField('valid'))
        return result.verification.valid

    def openssl(self, *argv):
        process = subprocess.run(['openssl', *map(str, argv)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8)
        self.assertEqual(process.returncode, 0, process.stderr)
        return process.stdout

    def transcript(self, content):
        return DOMAIN+struct.pack('!II', content.purpose, len(content.payload))+content.payload

    def test_distinct_keys_persist_with_private_permissions_and_external_public_keys(self):
        first, second = self.generate(), self.generate(self.root/'agent.pem')
        self.assertEqual(len(first), 32); self.assertNotEqual(first, second)
        self.assertEqual(self.ok('public', self.key).public_key.ed25519, first)
        info = self.key.stat()
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertEqual(info.st_uid, os.geteuid()); self.assertEqual(info.st_nlink, 1)
        encoded = self.openssl('pkey', '-in', self.key, '-pubout', '-outform', 'DER')
        self.assertEqual(encoded, bytes.fromhex('302a300506032b6570032100')+first)
        self.assertEqual({p.name for p in self.root.iterdir()}, {'actor.pem', 'agent.pem'})

    def test_generation_never_overwrites_an_existing_identity(self):
        public = self.generate(); before = self.key.read_bytes()
        self.error(7, 'generate', self.key)  # ALREADY_EXISTS
        self.assertEqual(self.key.read_bytes(), before)
        self.assertEqual(self.ok('public', self.key).public_key.ed25519, public)

    def test_concurrent_creation_has_one_winner_and_a_complete_key(self):
        def create(_): return self.cli('generate', self.key)
        with ThreadPoolExecutor(max_workers=8) as pool: results = list(pool.map(create, range(8)))
        self.assertEqual(sum(p.returncode == 0 for p, _ in results), 1)
        self.assertTrue(all(r.error.code == 7 for p, r in results if p.returncode))
        public = next(r.public_key.ed25519 for p, r in results if p.returncode == 0)
        self.assertEqual(self.ok('public', self.key).public_key.ed25519, public)
        self.assertEqual([p.name for p in self.root.iterdir()], ['actor.pem'])

    def test_missing_and_relative_paths_do_not_create_implicit_keys(self):
        self.error(6, 'public', self.key)  # NOT_FOUND
        self.error(6, 'sign', self.key, self.content())
        self.error(1, 'generate', Path('relative.pem'))  # INVALID_ARGUMENT
        self.assertFalse(self.key.exists())

    def test_shared_or_symlinked_key_directory_is_rejected(self):
        unsafe = self.root/'shared'; unsafe.mkdir(mode=0o755)
        self.error(13, 'generate', unsafe/'key.pem')  # FORBIDDEN
        private = self.root/'private'; private.mkdir(mode=0o700)
        alias = self.root/'alias'; alias.symlink_to(private, target_is_directory=True)
        self.error(13, 'generate', alias/'key.pem')
        self.assertEqual(list(private.iterdir()), [])

    def test_exposed_key_permissions_are_rejected_and_read_only_private_key_works(self):
        public = self.generate()
        for mode in (0o644, 0o640, 0o601):
            self.key.chmod(mode)
            self.error(13, 'public', self.key)
            self.error(13, 'sign', self.key, self.content())
        self.key.chmod(0o400)
        self.assertEqual(self.ok('public', self.key).public_key.ed25519, public)

    def test_key_symlink_hardlink_fifo_and_directory_are_rejected_without_hanging(self):
        self.generate(); before = self.key.read_bytes()
        alias = self.root/'alias.pem'; alias.symlink_to(self.key)
        self.error(13, 'public', alias)
        self.error(7, 'generate', alias)
        alias.unlink(); os.link(self.key, alias)
        self.error(13, 'public', self.key)
        alias.unlink(); os.mkfifo(alias, 0o600)
        self.error(13, 'public', alias)
        alias.unlink(); alias.mkdir(mode=0o700)
        self.error(13, 'public', alias)
        self.assertEqual(self.key.read_bytes(), before)

    def test_corrupt_oversized_and_trailing_key_data_are_rejected(self):
        self.generate(); valid = self.key.read_bytes()
        for data in (b'', b'not a key', b'x'*16385, valid+b'second content', b'junk\n'+valid):
            self.key.write_bytes(data)
            self.error(1, 'public', self.key)
        self.key.write_bytes(valid+b' \r\n\t')
        self.ok('public', self.key)

    def test_external_ed25519_keys_are_supported_and_other_algorithms_are_rejected(self):
        self.openssl('genpkey', '-algorithm', 'ED25519', '-out', self.key)
        self.key.chmod(0o600)
        self.assertEqual(len(self.ok('public', self.key).public_key.ed25519), 32)
        other = self.root/'rsa.pem'
        self.openssl('genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048', '-out', other)
        other.chmod(0o600)
        self.error(1, 'public', other)

    def test_production_signatures_match_independent_openssl_for_every_purpose(self):
        public = self.generate()
        data, signature = self.root/'input.bin', self.root/'signature.bin'
        public_file = self.root/'public.pem'
        self.openssl('pkey', '-in', self.key, '-pubout', '-out', public_file)
        for purpose in PURPOSES:
            content = self.content(purpose)
            proof = self.ok('sign', self.key, content).proof
            self.assertEqual(proof.public_key, public); self.assertEqual(proof.purpose, purpose)
            self.assertEqual(len(proof.signature), 64)
            data.write_bytes(self.transcript(content)); signature.write_bytes(proof.signature)
            self.openssl('pkeyutl', '-verify', '-rawin', '-pubin', '-inkey', public_file, '-in', data, '-sigfile', signature)
            self.openssl('pkeyutl', '-sign', '-rawin', '-inkey', self.key, '-in', data, '-out', signature)
            self.assertEqual(proof.signature, signature.read_bytes())
            self.assertTrue(self.verify(content, proof, public))

    def test_independent_signatures_verify_but_payload_key_and_signature_changes_fail(self):
        public, other = self.generate(), self.generate(self.root/'other.pem')
        content = self.content(); data = self.root/'input.bin'; data.write_bytes(self.transcript(content))
        signature = self.root/'signature.bin'
        self.openssl('pkeyutl', '-sign', '-rawin', '-inkey', self.key, '-in', data, '-out', signature)
        proof = pb.KeyProof(purpose=content.purpose, public_key=public, signature=signature.read_bytes())
        self.assertTrue(self.verify(content, proof, public))
        self.assertFalse(self.verify(content, proof, other))
        changed = self.content(payload=content.payload+b'x')
        self.assertFalse(self.verify(changed, proof, public))
        proof.signature = bytes([proof.signature[0]^1])+proof.signature[1:]
        self.assertFalse(self.verify(content, proof, public))
        proof.signature = signature.read_bytes(); proof.public_key = other
        self.assertFalse(self.verify(content, proof, public))

    def test_signatures_cannot_cross_purposes_even_when_both_claims_are_changed(self):
        public = self.generate(); content = self.content()
        proof = self.ok('sign', self.key, content).proof
        for purpose in (pb.ENROLLMENT_PROOF, pb.ISSUER_ADMINISTRATION, pb.ISSUER_CREDENTIAL):
            content.purpose = purpose
            self.assertFalse(self.verify(content, proof, public))
            proof.purpose = purpose
            self.assertFalse(self.verify(content, proof, public))

    def test_empty_and_maximum_payload_are_explicit_and_bounded(self):
        public = self.generate()
        for payload in (b'', b'\x00\xff'*32768):
            content = self.content(payload=payload)
            proof = self.ok('sign', self.key, content).proof
            self.assertTrue(self.verify(content, proof, public))
        self.error(1, 'sign', self.key, self.content(payload=b'x'*65537))
        self.error(1, 'sign', self.key, pb.SignRequest(purpose=pb.ACTOR_AUTHENTICATION))

    def test_unknown_fields_unknown_purposes_and_missing_proof_fields_fail_closed(self):
        public = self.generate(); content = self.content(); proof = self.ok('sign', self.key, content).proof
        for purpose in (0, 99): self.error(1, 'sign', self.key, self.content(purpose))
        malformed = pb.SignRequest(); malformed.ParseFromString(content.SerializeToString()+b'\xa0\x06\x01')
        self.error(1, 'sign', self.key, malformed)
        for request in (pb.VerifyRequest(), pb.VerifyRequest(content=content, proof=proof),
                        pb.VerifyRequest(content=content, expected_public_key=public)):
            self.error(1, 'verify', message=request)
        request = pb.VerifyRequest(content=content, proof=proof, expected_public_key=public)
        request.proof.ParseFromString(proof.SerializeToString()+b'\xa0\x06\x01')
        self.error(1, 'verify', message=request)

    def test_wrong_key_and_signature_lengths_are_rejected(self):
        public = self.generate(); content = self.content(); proof = self.ok('sign', self.key, content).proof
        for field, sizes in [('public_key', (0, 31, 33)), ('signature', (0, 63, 65))]:
            for size in sizes:
                bad = pb.KeyProof(); bad.CopyFrom(proof); setattr(bad, field, b'x'*size)
                self.error(1, 'verify', message=pb.VerifyRequest(content=content, proof=bad, expected_public_key=public))
        self.error(1, 'verify', message=pb.VerifyRequest(content=content, proof=proof, expected_public_key=b'x'*31))

    def test_signing_input_requires_exactly_one_complete_protobuf_frame(self):
        self.generate()
        valid = frame(self.content())
        for data in (b'', b'\0', b'\0\0\0\x05x', struct.pack('!I', 1048577), valid+valid, b'\0\0\0\x01\xff'):
            self.error(1, 'sign', self.key, raw=data)


if __name__ == '__main__':
    unittest.main(verbosity=2)
