"""Observe orphan control reservations using real processes and native input.

This is a recovery-gap diagnostic. It does not qualify supervisor recovery.
The fixture retains the private broker endpoint across world death.
"""
import json
import os
from pathlib import Path
import select
import signal
import unittest

import broker_pb2 as broker
import core_pb2 as core
from test_world_application_control import WorldApplicationControlTests


class ControlRecoveryBaseline(WorldApplicationControlTests):
    def test_native_cleanup_does_not_release_orphan_broker_reservation(self):
        app, surface, observe, control = self.begin_control()
        self.send_key_event('Shift_L', True)
        before = self.until(lambda: self.probe_events(app), lambda rows:
            any(row.get('event') == 'key' and row.get('pressed') for row in rows))
        self.assertTrue(any(row.get('event') == 'key' and row.get('pressed') for row in before))
        self.assertTrue(any(row.get('event') == 'button' and row.get('pressed') for row in before))
        world = self.world_process.proc
        children = Path(f'/proc/{world.pid}/task/{world.pid}/children').read_text().split()
        compositors = [int(pid) for pid in children
                       if Path(f'/proc/{pid}/comm').read_text().strip() == 'spatialwm']
        self.assertEqual(len(compositors), 1)
        compositor = os.pidfd_open(compositors[0])
        self.addCleanup(os.close, compositor)
        self.assertFalse(select.select([compositor], [], [], 0)[0])

        self.expected_world_exit = -signal.SIGKILL
        world.kill()
        self.assertEqual(world.wait(timeout=5), -signal.SIGKILL)
        self.assertEqual(select.select([compositor], [], [], 5)[0], [compositor])
        self.assert_released(app, inspect=False)
        # An independent native client still answers commands after world/compositor death.
        self.probe_command(app, 'resize', 640, 480)
        self.assertIsNone(self.broker.proc.poll())

        owner = self.principal()
        viewer = self.principal(self.second)
        access = self.broker.request(application_access=broker.ApplicationAccessRequest(
            principal=owner, applications=[app]))
        self.assertTrue(access.HasField('application_access'), access)
        self.assertEqual(len(access.application_access.applications), 1)
        self.assertTrue(access.application_access.applications[0].control)
        attempted = self.broker.request(control_acquire=broker.ApplicationControlRequest(
            principal=owner, application=app))
        self.denied(attempted, core.BUSY)

        # Retrieve and explicitly release the retained reservation only after native cleanup.
        # This diagnostic management action is not a production recovery mechanism.
        retained = self.broker.request(control_acquire=broker.ApplicationControlRequest(
            principal=viewer, application=app))
        self.assertTrue(retained.HasField('control_lease'), retained)
        lease = retained.control_lease
        self.assertEqual(lease.application, app)
        self.assertEqual(lease.principal, viewer)
        released = self.broker.request(control_release=broker.ApplicationControlRelease(
            id=lease.id, principal=viewer))
        self.assertTrue(released.HasField('control_released'), released)
        self.assertEqual(released.control_released.id, lease.id)
        replacement = self.broker.request(control_acquire=broker.ApplicationControlRequest(
            principal=owner, application=app))
        self.assertTrue(replacement.HasField('control_lease'), replacement)
        self.assertNotEqual(replacement.control_lease.id, lease.id)
        cleaned = self.broker.request(control_release=broker.ApplicationControlRelease(
            id=replacement.control_lease.id, principal=owner))
        self.assertTrue(cleaned.HasField('control_released'), cleaned)
        self.assertEqual(cleaned.control_released.id, replacement.control_lease.id)

        output = Path(os.environ['SPATIAL_CRASH_EVIDENCE'])
        output.mkdir(parents=True, exist_ok=True)
        (output / (self._testMethodName + '.json')).write_text(json.dumps(dict(
            world_exit_confirmed=True, compositor_exit_confirmed=True,
            native_release_confirmed=True, application_answers_after_world_exit=True,
            broker_alive=True, authorized_new_controller_blocked=True,
            explicit_diagnostic_release_restores_acquisition=True,
            production_recovery_qualified=False,
            limitation='Direct private broker channel retained by the fixture; no recovery supervisor is configured.'
        ), indent=2) + '\n')


if __name__ == '__main__':
    suite = unittest.TestSuite(ControlRecoveryBaseline(name)
        for name in ControlRecoveryBaseline.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
