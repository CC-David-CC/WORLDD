"""Real broker observation watches, epoch barriers, and retained owner recovery."""
import os
import select
import time
import unittest
import broker_pb2 as pb
import core_pb2 as core
import actor_local_view_pb2 as local
import grants_pb2 as grant
import issuer_pb2 as issuer
import test_control_owner_channels as owned
from test_world_identity import receive


class ObservationWatchTests(owned.ControlOwnerTests):
    def setUp(self):
        super().setUp()
        self.enroll_second()
        self.observation_events = {}
        self.control_events = {}

    def request(self, world, **operation):
        from harness.wire import frame
        self.world_sequence += 1
        sequence = self.world_sequence
        world.sendall(frame(pb.Request(protocol_version=1, request_id=sequence, **operation)))
        while True:
            reply = receive(world.recv, world, pb.Reply)
            self.assertEqual(reply.protocol_version, 1)
            if reply.HasField('observation_revoked'):
                self.assertEqual(reply.request_id, 0)
                self.observation_events.setdefault(world, []).append(reply.observation_revoked)
                continue
            if reply.HasField('control_revoked'):
                self.assertEqual(reply.request_id, 0)
                self.control_events.setdefault(world, []).append(reply.control_revoked)
                continue
            self.assertEqual(reply.request_id, sequence)
            return reply

    def fence(self, claims=None):
        reply = self.broker.request(authorization_fence=pb.AuthorizationFenceRequest(principal=self.principal(claims)))
        self.assertTrue(reply.HasField('authorization_fence'), reply)
        return reply.authorization_fence

    def watch_request(self, owner, claims=None, view_id=None, fence=None):
        principal = self.principal(claims)
        view = local.ActorLocalViewIdentity(protocol_version=1, world_id='observation-world',
            actor_id='identity:' + principal.issuer_id.hex() + ':' + principal.actor_id.hex(),
            view_id=view_id or os.urandom(32), authority_service_incarnation=owner.launch.service_incarnation,
            authority_generation=owner.launch.generation)
        return pb.ObservationWatchAcquire(principal=principal, view=view, presentation_generation=7,
            expected=fence or self.fence(claims))

    def watch(self, owner, world, **kwargs):
        request = self.watch_request(owner, **kwargs)
        reply = self.request(world, observation_acquire=request)
        self.assertTrue(reply.HasField('observation_watch'), reply)
        watch = reply.observation_watch
        self.assertEqual(len(watch.id), 32)
        self.assertEqual(watch.principal, request.principal)
        self.assertEqual(watch.view, request.view)
        self.assertEqual(watch.presentation_generation, request.presentation_generation)
        self.assertEqual(watch.fence, request.expected)
        return watch

    def release_watch(self, world, watch):
        release = pb.ObservationWatchRelease(id=watch.id, principal=watch.principal,
            view=watch.view, presentation_generation=watch.presentation_generation)
        reply = self.request(world, observation_release=release)
        self.assertTrue(reply.HasField('observation_released'), reply)
        self.assertEqual(reply.observation_released.id, watch.id)
        return reply

    def observation_event(self, world, watch):
        deadline = time.monotonic() + 3
        while not any(item.id == watch.id for item in self.observation_events.get(world, [])):
            self.assertTrue(select.select([world], [], [], max(0, deadline - time.monotonic()))[0],
                'No idle observation revocation event')
            reply = receive(world.recv, world, pb.Reply)
            self.assertEqual((reply.protocol_version, reply.request_id), (1, 0))
            self.assertTrue(reply.HasField('observation_revoked'), reply)
            self.observation_events.setdefault(world, []).append(reply.observation_revoked)
        event = next(item for item in self.observation_events[world] if item.id == watch.id)
        self.assertEqual(event, watch)

    def barrier(self, fence=None):
        fence = fence or self.fence()
        reply = self.broker.request(observation_barrier=pb.ObservationBarrierRequest(fence=fence))
        self.assertTrue(reply.HasField('observation_barrier'), reply)
        self.assertEqual(reply.observation_barrier.fence, fence)
        return reply.observation_barrier.settled

    def policy_change(self):
        return self.delegate([grant.APPLICATION_OBSERVE], self.app)

    def checked_cleanup(self, owner, watch):
        cleanup = self.cleanup_receipt(owner)
        cleanup.retired_presentation.world_id = watch.view.world_id
        cleanup.retired_presentation.presentation_generation = watch.presentation_generation
        return cleanup

    def test_watch_is_idempotent_and_inspectable_without_control_reservation(self):
        owner, world = self.register()
        watch = self.watch(owner, world)
        request = pb.ObservationWatchAcquire(principal=watch.principal, view=watch.view,
            presentation_generation=watch.presentation_generation, expected=watch.fence)
        self.assertEqual(self.request(world, observation_acquire=request).observation_watch, watch)
        state = self.inspect(owner)
        self.assertFalse(state.reservations)
        self.assertEqual(list(state.observation_watches), [watch])
        self.assertTrue(self.barrier())
        self.release_watch(world, watch)
        self.assertFalse(self.inspect(owner).observation_watches)

    def test_watch_grants_no_workspace_observation_or_control_authority(self):
        owner, world = self.register()
        watch = self.watch(owner, world, claims=self.second)
        access = self.broker.request(application_access=pb.ApplicationAccessRequest(
            principal=self.principal(self.second), applications=[self.app]))
        self.assertTrue(access.HasField('application_access'), access)
        self.assertEqual(len(access.application_access.applications), 1)
        decision = access.application_access.applications[0]
        self.assertTrue(decision.HasField('observe'))
        self.assertTrue(decision.HasField('control'))
        self.assertFalse(decision.observe)
        self.assertFalse(decision.control)
        denied = self.acquire(world, claims=self.second)
        self.assertEqual(denied.error.code, core.FORBIDDEN, denied)
        self.release_watch(world, watch)
        self.finish_application(self.app)

    def test_two_observers_and_an_independent_controller_coexist(self):
        first, a = self.register()
        second, b = self.register()
        wa = self.watch(first, a)
        wb = self.watch(second, b)
        control = self.lease(b)
        self.assertNotEqual(wa.id, wb.id)
        self.assertTrue(self.barrier())
        self.release_watch(a, wa)
        self.assertEqual(list(self.inspect(second).observation_watches), [wb])
        self.assertEqual(list(self.inspect(second).reservations), [control])
        self.release_watch(b, wb)
        self.assertTrue(self.release(b, control).HasField('control_released'))

    def test_policy_change_notifies_only_the_observation_owner(self):
        first, a = self.register()
        second, b = self.register()
        watch = self.watch(first, a)
        self.policy_change()
        self.observation_event(a, watch)
        self.assertFalse(select.select([b], [], [], .05)[0])
        self.assertFalse(self.inspect(second).observation_watches)
        self.release_watch(a, watch)
        self.finish_application(self.app)

    def test_old_watch_blocks_barrier_until_exact_release(self):
        owner, world = self.register()
        watch = self.watch(owner, world)
        self.policy_change()
        self.observation_event(world, watch)
        fence = self.fence()
        self.assertFalse(self.barrier(fence))
        wrong = pb.ObservationWatchRelease(id=watch.id, principal=watch.principal,
            view=watch.view, presentation_generation=watch.presentation_generation + 1)
        self.assertEqual(self.request(world, observation_release=wrong).error.code, core.FORBIDDEN)
        self.assertFalse(self.barrier(fence))
        self.release_watch(world, watch)
        self.assertTrue(self.barrier(fence))
        self.finish_application(self.app)

    def test_stale_epoch_cannot_acquire_and_leaves_owner_usable(self):
        owner, world = self.register()
        request = self.watch_request(owner)
        self.policy_change()
        denied = self.request(world, observation_acquire=request)
        self.assertEqual(denied.error.code, core.REVISION_CONFLICT, denied)
        self.assertFalse(self.inspect(owner).observation_watches)
        control = self.lease(world)
        self.assertTrue(self.release(world, control).HasField('control_released'))

    def test_invalidated_watch_requires_release_before_replacement(self):
        owner, world = self.register()
        watch = self.watch(owner, world)
        self.policy_change()
        self.observation_event(world, watch)
        request = self.watch_request(owner, view_id=watch.view.view_id)
        self.assertEqual(self.request(world, observation_acquire=request).error.code, core.BUSY)
        self.release_watch(world, watch)
        replacement = self.watch(owner, world, view_id=watch.view.view_id)
        self.assertNotEqual(replacement.id, watch.id)
        self.release_watch(world, replacement)

    def test_wrong_owner_generation_or_actor_cannot_register_watch(self):
        owner, world = self.register()
        for field, value, code in (('authority_generation', 2, core.FORBIDDEN),
                ('authority_service_incarnation', b'x' * 32, core.FORBIDDEN),
                ('actor_id', 'foreign-actor', core.INVALID_ARGUMENT)):
            with self.subTest(field=field):
                request = self.watch_request(owner)
                setattr(request.view, field, value)
                self.assertEqual(self.request(world, observation_acquire=request).error.code, code)
        watch = self.watch(owner, world)
        self.release_watch(world, watch)

    def test_another_owner_cannot_release_the_same_actor_view(self):
        first, a = self.register()
        second, b = self.register()
        watch = self.watch(first, a)
        release = pb.ObservationWatchRelease(id=watch.id, principal=watch.principal,
            view=watch.view, presentation_generation=watch.presentation_generation)
        self.assertEqual(self.request(b, observation_release=release).error.code, core.FORBIDDEN)
        self.assertEqual(list(self.inspect(first).observation_watches), [watch])
        self.release_watch(a, watch)

    def test_fence_retains_observation_until_checked_presentation_recovery(self):
        owner, world = self.register()
        watch = self.watch(owner, world)
        control = self.lease(world)
        self.assertTrue(self.admin('fence', owner).state.channel_fenced)
        denied = self.admin('recover', self.cleanup_receipt(owner))
        self.assertEqual(denied.error.code, core.FORBIDDEN, denied)
        state = self.inspect(owner)
        self.assertEqual(list(state.observation_watches), [watch])
        self.assertEqual(list(state.reservations), [control])
        self.assertFalse(self.barrier())
        recovered = self.admin('recover', self.checked_cleanup(owner, watch)).state
        self.assertFalse(recovered.observation_watches)
        self.assertFalse(recovered.reservations)
        self.assertEqual(list(recovered.recovery.observation_released), [watch])
        self.assertEqual(list(recovered.recovery.released), [control])
        self.assertTrue(self.barrier())
        self.finish_application(self.app)

    def test_wrong_presentation_recovery_retains_all_reservations(self):
        owner, world = self.register()
        watch = self.watch(owner, world)
        control = self.lease(world)
        self.admin('fence', owner)
        for field, value in (('world_id', 'foreign'), ('presentation_generation', 8)):
            with self.subTest(field=field):
                cleanup = self.checked_cleanup(owner, watch)
                setattr(cleanup.retired_presentation, field, value)
                self.assertEqual(self.admin('recover', cleanup).error.code, core.FORBIDDEN)
                state = self.inspect(owner)
                self.assertEqual(list(state.observation_watches), [watch])
                self.assertEqual(list(state.reservations), [control])
        self.assertTrue(self.admin('recover', self.checked_cleanup(owner, watch)).HasField('state'))

    def test_recovery_retry_preserves_exact_observation_receipt(self):
        owner, world = self.register()
        watch = self.watch(owner, world)
        self.admin('fence', owner)
        cleanup = self.checked_cleanup(owner, watch)
        recovered = self.admin('recover', cleanup).state
        self.assertEqual(self.admin('recover', cleanup).state, recovered)
        self.assertEqual(self.inspect(owner), recovered)
        cleanup.retired_presentation.presentation_generation += 1
        self.assertEqual(self.admin('recover', cleanup).error.code, core.INVALID_ARGUMENT)

    def test_owner_disconnect_preserves_observation_for_recovery(self):
        owner, world = self.register()
        watch = self.watch(owner, world)
        world.close()
        deadline = time.monotonic() + 3
        while not self.inspect(owner).channel_fenced:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.01)
        self.assertEqual(list(self.inspect(owner).observation_watches), [watch])
        self.assertFalse(self.barrier())
        self.assertEqual(list(self.admin('recover', self.checked_cleanup(owner, watch)).state.recovery.observation_released), [watch])
        self.assertTrue(self.barrier())

    def test_revoked_actor_can_release_observation_without_restored_rights(self):
        owner, world = self.register()
        watch = self.watch(owner, world)
        self.revoke(kind=issuer.ACTOR_TARGET)
        self.observation_event(world, watch)
        self.release_watch(world, watch)
        self.assertFalse(self.inspect(owner).observation_watches)

    def test_invalidation_is_emitted_once(self):
        owner, world = self.register()
        watch = self.watch(owner, world)
        self.policy_change()
        self.observation_event(world, watch)
        for _ in range(3):
            self.assertEqual(list(self.inspect(owner).observation_watches), [watch])
        self.assertFalse(select.select([world], [], [], .05)[0])
        self.release_watch(world, watch)

    def test_combined_snapshot_budget_bounds_observation_growth(self):
        owner, world = self.register()
        fence = self.fence()
        watches = []
        for _ in range(257):
            request = self.watch_request(owner, fence=fence)
            reply = self.request(world, observation_acquire=request)
            if reply.HasField('error'):
                self.assertEqual(reply.error.code, core.RESOURCE_EXHAUSTED, reply)
                break
            self.assertTrue(reply.HasField('observation_watch'), reply)
            watches.append(reply.observation_watch)
        else:
            self.fail('Observation count and snapshot limits did not stop allocation')
        self.assertGreater(len(watches), 0)
        self.assertLessEqual(len(watches), 256)
        state = self.inspect(owner)
        self.assertLessEqual(sum(w.ByteSize() + 16 for w in state.observation_watches), 60000)
        self.assertLessEqual(state.ByteSize(), 65536)
        for watch in watches:
            self.release_watch(world, watch)
        self.assertFalse(self.inspect(owner).observation_watches)


if __name__ == '__main__':
    names = [name for name in ObservationWatchTests.__dict__ if name.startswith('test_')]
    suite = unittest.TestSuite(ObservationWatchTests(name) for name in names)
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
