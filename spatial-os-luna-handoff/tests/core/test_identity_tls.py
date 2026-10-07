"""Independent TLS peers exercise the real issuer and its existing proofs."""
import os
import socket
import ssl
import struct
import subprocess
import time
import unittest

from google.protobuf import text_format
import core_pb2 as core
import issuer_pb2 as pb
from harness.wire import frame
from test_identity_enrollment import EnrollmentFixture
from test_identity_issuer import BINARY


class Peer:
    def __init__(self, stream):
        self.stream = stream
        self.sequence = 0

    def close(self):
        self.stream.close()

    def receive(self):
        def exact(size):
            data = b''
            while len(data) < size:
                part = self.stream.recv(size-len(data))
                if not part: raise EOFError('Issuer TLS stream closed')
                data += part
            return data
        length = struct.unpack('!I', exact(4))[0]
        if length > 1048576: raise AssertionError('Excessive issuer reply')
        return pb.IssuerReply.FromString(exact(length))

    def request(self, request):
        self.sequence += 1; request.request_id = self.sequence
        self.stream.sendall(frame(request))
        reply = self.receive()
        if reply.protocol_version != 1 or reply.request_id != self.sequence:
            raise AssertionError('Wrong issuer reply identity')
        return reply

    def call(self, **operation):
        return self.request(pb.IssuerRequest(protocol_version=1, **operation))


class TlsFixture(EnrollmentFixture):
    def setUp(self):
        super().setUp()
        self.info = self.init()
        self.server_key, self.server_cert = self.certificate('server', 'issuer.test')
        self.service_key, self.service_cert = self.certificate('service', 'service.test')
        self.config = pb.IssuerTlsServerConfig(protocol_version=1,
            certificate_path=str(self.server_cert), private_key_path=str(self.server_key),
            client_ca_path=str(self.service_cert), io_timeout_ms=700, idle_timeout_ms=3000,
            connection_limit=8)
        self.config_path = self.root/'tls.textproto'
        self.daemons = []
        self.addCleanup(self.stop_daemons)

    def certificate(self, name, dns):
        key, _ = self.key(name)
        cert = self.root/(name+'.crt')
        self.openssl('req', '-x509', '-new', '-key', key, '-out', cert, '-days', '2',
                     '-subj', '/CN='+dns, '-addext', 'subjectAltName=DNS:'+dns)
        cert.chmod(0o644)
        return key, cert

    def launch(self, address='127.0.0.1'):
        self.config_path.write_text(text_format.MessageToString(self.config))
        self.config_path.chmod(0o600)
        listener = socket.socket(); listener.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        listener.bind((address, 0)); listener.listen(32)
        self.port = listener.getsockname()[1]
        log = (self.root/f'tls-{len(self.daemons)}.log').open('wb')
        proc = subprocess.Popen([str(BINARY), '--state-dir', str(self.state),
                '--listen-fd', str(listener.fileno()), '--tls-config', str(self.config_path)],
                pass_fds=(listener.fileno(),), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log)
        listener.close(); self.daemons.append((proc, log))
        return proc

    def stop_daemons(self):
        for proc, log in self.daemons:
            if proc.poll() is None:
                proc.stdin.close()
                try: proc.wait(timeout=3)
                except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=3); raise
            for stream in (proc.stdin, proc.stdout, log):
                if not stream.closed: stream.close()

    def context(self, identity=True, trust=None):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        ctx.maximum_version = ssl.TLSVersion.TLSv1_3
        ctx.load_verify_locations(cafile=str(trust or self.server_cert))
        if identity: ctx.load_cert_chain(str(self.service_cert), str(self.service_key))
        return ctx

    def connect(self, context=None, hostname='issuer.test', receive_buffer=None):
        raw = socket.socket(); raw.settimeout(2)
        if receive_buffer: raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, receive_buffer)
        raw.connect(('127.0.0.1', self.port))
        try: peer = Peer((context or self.context()).wrap_socket(raw, server_hostname=hostname))
        except BaseException: raw.close(); raise
        self.addCleanup(peer.close)
        return peer

    def ready(self):
        proc = self.launch(); deadline = time.monotonic()+3
        while True:
            self.assertIsNone(proc.poll(), 'TLS issuer exited before accepting a connection')
            try:
                peer = self.connect()
                self.assertEqual(peer.call(info=pb.IssuerInfoRequest()).info, self.info)
                return peer
            except (ConnectionError, ssl.SSLError):
                if time.monotonic() > deadline: raise
                time.sleep(.02)

    def closed(self, peer):
        try:
            peer.stream.sendall(frame(pb.IssuerRequest(protocol_version=1, info=pb.IssuerInfoRequest())))
            result = peer.stream.recv(4)
            self.assertEqual(result, b'', 'Rejected peer received an application response')
        except (ssl.SSLError, ConnectionError, EOFError): pass


