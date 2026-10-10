"""Checked world cleanup must remain recoverable after its broker stops."""
import signal
import sqlite3
import unittest
import test_broker_retirement as existing
import session_probe_pb2 as probe
from harness.wire import frame


def varint(value):
    data = bytearray()
    while value > 127:
        data.append((value & 127) | 128); value >>= 7
    data.append(value)
    return bytes(data)


class StoppedBrokerDependencyBaseline(existing.BrokerRetirementTests):
    def test_stopped_broker_requires_durable_world_dependency_recovery(self):
        self.pin_broker(stop_world=False)
        world_cleanup = self.crash_world(); self.world_stopped = True
        self.assertTrue(world_cleanup.HasField('fence'))
        signal.pidfd_send_signal(self.pins[self.broker_pid], signal.SIGKILL); self.exited()
        self.checked(); broker_retirement = self.retired(); self.completed(broker_retirement)
        owner = self.owner.SerializeToString()
        request = probe.SessionProbeRequest.FromString(b'\x08\x01' + varint((19 << 3) | 2) + varint(len(owner)) + owner)
        self.coordinator_sequence += 1; request.request_id = self.coordinator_sequence
        self.coordinator.stdin.write(frame(request)); self.coordinator.stdin.flush()
        reply = self.message(self.coordinator.stdout, probe.SessionProbeReply)
        self.assertEqual(reply.request_id, request.request_id)
        with sqlite3.connect('file:' + str(self.world_journal / 'launch.db') + '?mode=ro', uri=True) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT record FROM launch').fetchone()[0], world_cleanup.SerializeToString())
        with sqlite3.connect('file:' + str(self.issuer.state / 'identity.db') + '?mode=ro', uri=True) as db:
            active = db.execute('SELECT active FROM authority_activation WHERE participant_id=? AND generation=?',
                (self.owner.launch.participant_id, self.owner.launch.generation)).fetchone()
            self.assertEqual(active, (1,))
        self.assertEqual(self.retired(), broker_retirement)
        self.finish()
        self.assertEqual(reply.WhichOneof('outcome'), 'dependent',
            'World dependency recovery is missing after checked broker cleanup: ' + str(reply))


if __name__ == '__main__':
    suite = unittest.TestSuite([StoppedBrokerDependencyBaseline('test_stopped_broker_requires_durable_world_dependency_recovery')])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
