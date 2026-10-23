"""Actual native v3 pin/arm checks. These do not establish lifetime delivery."""
import time
import unittest

import native_input_lifecycle_v3_pb2 as lifecycle
import native_input_observer_control_v2_pb2 as control
from test_native_observer_endpoint import NativeObserverEndpointTests


class NativeLifecycleEndpointV3Tests(NativeObserverEndpointTests):
    cause = lifecycle.TARGET_DESTRUCTION

    def setup_context(self):
        common = super().setup_context()
        self.survivor = self.peer.create_window()
        self.assertIsNone(self.compositor.operation(8, self.compositor.pack('I', self.survivor)))
        return common

    def lifecycle_request(self, common):
        request = lifecycle.LifecycleSetupRequestV3(
            common=common, peer_target_xid=self.survivor,
            peer_target_spatial_generation=self.target(self.survivor).generation,
            cause=self.cause)
        self.lifecycle_setup = request
        return request

    def send_lifecycle_setup(self, request):
        raw = lifecycle.LifecycleControlEnvelopeV3(lifecycle_setup_request=request)
        self.endpoint.sendall(raw.SerializeToString(deterministic=True))
        raw = self.endpoint.recv(4097)
        self.assertTrue(raw, self.log_path.read_text(errors='replace'))
        self.assertLessEqual(len(raw), 4096)
        reply = lifecycle.LifecycleEndpointReplyV3.FromString(raw)
        self.assertEqual(reply.WhichOneof('message'), 'lifecycle_setup_reply', reply)
        self.lifecycle_pins = reply.lifecycle_setup_reply
        return self.lifecycle_pins

    def send_setup(self, common):
        return self.send_lifecycle_setup(self.lifecycle_request(common)).old_pins

    def lifecycle_arm_context(self):
        common = super().arm_context()
        return lifecycle.LifecycleArmV3(common=common,
            peer_window=self.lifecycle_pins.peer_window, cause=self.cause)

    def send_lifecycle_arm(self, arm, suffix=b''):
        raw = lifecycle.LifecycleControlEnvelopeV3(lifecycle_arm=arm)
        self.endpoint.sendall(raw.SerializeToString(deterministic=True) + suffix)

    def accept_arm(self, arm):
        self.send_lifecycle_arm(arm)
        raw = self.endpoint.recv(4097)
        self.assertTrue(raw, self.log_path.read_text(errors='replace'))
        self.assertLessEqual(len(raw), 4096)
        reply = lifecycle.LifecycleEndpointReplyV3.FromString(raw)
        self.assertEqual(reply.WhichOneof('message'), 'lifecycle_arm_accepted', reply)
        self.assertEqual(reply.lifecycle_arm_accepted.accepted_command, arm)
        self.assertTrue(self.held(self.owner, 38))
        self.assertTrue(self.held(self.owner, 50))
        self.assertEqual(self.focus(self.owner), self.window)

    def invalid_lifecycle_setup(self, mutate):
        request = self.lifecycle_request(self.setup_context())
        mutate(request)
        raw = lifecycle.LifecycleControlEnvelopeV3(lifecycle_setup_request=request)
        self.endpoint.sendall(raw.SerializeToString(deterministic=True))
        self.assert_rejected()

    def invalid_lifecycle_arm(self, mutate):
        arm = self.lifecycle_arm_context()
        mutate(arm)
        self.send_lifecycle_arm(arm)
        self.assert_rejected('invalid arm or native pin')
        self.assertTrue(self.held(self.owner, 38))
        self.assertTrue(self.held(self.owner, 50))
        self.cancel()

    def test_setup_pins_distinct_same_application_survivor_and_exact_request(self):
        request = self.lifecycle_request(self.setup_context())
        pins = self.send_lifecycle_setup(request)
        self.assertEqual(pins.accepted_request, request)
        self.assertEqual(pins.peer_window.xid, self.survivor)
        self.assertNotEqual(pins.peer_window.xid, pins.old_pins.app_target.xid)
        self.assertNotEqual(pins.peer_window.native_object_epoch, pins.old_pins.app_target.native_object_epoch)
        self.assertEqual(pins.peer_window.application, pins.old_pins.app_target.application)
        self.assertNotEqual(pins.peer_window.owner.client_id, pins.old_pins.app_target_owner.client_id)

    def test_destruction_arm_preserves_native_held_state(self):
        self.accept_arm(self.lifecycle_arm_context())
        self.cancel()

    def test_disconnect_arm_preserves_native_held_state(self):
        self.cause = lifecycle.ACTOR_DISCONNECT
        self.accept_arm(self.lifecycle_arm_context())
        self.cancel()

    def test_revocation_arm_preserves_native_held_state(self):
        self.cause = lifecycle.ACTOR_REVOCATION
        self.accept_arm(self.lifecycle_arm_context())
        self.cancel()

    def test_same_surviving_target_is_valid_for_disconnect(self):
        self.cause = lifecycle.ACTOR_DISCONNECT
        common = self.setup_context()
        self.survivor = self.window
        pins = self.send_lifecycle_setup(self.lifecycle_request(common))
        self.assertEqual(pins.peer_window, pins.old_pins.app_target)

    def test_destruction_rejects_original_target_as_survivor(self):
        self.invalid_lifecycle_setup(lambda r: (
            setattr(r, 'peer_target_xid', r.common.target_xid),
            setattr(r, 'peer_target_spatial_generation', r.common.target_spatial_generation)))

    def test_foreign_workspace_survivor_rejects(self):
        self.invalid_lifecycle_setup(lambda r: (
            setattr(r, 'peer_target_xid', self.other_window),
            setattr(r, 'peer_target_spatial_generation', self.target(self.other_window).generation)))

    def test_stale_survivor_generation_rejects(self):
        self.invalid_lifecycle_setup(lambda r: setattr(r, 'peer_target_spatial_generation',
                                                       r.peer_target_spatial_generation + 1))

    def test_zero_survivor_rejects(self):
        self.invalid_lifecycle_setup(lambda r: setattr(r, 'peer_target_xid', 0))

    def test_unknown_setup_cause_rejects(self):
        self.invalid_lifecycle_setup(lambda r: setattr(r, 'cause', 99))

    def test_unspecified_setup_cause_rejects(self):
        self.invalid_lifecycle_setup(lambda r: setattr(r, 'cause', 0))

    def test_arm_changed_cause_rejects(self):
        self.invalid_lifecycle_arm(lambda a: setattr(a, 'cause', lifecycle.ACTOR_DISCONNECT))

    def test_arm_changed_survivor_epoch_rejects(self):
        self.invalid_lifecycle_arm(lambda a: setattr(a.peer_window, 'native_object_epoch',
                                                     a.peer_window.native_object_epoch + 1))

    def test_arm_changed_survivor_owner_rejects(self):
        self.invalid_lifecycle_arm(lambda a: a.peer_window.owner.CopyFrom(a.common.expected_window_owner))

    def test_arm_changed_old_device_epoch_rejects(self):
        self.invalid_lifecycle_arm(lambda a: setattr(a.common.old_actor_devices[0], 'native_object_epoch',
                                                     a.common.old_actor_devices[0].native_object_epoch + 1))

    def test_arm_changed_deadline_rejects(self):
        self.invalid_lifecycle_arm(lambda a: setattr(a.common, 'deadline_monotonic_ns',
                                                     a.common.deadline_monotonic_ns + 1))

    def test_arm_destroyed_survivor_rejects_without_cancelling_old_actor(self):
        arm = self.lifecycle_arm_context()
        self.assertIsNone(self.peer.operation(4, self.peer.pack('I', self.survivor)))
        self.send_lifecycle_arm(arm)
        self.assert_rejected('invalid arm or native pin')
        self.assertTrue(self.held(self.owner, 38))
        self.cancel()

    def test_arm_unknown_fields_rejects(self):
        arm = self.lifecycle_arm_context()
        self.send_lifecycle_arm(arm, b'\x28\x01')
        self.assert_rejected('invalid arm or native pin')
        self.cancel()

    def test_legacy_arm_cannot_replace_lifecycle_arm(self):
        arm = self.lifecycle_arm_context()
        self.endpoint.sendall(control.ObserverControlEnvelopeV2(arm=arm.common).SerializeToString(deterministic=True))
        self.assert_rejected('invalid arm or native pin')
        self.cancel()

    def test_lifecycle_arm_cannot_follow_legacy_setup(self):
        common = self.setup_context()
        pins = NativeObserverEndpointTests.send_setup(self, common)
        # Use an otherwise complete common arm from the trusted native fixture.
        # This connection has already consumed setup, so construct the held state here.
        import native_input_observer_v2_pb2 as observer
        import test_actor_binding as binding
        self.call(button=binding.inp.Button(seat=self.seat.seat, target=self.target(self.window),
                  x=4, y=5, button=1, pressed=True, focus=True))
        self.key(38, True); self.key(50, True)
        arm = observer.ObserverArmV2(session_id=common.session_id, arm_id=common.arm_id,
            arm_monotonic_ns=time.monotonic_ns(), deadline_monotonic_ns=common.deadline_monotonic_ns,
            old_world_actor_id=1, peer_world_actor_id=2, old_native_seat_id=self.seat.seat,
            peer_native_seat_id=self.second.seat, presentation_generation=common.presentation_generation,
            expected_application=common.expected_application, expected_window=pins.app_target,
            expected_window_owner=pins.app_target_owner, expected_seat_owner=pins.compositor_seat_owner,
            expected_recipient=pins.probe_marker.privileged_owner,
            expected_grab_owner=pins.probe_marker.privileged_owner,
            old_actor_devices=pins.old_actor_devices, peer_actor_devices=pins.peer_actor_devices,
            expected_xi2_event_type=4, expected_detail=1, expected_held_keys=[38, 50],
            expected_held_buttons=[1], expected_modifier_keycode=50)
        arm.old_actor.issuer_id = arm.peer_actor.issuer_id = b'i' * 32
        arm.old_actor.actor_id = b'o' * 32; arm.peer_actor.actor_id = b'p' * 32
        self.send_lifecycle_arm(lifecycle.LifecycleArmV3(common=arm, peer_window=pins.app_target,
                                                       cause=lifecycle.ACTOR_DISCONNECT))
        self.assert_rejected('invalid arm or native pin')
        self.cancel()

    def test_lifecycle_setup_replay_rejects(self):
        request = self.lifecycle_request(self.setup_context())
        self.send_lifecycle_setup(request)
        self.endpoint.sendall(lifecycle.LifecycleControlEnvelopeV3(
            lifecycle_setup_request=request).SerializeToString(deterministic=True))
        self.assert_rejected('one-shot setup replay')

    def test_lifecycle_arm_replay_rejects(self):
        arm = self.lifecycle_arm_context()
        self.accept_arm(arm)
        self.send_lifecycle_arm(arm)
        self.assert_rejected('one-shot arm replay')
        self.cancel()

    def test_lifecycle_setup_deadline_is_not_extended(self):
        request = self.lifecycle_request(self.setup_context())
        request.common.deadline_monotonic_ns = time.monotonic_ns() + 200_000_000
        self.send_lifecycle_setup(request)
        self.assert_rejected('deadline')

    def test_lifecycle_arm_before_setup_rejects(self):
        self.start_endpoint()
        self.send_lifecycle_arm(lifecycle.LifecycleArmV3())
        self.assert_rejected()


if __name__ == '__main__':
    suite = unittest.TestSuite(NativeLifecycleEndpointV3Tests(name)
        for name in NativeLifecycleEndpointV3Tests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