class TlsTests(TlsFixture):
    def test_mutual_tls_reaches_production_issuer(self):
        peer = self.ready()
        self.assertEqual(peer.stream.version(), 'TLSv1.3')
        hello = peer.call(hello=core.HelloRequest()).hello
        self.assertEqual(hello.implementation, 'production')
        self.assertEqual(hello.protocol, 'spatial-identity-protobuf/1')
        self.assertFalse(peer.stream.session.has_ticket)

    def test_service_certificate_does_not_replace_owner_proof(self):
        peer = self.ready(); challenge = self.challenge(peer)
        self.denied(self.execute(peer, challenge, self.proof(challenge, self.service_key,
            self.openssl('pkey', '-in', self.service_key, '-pubout', '-outform', 'DER')[12:])))
        self.assertEqual(self.policy(peer).policy.mode, pb.MANUAL)

    def test_connections_share_enrollment_authentication_and_revocation(self):
        first = self.ready(); second = self.connect()
        pending = self.enroll(first); approved = self.decide(second, pending).enrollment
        claims = self.claims(approved, self.info)
        challenge = first.call(authentication_begin=pb.AuthenticationBegin(credential=approved.credential,
            audience='world/test', channel_binding=b'x'*32)).challenge
        self.assertEqual(len(challenge.nonce), 32)
        result = second.call(authentication_complete=pb.AuthenticationComplete(nonce=challenge.nonce,
            proof=self.proof(challenge, self.actor_key, self.actor_public))).authentication
        self.assertEqual(result.identity.actor_id, claims.actor_id)
        self.assertTrue(result.identity.active)
        operation = pb.IssuerOperation(identity_revoke=pb.IdentityRevoke(
            target=pb.IdentityReference(kind=pb.CREDENTIAL_TARGET, id=claims.credential_id),
            expected_security_revision=0, reason='TLS regression'))
        self.assertTrue(self.execute(second, self.challenge(second, operation)).identity_status.credential_revoked)
        self.assertFalse(first.call(credential_check=approved.credential).identity_status.active)

    def test_missing_service_certificate_rejected(self):
        good = self.ready()
        try: self.closed(self.connect(self.context(identity=False)))
        except ssl.SSLError: pass
        self.assertEqual(good.call(info=pb.IssuerInfoRequest()).info, self.info)

    def test_untrusted_service_certificate_rejected(self):
        good = self.ready(); key, cert = self.certificate('untrusted', 'untrusted.test')
        ctx = self.context(identity=False); ctx.load_cert_chain(str(cert), str(key))
        try: self.closed(self.connect(ctx))
        except ssl.SSLError: pass
        self.assertEqual(good.call(info=pb.IssuerInfoRequest()).info, self.info)

    def test_expired_service_certificate_rejected(self):
        self.openssl('x509', '-in', self.service_cert, '-signkey', self.service_key,
            '-days', '-1', '-out', self.service_cert)
        self.launch()
        with self.assertRaises((ssl.SSLError, EOFError, ConnectionError)):
            self.connect().call(info=pb.IssuerInfoRequest())

    def test_independent_client_rejects_wrong_server_name_and_trust(self):
        self.ready()
        with self.assertRaises(ssl.SSLCertVerificationError): self.connect(hostname='wrong.test')
        with self.assertRaises(ssl.SSLCertVerificationError): self.connect(self.context(trust=self.service_cert))

    def test_tls12_and_plaintext_rejected(self):
        good = self.ready(); ctx = self.context()
        ctx.minimum_version = ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        with self.assertRaises(ssl.SSLError): self.connect(ctx)
        raw = socket.create_connection(('127.0.0.1', self.port), timeout=2); self.addCleanup(raw.close)
        raw.sendall(frame(pb.IssuerRequest(protocol_version=1, info=pb.IssuerInfoRequest())))
        # TLS may send an alert, but never an issuer reply.
        try: response = raw.recv(4096)
        except ConnectionError: response = b''
        self.assertTrue(not response or response[0] == 21, response)
        self.assertEqual(good.call(info=pb.IssuerInfoRequest()).info, self.info)

    def test_partial_and_pipelined_frames_keep_request_identity(self):
        peer = self.ready()
        data = frame(pb.IssuerRequest(protocol_version=1, request_id=41, info=pb.IssuerInfoRequest()))
        for part in (data[:1], data[1:3], data[3:5], data[5:]): peer.stream.sendall(part)
        self.assertEqual(peer.receive().request_id, 41)
        peer.stream.sendall(b''.join(frame(pb.IssuerRequest(protocol_version=1, request_id=i,
            info=pb.IssuerInfoRequest())) for i in range(12)))
        for i in range(12):
            reply = peer.receive(); self.assertEqual(reply.request_id, i); self.assertEqual(reply.info, self.info)

    def test_malformed_unknown_and_wrong_version_preserve_channel(self):
        peer = self.ready(); peer.stream.sendall(struct.pack('!I', 1)+b'\xff')
        reply = peer.receive(); self.assertEqual(reply.request_id, 0); self.denied(reply, core.INVALID_ARGUMENT)
        payload = pb.IssuerRequest(protocol_version=1, request_id=22, info=pb.IssuerInfoRequest()).SerializeToString()+b'\xf8\x07\x01'
        peer.stream.sendall(struct.pack('!I', len(payload))+payload)
        reply = peer.receive(); self.assertEqual(reply.request_id, 22); self.denied(reply, core.INVALID_ARGUMENT)
        self.denied(peer.request(pb.IssuerRequest(protocol_version=2, info=pb.IssuerInfoRequest())), core.UNSUPPORTED_VERSION)
        self.assertEqual(peer.call(info=pb.IssuerInfoRequest()).info, self.info)

    def test_excessive_frame_closes_only_offending_peer(self):
        good = self.ready(); bad = self.connect()
        bad.stream.sendall(struct.pack('!I', 1048577)); self.closed(bad)
        self.assertEqual(good.call(info=pb.IssuerInfoRequest()).info, self.info)

    def test_maximum_frame_crosses_tls_record_boundaries(self):
        peer = self.ready()
        # An unknown length-delimited protobuf field fills exactly the legal frame limit.
        payload = b'\xfa\x07\xfb\xff\x3f'+b'x'*(1048576-5)
        peer.stream.sendall(struct.pack('!I', len(payload))+payload)
        self.denied(peer.receive(), core.INVALID_ARGUMENT)
        self.assertEqual(peer.call(info=pb.IssuerInfoRequest()).info, self.info)

    def test_partial_frame_deadline_is_not_refreshed_by_progress(self):
        good = self.ready(); bad = self.connect()
        bad.stream.sendall(b'\x00'); time.sleep(.4); bad.stream.sendall(b'\x00'); time.sleep(.4)
        self.closed(bad)
        self.assertEqual(good.call(info=pb.IssuerInfoRequest()).info, self.info)

    def test_stalled_handshake_does_not_block_other_peers(self):
        good = self.ready(); raw = socket.create_connection(('127.0.0.1', self.port), timeout=2)
        self.addCleanup(raw.close)
        started = time.monotonic()
        self.assertEqual(good.call(info=pb.IssuerInfoRequest()).info, self.info)
        self.assertLess(time.monotonic()-started, .5)
        time.sleep(.8); self.assertEqual(raw.recv(1), b'')

    def test_idle_timeout_reclaims_peer(self):
        self.config.idle_timeout_ms = 100
        peer = self.ready(); time.sleep(.25); self.closed(peer)
        self.assertEqual(self.connect().call(info=pb.IssuerInfoRequest()).info, self.info)

    def test_stalled_reader_hits_write_deadline_without_blocking_other_peer(self):
        self.config.io_timeout_ms = 150; self.config.idle_timeout_ms = 30000
        good = self.ready(); bad = self.connect(receive_buffer=1024)
        request = frame(pb.IssuerRequest(protocol_version=1, hello=core.HelloRequest()))
        bad.stream.sendall(request*256)
        time.sleep(3)
        self.assertEqual(good.call(info=pb.IssuerInfoRequest()).info, self.info)
        # Drain previously accepted bytes. Idle expiry cannot occur during this test.
        deadline = time.monotonic()+2
        try:
            while bad.stream.recv(4096):
                self.assertLess(time.monotonic(), deadline, 'Stalled TLS writer did not close')
        except (ssl.SSLError, ConnectionError): pass

    def test_connection_limit_rejects_excess_peer_then_recovers(self):
        self.config.connection_limit = 1
        good = self.ready()
        with self.assertRaises((ssl.SSLError, ConnectionError)): self.connect()
        self.assertEqual(good.call(info=pb.IssuerInfoRequest()).info, self.info)
        good.close(); time.sleep(.05)
        self.assertEqual(self.connect().call(info=pb.IssuerInfoRequest()).info, self.info)

    def test_disconnect_mid_payload_does_not_kill_issuer(self):
        good = self.ready(); bad = self.connect()
        bad.stream.sendall(struct.pack('!I', 100)+b'\x08'); bad.close()
        self.assertEqual(good.call(info=pb.IssuerInfoRequest()).info, self.info)

    def test_supervisor_eof_stops_listener_and_all_peers(self):
        peer = self.ready(); proc, _ = self.daemons[-1]
        proc.stdin.close(); self.assertEqual(proc.wait(timeout=2), 0); self.closed(peer)
        with self.assertRaises(ConnectionRefusedError): socket.create_connection(('127.0.0.1', self.port), timeout=1)

    def test_private_key_permissions_and_links_fail_closed(self):
        for mode in (0o644, 0o660):
            self.server_key.chmod(mode); proc = self.launch(); self.assertEqual(proc.wait(timeout=2), 2)
        self.server_key.chmod(0o600)
        linked = self.root/'key-link.pem'; linked.symlink_to(self.server_key)
        self.config.private_key_path = str(linked)
        self.assertEqual(self.launch().wait(timeout=2), 2)
        linked.unlink(); os.link(self.server_key, linked)
        self.config.private_key_path = str(self.server_key)
        self.assertEqual(self.launch().wait(timeout=2), 2)

    def test_invalid_configuration_and_key_mismatch_fail_before_accept(self):
        original = pb.IssuerTlsServerConfig(); original.CopyFrom(self.config)
        for field, value in [('protocol_version', 2), ('io_timeout_ms', 0), ('idle_timeout_ms', 0),
                             ('connection_limit', 0), ('connection_limit', 129), ('certificate_path', 'relative'),
                             ('client_ca_path', str(self.root/'missing')), ('private_key_path', str(self.service_key))]:
            self.config.CopyFrom(original); setattr(self.config, field, value)
            self.assertEqual(self.launch().wait(timeout=2), 2, field)

    def test_encrypted_private_key_fails_without_prompting(self):
        encrypted = self.root/'encrypted.pem'
        self.openssl('pkey', '-in', self.server_key, '-aes256', '-passout', 'pass:fixture-only', '-out', encrypted)
        encrypted.chmod(0o600); self.config.private_key_path = str(encrypted)
        self.assertEqual(self.launch().wait(timeout=2), 2)

    def test_listener_requires_tcp_listening_socket(self):
        self.config_path.write_text(text_format.MessageToString(self.config)); self.config_path.chmod(0o600)
        a, b = socket.socketpair(); self.addCleanup(a.close); self.addCleanup(b.close)
        result = subprocess.run([str(BINARY), '--state-dir', str(self.state), '--listen-fd', str(a.fileno()),
            '--tls-config', str(self.config_path)], pass_fds=(a.fileno(),), input=b'', capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__': unittest.main(verbosity=2)
