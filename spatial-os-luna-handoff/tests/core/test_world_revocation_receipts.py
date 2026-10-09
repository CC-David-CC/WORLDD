"""Committed self-revocation receipts do not restore the caller's authority."""
import unittest
import core_pb2 as core
import issuer_pb2 as issuer
import world_session_pb2 as session
from test_world_bootstrap import BootstrapWorldFixture
from test_world_authority import RelayingIssuer


class WorldRevocationReceiptTests(BootstrapWorldFixture):
    def test_private_self_revocation_returns_receipt_then_denies_further_actions(self):
        world = self.scoped_world(); peer = world.peers[0]
        parent = self.root_grant(peer)
        result = self.revoke_grant(peer, parent)
        self.assertTrue(result.HasField('grant'), result)
        self.assertEqual(result.grant.id, parent.id)
        self.assertGreater(result.grant.revoked_revision, parent.created_revision)
        self.denied(peer.call(inspect=session.WorldSessionInspect()), core.UNAUTHORIZED)
        self.denied(self.navigation(peer), core.UNAUTHORIZED)
        self.denied(self.authenticate(peer), core.FORBIDDEN)

    def test_public_self_revocation_preserves_admission_but_not_navigation(self):
        world = self.scoped_world(session.REGISTERED_ACTORS); peer = world.peers[0]
        parent = self.root_grant(peer)
        result = self.revoke_grant(peer, parent)
        self.assertTrue(result.HasField('grant'), result)
        self.assertEqual(result.grant.id, parent.id)
        self.assertGreater(result.grant.revoked_revision, parent.created_revision)
        self.assertTrue(peer.call(inspect=session.WorldSessionInspect()).HasField('state'))
        self.denied(self.navigation(peer), core.FORBIDDEN)

    def test_issuer_revocation_before_local_commit_cannot_receive_success_receipt(self):
        def revoke():
            owner = self.connect()
            try:
                op = issuer.IssuerOperation(identity_revoke=issuer.IdentityRevoke(
                    target=issuer.IdentityReference(kind=issuer.ACTOR_TARGET, id=self.identity.actor_id),
                    expected_security_revision=0, reason='in-flight grant revoke regression'))
                result = self.execute(owner, self.challenge(owner, op))
                self.assertTrue(result.HasField('identity_status'), result)
            finally: owner.close()
        relay = RelayingIssuer(self, revoke)
        world = self.scoped_world(issuer_port=relay.port); peer = world.peers[0]
        parent = self.root_grant(peer); revision = self.own(peer).revision
        peer.action_sequence += 1; relay.armed.set()
        result = peer.call(revoke=session.WorldGrantRevoke(seq=peer.action_sequence,
            id=parent.id, expected_revision=revision))
        self.assertTrue(relay.delivered.is_set())
        self.assertTrue(result.HasField('error'), result)
        self.assertIn(result.error.code, (core.UNAUTHORIZED, core.UNAVAILABLE))
        self.assertFalse(result.HasField('grant'))


if __name__ == '__main__': unittest.main(verbosity=2)
