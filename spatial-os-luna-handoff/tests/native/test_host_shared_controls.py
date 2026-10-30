"""Shared host controls through the production native session and real authority."""
import os
import select
import signal
import socket
import struct
import time
import unittest

# Preserve the production fixture's exact command line and native setup.
import test_host_session_production as production
import control_pb2 as controls
import core_pb2 as core
import host_actor_controller_pb2 as host_actor
import host_session_pb2 as host


class SharedHostControlsTests(production.ProductionHostSessionTests):
    def prepare(self, **options):
        config, descriptors = super().prepare(**options)
        directory = self.root / 'controls-preferences'
        directory.mkdir(mode=0o700)
        self.preferences = directory / 'controls.pb'
        config.shared_actor_controls = True
        config.controls_preferences_path = str(self.preferences)
        if getattr(self, 'independent_capture_requested', False):
            self.capture_peers = []
            for _ in range(4):
                producer, consumer = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
                self.capture_peers.append(producer)
                self.addCleanup(producer.close)
                descriptor = consumer.detach()
                config.trusted_capture_fds.append(descriptor)
                descriptors.append(descriptor)
        return config, descriptors

    def shared(self, operation, generation=0, value=None):
        if operation != 'captured_device' and getattr(self, 'keyboard_active', False):
            self.heartbeat_keyboard()
        self.host_request_id = getattr(self, 'host_request_id', 0) + 1
        request = host.HostControlRequest(protocol_version=1,
            request_id=self.host_request_id, generation=generation)
        if value is None:
            setattr(request, operation, True)
        else:
            getattr(request, operation).CopyFrom(value)
        payload = request.SerializeToString()
        self.control.sendall(struct.pack('!I', len(payload)) + payload)
        size, = struct.unpack('!I', production.exact(self.control, 4))
        self.assertGreater(size, 0)
        self.assertLessEqual(size, 1024 * 1024)
        reply = host.HostControlReply.FromString(production.exact(self.control, size))
        self.assertEqual((reply.protocol_version, reply.request_id), (1, request.request_id))
        if operation == 'transition_proposal' and reply.generation != generation:
            self.keyboard_active = False
        return reply

    def status(self, generation=0, *, require_available=True):
        reply = self.shared('controls_status', generation)
        self.assertTrue(reply.accepted, reply)
        self.assertTrue(reply.HasField('controls'), reply)
        if require_available:
            self.assertTrue(reply.controls.available, reply)
        return reply.controls

    def event(self, generation=0, epoch=None, **fields):
        if epoch is None:
            epoch = self.epoch
        self.packet_sequence = getattr(self, 'packet_sequence', 0) + 1
        event = controls.DeviceEvent(protocol_version=1, source_id='native-keyboard',
            source_generation=getattr(self, 'keyboard_source_generation', 1),
            event_sequence=self.packet_sequence, **fields)
        packet = controls.CapturedDeviceEvent(protocol_version=1, capture_generation=epoch,
            monotonic_nanoseconds=time.monotonic_ns(), packet_sequence=self.packet_sequence, event=event)
        return self.shared('captured_device', generation, packet)

    def connect_keyboard(self, generation=0):
        self.epoch = self.status(generation).capture_generation
        self.assertGreater(self.epoch, 0)
        self.keyboard_source_generation = getattr(self, 'keyboard_source_generation', 0) + 1
        reply = self.event(generation, connected=controls.DeviceConnected(kind=controls.KEYBOARD))
        self.assertTrue(reply.accepted, reply)
        self.keyboard_generation = generation
        self.keyboard_active = True

    def heartbeat_keyboard(self):
        reply = self.event(self.keyboard_generation, heartbeat=controls.DeviceHeartbeat())
        self.assertTrue(reply.accepted, reply)

    def key(self, keysym, pressed=True, generation=0):
        producer = getattr(self, 'capture_producer', None)
        if producer is not None:
            sequence = producer.key(keysym, pressed)
            self.wait_capture_processed(sequence, generation)
            return self.status(generation)
        reply = self.event(generation, key=controls.DeviceKey(keysym=keysym, pressed=pressed))
        self.assertTrue(reply.accepted, reply)
        return reply.controls

    def start_independent_capture(self, generation=0):
        # Import lazily to keep this fixture independent of the capture suite's
        # class hierarchy while reusing its real private transport producer.
        from test_host_continuous_capture import IndependentProducer
        state = self.status(generation)
        self.assertTrue(state.capture_active, state)
        self.assertGreater(state.capture_source_slot, 0)
        self.epoch = state.capture_generation
        producer = IndependentProducer(self.capture_peers[state.capture_source_slot - 1], self.epoch)
        self.capture_producer = producer
        self.keyboard_active = False
        self.addCleanup(producer.close)
        self.wait_capture_processed(2, generation)
        return producer

    def wait_capture_processed(self, sequence, generation=0):
        deadline = time.monotonic() + 5
        last_status = None
        while time.monotonic() < deadline:
            last_status = self.status(generation, require_available=False)
            self.assertFalse(last_status.capture_retired, last_status)
            if last_status.processed_capture_sequence >= sequence and last_status.available:
                return last_status
        self.fail('Capture and controls did not settle through the output barrier: %r' % last_status)

    def state(self, generation=0):
        if getattr(self, 'keyboard_active', False):
            self.heartbeat_keyboard()
        reply = self.request(generation=generation)
        self.assertTrue(reply.accepted, reply)
        return reply.inspection.state

    def stop(self, generation=0):
        reply = self.shared('propose', generation, core.ActorActionRequest(stop=core.ActorStopRequest()))
        self.assertTrue(reply.accepted, reply)
        self.assertTrue(reply.proposed.accepted, reply)

    def shutdown_checked(self):
        self.host_process.send_signal(signal.SIGTERM)
        self.assertTrue(self.terminal_cleanup_reply().cleanup_complete)
        self.assertEqual(self.host_process.wait(timeout=60), 0, self.host_log.read_text())
        self.assert_checked_cleanup()

    def test_navigation_menu_persistence_and_fresh_input_barrier(self):
        self.independent_capture_requested = True
        initial = self.ready_host()
        producer = self.start_independent_capture()
        self.assertEqual(self.status().active_profile, 'level-click')
        self.key('w')
        self.assertTrue(self.shared('controls_tick').accepted)
        moving = self.state()
        self.assertEqual(list(moving.axes), [0, 0, -1, 0, 0, 0])
        self.assertEqual(moving.translation_frame, core.LEVEL_TRANSLATION)
        # Slow actor commands may leave longer gaps than the source timeout.
        # A live independent capture must preserve held movement across them.
        time.sleep(5.0)
        producer.check()
        after_slow_gap = self.state()
        self.assertEqual(list(after_slow_gap.axes), [0, 0, -1, 0, 0, 0])
        self.assertNotEqual(after_slow_gap.pose.position, initial.inspection.state.pose.position)
        self.assertFalse(self.status().capture_retired)
        self.stop()
        stopped = self.state()
        self.assertEqual(list(stopped.axes), [0] * 6)
        self.assertTrue(self.shared('controls_tick').accepted)
        self.assertEqual(self.state().pose, stopped.pose)
        self.key('w')  # The still-held physical key cannot renew stopped motion.
        self.assertTrue(self.shared('controls_tick').accepted)
        self.assertEqual(list(self.state().axes), [0] * 6)
        self.assertTrue(self.key('F10').menu_open)
        menu = self.shared('controls_menu')
        self.assertTrue(menu.accepted, menu)
        self.assertTrue(menu.menu.visible)
        self.assertEqual(len(menu.menu.entries), 19)
        self.key('Right')
        saved = controls.ControlProfiles.FromString(self.preferences.read_bytes())
        self.assertEqual(saved.active_profile, self.status().active_profile)
        self.assertNotEqual(saved.active_profile, 'level-click')
        self.assertEqual(self.preferences.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.preferences.parent.stat().st_mode & 0o777, 0o700)
        self.key('Right', False)
        self.key('Escape')
        self.assertFalse(self.shared('controls_menu').menu.visible)
        self.assertTrue(self.shared('controls_tick').accepted)
        self.assertEqual(list(self.state().axes), [0] * 6)
        self.key('w', False)
        self.key('w')
        self.assertTrue(self.shared('controls_tick').accepted)
        self.assertEqual(list(self.state().axes), [0, 0, -1, 0, 0, 0])
        # A living capture sends heartbeats independently of root requests.
        # Real silence retires the capture binding and must stop held motion.
        producer.silence()
        deadline = time.monotonic() + 3
        expired_status = self.status(require_available=False)
        while not expired_status.capture_retired and time.monotonic() < deadline:
            time.sleep(.02)
            expired_status = self.status(require_available=False)
        self.assertTrue(expired_status.capture_retired, expired_status)
        self.assertFalse(expired_status.capture_active, expired_status)
        expired = self.state()
        self.assertEqual(list(expired.axes), [0] * 6)
        self.assertEqual(self.state().pose, expired.pose)
        stale = self.event(key=controls.DeviceKey(keysym='w', pressed=True))
        self.assertFalse(stale.accepted, stale)
        self.assertIn('capture binding', stale.detail.lower())
        self.assertTrue(self.status().available)
        self.stop()
        self.shutdown_checked()

    def test_shared_a_b_a_retires_capture_without_restarting_native_services(self):
        first = self.ready_host(second=True)
        before = self.retained_pids()
        starts = {pid: self.process_start(pid) for group in before.values() for pid in group}
        starts[self.server.pid] = self.process_start(self.server.pid)
        pin = self.compositor_pin()
        self.connect_keyboard()
        a, b = [value.world_id for value, _, _ in self.worlds]
        for generation, destination in ((0, b), (1, a)):
            old_epoch = self.epoch
            proposal = host_actor.HostActorTransitionRequest(protocol_version=1,
                transition_id=os.urandom(32), destination_world_id=destination)
            reply = self.shared('transition_proposal', generation, proposal)
            self.assertTrue(reply.accepted, reply)
            self.assertTrue(reply.transition.actor_transition.destination_activation_confirmed, reply)
            self.assertEqual(reply.generation, generation + 1)
            self.assertEqual(self.retained_pids(), before)
            self.assertEqual({pid: self.process_start(pid) for pid in starts}, starts)
            self.assertEqual(select.select([pin], [], [], 0)[0], [])
            state = self.state(generation + 1)
            self.assertEqual(state.id, first.inspection.state.id)
            self.assertEqual(state.workspace, '')
            stale = self.event(generation + 1, old_epoch, key=controls.DeviceKey(keysym='w', pressed=True))
            self.assertFalse(stale.accepted, stale)
            self.assertIn('capture binding', stale.detail)
            self.connect_keyboard(generation + 1)
            self.assertGreater(self.epoch, old_epoch)
            self.stop(generation + 1)
        self.assertEqual(self.state(2).pose, first.inspection.state.pose)
        self.shutdown_checked()

    def test_denied_destination_retains_source_with_fresh_sequencing(self):
        first = self.ready_host(denied=True)
        self.connect_keyboard()
        old_epoch = self.epoch
        self.stop()
        proposal = host_actor.HostActorTransitionRequest(protocol_version=1,
            transition_id=os.urandom(32), destination_world_id=self.worlds[1][0].world_id)
        reply = self.shared('transition_proposal', value=proposal)
        self.assertTrue(reply.accepted, reply)
        self.assertFalse(reply.transition.actor_transition.destination_activation_confirmed, reply)
        self.assertTrue(reply.transition.actor_control_available, reply)
        self.assertEqual(self.state(reply.generation).id, first.inspection.state.id)
        self.assertGreater(self.status(reply.generation).capture_generation, old_epoch)
        self.assertFalse(self.event(reply.generation, old_epoch,
            key=controls.DeviceKey(keysym='w', pressed=True)).accepted)
        self.stop(reply.generation)
        self.connect_keyboard(reply.generation)
        self.key('w', generation=reply.generation)
        self.assertTrue(self.shared('controls_tick', reply.generation).accepted)
        self.assertEqual(list(self.state(reply.generation).axes), [0, 0, -1, 0, 0, 0])
        self.stop(reply.generation)
        self.shutdown_checked()

    def test_strict_durable_denial_blanks_foreground_and_preserves_owned_worlds(self):
        self.ready_host()
        pixels = self.foreground_pixels()
        self.assertTrue(any(pixels[0::4]) or any(pixels[1::4]) or any(pixels[2::4]))
        before = self.retained_pids()
        starts = {pid: self.process_start(pid) for group in before.values() for pid in group}
        pin = self.compositor_pin()
        denied = self.shared('propose', value=core.ActorActionRequest(
            process_launch=core.ProcessLaunchRequest(argv=['/bin/true'], operation_id='no-workspace')))
        self.assertFalse(denied.accepted, denied)
        self.assertIn('no selected workspace', denied.detail.lower())
        self.assertFalse(self.shared('controls_status').controls.available)
        self.assertFalse(self.shared('controls_menu').menu.visible)
        self.assertFalse(self.shared('propose', value=core.ActorActionRequest(stop=core.ActorStopRequest())).accepted)
        self.assert_foreground_blank()
        self.assertEqual(self.retained_pids(), before)
        self.assertEqual({pid: self.process_start(pid) for pid in starts}, starts)
        self.assertEqual(select.select([pin], [], [], 0)[0], [])
        time.sleep(.08)
        self.assert_foreground_blank()
        self.shutdown_checked()


if __name__ == '__main__':
    names = (
        'test_navigation_menu_persistence_and_fresh_input_barrier',
        'test_shared_a_b_a_retires_capture_without_restarting_native_services',
        'test_denied_destination_retains_source_with_fresh_sequencing',
        'test_strict_durable_denial_blanks_foreground_and_preserves_owned_worlds',
    )
    suite = unittest.TestSuite(SharedHostControlsTests(name) for name in names)
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
