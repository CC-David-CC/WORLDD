"""Real broker cancellation polling while its own action-issuer INFO is delayed.

The issuer participant watch and administrative actor connections stay direct.
ControlOwner uses the existing configured coordinator component fixture. It does
not claim independent native input delivery or process/native cleanup receipts.
"""
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import time
import unittest
from unittest.mock import patch
from google.protobuf import text_format
import broker_pb2 as broker
import core_pb2 as core
import issuer_pb2 as issuer
from harness.issuer_progress import DelayedInfoIssuer, IssuerLifetimeWitness
from harness.wire import frame
from test_world_identity import receive
from test_world_identity import WorldIdentityFixture
import test_application_access as access
import test_application_control_lease as leases
import test_control_owner_channels as owners


class ProgressLeaseBroker(leases.LeaseBroker):
    def close(self):
        if not getattr(self.fixture, "expected_issuer_shutdown", False):
            return super().close()
        if self.stream.fileno() < 0:
            return
        # A real issuer outage makes participant release uncertain. Keep the
        # production exit2 and private reason, then stop only this retained scope.
        if self.proc.poll() is None:
            self.stream.shutdown(socket.SHUT_WR)
        if self.resources:
            self.resources.close()
        else:
            self.proc.wait(timeout=8)
        self.stream.close()
        self.log.close()
        if self.owner_log:
            self.owner_log.close()
        text = self.path.read_text()
        self.fixture.assertEqual(self.proc.returncode, 2, text)
        self.fixture.assertIn("Workspace participant release remains unresolved", text)
        for marker in ("AddressSanitizer", "LeakSanitizer", "runtime error:"):
            self.fixture.assertNotIn(marker, text)


class BrokerProgressWitness(IssuerLifetimeWitness):
    def config_file(self, endpoint_port=None, **overrides):
        relay = getattr(self, "_info_relay", None)
        return super().config_file(endpoint_port=endpoint_port or (relay.port if relay else None), **overrides)

    def configure_participant(self, config, domains):
        super().configure_participant(config, domains)
        # Isolate the broker's action stream. Its participation watch must consume
        # real issuer revocations without passing through the delayed INFO relay.
        path = Path(config.participant_runtime_config)
        runtime = text_format.Parse(path.read_text(), issuer.AuthorityParticipantRuntimeConfig())
        direct = self.root/("direct-participant-"+path.name)
        direct.write_bytes(super().config_file().read_bytes())
        direct.chmod(0o600)
        runtime.issuer_client_config = str(direct)
        path.write_text(text_format.MessageToString(runtime))
        path.chmod(0o600)

    def arm_pending_info(self):
        self._progress = dict(issuer_io_budget_seconds=5, issuer_idle_budget_seconds=3,
            outer_broker_deadline_coverage="UNRUN; elapsed observation only",
            absolute_operation_300_second_deadline="UNRUN")
        self.addCleanup(self.retain_progress)
        self._info_relay.delay_armed.set()
        self.assertTrue(self._info_relay.delay_entered.wait(3), "Broker did not issue idle INFO")
        self.assertFalse(self._info_relay.delay_finished.is_set())
        observed = self.capture_issuer_lifetime("broker_action_INFO_pending")
        self.assertEqual(observed["policy"]["idle_timeout_ms"], 3000)
        self.assertEqual(observed["policy"]["io_timeout_ms"], 5000)
        self.assertTrue(all(item["exit_code"] is None for item in observed["processes"]))
        self._progress["pending_started_monotonic"] = time.monotonic()

    def retain_progress(self):
        self._progress["info_delays"] = list(self._info_relay.delay_records)
        self._progress["broker_exit_code"] = self.broker.proc.poll()
        self._progress["retained_monotonic"] = time.monotonic()
        path = Path(os.environ["SPATIAL_SECURITY_EVIDENCE"])/(self._testMethodName+"-broker-progress.json")
        path.write_text(json.dumps(self._progress, sort_keys=True, indent=2)+"\n")
        path.chmod(0o600)

    def completed_info(self):
        self.assertTrue(self._info_relay.delay_finished.wait(5))
        self.assertEqual(len(self._info_relay.delay_records), 1)
        delay = self._info_relay.delay_records[0]
        self.assertGreaterEqual(delay["elapsed_seconds"], 4.1)
        self.assertLess(delay["elapsed_seconds"], 5)
        self.assertFalse(self._info_relay.errors, self._info_relay.errors)
        return delay

    def payload_identity(self):
        queue = [self.broker.pid]
        found = []
        seen = set()
        while queue:
            pid = queue.pop()
            if pid in seen:
                continue
            seen.add(pid)
            self.assertLessEqual(len(seen), 256)
            root = Path("/proc")/str(pid)
            try:
                command = (root/"cmdline").read_bytes().split(bytes([0]))[:-1]
                queue.extend(int(value) for value in (root/"task"/str(pid)/"children").read_text().split())
                if command == [b"/usr/bin/python3", b"-c", access.HOLD.encode()]:
                    raw = (root/"stat").read_text()
                    found.append(dict(pid=pid, start_ticks=raw[raw.rfind(")")+2:].split()[19]))
            except FileNotFoundError:
                continue
        self.assertEqual(len(found), 1, found)
        return found[0]

    def assert_payload_identity(self, identity):
        raw = Path("/proc", str(identity["pid"]), "stat").read_text()
        self.assertEqual(raw[raw.rfind(")")+2:].split()[19], identity["start_ticks"])


