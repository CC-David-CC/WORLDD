"""Show the missing coordinator step after actual cleanup and durable broker recovery."""
import sqlite3
import unittest
import test_dependent_cleanup as durable
import session_probe_pb2 as probe
from harness.wire import frame


def varint(value):
    data = bytearray()
    while value > 127:
        data.append((value & 127) | 128); value >>= 7
    data.append(value)
    return bytes(data)


class CoordinatorRetirementBaseline(durable.DependentCleanupTests):
    def test_actual_durable_cleanup_needs_coordinator_retirement(self):
        cleanup = self.stopped_application(); recovered = self.recover()
        before = self.saved(cleanup, recovered)
        self.assertTrue(cleanup.HasField('fence'))
        owner = self.owner.SerializeToString()
        encoded = varint((16 << 3) | 2) + varint(len(owner)) + owner
        request = probe.SessionProbeRequest.FromString(b'\x08\x01' + encoded)
        self.coordinator_sequence += 1; request.request_id = self.coordinator_sequence
        self.coordinator.stdin.write(frame(request)); self.coordinator.stdin.flush()
        reply = self.message(self.coordinator.stdout, probe.SessionProbeReply)
        self.assertEqual(reply.protocol_version, 1); self.assertEqual(reply.request_id, request.request_id)
        self.assertEqual(self.saved(cleanup, recovered), before)
        with sqlite3.connect('file:' + str(self.issuer.state / 'identity.db') + '?mode=ro', uri=True) as db:
            active = db.execute('SELECT active FROM authority_activation WHERE participant_id=? AND generation=?',
                (self.owner.launch.participant_id, self.owner.launch.generation)).fetchone()
            retired = db.execute('SELECT COUNT(*) FROM authority_retirement WHERE participant_id=? AND generation=?',
                (self.owner.launch.participant_id, self.owner.launch.generation)).fetchone()[0]
        self.assertEqual(active, (1,)); self.assertEqual(retired, 0)
        self.finish_session()
        self.assertEqual(reply.WhichOneof('outcome'), 'retirement',
            'Coordinator retirement is missing after actual durable cleanup: ' + str(reply))


if __name__ == '__main__':
    suite = unittest.TestSuite([CoordinatorRetirementBaseline('test_actual_durable_cleanup_needs_coordinator_retirement')])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
