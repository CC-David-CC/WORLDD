"""A real broker crash must not discard its retained supervisor's final cleanup."""
import os
from pathlib import Path
import select
import signal
import sqlite3
import unittest
import test_session_application as existing
import launch_pb2 as launch


class BrokerCleanupBaseline(existing.SessionApplicationTests):
    def test_checked_broker_crash_cleanup_is_delivered_to_retained_session(self):
        self.held_application()
        original = self.read_record()
        world = self.request(stop_world=self.owner).cleanup
        self.assertEqual(world.stage, launch.OWNED_SCOPES_STOPPED)
        self.released()
        broker = self.world_ids.workspaced_pid
        scope = self.cgroup / original.process.scope_name
        members = set()
        for path in scope.rglob('cgroup.procs'): members.update(map(int, path.read_text().split()))
        self.assertIn(broker, members)
        self.assertGreaterEqual(len(members), 3, 'Pin the actual broker and its confined application processes')
        status = Path('/proc/' + str(broker) + '/status').read_text()
        supervisor = int(next(row.split()[1] for row in status.splitlines() if row.startswith('PPid:')))
        pins = {pid: os.pidfd_open(pid) for pid in members | {supervisor}}
        try:
            signal.pidfd_send_signal(pins[broker], signal.SIGKILL)
            for pin in pins.values(): self.assertEqual(select.select([pin], [], [], 8)[0], [pin])
            saved = self.checked_cleanup()
            self.assertEqual(saved.binding, original.binding)
            self.assertEqual(saved.process, original.process)
            self.assertEqual(saved.native, original.native)
            self.assertTrue(saved.HasField('fence'))
            with sqlite3.connect('file:' + str(self.issuer.state / 'identity.db') + '?mode=ro', uri=True) as db:
                state = db.execute('SELECT active FROM authority_activation WHERE participant_id=? AND generation=?',
                    (original.binding.participant_id, original.authorization.request.previous_generation + 1)).fetchone()
                self.assertEqual(state, (1,))
                self.assertEqual(db.execute('SELECT COUNT(*) FROM authority_retirement').fetchone()[0], 0)
            self.assertIsNone(self.coordinator.poll(), 'The retained coordinator must still own the final reply channel')
            reply = self.request(expect_error=True, stop_session=launch.AuthorityLaunchQuery())
            self.assertEqual(self.read_record(), saved)
            self.assertTrue(reply.HasField('cleanup'),
                'Session discarded checked broker cleanup after its supervisor exited: ' + str(reply))
        finally:
            for pin in pins.values(): os.close(pin)


if __name__ == '__main__':
    suite = unittest.TestSuite([BrokerCleanupBaseline('test_checked_broker_crash_cleanup_is_delivered_to_retained_session')])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
