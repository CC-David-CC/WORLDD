"""Real native input cancellation remains live during a pending issuer INFO."""
import json
import os
from pathlib import Path
import socket
import time
import unittest
import core_pb2 as core
from harness.issuer_progress import DelayedInfoIssuer, IssuerLifetimeWitness
from test_world_identity import WorldIdentityFixture


class WorldIssuerProgressTests(IssuerLifetimeWitness, WorldIdentityFixture):
    def config_file(self, endpoint_port=None, **overrides):
        relay = getattr(self, "_info_relay", None)
        return super().config_file(endpoint_port=endpoint_port or (relay.port if relay else None), **overrides)

    def pending_info_with_native_input(self):
        self.assertIn(socket.gethostname().split(".")[0], ("sb16", "sb16-2"))
        self.assertTrue(os.environ.get("SPATIAL_TEST_DISPLAY"))
        self._info_relay = DelayedInfoIssuer(self)
        world, peer, probe = self.held()
        self._progress_observations = dict(stage="native_input_held", issuer_io_budget_seconds=5,
            broker_outer_wait="UNRUN in this native INFO case", operation_300_second_deadline="UNRUN")
        def retain_progress():
            self.write_observations(dict(retained_monotonic=time.monotonic(), world_exit_code=world.proc.poll(),
                info_delays=list(self._info_relay.delay_records)))
        self.addCleanup(retain_progress)
        pressed_key = probe.wait(lambda row:row.get("event")=="key" and row["pressed"], timeout=2)
        pressed_button = probe.wait(lambda row:row.get("event")=="button" and row["pressed"], timeout=2)
        self._progress_observations["independent_pressed_control"] = dict(key=pressed_key, button=pressed_button)
        with probe.condition:
            offset = len(probe.events)
        self._info_relay.delay_armed.set()
        self.assertTrue(self._info_relay.delay_entered.wait(3), "Real world did not issue idle INFO")
        self.assertFalse(self._info_relay.delay_finished.is_set())
        before = self.capture_issuer_lifetime("real_info_reply_pending")
        self.assertEqual(before["policy"]["idle_timeout_ms"], 3000)
        self.assertEqual(before["policy"]["io_timeout_ms"], 5000)
        self.assertTrue(all(item["exit_code"] is None for item in before["processes"]))
        def retain_probe():
            target = Path(os.environ["SPATIAL_SECURITY_EVIDENCE"])
            probe.log.flush()
            for path in (probe.path, Path(probe.stderr.name)):
                (target/(self._testMethodName+"-"+path.name)).write_bytes(path.read_bytes())
        self.addCleanup(retain_probe)
        return world, peer, probe, offset

    def independent_release(self, probe, offset):
        # This is a separately compiled X client, with no world RPC or SUT event queue.
        key = probe.wait(lambda row:row.get("event")=="key" and not row["pressed"], after=offset, timeout=1.5)
        button = probe.wait(lambda row:row.get("event")=="button" and not row["pressed"], after=offset, timeout=1.5)
        self.assertIsNone(probe.proc.poll(), "Input target process died")
        return dict(key=key, button=button, observed_monotonic=time.monotonic())

    def write_observations(self, value):
        path = Path(os.environ["SPATIAL_SECURITY_EVIDENCE"])/(self._testMethodName+"-progress.json")
        self._progress_observations.update(value)
        path.write_text(json.dumps(self._progress_observations, sort_keys=True, indent=2)+"\n")
        path.chmod(0o600)

    def test_controller_loss_releases_native_input_while_info_reply_is_pending(self):
        world, peer, probe, offset = self.pending_info_with_native_input()
        closed = time.monotonic()
        self.write_observations(dict(controller_closed_monotonic=closed))
        peer.close()
        released = self.independent_release(probe, offset)
        self.assertFalse(self._info_relay.delay_finished.is_set(),
                         "Controller cancellation waited for the delayed INFO reply")
        self.assertLess(released["observed_monotonic"]-closed, 1.5)
        self.assertTrue(self._info_relay.delay_finished.wait(5))
        self.assertEqual(len(self._info_relay.delay_records), 1)
        delayed = self._info_relay.delay_records[0]
        self.assertGreaterEqual(delayed["elapsed_seconds"], 4.1)
        self.assertLess(delayed["elapsed_seconds"], 5)
        started = time.monotonic()
        self.assertTrue(self.authenticate(world.peers[1]).HasField("opened"))
        authentication_elapsed = time.monotonic()-started
        self.assertLess(authentication_elapsed, 5)
        after = self.capture_issuer_lifetime("controller_cancelled_and_same_issuer_connection_usable")
        self.assertTrue(all(item["exit_code"] is None for item in after["processes"]))
        self.write_observations(dict(controller_closed_monotonic=closed, release=released,
            info_delay=delayed, authentication_elapsed_seconds=authentication_elapsed,
            issuer_io_budget_seconds=5, broker_outer_wait="UNRUN in this native INFO case",
            absolute_operation_deadline="UNRUN; engine owner-delay case separately records actual broker RPC elapsed"))

    def test_actual_issuer_exit_fails_closed_and_releases_input_during_pending_info(self):
        world, peer, probe, offset = self.pending_info_with_native_input()
        stopped = time.monotonic()
        self.write_observations(dict(issuer_stop_started_monotonic=stopped))
        self.stop_daemons()
        self.assertTrue(all(proc.poll()==0 for proc,_ in self.daemons), "Actual issuer shutdown was not confirmed")
        released = self.independent_release(probe, offset)
        self.assertFalse(self._info_relay.delay_finished.is_set(),
                         "Issuer-loss cancellation waited for a stale INFO reply")
        after = len(probe.events)
        outcome = None
        try:
            reply = peer.act(key=core.ActorKeyRequest(keysym="a", pressed=True))
            self.assertTrue(reply.HasField("error"), reply)
            self.assertEqual(reply.error.code, core.UNAVAILABLE, reply)
            outcome = "typed UNAVAILABLE"
        except EOFError:
            world.log.flush()
            diagnostic = Path(world.log.name).read_text()
            self.assertIn("issuer exchange failed", diagnostic)
            self.assertIn("identity service disabled", diagnostic)
            outcome = "actor EOF with exact private issuer failure diagnostic"
        events = probe.input_events(after=after)
        self.assertFalse(any(row["event"] in ("key","button") and row["pressed"] for row in events), events)
        self.capture_issuer_lifetime("issuer_exit_confirmed_no_reconnect_or_input")
        self.write_observations(dict(issuer_stopped_monotonic=stopped, release=released,
            denied_action=outcome, issuer_io_budget_seconds=5,
            broker_outer_wait="UNRUN in this native INFO case", info_delays=self._info_relay.delay_records))


if __name__ == "__main__":
    unittest.main(verbosity=2)