class BrokerIssuerProgressTests(BrokerProgressWitness, leases.ApplicationControlLeaseTests):
    def setUp(self):
        super().setUp()
        self._info_relay = DelayedInfoIssuer(self)

    def application(self, *args, **kwargs):
        with patch.object(access, "Broker", ProgressLeaseBroker):
            return access.ApplicationAccessTests.setup_application(self, *args, **kwargs)

    def test_broker_actor_revocation_cancels_control_while_info_is_pending(self):
        app, record, lease = self.second_controller()
        identity = self.payload_identity()
        self.arm_pending_info()
        started = time.monotonic()
        peer = self.connect()
        try:
            operation = issuer.IssuerOperation(identity_revoke=issuer.IdentityRevoke(
                target=issuer.IdentityReference(kind=issuer.ACTOR_TARGET, id=self.second.actor_id),
                expected_security_revision=0, reason="broker pending INFO control revocation"))
            reply = self.execute(peer, self.challenge(peer, operation))
            self.assertTrue(reply.HasField("identity_status"), reply)
        finally:
            peer.close()
        event = self.broker.event(lease)
        elapsed = time.monotonic()-started
        self.assertLess(elapsed, 1.5)
        self.assertFalse(self._info_relay.delay_finished.is_set(), "Broker revocation waited for INFO")
        self.assert_payload_identity(identity)
        self._progress.update(cancellation_elapsed_seconds=elapsed, control_revoked=event.SerializeToString().hex(),
            source_process=identity)
        self.completed_info()
        self.release(lease, self.second)
        self.assertEqual(self.application_access(app), [(True, True)])
        denied = self.broker.request(application_access=broker.ApplicationAccessRequest(
            principal=self.principal(self.second), applications=[app]))
        self.assertTrue(denied.HasField("error"), denied)
        self.assertEqual(denied.error.code, core.UNAUTHORIZED)
        self.assertEqual(denied.error.message, "Workspace actor identity is inactive")
        self._progress["revoked_actor_denial"] = dict(code=denied.error.code, reason=denied.error.message)
        self.assert_payload_identity(identity)
        self.finish_application(app)

    def test_broker_actual_issuer_exit_fails_closed_while_info_is_pending(self):
        app = self.application()
        lease = self.acquire(app)
        identity = self.payload_identity()
        self.arm_pending_info()
        started = time.monotonic()
        self.stop_daemons()
        self.assertTrue(all(proc.poll() == 0 for proc, _ in self.daemons))
        self.expected_issuer_shutdown = True
        event = self.broker.event(lease)
        elapsed = time.monotonic()-started
        self.assertLess(elapsed, 1.5)
        self.assertFalse(self._info_relay.delay_finished.is_set(), "Broker issuer loss waited for INFO")
        self._progress.update(cancellation_elapsed_seconds=elapsed, control_revoked=event.SerializeToString().hex(),
            original_process=identity, real_issuer_exit_confirmed=True)
        # Release acknowledgements remain possible after authority loss. This is
        # cancellation only, never a fresh control or execution grant.
        self.release(lease)
        result = self.broker.request(control_acquire=broker.ApplicationControlRequest(
            principal=self.principal(), application=app))
        self.assertTrue(result.HasField("error"), result)
        self.assertIn(result.error.code, (core.UNAVAILABLE, core.UNAUTHORIZED))
        self._progress["denied_control_code"] = result.error.code
        self.broker.close()
        self.assertIsNotNone(self.broker.proc.poll())


