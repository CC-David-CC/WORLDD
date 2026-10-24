"""Private native capture ownership, descriptor preflight, and exact retirement."""
import os
import socket
import unittest
import control_pb2 as controls
import core_pb2 as core
import presentation_pb2 as protocol
import test_geometry_presentation as geometry


class CapturePresentationTests(geometry.GeometryPresentationTests):
    def pair(self):
        first, second = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        return first, second

    def start_capture(self, generation, endpoint, config=None):
        if config is None:
            config = controls.NativeCaptureConfig(spacemouse=True)
        return self.control((endpoint.fileno(),), start_capture=protocol.HostCaptureStart(
            capture_generation=generation, config=config))

    def stop_capture(self, generation):
        return self.control(stop_capture=protocol.HostCaptureStop(capture_generation=generation))

    def test_exact_epoch_retirement_and_stale_stop(self):
        self.launch()
        receiver, producer = self.pair()
        started = self.start_capture(1, producer)
        self.assertTrue(started.capture.active, started)
        stopped = self.stop_capture(1)
        self.assertEqual(stopped.capture.capture_generation, 1, stopped)
        self.assertFalse(stopped.capture.active)
        self.assertFalse(self.stop_capture(1).capture.active)
        next_receiver, next_producer = self.pair()
        self.assertTrue(self.start_capture(2, next_producer).capture.active)
        stale = self.stop_capture(1)
        self.assertTrue(stale.HasField('error'), stale)
        self.assertIn('Stale or unknown capture retirement', stale.error.message)
        # A duplicate start must not close the active epoch or admit its new endpoint.
        _, duplicate = self.pair()
        self.assertTrue(self.start_capture(2, duplicate).HasField('error'))
        self.assertFalse(self.stop_capture(2).capture.active)
        self.assertIsNone(self.proc.poll())

    def test_descriptor_counts_and_invalid_sources_precede_activation(self):
        self.launch()
        _, endpoint = self.pair()
        request = protocol.HostCaptureStart(capture_generation=1,
            config=controls.NativeCaptureConfig(spacemouse=True))
        for descriptors in ((), (endpoint.fileno(), endpoint.fileno())):
            reply = self.control(descriptors, start_capture=request)
            self.assertEqual(reply.error.code, core.INVALID_ARGUMENT, reply)
            self.assertIn('descriptor count', reply.error.message)
        invalid = controls.NativeCaptureConfig()
        invalid.physical_sources.add(device_node='/dev/null', kind=controls.KEYBOARD)
        invalid.physical_sources.add(device_node='/dev/zero', kind=controls.MOUSE)
        rejected = self.start_capture(1, endpoint, invalid)
        self.assertEqual(rejected.error.code, core.INVALID_ARGUMENT, rejected)
        self.assertIn('distinct matching kernel device', rejected.error.message)
        fd = os.memfd_create('not-a-capture-endpoint', os.MFD_CLOEXEC)
        try:
            reply = self.control((fd,), start_capture=request)
        finally:
            os.close(fd)
        self.assertTrue(reply.HasField('error'), reply)
        self.assertIn('private kernel peer', reply.error.message)
        self.assertTrue(self.start_capture(1, endpoint).capture.active)
        self.assertFalse(self.stop_capture(1).capture.active)

    def test_failed_attempt_retirement_fences_epoch_and_preserves_live_capture(self):
        self.launch()
        _, endpoint = self.pair()
        invalid = controls.NativeCaptureConfig()
        invalid.physical_sources.add(device_node='/dev/null', kind=controls.KEYBOARD)
        self.assertTrue(self.start_capture(7, endpoint, invalid).HasField('error'))
        retired = self.stop_capture(7)
        self.assertEqual(retired.capture.capture_generation, 7, retired)
        self.assertFalse(retired.capture.active)
        self.assertTrue(self.start_capture(7, endpoint).HasField('error'))
        self.assertTrue(self.start_capture(8, endpoint).capture.active)
        future = self.stop_capture(9)
        self.assertTrue(future.HasField('error'), future)
        self.assertIn('Stale or unknown capture retirement', future.error.message)
        self.assertFalse(self.stop_capture(8).capture.active)


if __name__ == '__main__':
    names = [name for name in CapturePresentationTests.__dict__ if name.startswith('test_')]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(CapturePresentationTests(name) for name in names))
    raise SystemExit(not result.wasSuccessful())
