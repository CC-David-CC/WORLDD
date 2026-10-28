"""Real retained host, world, broker, native textures, and observation cleanup."""
import copy
import os
import struct
import time
import unittest
import actor_local_view_pb2 as local
import core_pb2 as core
import grants_pb2 as grants
import issuer_pb2 as identity
import launch_pb2 as launch
import session_probe_pb2 as probe
import world_session_pb2 as session
# Parse SOURCE and the launch probe before native security consumes its argument.
import test_session_application as baseline
import test_resource_isolation as native


class LocalForegroundTests(baseline.SessionApplicationTests):
    coordinator_probe = 'spatial-local-foreground-probe'
    shared_presentation = True
    native_screen_geometry = '256x256x24'

    def config_file(self, name, value):
        if isinstance(value, core.RuntimeConfig):
            self.expected_vertical_fov = value.render.vertical_fov
        return super().config_file(name, value)

    def workspace_grant_roots(self):
        scope = grants.Scope(domain=grants.Domain(kind=grants.WORKSPACE, id=baseline.WORKSPACE))
        rights = [r for r in baseline.RIGHTS if r not in (grants.APPLICATION_OBSERVE, grants.APPLICATION_CONTROL)]
        return [grants.GrantTerms(subject=self.principal, scope=scope, rights=rights + [grants.POLICY_ADMINISTER]),
            grants.GrantTerms(subject=self.principal, scope=scope, rights=[grants.APPLICATION_OBSERVE]),
            grants.GrantTerms(subject=self.principal, scope=scope, rights=[grants.APPLICATION_CONTROL])]

    def world_grant_roots(self):
        scope = grants.Scope(domain=grants.Domain(kind=grants.WORLD, id=self.world_domain))
        return [grants.GrantTerms(subject=self.principal, scope=scope,
                rights=[grants.WORLD_ENTER, grants.WORLD_NAVIGATE, grants.WORLD_MANIPULATE, grants.POLICY_ADMINISTER]),
            grants.GrantTerms(subject=self.principal, scope=scope, rights=[grants.WORLD_OBSERVE])]

    def actor_call(self, stream, request=None, **operation):
        reply = super().actor_call(stream, request, **operation)
        if reply.HasField('opened'):
            value = reply.opened
            self.local_identity = local.ActorLocalViewIdentity(protocol_version=1, world_id=value.world_id,
                actor_id=value.actor_id, view_id=value.geometry_view_id,
                authority_service_incarnation=value.authority_service_incarnation,
                authority_generation=value.authority_generation)
        return reply

    def foreground(self, *, identity_value=None, width=256, height=256, error=False):
        return self.request(expect_error=error, local_present=probe.LocalForegroundPresent(
            identity=identity_value or self.local_identity, width=width, height=height))

    def display_peer(self):
        if hasattr(self, 'pixels_peer'):
            return self.pixels_peer
        peer = native.Client(self.display, self.cookie)
        self.addCleanup(peer.close)
        name = b'Composite'
        head, _ = peer.operation(98, peer.pack('HH', len(name), 0) + name, reply=True)
        self.assertTrue(head[8], 'Composite extension is missing')
        head, _ = peer.operation(head[9], peer.pack('I', peer.root), 7, True)
        overlay = struct.unpack_from(peer.endian + 'I', head, 8)[0]
        children = peer.tree(overlay)
        self.assertEqual(len(children), 1, children)
        self.output_window = children[0]
        self.pixels_peer = peer
        return peer

    def pixel(self):
        peer = self.display_peer()
        geometry, _ = peer.operation(14, peer.pack('I', self.output_window), reply=True)
        screen, _ = peer.operation(14, peer.pack('I', peer.root), reply=True)
        dimensions = struct.unpack_from(peer.endian + 'HH', geometry, 16)
        screen_dimensions = struct.unpack_from(peer.endian + 'HH', screen, 16)
        self.assertGreaterEqual(min(*dimensions, *screen_dimensions), 256,
            'The independent pixel sample must fit the output and native screen')
        result = peer.operation(73, peer.pack('IhhHHI', self.output_window, 128, 128, 1, 1, 0xffffffff), 2, True)
        self.assertIsInstance(result, tuple, ('Native foreground readback error', result, dimensions, screen_dimensions))
        _, body = result
        return int.from_bytes(body, 'little' if peer.endian == '<' else 'big') & 0xffffff

    def show(self, color=(17, 34, 51)):
        self.held_application()
        self.native_command('paint', *color)
        reply = self.foreground()
        self.assertTrue(reply.HasField('local_frame'), reply)
        self.assertTrue(reply.local_frame.bound and reply.local_frame.presented)
        self.assertEqual(reply.local_frame.identity, self.local_identity)
        self.assertGreater(reply.local_frame.vertical_fov, 0)
        self.assertAlmostEqual(reply.local_frame.vertical_fov, self.expected_vertical_fov)
        self.assertEqual(self.pixel(), color[0] << 16 | color[1] << 8 | color[2])
        return reply.local_frame

    def policy_revoke(self, right, workspace=True):
        query = session.WorldGrantList()
        if workspace:
            query.workspace = baseline.WORKSPACE
        listed = self.actor_call(self.stream, grants=query)
        self.assertTrue(listed.HasField('grants'), listed)
        matches = [g for g in listed.grants.grants if list(g.terms.rights) == [right] and not g.revoked_revision]
        self.assertEqual(len(matches), 1)
        self.action_sequence += 1
        operation = session.WorldGrantRevoke(seq=self.action_sequence, id=matches[0].id,
            expected_revision=listed.grants.revision)
        if workspace:
            operation.workspace = baseline.WORKSPACE
        revoked = self.actor_call(self.stream, revoke=operation)
        self.assertTrue(revoked.HasField('grant'), revoked)
        self.assertGreater(revoked.grant.revoked_revision, 0)
        return revoked.grant

    def finish_foreground(self, *, retired=False):
        if not retired:
            self.assertTrue(self.request(local_retire=self.local_identity).local_retired)
        cleanup = self.request(stop_world=self.owner).cleanup
        self.assertEqual(cleanup.stage, launch.OWNED_SCOPES_STOPPED)
        self.finish_session()

    def test_live_foreground_repaints_application_and_retains_independent_watch(self):
        first = self.show()
        state = self.request(inspect=self.owner).owner
        self.assertEqual(len(state.observation_watches), 1)
        self.assertEqual(len(state.reservations), 1)
        watch = state.observation_watches[0]
        self.assertEqual(watch.view, self.local_identity)
        self.assertEqual(watch.presentation_generation, first.presentation_generation)
        self.native_command('paint', 51, 34, 17)
        second = self.foreground().local_frame
        self.assertGreater(second.frame_id, first.frame_id)
        self.assertEqual(second.presentation_generation, first.presentation_generation)
        self.assertEqual(self.pixel(), 0x332211)
        self.assertEqual(list(self.request(inspect=self.owner).owner.observation_watches), [watch])
        self.finish_foreground()

    def test_workspace_observation_revocation_clears_before_receipt_and_preserves_execution(self):
        self.show()
        self.policy_revoke(grants.APPLICATION_OBSERVE)
        self.assertEqual(self.pixel(), 0)
        state = self.request(inspect=self.owner).owner
        self.assertFalse(state.observation_watches)
        self.assertFalse(state.reservations)
        self.released()
        self.native_command('paint', 51, 34, 17)
        self.assertTrue(self.foreground().local_frame.presented)
        self.assertNotEqual(self.pixel(), 0x332211)
        self.finish_foreground()

    def test_actor_close_clears_foreground_without_stopping_application(self):
        self.show()
        self.assertTrue(self.actor_call(self.stream, close=session.WorldSessionClose()).closed.closed)
        self.assertEqual(self.pixel(), 0, 'Closed acknowledgement preceded foreground clearing')
        self.assertFalse(self.request(inspect=self.owner).owner.observation_watches)
        self.released()
        self.finish_foreground()

    def test_continuous_navigation_commits_coherent_sampled_camera_frames(self):
        self.show()
        self.act(stop=core.ActorStopRequest())
        samples = []
        for _ in range(5):
            self.act(input=core.ActorInputRequest(axes=[.2, 0, 0, 0, 0, 0]))
            before = self.actor_call(self.stream, inspect=session.WorldSessionInspect()).state
            frame = self.foreground().local_frame
            after = self.actor_call(self.stream, inspect=session.WorldSessionInspect()).state
            self.assertTrue(frame.presented)
            self.assertEqual(frame.room, before.room)
            self.assertEqual(frame.room, after.room)
            self.assertGreaterEqual(frame.camera.position.x + 1e-8, before.pose.position.x)
            self.assertLessEqual(frame.camera.position.x - 1e-8, after.pose.position.x)
            samples.append(frame.camera.position.x)
        self.assertGreater(samples[-1], samples[0])
        self.act(stop=core.ActorStopRequest())
        self.finish_foreground()

    def test_world_observation_revocation_blanks_and_keeps_independent_navigation(self):
        self.show()
        self.policy_revoke(grants.WORLD_OBSERVE, workspace=False)
        self.assertEqual(self.pixel(), 0)
        self.assertFalse(self.request(inspect=self.owner).owner.observation_watches)
        self.assertTrue(self.actor_call(self.stream, inspect=session.WorldSessionInspect()).HasField('state'))
        self.act(input=core.ActorInputRequest(axes=[.2, 0, 0, 0, 0, 0]))
        self.act(stop=core.ActorStopRequest())
        self.released()
        denied = self.foreground(error=True)
        self.assertEqual(denied.error.code, core.FORBIDDEN)
        self.finish_foreground()

    def test_retirement_and_rebinding_create_a_fresh_observation_reservation(self):
        first = self.show()
        old = self.request(inspect=self.owner).owner.observation_watches[0]
        self.assertTrue(self.request(local_retire=self.local_identity).local_retired)
        self.assertEqual(self.pixel(), 0)
        self.assertFalse(self.request(inspect=self.owner).owner.observation_watches)
        fresh = self.foreground().local_frame
        self.assertGreater(fresh.frame_id, first.frame_id)
        self.assertEqual(self.pixel(), 0x112233)
        self.assertNotEqual(self.request(inspect=self.owner).owner.observation_watches[0].id, old.id)
        self.finish_foreground()

    def test_foreign_view_rejection_and_retirement_cannot_blank_current_view(self):
        self.show()
        foreign = copy.deepcopy(self.local_identity)
        foreign.view_id = os.urandom(32)
        self.assertEqual(self.foreground(identity_value=foreign, error=True).error.code, core.FORBIDDEN)
        self.assertEqual(self.pixel(), 0x112233)
        self.assertTrue(self.request(local_retire=foreign).local_retired)
        self.assertEqual(self.pixel(), 0x112233)
        self.finish_foreground()

    def test_actor_observation_cannot_overwrite_visible_foreground(self):
        self.show()
        self.native_command('paint', 51, 34, 17)
        observed = self.act(observe=core.ActorObserveRequest(width=64, height=64, include_pixels=True))
        self.assertTrue(observed.HasField('observation'), observed)
        self.assertTrue(observed.observation.HasField('image'))
        self.assertEqual(self.pixel(), 0x112233)
        self.assertTrue(self.foreground().local_frame.presented)
        self.assertEqual(self.pixel(), 0x332211)
        self.finish_foreground()

    def test_observation_survives_control_revocation_without_rearming_input(self):
        self.show()
        self.policy_revoke(grants.APPLICATION_CONTROL)
        self.assertEqual(self.pixel(), 0)
        self.released()
        self.assertTrue(self.foreground().local_frame.presented)
        self.assertEqual(self.pixel(), 0x112233)
        self.action_sequence += 1
        denied = self.actor_call(self.stream, action=core.ActorActionRequest(seq=self.action_sequence,
            key=core.ActorKeyRequest(keysym='F5', pressed=True)))
        self.assertEqual(denied.error.code, core.FORBIDDEN, denied)
        self.assertEqual(self.pixel(), 0x112233)
        self.finish_foreground()

    def test_invalid_viewport_cannot_replace_an_existing_foreground(self):
        self.show()
        self.assertEqual(self.foreground(width=0, error=True).error.code, core.INVALID_ARGUMENT)
        self.assertEqual(self.pixel(), 0x112233)
        self.finish_foreground()

    def test_native_default_viewport_matches_display_and_live_application(self):
        self.held_application()
        self.native_command('paint', 17, 34, 51)
        reply = self.foreground(width=0, height=0)
        self.assertTrue(reply.HasField('local_frame'), reply)
        frame = reply.local_frame
        self.assertTrue(frame.bound and frame.presented)
        peer = self.display_peer()
        screen, _ = peer.operation(14, peer.pack('I', peer.root), reply=True)
        dimensions = struct.unpack_from(peer.endian + 'HH', screen, 16)
        self.assertEqual((frame.viewport.width, frame.viewport.height), dimensions)
        self.assertEqual(dimensions, (256, 256))
        self.assertEqual(self.pixel(), 0x112233)
        self.native_command('paint', 51, 34, 17)
        self.assertGreater(self.foreground(width=0, height=0).local_frame.frame_id, frame.frame_id)
        self.assertEqual(self.pixel(), 0x332211)
        self.finish_foreground()

    def test_actor_identity_revocation_clears_before_issuer_acknowledgement(self):
        self.show()
        operation = identity.IssuerOperation(identity_revoke=identity.IdentityRevoke(
            target=identity.IdentityReference(kind=identity.ACTOR_TARGET, id=self.claims.actor_id),
            expected_security_revision=0, reason='retained foreground regression'))
        peer = self.issuer.connect()
        self.addCleanup(peer.close)
        revoked = self.issuer.execute(peer, self.issuer.challenge(peer, operation))
        self.assertTrue(revoked.HasField('identity_status'), revoked)
        status = revoked.identity_status
        if status.revocation_pending:
            self.assertTrue(status.active, status)
            self.assertEqual(status.pending_commit_revision, status.security_revision + 1)
            expected_revision = status.pending_commit_revision
            def identity_status():
                query = identity.IssuerOperation(identity_read=operation.identity_revoke.target)
                reply = self.issuer.execute(peer, self.issuer.challenge(peer, query))
                self.assertTrue(reply.HasField('identity_status'), reply)
                return reply.identity_status
            status = self.until(identity_status, lambda value:
                value.actor_revoked and not value.active and not value.revocation_pending)
            self.assertEqual(status.security_revision, expected_revision)
        self.assertFalse(status.active, status)
        self.assertTrue(status.actor_revoked, status)
        self.assertFalse(status.revocation_pending, status)
        revision = status.security_revision
        self.assertGreater(revision, 0)
        # IdentityStatus confirms the policy commit. The signed completion barrier
        # is the existing production acknowledgement for distributed cleanup.
        def completion():
            query = identity.IssuerOperation(barrier_read=identity.AuthorityBarrierRead(revision=revision))
            reply = self.issuer.execute(peer, self.issuer.challenge(peer, query))
            self.assertTrue(reply.HasField('authority_barrier'), reply)
            barrier = reply.authority_barrier
            self.assertEqual(barrier.revision, revision)
            self.assertEqual(barrier.target, operation.identity_revoke.target)
            return barrier
        barrier = self.until(completion, lambda value: value.complete)
        self.assertEqual(barrier.pending_count, 0)
        self.assertGreaterEqual(barrier.participant_count, 2)
        self.assertEqual(len(barrier.obligations), barrier.participant_count)
        self.assertTrue(all(value.complete for value in barrier.obligations))
        self.assertEqual(self.pixel(), 0)
        self.assertFalse(self.request(inspect=self.owner).owner.observation_watches)
        self.finish_foreground()


if __name__ == '__main__':
    names = [name for name in LocalForegroundTests.__dict__ if name.startswith('test_')]
    suite = unittest.TestSuite(LocalForegroundTests(name) for name in names)
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
