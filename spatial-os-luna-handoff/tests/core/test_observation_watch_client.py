"""The production broker client checks observation receipts and asynchronous invalidation."""
import select
import unittest
import broker_pb2 as pb
import core_pb2 as core
import actor_local_view_pb2 as local
import test_broker_control_client as baseline
from harness.wire import frame


class ObservationClientTests(baseline.BrokerControlClientTests):
    def setUp(self):
        super().setUp()
        view = local.ActorLocalViewIdentity(protocol_version=1, world_id='world',
            actor_id='identity:' + self.principal.issuer_id.hex() + ':' + self.principal.actor_id.hex(),
            view_id=b'v' * 32, authority_service_incarnation=b's' * 32, authority_generation=1)
        self.watch = pb.ObservationWatch(id=b'o' * 32, principal=self.principal, view=view,
            presentation_generation=7, fence=pb.AuthorizationFence(incarnation=b'f' * 16, epoch=3))

    def request_watch(self, watch=None):
        watch = watch or self.watch
        return self.send(observation_acquire=pb.ObservationWatchAcquire(principal=watch.principal,
            view=watch.view, presentation_generation=watch.presentation_generation, expected=watch.fence))

    def acquire_watch(self):
        request = self.request_watch()
        self.respond(request, observation_watch=self.watch)
        self.assertEqual(self.result('observation_watch'), self.watch)

    def result(self, field, request_id=None):
        if field != 'observation_revoked':
            return super().result(field, request_id)
        reply = baseline.receive(self.stream, pb.Reply)
        self.assertTrue(reply.HasField(field), reply)
        self.assertEqual((reply.protocol_version, reply.request_id), (1, 0))
        return reply.observation_revoked

    def watch_event(self, watch=None, **fields):
        reply = pb.Reply(protocol_version=1, observation_revoked=watch or self.watch)
        for name, value in fields.items():
            setattr(reply, name, value)
        return frame(reply)

    def release_watch(self):
        return self.send(observation_release=pb.ObservationWatchRelease(id=self.watch.id,
            principal=self.watch.principal, view=self.watch.view,
            presentation_generation=self.watch.presentation_generation))

    def altered(self, field, value):
        watch = pb.ObservationWatch()
        watch.CopyFrom(self.watch)
        if field == 'view_id':
            watch.view.view_id = value
        elif field == 'epoch':
            watch.fence.epoch = value
        else:
            setattr(watch, field, value)
        return watch

    def test_exact_acquisition_and_repetition_preserve_identity(self):
        self.acquire_watch()
        self.acquire_watch()

    def test_idle_revocation_arrives_without_another_request(self):
        self.acquire_watch()
        self.peer.sendall(self.watch_event())
        self.assertEqual(self.result('observation_revoked'), self.watch)

    def test_early_revocation_is_retained_before_acquisition_reply(self):
        request = self.request_watch()
        self.peer.sendall(self.watch_event())
        self.respond(request, observation_watch=self.watch)
        self.assertEqual(self.result('observation_revoked'), self.watch)
        self.assertEqual(self.result('observation_watch'), self.watch)

    def test_revocation_during_unrelated_reply_keeps_correlation(self):
        self.acquire_watch()
        request = self.send(authorization_fence=pb.AuthorizationFenceRequest(principal=self.principal))
        self.peer.sendall(self.watch_event())
        self.respond(request, authorization_fence=self.watch.fence)
        self.assertEqual(self.result('observation_revoked'), self.watch)
        self.assertEqual(self.result('authorization_fence'), self.watch.fence)

    def test_duplicate_revocation_fails_closed(self):
        self.acquire_watch()
        self.peer.sendall(self.watch_event())
        self.result('observation_revoked')
        self.peer.sendall(self.watch_event())
        self.rejected()

    def test_unknown_revocation_fails_closed(self):
        self.peer.sendall(self.watch_event())
        self.rejected()

    def test_wrong_view_revocation_fails_closed(self):
        self.acquire_watch()
        self.peer.sendall(self.watch_event(self.altered('view_id', b'x' * 32)))
        self.rejected()

    def test_wrong_epoch_revocation_fails_closed(self):
        self.acquire_watch()
        self.peer.sendall(self.watch_event(self.altered('epoch', 4)))
        self.rejected()

    def test_wrong_generation_revocation_fails_closed(self):
        self.acquire_watch()
        self.peer.sendall(self.watch_event(self.altered('presentation_generation', 8)))
        self.rejected()

    def test_event_requires_zero_request_id(self):
        self.acquire_watch()
        self.peer.sendall(self.watch_event(request_id=3))
        self.rejected()

    def test_event_requires_current_protocol(self):
        self.acquire_watch()
        self.peer.sendall(self.watch_event(protocol_version=2))
        self.rejected()

    def test_substituted_view_in_acquisition_fails_closed(self):
        request = self.request_watch()
        self.respond(request, observation_watch=self.altered('view_id', b'x' * 32))
        self.rejected()

    def test_substituted_epoch_in_acquisition_fails_closed(self):
        request = self.request_watch()
        self.respond(request, observation_watch=self.altered('epoch', 4))
        self.rejected()

    def test_missing_watch_identity_fails_closed(self):
        request = self.request_watch()
        self.respond(request, observation_watch=self.altered('id', b''))
        self.rejected()

    def test_release_requires_exact_lease_receipt(self):
        self.acquire_watch()
        request = self.release_watch()
        self.respond(request, observation_released=pb.ObservationWatchReleased(id=b'x' * 32))
        self.rejected()

    def test_barrier_requires_exact_fence_receipt(self):
        request = self.send(observation_barrier=pb.ObservationBarrierRequest(fence=self.watch.fence))
        self.respond(request, observation_barrier=pb.ObservationBarrierReply(
            fence=pb.AuthorizationFence(incarnation=b'f' * 16, epoch=4), settled=True))
        self.rejected()

    def test_checked_barrier_preserves_pending_and_settled_states(self):
        for settled in (False, True):
            request = self.send(observation_barrier=pb.ObservationBarrierRequest(fence=self.watch.fence))
            self.respond(request, observation_barrier=pb.ObservationBarrierReply(fence=self.watch.fence, settled=settled))
            self.assertEqual(self.result('observation_barrier').settled, settled)

    def test_released_watch_cannot_revoke_replacement(self):
        self.acquire_watch()
        request = self.release_watch()
        self.respond(request, observation_released=pb.ObservationWatchReleased(id=self.watch.id))
        self.result('observation_released')
        old = pb.ObservationWatch()
        old.CopyFrom(self.watch)
        self.watch.id = b'n' * 32
        self.acquire_watch()
        self.peer.sendall(self.watch_event(old))
        self.rejected()

    def test_partial_event_remains_nonblocking_until_completion(self):
        self.acquire_watch()
        event = self.watch_event()
        self.peer.sendall(event[:2])
        self.assertFalse(select.select([self.stream], [], [], .05)[0])
        self.peer.sendall(event[2:])
        self.assertEqual(self.result('observation_revoked'), self.watch)

    def test_typed_denial_keeps_transport_usable(self):
        request = self.request_watch()
        self.respond(request, error=core.Error(code=core.FORBIDDEN, message='denied'))
        self.assertEqual(self.result('error').code, core.FORBIDDEN)
        self.acquire_watch()

    def test_observation_revocation_does_not_revoke_independent_control(self):
        self.acquire()
        self.acquire_watch()
        self.peer.sendall(self.watch_event())
        self.result('observation_revoked')
        self.acquire()

    def test_wrong_early_context_cannot_become_an_acquisition(self):
        self.request_watch()
        self.peer.sendall(self.watch_event(self.altered('view_id', b'x' * 32)))
        self.rejected()

    def test_repeated_acquisition_during_revocation_stays_inactive(self):
        self.acquire_watch()
        request = self.request_watch()
        self.peer.sendall(self.watch_event())
        self.respond(request, observation_watch=self.watch)
        self.result('observation_revoked')
        self.assertEqual(self.result('observation_watch'), self.watch)


if __name__ == '__main__':
    names = [name for name in ObservationClientTests.__dict__ if name.startswith('test_')]
    suite = unittest.TestSuite(ObservationClientTests(name) for name in names)
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