class BrokerOwnerLossIssuerProgressTests(BrokerProgressWitness, owners.ControlOwnerTests):
    def setUp(self):
        # ControlOwnerTests provisions its application in setUp. Create the relay
        # after the real issuer fixture setup but before that broker starts.
        WorldIdentityFixture.setUp(self)
        self.brokers = []
        self.sequence = self.admin_sequence = self.world_sequence = 0
        self.addCleanup(self.close_brokers)
        self._info_relay = DelayedInfoIssuer(self)
        with patch.object(access, "Broker", owners.OwnedBroker):
            self.app = access.ApplicationAccessTests.setup_application(self)

    def register_with_kernel_witness(self):
        owner = self.owner_binding()
        channel, endpoint = socket.socketpair()
        channel.settimeout(5)
        witness = endpoint.dup()
        witness.setblocking(False)
        self.addCleanup(channel.close)
        self.addCleanup(witness.close)
        def identity(stream):
            info = os.fstat(stream.fileno())
            return dict(device=info.st_dev, inode=info.st_ino)
        endpoint_identity = identity(endpoint)
        self.assertEqual(identity(witness), endpoint_identity)
        self.assertNotEqual(identity(channel), endpoint_identity)
        try:
            reply = self.admin("register", owner, [endpoint.fileno()])
        finally:
            endpoint.close()
        self.assertTrue(reply.HasField("state"), reply)
        self.assertFalse(reply.state.channel_fenced)
        raw = Path("/proc", str(self.broker.pid), "stat").read_text()
        record = dict(broker_pid=self.broker.pid, broker_start_ticks=raw[raw.rfind(")")+2:].split()[19],
            broker_endpoint=endpoint_identity, duplicated_witness=identity(witness),
            owner_endpoint=identity(channel), registered_monotonic=time.monotonic(),
            witness_mode="MSG_PEEK only; duplicate broker endpoint; no duplicate owner endpoint")
        return owner, channel, witness, record

    def pending_release(self, channel, lease):
        self.world_sequence += 1
        request = broker.Request(protocol_version=1, request_id=self.world_sequence,
            control_release=broker.ApplicationControlRelease(id=lease.id, principal=lease.principal))
        payload = frame(request)
        channel.sendall(payload)
        return request.request_id, payload

    def kernel_state(self, witness, size=1):
        poller = select.poll()
        poller.register(witness, select.POLLIN | select.POLLRDHUP)
        masks = poller.poll(0)
        try:
            peek = witness.recv(size, socket.MSG_PEEK | socket.MSG_DONTWAIT)
        except BlockingIOError:
            peek = None
        return dict(monotonic=time.monotonic(), poll_masks=[mask for _, mask in masks],
            peek_hex=None if peek is None else peek.hex())

    def await_fenced(self, owner, lease, identity, started):
        observations = []
        self._progress["owner_inspections"] = observations
        while True:
            before = time.monotonic()
            state = self.inspect(owner)
            observations.append(dict(started_monotonic=before, finished_monotonic=time.monotonic(),
                channel_fenced=state.channel_fenced, reservation_count=len(state.reservations)))
            self.assertEqual(list(state.reservations), [lease])
            self.assertFalse(state.HasField("recovery"), "Lost owner must not invent cleanup proof")
            if state.channel_fenced:
                break
            self.assertLess(time.monotonic()-started, 1.5)
            time.sleep(.01)
        elapsed = time.monotonic()-started
        self.assertLess(elapsed, 1.5)
        self.assertFalse(self._info_relay.delay_finished.is_set(), "Owner fencing waited for INFO")
        self.assert_payload_identity(identity)
        self._progress.update(cancellation_elapsed_seconds=elapsed, channel_fenced=True,
            retained_reservation=lease.SerializeToString().hex(), source_process=identity,
            native_input_and_cleanup_receipt_coverage="UNRUN; configured coordinator component only")
        self.completed_info()
        self.assertEqual(list(self.inspect(owner).reservations), [lease])
        self.assert_payload_identity(identity)
        self.finish_application(self.app)

    def test_broker_control_owner_loss_is_fenced_while_info_is_pending(self):
        owner, channel, witness, record = self.register_with_kernel_witness()
        lease = self.lease(channel)
        self.assertEqual(list(self.inspect(owner).reservations), [lease])
        identity = self.payload_identity()
        self.arm_pending_info()
        self._progress["owner_socket_identity"] = record
        before = self.kernel_state(witness)
        self._progress["kernel_before_owner_close"] = before
        self.assertIsNone(before["peek_hex"], before)
        started = time.monotonic()
        self._progress["owner_closed_monotonic"] = started
        channel.close()
        kernel = self.kernel_state(witness)
        self._progress["kernel_owner_eof"] = kernel
        self.assertEqual(kernel["peek_hex"], "", "Duplicate broker endpoint must independently observe EOF")
        self.await_fenced(owner, lease, identity, started)

    def test_broker_half_closed_owner_with_queued_request_is_fenced_during_info(self):
        owner, channel, witness, record = self.register_with_kernel_witness()
        # Prove this exact ordinary release operation succeeds before holding INFO.
        control = self.lease(channel)
        self.assertTrue(self.release(channel, control).HasField("control_released"))
        lease = self.lease(channel)
        identity = self.payload_identity()
        self.arm_pending_info()
        self._progress["owner_socket_identity"] = record
        request_id, payload = self.pending_release(channel, lease)
        queued = self.kernel_state(witness, len(payload)+1)
        self._progress["queued_before_half_close"] = queued
        self.assertEqual(queued["peek_hex"], payload.hex())
        started = time.monotonic()
        self._progress["owner_half_closed_monotonic"] = started
        channel.shutdown(socket.SHUT_WR)
        kernel = self.kernel_state(witness, len(payload)+1)
        self._progress.update(kernel_half_close=kernel, queued_release_request_id=request_id)
        self.assertTrue(any(mask & select.POLLRDHUP for mask in kernel["poll_masks"]), kernel)
        self.await_fenced(owner, lease, identity, started)

    def test_broker_live_owner_queued_request_is_not_dispatched_during_info(self):
        owner, channel, witness, record = self.register_with_kernel_witness()
        control = self.lease(channel)
        self.assertTrue(self.release(channel, control).HasField("control_released"))
        lease = self.lease(channel)
        identity = self.payload_identity()
        self.arm_pending_info()
        self._progress["owner_socket_identity"] = record
        request_id, payload = self.pending_release(channel, lease)
        observations = []
        self._progress["live_queued_observations"] = observations
        started = time.monotonic()
        while True:
            state = self.inspect(owner)
            kernel = self.kernel_state(witness, len(payload)+1)
            observations.append(dict(kernel=kernel, channel_fenced=state.channel_fenced,
                reservation_count=len(state.reservations)))
            self.assertFalse(state.channel_fenced)
            self.assertFalse(state.HasField("recovery"))
            self.assertEqual(list(state.reservations), [lease])
            self.assertEqual(kernel["peek_hex"], payload.hex(), "Progress must not consume ordinary owner bytes")
            self.assertFalse(any(mask & (select.POLLRDHUP | select.POLLHUP | select.POLLERR)
                for mask in kernel["poll_masks"]))
            self.assertFalse(select.select([channel], [], [], 0)[0], "Progress must not dispatch queued release")
            self.assertFalse(self._info_relay.delay_finished.is_set())
            self.assert_payload_identity(identity)
            if time.monotonic()-started >= 1.5:
                break
            time.sleep(.01)
        self._progress["queued_observation_seconds"] = time.monotonic()-started
        self.completed_info()
        reply = receive(channel.recv, channel, broker.Reply)
        self.assertEqual(reply.protocol_version, 1)
        self.assertEqual(reply.request_id, request_id)
        self.assertTrue(reply.HasField("control_released"), reply)
        state = self.inspect(owner)
        self.assertFalse(state.channel_fenced)
        self.assertFalse(state.reservations)
        self.assertFalse(state.HasField("recovery"))
        self.assert_payload_identity(identity)
        self.finish_application(self.app)


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTest(BrokerIssuerProgressTests("test_broker_actor_revocation_cancels_control_while_info_is_pending"))
    suite.addTest(BrokerIssuerProgressTests("test_broker_actual_issuer_exit_fails_closed_while_info_is_pending"))
    suite.addTest(BrokerOwnerLossIssuerProgressTests("test_broker_control_owner_loss_is_fenced_while_info_is_pending"))
    suite.addTest(BrokerOwnerLossIssuerProgressTests("test_broker_half_closed_owner_with_queued_request_is_fenced_during_info"))
    suite.addTest(BrokerOwnerLossIssuerProgressTests("test_broker_live_owner_queued_request_is_not_dispatched_during_info"))
    return suite


if __name__ == "__main__":
    unittest.main(verbosity=2)
