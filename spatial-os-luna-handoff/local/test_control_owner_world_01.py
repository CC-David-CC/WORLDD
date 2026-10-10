"""Real world actors use owner-bound broker channels and native input.

The fixture provisions the administrative owner binding. Production supervisor
binding and selective recovery release remain separate unfinished requirements.
"""
import json
import os
from pathlib import Path
import select
import signal
import unittest
from unittest.mock import patch

import broker_pb2 as pb
import core_pb2 as core
import test_application_access as access
import test_application_observation as observation
from test_control_owner_channels import ControlOwnerTests, OwnedBroker
from test_world_application_control import WorldApplicationControlTests


class OwnerWorldChannel:
    def __init__(self, broker):
        self.proc = broker.proc
        fixture = broker.fixture
        fixture.managed_owner, self.stream = fixture.register_owner_channel()

    def close(self):
        self.stream.close()


class ControlOwnerWorldTests(WorldApplicationControlTests):
    owner_binding = ControlOwnerTests.owner_binding
    admin = ControlOwnerTests.admin
    inspect_owner = ControlOwnerTests.inspect
    register_owner_channel = ControlOwnerTests.register
    channel_request = ControlOwnerTests.request
    finish_application = access.ApplicationAccessTests.finish_application

    def setUp(self):
        super().setUp()
        self.admin_sequence = self.world_sequence = 0

    def setup_world(self, *args, **kwargs):
        with patch.object(access, 'Broker', OwnedBroker), patch.object(observation, 'BrokerRelay', OwnerWorldChannel):
            result = observation.ApplicationObservationTests.setup_world(self, *args, **kwargs)
        # Only worldd retains this endpoint. The fixture retains separate root/admin channels.
        self.relay.stream.close()
        return result

    def pin_compositor(self):
        world = self.world_process.proc
        children = Path(f'/proc/{world.pid}/task/{world.pid}/children').read_text().split()
        matches = [int(pid) for pid in children if Path(f'/proc/{pid}/comm').read_text().strip() == 'spatialwm']
        self.assertEqual(len(matches), 1)
        descriptor = os.pidfd_open(matches[0])
        self.addCleanup(os.close, descriptor)
        self.assertFalse(select.select([descriptor], [], [], 0)[0])
        return descriptor

    def held_application(self):
        app, surface, observe, control = self.begin_control()
        self.send_key_event('Shift_L', True)
        self.until(lambda: self.probe_events(app), lambda rows:
            any(row.get('event') == 'key' and row.get('pressed') for row in rows))
        state = self.inspect_owner(self.managed_owner)
        self.assertFalse(state.channel_fenced)
        self.assertEqual(len(state.reservations), 1)
        lease = state.reservations[0]
        self.assertEqual(lease.application, app)
        self.assertEqual(lease.principal, self.principal(self.second))
        return app, control, lease

    def retain(self, **observations):
        output = Path(os.environ['SPATIAL_CONTROL_OWNER_WORLD_EVIDENCE'])
        output.mkdir(parents=True, exist_ok=True)
        observations.update(production_supervisor_binding=False, production_recovery_release=False,
                            limitation='Fixture provisions the owner; real broker/world/native paths execute all actions.')
        (output/(self._testMethodName+'.json')).write_text(json.dumps(observations, indent=2)+'\n')

    def test_native_revocation_releases_managed_reservation(self):
        app, control, lease = self.held_application()
        self.revoke_grant(self.peer, control)
        self.assert_released(app)
        state = self.until(lambda: self.inspect_owner(self.managed_owner), lambda value: not value.reservations)
        self.assertFalse(state.channel_fenced)
        self.probe_command(app, 'resize', 620, 460)
        self.actor_aim(self.peer)
        self.actor_pointer(True, self.peer)
        self.actor_pointer(False, self.peer)
        self.send_key_event('a', True, self.peer)
        self.send_key_event('a', False, self.peer)
        self.until(lambda: self.probe_events(app), lambda rows: any(row.get('text_hex') == '61' for row in rows))
        self.retain(native_release_confirmed=True, reservation_released=True,
                    application_alive=True, authorized_handoff_delivered_native_input=True)

    def test_world_crash_retains_only_its_owned_reservations(self):
        app, control, lease = self.held_application()
        other = self.launch_application()
        other_owner, other_channel = self.register_owner_channel()
        acquired = self.channel_request(other_channel, control_acquire=pb.ApplicationControlRequest(
            principal=self.principal(), application=other))
        self.assertTrue(acquired.HasField('control_lease'), acquired)
        independent = acquired.control_lease
        compositor = self.pin_compositor()
        self.expected_world_exit = -signal.SIGKILL
        self.world_process.proc.kill()
        self.assertEqual(self.world_process.proc.wait(timeout=5), -signal.SIGKILL)
        self.assertEqual(select.select([compositor], [], [], 5)[0], [compositor])
        self.assert_released(app, inspect=False)
        self.probe_command(app, 'resize', 640, 480)
        self.assertIsNone(self.broker.proc.poll())
        state = self.until(lambda: self.inspect_owner(self.managed_owner), lambda value: value.channel_fenced)
        self.assertEqual(list(state.reservations), [lease])
        self.assertFalse(self.inspect_owner(other_owner).channel_fenced)
        self.assertEqual(list(self.inspect_owner(other_owner).reservations), [independent])
        self.denied(self.channel_request(other_channel, control_release=pb.ApplicationControlRelease(
            id=lease.id, principal=lease.principal)), core.FORBIDDEN)
        self.denied(self.channel_request(other_channel, control_acquire=pb.ApplicationControlRequest(
            principal=self.principal(), application=app)), core.BUSY)
        released = self.channel_request(other_channel, control_release=pb.ApplicationControlRelease(
            id=independent.id, principal=independent.principal))
        self.assertTrue(released.HasField('control_released'), released)
        self.finish_application(other)
        self.retain(world_exit_confirmed=True, compositor_exit_confirmed=True, native_release_confirmed=True,
                    application_alive=True, exact_owner_fenced=True, crashed_reservation_retained=True,
                    other_owner_untouched=True, other_owner_release_succeeded=True)

    def test_explicit_channel_fence_releases_native_input_without_stopping_application(self):
        app, control, lease = self.held_application()
        compositor = self.pin_compositor()
        self.expected_world_exit = 2
        reply = self.admin('fence', self.managed_owner)
        self.assertTrue(reply.HasField('state'), reply)
        self.assertTrue(reply.state.channel_fenced)
        self.assertEqual(list(reply.state.reservations), [lease])
        self.assertEqual(self.world_process.proc.wait(timeout=5), 2)
        self.assertEqual(select.select([compositor], [], [], 5)[0], [compositor])
        self.assert_released(app, inspect=False)
        self.probe_command(app, 'resize', 660, 500)
        self.assertIsNone(self.broker.proc.poll())
        self.assertEqual(list(self.inspect_owner(self.managed_owner).reservations), [lease])
        self.retain(world_exit_confirmed=True, compositor_exit_confirmed=True, native_release_confirmed=True,
                    application_alive=True, reservation_retained_pending_recovery=True)


if __name__ == '__main__':
    suite = unittest.TestSuite(ControlOwnerWorldTests(name) for name in ControlOwnerWorldTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
