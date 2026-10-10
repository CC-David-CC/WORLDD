"""Baseline for checked cleanup delivery after a real world crash."""
import os
from pathlib import Path
import select
import signal
import sqlite3
import subprocess
import unittest
from unittest.mock import patch
import test_session_world_06 as current
import broker_pb2 as broker
import core_pb2 as core
import launch_pb2 as launch
from harness.wire import frame

class WorldCrashTests(current.SessionWorldTests):
    def start_coordinator(self, *args, **kwargs):
        original = subprocess.Popen
        expected = current.base.baseline.supervisor.owned.existing.PROBE.with_name('spatial-session-world-probe')
        replacement = Path(os.environ['SPATIAL_WORLD_CRASH_PROBE'])
        def invoke(command, *more, **options):
            if command[0] == str(expected): command = [str(replacement), *command[1:], str(expected.parent)]
            return original(command, *more, **options)
        with patch.object(subprocess, 'Popen', side_effect=invoke):
            return super().start_coordinator(*args, **kwargs)

    def test_world_crash_delivers_checked_cleanup_while_broker_survives(self):
        proc, owner, initial = self.ready()
        parent = int(next(line.split()[1] for line in Path('/proc/' + str(self.world_ids.worldd_pid) + '/status').read_text().splitlines() if line.startswith('PPid:')))
        self.assertEqual(int(next(line.split()[1] for line in Path('/proc/' + str(parent) + '/status').read_text().splitlines() if line.startswith('PPid:'))), proc.pid)
        pins = [os.pidfd_open(pid) for pid in (self.world_ids.worldd_pid, self.world_ids.spatialwm_pid, parent)]
        try:
            signal.pidfd_send_signal(pins[0], signal.SIGKILL)
            for pin in pins: self.assertEqual(select.select([pin], [], [], 5)[0], [pin])
            with sqlite3.connect('file:' + str(self.world_journal / 'launch.db') + '?mode=ro', uri=True) as db:
                saved = launch.AuthorityLaunchRecord.FromString(db.execute('SELECT record FROM launch WHERE singleton=1').fetchone()[0])
            self.assertEqual(saved.stage, launch.OWNED_SCOPES_STOPPED)
            self.assertEqual(saved.process_stopped.identity, initial.process)
            self.assertEqual(saved.native_stopped.identity, initial.native)
            # Check kernel exit, then ask the still-running coordinator to recover this exact owner.
            request = broker.ControlSupervisorRequest(protocol_version=1, request_id=91, fence=owner)
            proc.stdin.write(frame(request)); proc.stdin.flush()
            state = self.message(proc.stdout, broker.ControlSupervisorReply)
            self.assertTrue(state.state.channel_fenced, state)
            cleanup = self.message(proc.stdout, launch.AuthorityLaunchReply)
            survived = self.message(proc.stdout, core.Reply)
            self.assertEqual(survived.hello.implementation, 'production')
            self.assertEqual(proc.wait(timeout=15), 0, self.logpath.read_text())
            self.assertTrue(cleanup.HasField('view'), 'Missing checked cleanup delivery after world crash: ' + str(cleanup))
            self.assertEqual(cleanup.view.record.stage, launch.OWNED_SCOPES_STOPPED)
            self.assertEqual(cleanup.view.record.native, initial.native)
            self.assertEqual(cleanup.view.record.process, initial.process)
            self.assertEqual(cleanup.view.record.process_stopped.identity, initial.process)
            self.assertEqual(cleanup.view.record.native_stopped.identity, initial.native)
        finally:
            for pin in pins: os.close(pin)

if __name__ == '__main__':
    suite=unittest.TestSuite([WorldCrashTests('test_world_crash_delivers_checked_cleanup_while_broker_survives')])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
