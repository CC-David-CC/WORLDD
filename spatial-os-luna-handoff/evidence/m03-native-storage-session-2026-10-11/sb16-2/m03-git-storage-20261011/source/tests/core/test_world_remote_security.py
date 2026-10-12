"""Independent transport failures must not grant identity or disrupt another actor."""
import socket
import ssl
import struct
import time
import unittest
import core_pb2 as core
import world_session_pb2 as session
from harness.wire import frame
from test_world_remote import RemoteWorldTests,TlsActorPeer


class RemoteSecurityTests(RemoteWorldTests):
    def context(self,identity=True,trust=None):
        # Keep issuer fixture setup on its original certificate context.
        return super().context(identity,trust)

    def remote_context(self,world,identity=True,trust=None,version=ssl.TLSVersion.TLSv1_3):
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version=ctx.maximum_version=version
        ctx.load_verify_locations(cafile=str(trust or world.server_cert))
        if identity:ctx.load_cert_chain(str(self.service_cert),str(self.service_key))
        return ctx

    def connect_with(self,world,context,hostname='world.test'):
        raw=socket.create_connection(('127.0.0.1',world.port),timeout=2)
        try:stream=context.wrap_socket(raw,server_hostname=hostname)
        except BaseException:raw.close();raise
        peer=TlsActorPeer(stream);world.peers.append(peer);return peer

    def received(self,stream):
        def exact(size):
            data=b''
            while len(data)<size:
                part=stream.recv(size-len(data))
                if not part:raise EOFError('Remote stream closed')
                data+=part
            return data
        size=struct.unpack('!I',exact(4))[0];self.assertLessEqual(size,1048576)
        return session.WorldSessionReply.FromString(exact(size))

    def assert_closed(self,stream):
        try:self.assertEqual(stream.recv(4),b'')
        except (ssl.SSLError,ConnectionResetError,BrokenPipeError):pass

    def live_actor(self,world):
        peer=world.connect(self);self.assertTrue(self.authenticate(peer).HasField('opened'));return peer

    def test_plaintext_is_rejected_without_disrupting_an_actor(self):
        world=self.remote_world();actor=self.live_actor(world)
        raw=socket.create_connection(('127.0.0.1',world.port),timeout=2);self.addCleanup(raw.close)
        raw.sendall(frame(session.WorldSessionRequest(protocol_version=1,request_id=1,inspect=session.WorldSessionInspect())))
        self.assert_closed(raw)
        self.assertTrue(actor.call(inspect=session.WorldSessionInspect()).HasField('state'))

    def test_missing_client_certificate_is_rejected(self):
        world=self.remote_world()
        try:
            peer=self.connect_with(world,self.remote_context(world,identity=False))
            peer.stream.sendall(frame(session.WorldSessionRequest(protocol_version=1,request_id=1,inspect=session.WorldSessionInspect())))
            self.assert_closed(peer.stream)
        except (ssl.SSLError,ConnectionResetError,BrokenPipeError):pass
        self.assertEqual(len(world.management('world.list')['entities']),1)

    def test_untrusted_client_certificate_is_rejected(self):
        world=self.remote_world();key,cert=self.certificate('untrusted-client','stranger.test')
        context=self.remote_context(world,identity=False);context.load_cert_chain(str(cert),str(key))
        try:
            peer=self.connect_with(world,context)
            peer.stream.sendall(frame(session.WorldSessionRequest(protocol_version=1,request_id=1,inspect=session.WorldSessionInspect())))
            self.assert_closed(peer.stream)
        except (ssl.SSLError,ConnectionResetError,BrokenPipeError):pass
        self.assertEqual(len(world.management('world.list')['entities']),1)

    def test_server_name_mismatch_fails_verification(self):
        world=self.remote_world()
        with self.assertRaises(ssl.SSLCertVerificationError):
            self.connect_with(world,self.remote_context(world),hostname='wrong-world.test')

    def test_untrusted_server_fails_verification(self):
        world=self.remote_world()
        with self.assertRaises(ssl.SSLCertVerificationError):
            self.connect_with(world,self.remote_context(world,trust=self.server_cert))

    def test_tls12_cannot_reach_actor_dispatch(self):
        world=self.remote_world()
        with self.assertRaises((ssl.SSLError,ConnectionResetError)):
            self.connect_with(world,self.remote_context(world,version=ssl.TLSVersion.TLSv1_2))
        self.assertEqual(len(world.management('world.list')['entities']),1)

    def test_fragmented_frame_reaches_existing_actor(self):
        world=self.remote_world();peer=self.live_actor(world);peer.sequence+=1
        request=session.WorldSessionRequest(protocol_version=1,request_id=peer.sequence,inspect=session.WorldSessionInspect())
        data=frame(request);peer.stream.sendall(data[:2]);time.sleep(.04);peer.stream.sendall(data[2:])
        reply=self.received(peer.stream);self.assertEqual(reply.request_id,peer.sequence)
        self.assertTrue(reply.HasField('state'),reply)

    def test_excessive_frame_closes_only_its_remote_channel(self):
        world=self.remote_world();actor=self.live_actor(world);bad=world.connect(self)
        bad.stream.sendall(struct.pack('!I',1048577));self.assert_closed(bad.stream)
        self.assertTrue(actor.call(inspect=session.WorldSessionInspect()).HasField('state'))

    def test_stalled_partial_frame_expires_without_harming_an_actor(self):
        world=self.remote_world();actor=self.live_actor(world);bad=world.connect(self)
        bad.stream.sendall(b'\x00\x00');time.sleep(.85);self.assert_closed(bad.stream)
        self.assertTrue(actor.call(inspect=session.WorldSessionInspect()).HasField('state'))

    def test_malformed_protobuf_does_not_authenticate_and_connection_recovers(self):
        world=self.remote_world();peer=world.connect(self)
        peer.stream.sendall(b'\x00\x00\x00\x01\xff');reply=self.received(peer.stream)
        self.assertTrue(reply.HasField('error'),reply);self.assertEqual(reply.error.code,core.INVALID_ARGUMENT)
        self.assertTrue(self.authenticate(peer).HasField('opened'))

    def test_authenticated_idle_actor_survives_unauthenticated_timeout(self):
        world=self.remote_world();actor=self.live_actor(world);idle=world.connect(self)
        time.sleep(3.2);self.assert_closed(idle.stream)
        self.assertTrue(actor.call(inspect=session.WorldSessionInspect()).HasField('state'))

    def test_closed_channels_release_slots_for_repeated_connections(self):
        world=self.remote_world()
        for _ in range(20):
            peer=self.live_actor(world)
            self.assertTrue(peer.call(close=session.WorldSessionClose()).HasField('closed'))
            peer.close()
        self.assertEqual(len(world.management('world.list')['entities']),2)

    def test_remote_actor_cannot_inject_application_keys(self):
        world=self.remote_world();peer=self.live_actor(world)
        result=peer.act(key=core.ActorKeyRequest(keysym='a',pressed=True))
        self.assertTrue(result.HasField('error'),result);self.assertEqual(result.error.code,core.UNIMPLEMENTED)
        self.assertFalse(peer.call(inspect=session.WorldSessionInspect()).state.held_keys)
        self.assertTrue(self.navigation(peer).HasField('action'))


if __name__=='__main__':
    tests=unittest.TestSuite(RemoteSecurityTests(name) for name in RemoteSecurityTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(tests).wasSuccessful())
