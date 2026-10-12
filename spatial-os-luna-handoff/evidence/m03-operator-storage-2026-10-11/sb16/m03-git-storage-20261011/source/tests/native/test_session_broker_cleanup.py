"""Retained cleanup after an actual broker crash and native-server delay."""
import os
from pathlib import Path
import select
import signal
import sqlite3
import unittest
import test_session_application as existing
import broker_pb2 as broker
import launch_pb2 as launch
import session_probe_pb2 as probe
from harness.wire import frame, make_request


class SessionBrokerCleanupTests(existing.SessionApplicationTests):
    def pin_broker(self, stop_world=True):
        self.held_application()
        self.original = self.read_record()
        if stop_world:
            world = self.request(stop_world=self.owner).cleanup
            self.assertEqual(world.stage, launch.OWNED_SCOPES_STOPPED)
            self.released()
        self.broker_pid = self.world_ids.workspaced_pid
        scope = self.cgroup / self.original.process.scope_name
        members = set()
        for path in scope.rglob('cgroup.procs'): members.update(map(int, path.read_text().split()))
        self.assertIn(self.broker_pid, members); self.assertGreaterEqual(len(members), 3)
        status = Path('/proc/' + str(self.broker_pid) + '/status').read_text()
        self.supervisor_pid = int(next(row.split()[1] for row in status.splitlines() if row.startswith('PPid:')))
        self.members = members
        self.pins = {pid: os.pidfd_open(pid) for pid in members | {self.supervisor_pid}}
        for pin in self.pins.values(): self.addCleanup(os.close, pin)

    def exited(self, include_supervisor=True):
        expected = self.pins if include_supervisor else {pid: self.pins[pid] for pid in self.members}
        for pin in expected.values(): self.assertEqual(select.select([pin], [], [], 8)[0], [pin])

    def issuer_pending(self):
        with sqlite3.connect('file:' + str(self.issuer.state / 'identity.db') + '?mode=ro', uri=True) as db:
            state = db.execute('SELECT active FROM authority_activation WHERE participant_id=? AND generation=?',
                (self.original.binding.participant_id, self.original.authorization.request.previous_generation + 1)).fetchone()
            self.assertEqual(state, (1,))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM authority_retirement').fetchone()[0], 0)

    def matching_cleanup(self, saved):
        self.assertEqual(saved, self.checked_cleanup())
        self.assertEqual(saved.binding, self.original.binding)
        self.assertEqual(saved.process, self.original.process)
        self.assertEqual(saved.native, self.original.native)
        self.assertTrue(saved.HasField('fence'))
        self.issuer_pending()

    def test_crashed_broker_delivers_checked_receipt_and_exact_retry(self):
        self.pin_broker()
        independent = self.supervisor.issue(workspace=b'independent', application=b'survivor')
        survivor = self.connect(independent.cookie)
        identity = survivor.inspect()
        signal.pidfd_send_signal(self.pins[self.broker_pid], signal.SIGKILL)
        self.exited()
        saved = self.request(stop_broker=launch.AuthorityLaunchQuery()).cleanup
        self.matching_cleanup(saved)
        self.assertEqual(self.request(stop_broker=launch.AuthorityLaunchQuery()).cleanup, saved)
        self.assertEqual(survivor.inspect(), identity)
        self.request(expect_error=True, broker=make_request('system.hello', {}))
        self.finish_session()

    def test_broker_cleanup_does_not_replace_unconfirmed_world_cleanup(self):
        self.pin_broker(stop_world=False)
        signal.pidfd_send_signal(self.pins[self.broker_pid], signal.SIGKILL)
        self.exited()
        saved = self.request(stop_broker=launch.AuthorityLaunchQuery()).cleanup
        self.matching_cleanup(saved)
        rejected = self.request(expect_error=True, stop_session=launch.AuthorityLaunchQuery())
        self.assertIn('Stop retained world scopes', rejected.error.message)
        changed = broker.ControlOwner(); changed.CopyFrom(self.owner); changed.launch.generation += 1
        self.request(expect_error=True, stop_world=changed)
        world = self.request(stop_world=self.owner).cleanup
        self.assertEqual(world.stage, launch.OWNED_SCOPES_STOPPED)
        self.assertEqual(world.process, self.world_record.process)
        self.assertEqual(world.native, self.world_record.native)
        with sqlite3.connect('file:' + str(self.world_journal / 'launch.db') + '?mode=ro', uri=True) as db:
            self.assertEqual(db.execute('SELECT record FROM launch').fetchone()[0], world.SerializeToString())
        self.request(expect_error=True, recover_world=self.owner)
        self.request(expect_error=True, retire_world=self.owner)
        self.issuer_pending(); self.finish_session()

    def test_process_exit_waits_for_actual_native_cleanup_reply(self):
        self.pin_broker()
        server = os.pidfd_open(self.server.pid)
        try:
            signal.pidfd_send_signal(server, signal.SIGSTOP)
            signal.pidfd_send_signal(self.pins[self.broker_pid], signal.SIGKILL)
            self.exited(include_supervisor=False)
            self.until(self.read_record, lambda value: value.HasField('process_stopped'))
            pending = self.read_record()
            self.assertEqual(pending.stage, launch.CLEANUP_PENDING)
            self.assertFalse(pending.HasField('native_stopped'))
            self.coordinator_sequence += 1
            request = probe.SessionProbeRequest(protocol_version=1, request_id=self.coordinator_sequence,
                stop_broker=launch.AuthorityLaunchQuery())
            self.coordinator.stdin.write(frame(request)); self.coordinator.stdin.flush()
            self.assertEqual(select.select([self.coordinator.stdout], [], [], .05)[0], [])
            self.issuer_pending()
        finally:
            signal.pidfd_send_signal(server, signal.SIGCONT); os.close(server)
        reply = self.message(self.coordinator.stdout, probe.SessionProbeReply)
        self.assertEqual(reply.request_id, request.request_id)
        self.assertTrue(reply.HasField('cleanup'), reply)
        self.exited(); self.matching_cleanup(reply.cleanup); self.finish_session()

    def test_supervisor_death_without_receipt_cannot_claim_cleanup(self):
        self.pin_broker()
        signal.pidfd_send_signal(self.pins[self.supervisor_pid], signal.SIGKILL)
        pin = self.pins[self.supervisor_pid]
        self.assertEqual(select.select([pin], [], [], 8)[0], [pin])
        self.request(expect_error=True, stop_broker=launch.AuthorityLaunchQuery())
        self.request(expect_error=True, stop_broker=launch.AuthorityLaunchQuery())
        self.request(expect_error=True, stop_session=launch.AuthorityLaunchQuery())
        saved = self.read_record()
        self.assertNotEqual(saved.stage, launch.OWNED_SCOPES_STOPPED)
        self.assertFalse(saved.HasField('process_stopped')); self.assertFalse(saved.HasField('native_stopped'))
        self.issuer_pending()


if __name__ == '__main__':
    names = ['SessionBrokerCleanupTests.' + name for name in SessionBrokerCleanupTests.__dict__ if name.startswith('test_')]
    unittest.main(defaultTest=names, verbosity=2)
