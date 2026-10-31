"""Independent human capture through the production receiver and native session."""
import concurrent.futures
import os
import signal
from pathlib import Path
import queue
import socket
import threading
import time
import unittest
import control_pb2 as controls
import core_pb2 as core
import host_actor_controller_pb2 as actor
import test_host_shared_controls as shared


class IndependentProducer:
    def __init__(self, endpoint, epoch):
        self.endpoint = endpoint
        endpoint.setblocking(False)
        self.epoch = epoch
        self.sequence = 0
        self.sources = {'fixture-keyboard': 0, 'fixture-mouse': 0}
        self.commands = queue.Queue(maxsize=64)
        self.stopped = threading.Event()
        self.ready = threading.Event()
        self.failure = None
        self.thread = threading.Thread(target=self.run, name='independent-human-capture')
        self.thread.start()
        if not self.ready.wait(3):
            raise RuntimeError('Independent producer failed to start')
        self.check()

    def emit(self, source, **fields):
        self.sources[source] += 1
        self.sequence += 1
        event = controls.DeviceEvent(protocol_version=1, source_id=source, source_generation=1,
                                     event_sequence=self.sources[source], **fields)
        packet = controls.CapturedDeviceEvent(protocol_version=1, capture_generation=self.epoch,
            packet_sequence=self.sequence, monotonic_nanoseconds=time.monotonic_ns(), event=event)
        data = packet.SerializeToString()
        if self.endpoint.send(data) != len(data):
            raise RuntimeError('Partial private capture datagram')
        return self.sequence

    def run(self):
        try:
            self.emit('fixture-keyboard', connected=controls.DeviceConnected(kind=controls.KEYBOARD))
            self.emit('fixture-mouse', connected=controls.DeviceConnected(kind=controls.MOUSE))
            self.ready.set()
            heartbeat = time.monotonic() + .1
            while not self.stopped.is_set():
                try:
                    fields, result = self.commands.get(timeout=max(0, heartbeat - time.monotonic()))
                except queue.Empty:
                    fields = result = None
                if fields is not None:
                    result.set_result(self.emit('fixture-keyboard', **fields))
                if time.monotonic() >= heartbeat:
                    for source in self.sources:
                        self.emit(source, heartbeat=controls.DeviceHeartbeat())
                    heartbeat = time.monotonic() + .1
        except BaseException as error:
            self.failure = error
            self.ready.set()

    def check(self):
        if self.failure is not None:
            raise RuntimeError('Independent capture producer failed') from self.failure
        if not self.thread.is_alive():
            raise RuntimeError('Independent capture producer stopped')

    def key(self, name, pressed=True):
        self.check()
        result = concurrent.futures.Future()
        self.commands.put(({'key': controls.DeviceKey(keysym=name, pressed=pressed)}, result), timeout=1)
        return result.result(timeout=3)

    def silence(self):
        self.stopped.set()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise RuntimeError('Independent producer did not stop')

    def close(self):
        self.silence()
        self.endpoint.close()


