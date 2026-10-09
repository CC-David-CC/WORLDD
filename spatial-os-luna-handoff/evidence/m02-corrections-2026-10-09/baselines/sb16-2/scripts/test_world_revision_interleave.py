"""An older read reply must not disable an unrelated actor after a newer feed event."""
import unittest
import core_pb2 as core
import issuer_pb2 as issuer
import world_session_pb2 as session
import test_world_authority as existing
import test_world_bootstrap_races as races


class RevisionInterleaveTests(existing.WorldAuthorityTests):
    def unrelated_revocation_during(self, operation):
        self.enroll_second()
        def revoke():
            peer = self.connect()
            try:
                mutation = issuer.IssuerOperation(identity_revoke=issuer.IdentityRevoke(
                    target=issuer.IdentityReference(kind=issuer.CREDENTIAL_TARGET, id=self.second.credential_id),
                    expected_security_revision=0, reason='independent credential interleaving'))
                result = self.execute(peer, self.challenge(peer, mutation))
                self.assertTrue(result.HasField('identity_status'), result)
            finally: peer.close()
        relay = races.BootstrapRelay(self, revoke, operation)
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


def suite():
    return unittest.TestSuite(RevisionInterleaveTests(name) for name in vars(RevisionInterleaveTests) if name.startswith('test_'))


if __name__ == '__main__': unittest.main(defaultTest='suite', verbosity=2)
