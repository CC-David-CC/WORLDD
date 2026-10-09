"""Production issuer client against real identityd and independent TLS fault peers."""
from pathlib import Path
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import unittest

from google.protobuf import text_format
import core_pb2 as core
import issuer_pb2 as pb
import grants_pb2 as grants
from harness.wire import frame
from test_identity_tls import TlsFixture
from test_identity_issuer import Connection

CLIENT = Path(sys.argv.pop(1))


class ClientProcess(Connection):
    def __init__(self, config, log):
        if not CLIENT.is_file(): raise AssertionError('Production issuer client is missing')
        self.stderr = log.open('wb')
        self.proc = subprocess.Popen([str(CLIENT), '--config', str(config)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.stderr)
        self.sequence = 0


class FaultPeer:
    """Transport witness only. It does not simulate issuer authority or a world."""
    def __init__(self, fixture, response=None, startup=None, tls12=False, stall_handshake=False):
        self.fixture = fixture; self.response = response; self.startup = startup
        self.requests = []; self.connections = 0; self.error = None; self.name = None
        self.stop = threading.Event(); self.stream = None
        self.listener = socket.socket(); self.listener.bind(('127.0.0.1', 0)); self.listener.listen(4)
        self.listener.settimeout(.1); self.port = self.listener.getsockname()[1]
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version = self.context.maximum_version = ssl.TLSVersion.TLSv1_2 if tls12 else ssl.TLSVersion.TLSv1_3
        self.context.load_cert_chain(str(fixture.server_cert), str(fixture.server_key))
        self.context.load_verify_locations(str(fixture.service_cert)); self.context.verify_mode = ssl.CERT_REQUIRED
        self.context.set_servername_callback(lambda sock, name, ctx: setattr(self, 'name', name))
        self.stall_handshake = stall_handshake
        self.thread = threading.Thread(target=self.run, daemon=True); self.thread.start()
        fixture.addCleanup(self.close)

    def close(self):
        self.stop.set(); self.listener.close()
        stream = self.stream
        if stream:
            try: stream.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            stream.close()
        self.thread.join(3)
        if self.thread.is_alive(): raise AssertionError('TLS fault witness did not stop')
        if self.error: raise self.error

    def reply(self, request):
        reply = pb.IssuerReply(protocol_version=1, request_id=request.request_id)
        if request.HasField('hello'):
            reply.hello.CopyFrom(core.HelloReply(protocol='spatial-identity-protobuf/1', implementation='production',
                build_id='sha256:'+'a'*64, capabilities=['identity.issuer-control/v1', 'identity.enrollment/v1',
                'identity.revocation/v1', 'identity.authentication/v1']))
        elif request.HasField('info'): reply.info.CopyFrom(self.fixture.info)
        else: reply.error.CopyFrom(core.Error(code=core.FORBIDDEN, message='Transport witness rejection'))
        return reply

    def receive(self):
        def exact(size):
            data = b''
            while len(data) < size:
                part = self.stream.recv(size-len(data))
                if not part: raise EOFError()
                data += part
            return data
        size = struct.unpack('!I', exact(4))[0]
        if size > 1048576: raise AssertionError('Client sent excessive frame')
        return pb.IssuerRequest.FromString(exact(size))

    def run(self):
        try:
            while not self.stop.is_set():
                try: raw, _ = self.listener.accept()
                except socket.timeout: continue
                self.connections += 1; raw.settimeout(2); self.stream = raw
                if self.stall_handshake:
                    self.stop.wait(2); return
                try:
                    self.stream = self.context.wrap_socket(raw, server_side=True)
                    while not self.stop.is_set():
                        request = self.receive(); self.requests.append(request)
                        reply = self.reply(request)
                        operation = self.startup if len(self.requests) <= 2 else self.response
                        if operation: operation(self, request, reply)
                        else: self.stream.sendall(frame(reply))
                except (ssl.SSLError, ConnectionError, EOFError, socket.timeout): pass
                finally:
                    if self.stream: self.stream.close(); self.stream = None
        except OSError:
            if not self.stop.is_set(): self.error = AssertionError('Unexpected TLS witness socket failure')
        except BaseException as error: self.error = error


class ClientFixture(TlsFixture):
    def config_file(self, endpoint_port=None, **overrides):
        config = pb.IssuerTlsClientConfig(protocol_version=1, address='127.0.0.1', port=endpoint_port or self.port,
            server_name='issuer.test', server_ca_path=str(self.server_cert),
            certificate_path=str(self.service_cert), private_key_path=str(self.service_key),
            issuer_id=self.info.issuer_id, issuer_public_key=self.info.public_key, io_timeout_ms=700)
        for key, value in overrides.items(): setattr(config, key, value)
        path = self.root/'client.textproto'; path.write_text(text_format.MessageToString(config)); path.chmod(0o600)
        return path

    def client(self, endpoint_port=None, **overrides):
        client = ClientProcess(self.config_file(endpoint_port, **overrides), self.root/f'client-{len(self.connections)}.log')
        self.connections.append(client)
        return client

    def rejected(self, endpoint_port=None, **overrides):
        client = self.client(endpoint_port, **overrides)
        output, _ = client.proc.communicate(frame(pb.IssuerRequest(protocol_version=1,
            enrollment_begin=self.intent())), timeout=3)
        self.assertEqual(client.proc.returncode, 2)
        self.assertEqual(output, b'', 'Rejected service received or produced actor data')
        return client


class ClientTests(ClientFixture):
    def test_bootstrap_profile_publication_and_exact_read_reach_real_issuer(self):
        self.ready(); client = self.client()
        profile = pb.BootstrapProfile(id='starter')
        template = profile.grants.add(id='entry')
        template.terms.scope.domain.CopyFrom(grants.Domain(kind=grants.WORLD, id='park'))
        template.terms.rights.append(grants.WORLD_ENTER)
        operation = pb.IssuerOperation(profile_put=pb.BootstrapProfilePut(profile=profile, expected_revision=0))
        published = self.execute(client, self.challenge(client, operation))
        self.assertEqual(published.WhichOneof('outcome'), 'profile')
        self.assertEqual(published.profile.profile, profile)
        result = client.call(profile_read=pb.BootstrapProfileRead(id='starter', revision=1))
        self.assertEqual(result.profile, published.profile)
        self.assertEqual(result.profile.issuer_id, self.info.issuer_id)
        self.assertEqual(result.profile.revision, 1)

    def test_bootstrap_valid_fragmented_reply_and_error_keep_client_usable(self):
        def respond(peer, request, reply):
            if request.profile_read.id == 'starter':
                reply.profile.CopyFrom(pb.BootstrapProfileState(issuer_id=self.info.issuer_id,
                    revision=7, profile=pb.BootstrapProfile(id='starter')))
            data = frame(reply)
            for part in (data[:2], data[2:9], data[9:]): peer.stream.sendall(part)
        peer = FaultPeer(self, response=respond); client = self.client(peer.port)
        self.denied(client.call(profile_read=pb.BootstrapProfileRead(id='missing', revision=1)), core.FORBIDDEN)
        result = client.call(profile_read=pb.BootstrapProfileRead(id='starter', revision=7))
        self.assertEqual(result.profile.profile.id, 'starter'); self.assertEqual(result.profile.revision, 7)
        self.assertEqual(len(peer.requests), 4)

    def test_bootstrap_invalid_local_revision_does_not_send_or_poison(self):
        peer = FaultPeer(self); client = self.client(peer.port)
        for request in (pb.BootstrapProfileRead(id='starter'), pb.BootstrapProfileRead(id='starter', revision=0),
                        pb.BootstrapProfileRead(id='starter', revision=2**63), pb.BootstrapProfileRead(id='', revision=1)):
            self.denied(client.call(profile_read=request), core.INVALID_ARGUMENT)
        self.assertEqual(client.call(info=pb.IssuerInfoRequest()).info, self.info)
        self.assertEqual([r.WhichOneof('operation') for r in peer.requests], ['hello','info','info'])

    def test_bootstrap_substituted_or_malformed_reply_poisons_client(self):
        for mode in ('issuer','profile','revision','zero','overflow','missing','subject','domain','duplicate','oversized','unknown'):
            with self.subTest(mode=mode):
                def respond(peer, request, reply):
                    state = pb.BootstrapProfileState(issuer_id=self.info.issuer_id, revision=1,
                        profile=pb.BootstrapProfile(id='starter'))
                    template = state.profile.grants.add(id='entry')
                    template.terms.scope.domain.CopyFrom(grants.Domain(kind=grants.WORLD, id='park'))
                    template.terms.rights.append(grants.WORLD_ENTER)
                    if mode == 'issuer': state.issuer_id=b'x'*32
                    elif mode == 'profile': state.profile.id='other'
                    elif mode == 'revision': state.revision=2
                    elif mode == 'zero': state.revision=0
                    elif mode == 'overflow': state.revision=2**63
                    elif mode == 'missing': state.ClearField('profile')
                    elif mode == 'subject': template.terms.subject.CopyFrom(grants.ActorRef(issuer_id=b'i'*32, actor_id=b'a'*32))
                    elif mode == 'domain': template.terms.rights.append(grants.FILE_READ)
                    elif mode == 'duplicate': state.profile.grants.add().CopyFrom(template)
                    elif mode == 'oversized':
                        for i in range(5):
                            item = state.profile.grants.add(id=str(i), terms=template.terms)
                            item.terms.scope.components.extend([b'x'*256]*32)
                    elif mode == 'unknown': state.ParseFromString(state.SerializeToString()+b'\xf8\x07\x01')
                    reply.profile.CopyFrom(state); peer.stream.sendall(frame(reply))
                peer = FaultPeer(self, response=respond); client = self.client(peer.port)
                request = pb.IssuerRequest(protocol_version=1, profile_read=pb.BootstrapProfileRead(id='starter', revision=1))
                output, _ = client.proc.communicate(frame(request)*2, timeout=3)
                self.assertEqual(client.proc.returncode, 2); self.assertEqual(output, b'')
                self.assertEqual(len(peer.requests), 3); self.assertEqual(peer.connections, 1)

    def test_client_reaches_real_issuer_and_preserves_caller_request_ids(self):
        self.ready(); client = self.client()
        for request_id in (0, 18446744073709551615, 1):
            request = pb.IssuerRequest(protocol_version=1, request_id=request_id, info=pb.IssuerInfoRequest())
            client.proc.stdin.write(frame(request)); client.proc.stdin.flush()
            reply = client.receive(); self.assertEqual(reply.request_id, request_id); self.assertEqual(reply.info, self.info)

    def test_real_enrollment_authentication_and_revocation_use_checked_client(self):
        self.ready(); client = self.client()
        pending = self.enroll(client); approved = self.decide(client, pending).enrollment
        claims = self.claims(approved, self.info)
        challenge = client.call(authentication_begin=pb.AuthenticationBegin(credential=approved.credential,
            audience='client-world', channel_binding=b'b'*32)).challenge
        result = client.call(authentication_complete=pb.AuthenticationComplete(nonce=challenge.nonce,
            proof=self.proof(challenge, self.actor_key, self.actor_public))).authentication
        self.assertTrue(result.identity.active); self.assertEqual(result.identity.actor_id, claims.actor_id)
        self.assertEqual(result.audience, 'client-world'); self.assertEqual(result.channel_binding, b'b'*32)
        operation = pb.IssuerOperation(identity_revoke=pb.IdentityRevoke(
            target=pb.IdentityReference(kind=pb.CREDENTIAL_TARGET, id=claims.credential_id),
            expected_security_revision=0, reason='client regression'))
        self.assertTrue(self.execute(client, self.challenge(client, operation)).identity_status.credential_revoked)
        self.assertFalse(client.call(credential_check=approved.credential).identity_status.active)

    def test_valid_issuer_error_keeps_connection_usable(self):
        self.ready(); client = self.client()
        challenge = self.challenge(client)
        self.denied(self.execute(client, challenge, self.proof(challenge, self.actor_key, self.actor_public)))
        self.assertEqual(client.call(info=pb.IssuerInfoRequest()).info, self.info)

    def test_numeric_ipv6_endpoint_reaches_real_issuer(self):
        self.launch('::1'); client = self.client(address='::1')
        self.assertEqual(client.call(info=pb.IssuerInfoRequest()).info, self.info)

    def test_invalid_local_payload_does_not_poison_connection(self):
        self.ready(); client = self.client()
        client.proc.stdin.write(struct.pack('!I', 1)+b'\xff'); client.proc.stdin.flush()
        self.denied(client.receive(), core.INVALID_ARGUMENT)
        self.denied(client.request(pb.IssuerRequest(protocol_version=2, info=pb.IssuerInfoRequest())), core.UNSUPPORTED_VERSION)
        self.assertEqual(client.call(info=pb.IssuerInfoRequest()).info, self.info)

    def test_client_verifies_server_name(self):
        self.ready(); self.rejected(server_name='wrong.test')

    def test_client_verifies_explicit_server_trust(self):
        self.ready(); self.rejected(server_ca_path=str(self.service_cert))

    def test_client_rejects_expired_server_certificate(self):
        self.openssl('x509', '-in', self.server_cert, '-signkey', self.server_key, '-days', '-1', '-out', self.server_cert)
        self.launch(); self.rejected()

    def test_common_name_without_dns_san_is_rejected(self):
        self.openssl('req', '-x509', '-new', '-key', self.server_key, '-out', self.server_cert,
            '-days', '2', '-subj', '/CN=issuer.test')
        self.launch(); self.rejected()

    def test_client_certificate_must_be_trusted_by_issuer(self):
        self.ready(); key, cert = self.certificate('wrong-service', 'wrong-service.test')
        self.rejected(certificate_path=str(cert), private_key_path=str(key))

    def test_issuer_pins_are_checked_before_actor_data(self):
        for field in ('issuer_id', 'issuer_public_key'):
            with self.subTest(field=field):
                peer = FaultPeer(self); self.rejected(peer.port, **{field: b'x'*32})
                self.assertEqual([r.WhichOneof('operation') for r in peer.requests], ['hello', 'info'])

    def test_client_uses_sni_and_unique_internal_request_ids(self):
        peer = FaultPeer(self); client = self.client(peer.port)
        for _ in range(3): self.assertEqual(client.call(info=pb.IssuerInfoRequest()).info, self.info)
        self.assertEqual(peer.name, 'issuer.test')
        self.assertEqual([r.request_id for r in peer.requests], [1, 2, 3, 4, 5])

    def test_missing_production_capability_rejected_before_actor_data(self):
        def respond(peer, request, reply):
            if reply.HasField('hello'): del reply.hello.capabilities[:]
            peer.stream.sendall(frame(reply))
        peer = FaultPeer(self, startup=respond); self.rejected(peer.port)
        self.assertEqual(len(peer.requests), 1)

    def test_startup_reply_identity_is_checked(self):
        def respond(peer, request, reply):
            reply.request_id += 1; peer.stream.sendall(frame(reply))
        peer = FaultPeer(self, startup=respond); self.rejected(peer.port)
        self.assertEqual(len(peer.requests), 1)

    def test_bad_reply_envelopes_poison_client(self):
        def mutate(reply, mode):
            if mode == 'id': reply.request_id -= 1
            elif mode == 'version': reply.protocol_version = 2
            elif mode == 'outcome': reply.policy.CopyFrom(pb.PolicyState(revision=1))
            elif mode == 'empty': reply.ClearField('info')
            elif mode == 'error-zero': reply.error.SetInParent()
            elif mode == 'error-unknown': reply.error.code = 999
            elif mode == 'issuer-change': reply.info.issuer_id = b'x'*32
            payload = reply.SerializeToString()
            if mode == 'unknown-field': payload += b'\xf8\x07\x01'
            if mode == 'malformed': payload = b'\xff'
            return struct.pack('!I', len(payload))+payload
        for mode in ('id','version','outcome','empty','error-zero','error-unknown','issuer-change','unknown-field','malformed'):
            with self.subTest(mode=mode):
                peer = FaultPeer(self, response=lambda p, q, r: p.stream.sendall(mutate(r, mode)))
                client = self.client(peer.port)
                output, _ = client.proc.communicate(frame(pb.IssuerRequest(protocol_version=1, info=pb.IssuerInfoRequest())), timeout=3)
                self.assertEqual(client.proc.returncode, 2); self.assertEqual(output, b'')
                self.assertEqual(len(peer.requests), 3)

    def test_truncated_and_excessive_replies_fail_closed(self):
        for payload in (b'\x00\x00', struct.pack('!I', 1048577), struct.pack('!I', 100)+b'\x08'):
            with self.subTest(payload=payload):
                def respond(peer, request, reply):
                    peer.stream.sendall(payload); peer.stream.shutdown(socket.SHUT_RDWR)
                peer = FaultPeer(self, response=respond); client = self.client(peer.port)
                output, _ = client.proc.communicate(frame(pb.IssuerRequest(protocol_version=1, info=pb.IssuerInfoRequest())), timeout=3)
                self.assertEqual(client.proc.returncode, 2); self.assertEqual(output, b'')

    def test_fragmented_reply_is_received_without_losing_frame_identity(self):
        def respond(peer, request, reply):
            data = frame(reply)
            for part in (data[:1], data[1:3], data[3:7], data[7:]): peer.stream.sendall(part)
        peer = FaultPeer(self, response=respond); client = self.client(peer.port)
        self.assertEqual(client.call(info=pb.IssuerInfoRequest()).info, self.info)

    def test_reply_deadline_is_not_refreshed_by_partial_progress(self):
        def respond(peer, request, reply):
            for byte in frame(reply):
                peer.stream.sendall(bytes([byte]))
                if peer.stop.wait(.1): return
        peer = FaultPeer(self, response=respond); client = self.client(peer.port, io_timeout_ms=150)
        started = time.monotonic()
        output, _ = client.proc.communicate(frame(pb.IssuerRequest(protocol_version=1, info=pb.IssuerInfoRequest())), timeout=2)
        self.assertEqual(client.proc.returncode, 2); self.assertEqual(output, b''); self.assertLess(time.monotonic()-started, 1)

    def test_lost_reply_does_not_reconnect_or_replay(self):
        def respond(peer, request, reply): peer.stream.shutdown(socket.SHUT_RDWR)
        peer = FaultPeer(self, response=respond); client = self.client(peer.port)
        request = pb.IssuerRequest(protocol_version=1, enrollment_begin=self.intent())
        output, _ = client.proc.communicate(frame(request)*2, timeout=3)
        self.assertEqual(client.proc.returncode, 2); self.assertEqual(output, b'')
        self.assertEqual(peer.connections, 1); self.assertEqual(len(peer.requests), 3)

    def test_tcp_handshake_timeout_is_bounded(self):
        peer = FaultPeer(self, stall_handshake=True); started = time.monotonic()
        self.rejected(peer.port, io_timeout_ms=150)
        self.assertLess(time.monotonic()-started, 1)

    def test_tls12_has_no_fallback(self):
        peer = FaultPeer(self, tls12=True); self.rejected(peer.port); self.assertEqual(peer.requests, [])

    def test_invalid_config_does_not_connect(self):
        peer = FaultPeer(self)
        for key, value in [('address','localhost'),('address','127.0.0.1\0suffix'),('port',0),('port',65536),
            ('server_name',''),('server_name','.issuer.test'),('server_name','*.test'),('server_name','bad\0name'),
            ('server_name','127.0.0.1'),
            ('issuer_id',b''),('issuer_public_key',b'bad'),('io_timeout_ms',0),('protocol_version',2)]:
            with self.subTest(key=key, value=value): self.rejected(peer.port, **{key:value})
        self.assertEqual(peer.connections, 0)

    def test_unsafe_client_key_fails_before_connect(self):
        peer = FaultPeer(self); self.service_key.chmod(0o644); self.rejected(peer.port)
        self.assertEqual(peer.connections, 0)

    def test_tick_callback_cancels_and_poisons_library_connection(self):
        peer = FaultPeer(self, response=lambda p, q, r: p.stop.wait(2))
        result = subprocess.run([str(CLIENT.parent/'spatial-issuer-client-probe'), str(self.config_file(peer.port))],
            capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(peer.connections, 1); self.assertEqual(len(peer.requests), 3)


if __name__ == '__main__': unittest.main(verbosity=2)
