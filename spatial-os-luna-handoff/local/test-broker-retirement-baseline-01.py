"""An actual checked broker crash still needs authenticated issuer retirement."""
import signal
import unittest
import test_session_broker_cleanup as existing
import launch_pb2 as launch
import session_probe_pb2 as probe
from harness.wire import frame


class BrokerRetirementBaseline(existing.SessionBrokerCleanupTests):
    def test_checked_broker_cleanup_needs_issuer_retirement(self):
        self.pin_broker()
        signal.pidfd_send_signal(self.pins[self.broker_pid], signal.SIGKILL)
        self.exited()
        saved = self.request(stop_broker=launch.AuthorityLaunchQuery()).cleanup
        self.matching_cleanup(saved)
        # Proposed private operation 18. The old generated schema preserves unknown bytes.
        request = probe.SessionProbeRequest.FromString(b'\x08\x01\x92\x01\x00')
        self.coordinator_sequence += 1; request.request_id = self.coordinator_sequence
        self.coordinator.stdin.write(frame(request)); self.coordinator.stdin.flush()
        reply = self.message(self.coordinator.stdout, probe.SessionProbeReply)
        self.assertEqual(reply.request_id, request.request_id)
        self.assertEqual(self.read_record(), saved)
        self.issuer_pending()
        self.finish_session()
        self.assertEqual(reply.WhichOneof('outcome'), 'retirement',
            'Broker retirement is missing after actual checked cleanup: ' + str(reply))


if __name__ == '__main__':
    suite = unittest.TestSuite([BrokerRetirementBaseline('test_checked_broker_cleanup_needs_issuer_retirement')])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
