"""Cross-host world checks with actor keys generated and retained on their own host."""
import io
import os
import re
import shlex
import struct
import subprocess
import unittest

import key_pb2 as keys
from harness.wire import frame, read_message
from test_identity_issuer import DOMAIN
import test_world_shared_crosshost as baseline


class RemoteActorKey:
    def __init__(self, fixture):
        self.fixture = fixture
        self.host = os.environ['SPATIAL_REMOTE_HOST']
        if self.host not in ('sb16', 'sb16-2'):
            raise ValueError('Unauthorized actor key host')
        self.binary = os.environ['SPATIAL_REMOTE_KEY_BINARY']
        self.directory = subprocess.check_output(['ssh', self.host,
            'umask 077; mktemp -d /tmp/spatial-m05-key-XXXXXXXX'], text=True, timeout=10).strip()
        if not self.directory.startswith('/tmp/spatial-m05-key-') or '/' in self.directory[5:]:
            raise ValueError('Invalid remote key directory')
        self.path = self.directory + '/actor.pem'
        fixture.addCleanup(self.close)
        reply = self.call('generate')
        fixture.assertTrue(reply.HasField('public_key'), reply)
        self.public = reply.public_key.ed25519
        fixture.assertEqual(len(self.public), 32)
        self.record('generated', self.public.hex())
        mode = subprocess.check_output(['ssh', self.host,
            shlex.join(['stat', '-c', '%a', self.path])], text=True, timeout=10).strip()
        fixture.assertEqual(mode, '600')

    def record(self, operation, detail):
        with (self.fixture.root / 'remote-actor-key.log').open('a') as log:
            log.write(f'{self.host} {operation} {detail}\n')

    def call(self, operation, request=None):
        command = shlex.join([self.binary, operation, '--key', self.path])
        result = subprocess.run(['ssh', self.host, command], input=frame(request) if request else b'',
                                capture_output=True, timeout=10)
        self.fixture.assertEqual(result.returncode, 0, result.stderr)
        self.fixture.assertNotIn(b'PRIVATE KEY', result.stdout)
        stream = io.BytesIO(result.stdout)
        reply = read_message(stream, keys.KeyReply)
        self.fixture.assertEqual(stream.read(), b'')
        self.fixture.assertEqual(reply.protocol_version, 1)
        self.fixture.assertFalse(reply.HasField('error'), reply)
        return reply

    def sign(self, request):
        reply = self.call('sign', request)
        self.fixture.assertTrue(reply.HasField('proof'), reply)
        self.fixture.assertEqual(reply.proof.public_key, self.public)
        self.fixture.assertEqual(reply.proof.purpose, request.purpose)
        # Verify the remote production signature with independent OpenSSL code.
        public = self.fixture.root / 'remote-public.der'
        data = self.fixture.root / 'remote-signing.bin'
        signature = self.fixture.root / 'remote-signature.bin'
        public.write_bytes(bytes.fromhex('302a300506032b6570032100') + self.public)
        data.write_bytes(DOMAIN + struct.pack('!II', request.purpose, len(request.payload)) + request.payload)
        signature.write_bytes(reply.proof.signature)
        self.fixture.openssl('pkeyutl', '-verify', '-rawin', '-pubin', '-inkey', public,
                             '-keyform', 'DER', '-in', data, '-sigfile', signature)
        self.record('verified-signature-purpose', str(request.purpose))
        return reply.proof

    def close(self):
        command = shlex.join(['rm', '-f', '--', self.path]) + ' && ' + shlex.join(['rmdir', '--', self.directory])
        subprocess.run(['ssh', self.host, command], check=True, timeout=10)
        self.record('removed', 'temporary actor key')


class RemoteKeyWorldTests(baseline.SharedCrossHostTests):
    def setUp(self):
        super().setUp()
        # Finish worlds before the inherited fixture copies their service logs.
        self.addCleanup(self.finish_worlds)

    def finish_worlds(self):
        for world in reversed(self.worlds):
            running = world.proc.poll() is None
            world.close()
            if running:
                self.assertEqual(world.proc.returncode, 0)
        for path in self.root.glob('*.log'):
            self.assertIsNone(re.search(r'ERROR: (AddressSanitizer|LeakSanitizer)|'
                r'SUMMARY: (AddressSanitizer|UndefinedBehaviorSanitizer)|runtime error:',
                path.read_text(errors='replace')), str(path))

    def key(self, name):
        if name != 'second':
            return super().key(name)
        remote = RemoteActorKey(self)
        return remote, remote.public

    def proof(self, challenge, key=None, public=None, purpose=None, payload=None):
        if not isinstance(key, RemoteActorKey):
            return super().proof(challenge, key, public, purpose, payload)
        self.assertEqual(public, key.public)
        content = challenge.signing_request
        return key.sign(keys.SignRequest(purpose=content.purpose if purpose is None else purpose,
                                        payload=content.payload if payload is None else payload))


if __name__ == '__main__':
    unittest.main(verbosity=2)
