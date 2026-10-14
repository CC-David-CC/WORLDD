"""An older read reply must not disable an unrelated actor after a newer feed event."""
import unittest
import struct
import core_pb2 as core
import issuer_pb2 as issuer
import world_session_pb2 as session
import test_world_authority as existing
import test_world_bootstrap_races as races
from harness.wire import frame


class ReplayedStatusRelay(existing.RelayingIssuer):
    def __init__(self, fixture, operation):
        self.operation = operation
        super().__init__(fixture, lambda: None)

    def relay(self, raw):
        upstream = None; stream = raw
        try:
            raw.settimeout(2); stream = self.context.wrap_socket(raw, server_side=True); self.streams.append(stream)
            upstream = self.fixture.connect()
            def exact(size):
                data = b''
                while len(data) < size:
                    part = stream.recv(size-len(data))
                    if not part: raise EOFError()
                    data += part
                return data
            while not self.stop.is_set():
                size = struct.unpack('!I', exact(4))[0]
                if size > 1048576: raise AssertionError('Excessive relay frame')
                request = issuer.IssuerRequest.FromString(exact(size)); sequence = request.request_id
                reply = upstream.request(request); reply.request_id = sequence
                if request.HasField(self.operation) and self.armed.is_set():
                    self.armed.clear()
                    status = reply.identity_status if self.operation == 'credential_check' else reply.actor_status
                    if status.security_revision != 1: raise AssertionError('Expected a genuine revision-one reply')
                    status.security_revision = 0
                    self.delivered.set()
                stream.sendall(frame(reply))
        except (OSError, EOFError): pass
        except BaseException as error: self.errors.append(error)
        finally:
            stream.close()
            if upstream: upstream.close()


class RevisionInterleaveTests(existing.WorldAuthorityTests):
    def config_file(self, endpoint_port=None, **overrides):
        # Healthy authority setup uses the accepted five-second request budget.
        # The relay still enforces the original ordering and delivery assertions.
        overrides.setdefault('io_timeout_ms', 5000)
        return super().config_file(endpoint_port, **overrides)

    def connect(self, *args, **kwargs):
        peer = super().connect(*args, **kwargs)
        peer.stream.settimeout(5)
        return peer

    def revoke_second(self):
        peer = self.connect()
        try:
            mutation = issuer.IssuerOperation(identity_revoke=issuer.IdentityRevoke(
                target=issuer.IdentityReference(kind=issuer.CREDENTIAL_TARGET, id=self.second.credential_id),
                expected_security_revision=0, reason='independent credential interleaving'))
            result = self.execute(peer, self.challenge(peer, mutation))
            self.assertTrue(result.HasField('identity_status'), result)
        finally: peer.close()

    def unrelated_revocation_during(self, operation):
        self.enroll_second()
        relay = races.BootstrapRelay(self, self.revoke_second, operation)
        world = self.scoped_world(issuer_port=relay.port)
        peer = world.peers[0]
        relay.armed.set()
        first = peer.act(stop=core.ActorStopRequest())
        self.assertTrue(relay.delivered.is_set(), 'The real revocation must precede the held status reply')
        self.assertEqual(relay.remaining, 0)
        # A stale in-flight action may fail. The independent identity remains usable.
        self.assertTrue(first.HasField('action') or first.HasField('error'), first)
        retry = peer.call(inspect=session.WorldSessionInspect())
        self.assertTrue(retry.HasField('state'), retry)
        world.close(); self.assertEqual(world.proc.returncode, 0)

    def test_old_credential_reply_does_not_disable_unrelated_actor(self):
        self.unrelated_revocation_during('credential_check')

    def test_old_actor_reply_does_not_disable_unrelated_actor(self):
        self.unrelated_revocation_during('actor_status')

    def replayed_before_query(self, operation):
        self.enroll_second(); relay = ReplayedStatusRelay(self, operation)
        world = self.scoped_world(issuer_port=relay.port); peer = world.peers[0]
        self.revoke_second()
        self.assertTrue(peer.call(inspect=session.WorldSessionInspect()).HasField('state'))
        relay.armed.set()
        denied = peer.act(stop=core.ActorStopRequest())
        self.assertTrue(denied.HasField('error'), denied)
        self.assertTrue(relay.delivered.is_set())
        retry = peer.call(inspect=session.WorldSessionInspect())
        self.assertTrue(retry.HasField('error'), retry)
        self.assertEqual(retry.error.code, core.UNAVAILABLE)
        self.assertIn('disabled', retry.error.message)

    def test_credential_reply_older_than_query_context_is_terminal(self):
        self.replayed_before_query('credential_check')

    def test_actor_reply_older_than_query_context_is_terminal(self):
        self.replayed_before_query('actor_status')


def suite():
    return unittest.TestSuite(RevisionInterleaveTests(name) for name in vars(RevisionInterleaveTests) if name.startswith('test_'))


if __name__ == '__main__': unittest.main(defaultTest='suite', verbosity=2)
