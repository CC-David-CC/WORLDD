"""Check typed recovery transport without simulating runtime effects."""
import unittest
from harness.wire import make_request, result_value, OUTCOMES, pb


class RecoveryWireTests(unittest.TestCase):
    def test_all_fault_boundaries_round_trip_as_typed_enums(self):
        cases = [('world.before_commit', pb.FAULT_WORLD_BEFORE_COMMIT),
                 ('world.after_commit_before_reply', pb.FAULT_WORLD_AFTER_COMMIT_BEFORE_REPLY),
                 ('git.after_worktree_before_ready', pb.FAULT_GIT_AFTER_WORKTREE_BEFORE_READY),
                 ('storage.next_write', pb.FAULT_STORAGE_NEXT_WRITE)]
        for point, expected in cases:
            params = dict(point=point, action='exit')
            if point == 'storage.next_write':
                params.update(action='error', errno='ENOSPC')
            request = make_request('fault.arm', params)
            decoded = pb.Request.FromString(request.SerializeToString())
            self.assertEqual(decoded.WhichOneof('operation'), 'fault_arm')
            self.assertEqual(decoded.fault_arm.point, expected)
            self.assertEqual(decoded.fault_arm.action, pb.FAULT_ERROR if 'errno' in params else pb.FAULT_EXIT)
            self.assertEqual(decoded.fault_arm.HasField('storage_error'), 'errno' in params)
            if 'errno' in params:
                self.assertEqual(decoded.fault_arm.storage_error, pb.STORAGE_FAULT_ENOSPC)
        self.assertEqual(OUTCOMES['fault_arm'], 'fault_armed')

    def test_fault_fields_reject_unknown_values_and_parameters(self):
        good = dict(point='storage.next_write', action='error', errno='ENOSPC')
        for field in good:
            for invalid in [None, True, 1, [], {}, 'unknown', '']:
                with self.subTest(field=field, invalid=invalid), self.assertRaises(ValueError):
                    make_request('fault.arm', dict(good, **{field: invalid}))
        for invalid in [None, [], dict(good, repeat=2), dict(good, storage_error='ENOSPC')]:
            with self.assertRaises(ValueError):
                make_request('fault.arm', invalid)

    def test_absent_fault_parameters_remain_unspecified(self):
        request = make_request('fault.arm', {})
        self.assertEqual(request.WhichOneof('operation'), 'fault_arm')
        self.assertEqual(request.fault_arm.point, pb.FAULT_POINT_UNSPECIFIED)
        self.assertEqual(request.fault_arm.action, pb.FAULT_ACTION_UNSPECIFIED)
        self.assertFalse(request.fault_arm.HasField('storage_error'))

    def test_surface_slot_preserves_identity_and_rejects_extra_fields(self):
        request = make_request('surface.attach_slot', dict(id='live:session:42', slot='editor-slot'))
        decoded = pb.Request.FromString(request.SerializeToString())
        self.assertEqual(decoded.WhichOneof('operation'), 'surface_attach_slot')
        self.assertEqual(decoded.surface_attach_slot.id, 'live:session:42')
        self.assertEqual(decoded.surface_attach_slot.slot, 'editor-slot')
        self.assertEqual(OUTCOMES['surface_attach_slot'], 'surface_record')
        for invalid in [dict(id=42, slot='s'), dict(id='s', slot=None), dict(id='s', slot='s', xid=42)]:
            with self.assertRaises(ValueError):
                make_request('surface.attach_slot', invalid)

    def test_surface_handle_preserves_uint64_and_message_presence(self):
        for generation in [0, 1, 2**53 + 1, 2**64 - 1]:
            request = make_request('surface.validate', dict(handle=dict(id='s', generation=generation)))
            decoded = pb.Request.FromString(request.SerializeToString())
            self.assertEqual(decoded.surface_validate.handle.id, 's')
            self.assertEqual(decoded.surface_validate.handle.generation, generation)
        self.assertEqual(OUTCOMES['surface_validate'], 'surface_validation')
        self.assertFalse(make_request('surface.validate', {}).surface_validate.HasField('handle'))
        self.assertTrue(make_request('surface.validate', dict(handle={})).surface_validate.HasField('handle'))

    def test_surface_handle_rejects_invalid_shapes_and_types(self):
        invalid_handles = [None, [], 's', dict(id=1), dict(id='s', xid=1)]
        invalid_handles += [dict(id='s', generation=value) for value in [True, -1, 2**64, 1.5, '1', None]]
        for handle in invalid_handles:
            with self.subTest(handle=handle), self.assertRaises(ValueError):
                make_request('surface.validate', dict(handle=handle))
        with self.assertRaises(ValueError):
            make_request('surface.validate', dict(handle=dict(id='s', generation=1), token='actor'))

    def test_recovery_replies_require_explicit_status(self):
        for outcome, field in [('fault_armed', 'armed'), ('surface_validation', 'valid')]:
            reply = pb.Reply(protocol_version=1)
            body = getattr(reply, outcome)
            body.SetInParent()
            with self.assertRaises(ValueError):
                result_value(reply)
            for status in [False, True]:
                setattr(body, field, status)
                self.assertEqual(result_value(pb.Reply.FromString(reply.SerializeToString())), {field: status})

    def test_fixture_fault_is_not_an_actor_action(self):
        request = make_request('actor.act', dict(token='t', seq=1, op='fault.arm',
                                               args=dict(point='world.before_commit', action='exit')))
        self.assertEqual(request.WhichOneof('operation'), 'actor_act')
        self.assertIsNone(request.actor_act.WhichOneof('action'))
        self.assertNotIn('fault_arm', pb.ActorActionRequest.DESCRIPTOR.fields_by_name)
