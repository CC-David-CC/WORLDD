"""Independent counterexamples for private observation recovery receipts."""
import copy
import unittest
import broker_pb2 as pb
import actor_local_view_pb2 as local
import grants_pb2 as grant
import test_control_reply as baseline


class ObservationReplyTests(baseline.ReplyTests):
    def watch(self, index=1):
        principal = grant.ActorRef(issuer_id=self.owner.issuer_id, actor_id=b'u' * 32)
        view = local.ActorLocalViewIdentity(protocol_version=1, world_id='world',
            actor_id='identity:' + principal.issuer_id.hex() + ':' + principal.actor_id.hex(),
            view_id=index.to_bytes(32, 'big'), authority_service_incarnation=self.owner.launch.service_incarnation,
            authority_generation=self.owner.launch.generation)
        return self.reply.state.observation_watches.add(id=(index + 1000).to_bytes(32, 'big'),
            principal=principal, view=view, presentation_generation=7,
            fence=pb.AuthorizationFence(incarnation=b'f' * 16, epoch=3))

    def recovered_watch(self):
        self.recovery()
        watch = copy.deepcopy(self.watch())
        cleanup = self.reply.state.recovery.cleanup
        cleanup.retired_presentation.world_id = watch.view.world_id
        cleanup.retired_presentation.presentation_generation = watch.presentation_generation
        self.request.recover.CopyFrom(cleanup)
        self.reply.state.recovery.observation_released.add().CopyFrom(watch)
        self.reply.state.ClearField('observation_watches')
        return watch

    def test_combined_control_and_observation_snapshot_is_preserved(self):
        self.reservation()
        self.watch()
        self.check(True)

    def test_registration_cannot_claim_existing_observation(self):
        self.watch()
        self.request.register.CopyFrom(self.owner)
        self.check(False)

    def test_malformed_observation_view_is_rejected(self):
        self.watch().view.view_id = b'short'
        self.check(False)

    def test_observation_must_match_owner_generation(self):
        self.watch().view.authority_generation += 1
        self.check(False)

    def test_observation_must_match_owner_incarnation(self):
        self.watch().view.authority_service_incarnation = b'x' * 32
        self.check(False)

    def test_principal_and_view_must_describe_same_actor(self):
        self.watch().principal.actor_id = b'x' * 32
        self.check(False)

    def test_duplicate_observation_id_or_view_is_rejected(self):
        self.watch()
        original = copy.deepcopy(self.reply)
        duplicate = self.reply.state.observation_watches.add()
        duplicate.CopyFrom(original.state.observation_watches[0])
        duplicate.view.view_id = b'x' * 32
        self.check(False)
        self.reply.CopyFrom(original)
        duplicate = self.reply.state.observation_watches.add()
        duplicate.CopyFrom(original.state.observation_watches[0])
        duplicate.id = b'x' * 32
        self.check(False)

    def test_observation_cannot_reuse_control_reservation_id(self):
        self.reservation()
        self.watch().id = self.reply.state.reservations[0].id
        self.check(False)

    def test_exact_recovery_and_later_inspection_preserve_observation_receipt(self):
        self.recovered_watch()
        self.check(True)
        self.request.inspect.CopyFrom(self.owner)
        self.check(True)

    def test_recovery_requires_matching_presentation_retirement(self):
        self.recovered_watch()
        original_request = copy.deepcopy(self.request)
        original_reply = copy.deepcopy(self.reply)
        self.request.recover.ClearField('retired_presentation')
        self.reply.state.recovery.cleanup.ClearField('retired_presentation')
        self.check(False)
        self.request.CopyFrom(original_request)
        self.reply.CopyFrom(original_reply)
        self.request.recover.retired_presentation.presentation_generation += 1
        self.reply.state.recovery.cleanup.CopyFrom(self.request.recover)
        self.check(False)

    def test_recovered_state_cannot_keep_active_observation(self):
        self.recovered_watch()
        self.watch(index=2)
        self.check(False)

    def test_combined_snapshot_byte_budget_is_enforced(self):
        for index in range(1, 257):
            watch = self.watch(index=index)
            watch.view.world_id = 'w' * 256
            if sum(w.ByteSize() + 16 for w in self.reply.state.observation_watches) > 60000:
                break
        self.assertLessEqual(self.reply.ByteSize(), 65536)
        self.check(False)


if __name__ == '__main__':
    names = [name for name in ObservationReplyTests.__dict__ if name.startswith('test_')]
    suite = unittest.TestSuite(ObservationReplyTests(name) for name in names)
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