class ContinuousCaptureTests(shared.SharedHostControlsTests):
    def prepare(self, capture=True, native=False, **options):
        config, descriptors = super().prepare(**options)
        continuation=getattr(self,'presentation_continuations_override',None)
        if continuation is None:continuation=os.environ.get('SPATIAL_TEST_PRESENTATION_CONTINUATIONS') == '1'
        if continuation:config.enable_presentation_continuations = True
        self.presentation_continuations_selected=config.enable_presentation_continuations
        compositor = shared.production.HOST_SESSION_BINARY.with_name('spatialwm')
        self.assertTrue(compositor.is_file() and os.access(compositor, os.X_OK))
        config.compositor_executable = str(compositor)
        if native:
            config.native_capture.spacemouse = True
            capture = False
        self.capture_peers = []
        if capture:
            for _ in range(4):
                producer, consumer = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
                self.capture_peers.append(producer)
                self.addCleanup(producer.close)
                descriptor = consumer.detach()
                config.trusted_capture_fds.append(descriptor)
                descriptors.append(descriptor)
        return config, descriptors

    def launch_host(self, *args, **kwargs):
        # Register first so this runs after the inherited process cleanup.
        def save_host_log():
            evidence = os.environ.get('SPATIAL_SECURITY_EVIDENCE')
            log = getattr(self, 'host_log', None)
            if evidence and log is not None and log.is_file():
                directory = Path(evidence)
                directory.mkdir(parents=True, exist_ok=True)
                (directory / (self._testMethodName + '-host-session.log')).write_bytes(log.read_bytes())
        self.addCleanup(save_host_log)
        return super().launch_host(*args, **kwargs)

    def producer(self, generation=0):
        status = self.status(generation)
        self.assertTrue(status.capture_active, status)
        self.assertGreater(status.capture_source_slot, 0)
        producer = IndependentProducer(self.capture_peers[status.capture_source_slot - 1], status.capture_generation)
        self.addCleanup(producer.close)
        self.wait_processed(2, generation)
        return producer

    def wait_processed(self, sequence, generation=0):
        deadline = time.monotonic() + 5
        last_status = None
        while time.monotonic() < deadline:
            status = self.status(generation, require_available=False)
            last_status = status
            self.assertFalse(status.capture_retired, status)
            if status.processed_capture_sequence >= sequence and status.available:
                return status
        self.fail('Captured input did not reach checked handling: %r' % last_status)

    def wait_accepted(self, sequence, generation=0, timeout=.35):
        deadline = time.monotonic() + timeout
        last_status = None
        while time.monotonic() < deadline:
            status = self.status(generation, require_available=False)
            last_status = status
            self.assertFalse(status.capture_retired, status)
            if status.accepted_capture_sequence >= sequence and status.available:
                return status
        self.fail('Capture input was not accepted into the bounded actor queue: %r' % last_status)

    def wait_presented(self, sequence, generation=0):
        deadline = time.monotonic() + 5
        last_status = None
        while time.monotonic() < deadline:
            status = self.status(generation, require_available=False)
            last_status = status
            self.assertFalse(status.capture_retired, status)
            if status.presented_capture_sequence >= sequence and status.available:
                return status
        self.fail('Native output did not settle for capture sequence %d: %r' % (sequence, last_status))

    def test_continuous_capture_navigation_menu_and_f12(self):
        self.ready_host()
        producer = self.producer()
        before = self.foreground_pixels()
        opened = producer.key('F10')
        self.wait_processed(opened)
        self.wait_presented(opened)
        self.assertTrue(self.shared('controls_menu').menu.visible)
        self.assertNotEqual(self.foreground_pixels(), before)
        self.wait_processed(producer.key('F10', False))
        escaped = producer.key('Escape')
        self.wait_processed(escaped)
        self.wait_presented(escaped)
        self.assertFalse(self.shared('controls_menu').menu.visible)
        self.wait_processed(producer.key('w'))
        self.assertTrue(self.shared('controls_tick').accepted)
        self.assertEqual(list(self.state().axes), [0, 0, -1, 0, 0, 0])
        self.wait_processed(producer.key('F12'))
        self.assertEqual(list(self.state().axes), [0] * 6)
        # Root exchanges do not supply capture heartbeats.
        until = time.monotonic() + 1
        while time.monotonic() < until:
            self.assertEqual(list(self.state().axes), [0] * 6)
            self.assertFalse(self.status().capture_retired)
        producer.check()
        self.wait_processed(producer.key('w', False))
        self.wait_processed(producer.key('w'))
        self.assertTrue(self.shared('controls_tick').accepted)
        self.assertEqual(list(self.state().axes), [0, 0, -1, 0, 0, 0])
        self.stop()
        self.shutdown_checked()

    def test_stalled_compositor_does_not_block_actor_capture_or_motion(self):
        self.presentation_continuations_override=True
        self.ready_host()
        producer=self.producer()
        self.wait_presented(2)
        before_output=self.status().presented_capture_sequence
        compositor_pids=self.live_compositor_pids()
        self.assertEqual(len(compositor_pids),1,compositor_pids)
        compositor=compositor_pids[0]
        before=self.state().pose.position
        before_pixels=self.foreground_pixels()
        os.kill(compositor,signal.SIGSTOP)
        try:
            started=time.monotonic()
            opened=producer.key('F10')
            self.wait_processed(opened)
            self.assertLess(self.status().presented_capture_sequence,opened,
                'A stopped compositor reported that the new screen output had settled')
            self.assertLess(time.monotonic()-started,.35,
                'Actor capture waited beyond the approved 350 ms observation window for a stalled controls presentation')
            self.wait_processed(producer.key('F10',False))
            closed=producer.key('Escape')
            self.wait_processed(closed)
            moving=producer.key('w')
            self.wait_processed(moving)
            time.sleep(.35)
            released=producer.key('w',False)
            self.wait_processed(released)
            self.assertEqual(self.status().presented_capture_sequence,before_output,
                'Capture input advanced the output receipt while the compositor was stopped')
        finally:
            try:os.kill(compositor,signal.SIGCONT)
            except ProcessLookupError:pass
        # Inspect only after restoring the compositor. The capture release was
        # already processed while it was stopped, so any displacement must have
        # accumulated during the stalled-presentation interval.
        after=self.state().pose.position
        self.assertLess(after.z,before.z-.1,'Actor movement stopped with the controls presenter')
        shown=producer.key('F10')
        self.wait_processed(shown)
        self.wait_presented(released)
        self.wait_presented(shown)
        self.assertTrue(self.shared('controls_menu').menu.visible)
        self.wait_processed(producer.key('F10',False))
        deadline=time.monotonic()+2
        pixels=self.foreground_pixels()
        while pixels==before_pixels and time.monotonic()<deadline:
            time.sleep(.02);pixels=self.foreground_pixels()
        self.assertNotEqual(pixels,before_pixels,'Queued controls presentation did not appear after the compositor resumed')
        self.stop()
        self.shutdown_checked()

    def test_a_b_a_replaces_stream_without_native_service_restart(self):
        self.ready_host(second=True)
        worlds = self.retained_pids()
        compositor = self.live_compositor_pids()
        self.assertEqual(len(compositor), 1)
        pids = {pid for group in worlds.values() for pid in group} | {self.server.pid, compositor[0]}
        starts = {pid: self.process_start(pid) for pid in pids}
        producer = self.producer()
        slots = [self.status().capture_source_slot]
        a, b = [world.world_id for world, _, _ in self.worlds]
        for generation, destination in ((0, b), (1, a)):
            reply = self.shared('transition_proposal', generation, actor.HostActorTransitionRequest(
                protocol_version=1, transition_id=os.urandom(32), destination_world_id=destination))
            self.assertTrue(reply.accepted, reply)
            self.assertTrue(reply.transition.actor_transition.destination_activation_confirmed, reply)
            producer.close()
            producer = self.producer(generation + 1)
            status = self.status(generation + 1)
            self.assertGreater(status.capture_source_slot, slots[-1])
            slots.append(status.capture_source_slot)
            self.wait_processed(producer.key('F10'), generation + 1)
            self.assertTrue(self.shared('controls_menu', generation + 1).menu.visible)
            self.wait_processed(producer.key('Escape'), generation + 1)
            self.assertEqual(self.retained_pids(), worlds)
            self.assertEqual({pid: self.process_start(pid) for pid in starts}, starts)
        self.shutdown_checked()

    def test_real_source_silence_retires_capture_and_stops_motion(self):
        self.ready_host()
        producer = self.producer()
        self.wait_processed(producer.key('w'))
        self.assertTrue(self.shared('controls_tick').accepted)
        self.assertEqual(list(self.state().axes), [0, 0, -1, 0, 0, 0])
        producer.silence()  # The socket stays open. This is source silence, not EOF.
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = self.status()
            if status.capture_retired:
                break
        self.assertTrue(status.capture_retired, status)
        self.assertFalse(status.capture_active, status)
        self.assertEqual(list(self.state().axes), [0] * 6)
        self.stop()
        self.shutdown_checked()

    def test_native_capture_endpoint_remains_live_without_required_spnav_device(self):
        self.ready_host(native=True)
        status = self.status()
        self.assertTrue(status.capture_active, status)
        self.assertEqual(status.capture_source_slot, 0)
        time.sleep(.5)
        status = self.status()
        self.assertTrue(status.capture_active, status)
        self.assertFalse(status.capture_retired, status)
        self.stop()
        self.shutdown_checked()

    def assert_prestart_rejection(self, config, descriptors, detail):
        self.launch_host(config, descriptors)
        self.assertNotEqual(self.host_process.wait(timeout=15), 0)
        self.assertIn(detail, self.host_log.read_text())
        self.assertTrue(self.terminal_cleanup_reply().cleanup_complete)
        self.assertFalse((self.root / 'host-broker.owned').exists())
        self.assertFalse((self.host_broker_journal / 'launch.db').exists())

    def test_capture_alias_rejected_before_services(self):
        config, descriptors = self.prepare()
        config.trusted_capture_fds.append(config.trusted_capture_fds[0])
        self.assert_prestart_rejection(config, descriptors, 'Aliased startup')

    def test_capture_stream_socket_rejected_before_services(self):
        config, descriptors = self.prepare(capture=False)
        producer, consumer = socket.socketpair()
        self.addCleanup(producer.close)
        descriptor = consumer.detach()
        config.trusted_capture_fds.append(descriptor)
        descriptors.append(descriptor)
        self.assert_prestart_rejection(config, descriptors, 'configured private kernel peer')

    def test_invalid_native_device_rejected_before_services_or_grabs(self):
        config, descriptors = self.prepare(capture=False)
        config.native_capture.physical_sources.add(device_node='/dev/null', kind=controls.KEYBOARD)
        config.native_capture.physical_sources.add(device_node='/dev/zero', kind=controls.MOUSE)
        self.assert_prestart_rejection(config, descriptors, 'distinct matching kernel device')


if __name__ == '__main__':
    names = [name for name in ContinuousCaptureTests.__dict__ if name.startswith('test_')]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(ContinuousCaptureTests(name) for name in names))
    raise SystemExit(not result.wasSuccessful())
