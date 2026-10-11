"""Remote transport must bound clients and preserve actor lifetime rules."""
import socket
import time
import unittest
import core_pb2 as core
import issuer_pb2 as issuer
import world_session_pb2 as session
from test_world_remote import RemoteWorldProcess
from test_world_remote_security import RemoteSecurityTests


class RemoteLifecycleTests(RemoteSecurityTests):
    def remote_world(self):
        local=self.scoped_world(authenticate=False);local.close()
        return self.restart(local)

    def restart(self,previous):
        world=RemoteWorldProcess(self,previous);self.worlds.append(world)
        world.config_path=previous.config_path;world.runtime_path=previous.runtime_path
        world.management('system.hello');return world

    def stopped(self,world,actor_id):
        # The fixture sets motion expiry to ten seconds. These checks finish much sooner.
        time.sleep(.15);before=world.management('world.get',id=actor_id)['pose']
        time.sleep(.15);self.assertEqual(before,world.management('world.get',id=actor_id)['pose'])

    def test_stalled_handshake_expires_and_does_not_block_valid_actor(self):
        world=self.remote_world();raw=socket.create_connection(('127.0.0.1',world.port),timeout=2)
        self.addCleanup(raw.close);actor=self.live_actor(world)
        time.sleep(.85);self.assert_closed(raw)
        self.assertTrue(actor.call(inspect=session.WorldSessionInspect()).HasField('state'))

    def test_connection_limit_rejects_excess_and_recovers_after_close(self):
        world=self.remote_world();peers=[world.connect(self) for _ in range(8)]
        # A complete protobuf reply confirms each accepted slot is occupied.
        for peer in peers:self.denied(peer.call(inspect=session.WorldSessionInspect()),core.UNAUTHORIZED)
        raw=socket.create_connection(('127.0.0.1',world.port),timeout=.4);self.addCleanup(raw.close)
        self.assert_closed(raw)
        for peer in peers:peer.close()
        time.sleep(.05);self.live_actor(world)

    def test_disconnect_stops_real_motion_before_motion_expiry(self):
        world=self.remote_world();peer=self.live_actor(world)
        state=peer.call(inspect=session.WorldSessionInspect()).state
        self.assertTrue(self.navigation(peer).HasField('action'));time.sleep(.08)
        self.assertNotEqual(state.pose,peer.call(inspect=session.WorldSessionInspect()).state.pose)
        peer.close();self.stopped(world,state.id)
        replacement=self.live_actor(world)
        restored=replacement.call(inspect=session.WorldSessionInspect()).state
        self.assertEqual(restored.id,state.id);self.assertFalse(any(restored.axes))

    def test_credential_revocation_cancels_idle_remote_actor(self):
        world=self.remote_world();peer=self.live_actor(world)
        state=peer.call(inspect=session.WorldSessionInspect()).state
        self.assertTrue(self.navigation(peer).HasField('action'));self.revoke()
        self.stopped(world,state.id)
        self.denied(peer.call(inspect=session.WorldSessionInspect()),core.UNAUTHORIZED)

    def test_replayed_proof_cannot_reopen_closed_session(self):
        world=self.remote_world();peer=world.connect(self)
        challenge=peer.call(begin=session.WorldAuthenticationBegin(credential=self.record.credential)).challenge
        completion=issuer.AuthenticationComplete(nonce=challenge.nonce,
            proof=self.proof(challenge,self.actor_key,self.actor_public))
        self.assertTrue(peer.call(complete=completion).HasField('opened'))
        self.assertTrue(peer.call(close=session.WorldSessionClose()).HasField('closed'))
        self.denied(peer.call(complete=completion),core.UNAUTHORIZED)
        self.assertTrue(self.authenticate(peer).HasField('opened'))

    def test_restart_preserves_checkpoint_and_rejects_old_challenge(self):
        world=self.remote_world();peer=self.live_actor(world)
        self.assertTrue(self.navigation(peer).HasField('action'));time.sleep(.08)
        self.assertTrue(peer.act(stop=core.ActorStopRequest()).HasField('action'))
        before=peer.call(inspect=session.WorldSessionInspect()).state;world.management('world.checkpoint')
        pending=world.connect(self)
        challenge=pending.call(begin=session.WorldAuthenticationBegin(credential=self.record.credential)).challenge
        completion=issuer.AuthenticationComplete(nonce=challenge.nonce,
            proof=self.proof(challenge,self.actor_key,self.actor_public))
        old_info=world.management('world.info');world.close();restarted=self.restart(world)
        fresh=restarted.connect(self);self.denied(fresh.call(complete=completion),core.UNAUTHORIZED)
        self.assertTrue(self.authenticate(fresh).HasField('opened'))
        after=fresh.call(inspect=session.WorldSessionInspect()).state
        self.assertEqual(restarted.management('world.info'),old_info)
        self.assertEqual(after.id,before.id);self.assertEqual(after.room,before.room)
        self.assertEqual(after.pose,before.pose);self.assertFalse(any(after.axes));self.assertFalse(after.workspace)


if __name__=='__main__':
    tests=unittest.TestSuite(RemoteLifecycleTests(name) for name in RemoteLifecycleTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(tests).wasSuccessful())
