"""Real broker cancellation polling and in-dispatch reply classification races.

INFO cases keep the participant watch and administrative actor connections direct.
Status races forward real issuer replies and witness consumed watcher revisions.
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
import grants_pb2 as grant
from harness.issuer_progress import DelayedInfoIssuer, IssuerLifetimeWitness
from harness.resource_broker import ResourceBroker
from harness.wire import frame
from test_world_identity import receive
from test_world_identity import WorldIdentityFixture
import test_application_access as access
import test_application_control_lease as leases
import test_control_owner_channels as owners
import test_workspace_authority as workspace
import test_world_authority as world_authority


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
        raw_reply = result.SerializeToString()
        error = result.error if result.HasField("error") else None
        self._progress["fresh_control_reply"] = dict(
            protobuf_hex=raw_reply.hex(),
            error_code=error.code if error is not None else None,
            error_message=error.message if error is not None else None)
        # Persist the exact protobuf before a rejection assertion can abort the method.
        self.retain_progress()
        self.assertTrue(result.HasField("error"),
            "Fresh control acquire reply: " + text_format.MessageToString(result))
        self.assertIn(result.error.code, (core.UNAVAILABLE, core.UNAUTHORIZED),
            "Fresh control acquire reply: " + text_format.MessageToString(result))
        self._progress["denied_control_code"] = result.error.code
        self.broker.close()
        self.assertIsNotNone(self.broker.proc.poll())

class BrokerDurabilityAcquirePrecedenceTests(BrokerIssuerProgressTests):
    def prepare_gated_application(self):
        gate = self.root/'revocation-commit-gate'
        gate.mkdir(mode=0o700)
        os.mkfifo(gate/'release', 0o600)
        self._commit_gate_released = False
        self.assertTrue(os.environ.get('SPATIAL_TEST_BROKER_CONFIG'))

        def launch(executable, command, *args, **kwargs):
            return ResourceBroker(executable, [*command, '--fixture-faults'], *args, **kwargs)

        with patch.dict(os.environ, SPATIAL_TEST_REVOCATION_COMMIT_GATE=str(gate)), patch.object(workspace, 'ResourceBroker', launch):
            self.app = self.application()

        def release_gate():
            if self._commit_gate_released or not (gate/'entered').exists():
                return
            self.release_commit_gate()

        # This cleanup runs before the broker-close cleanup registered during setup.
        self.addCleanup(release_gate)
        return gate

    def revoke_second_actor(self):
        peer = self.connect()
        try:
            operation = issuer.IssuerOperation(identity_revoke=issuer.IdentityRevoke(
                target=issuer.IdentityReference(kind=issuer.ACTOR_TARGET, id=self.second.actor_id),
                expected_security_revision=0, reason='control acquire precedence during durability loan'))
            result = self.execute(peer, self.challenge(peer, operation))
            self.assertTrue(result.HasField('identity_status'), result)
        finally:
            peer.close()

    def test_control_acquire_classifies_revoked_and_active_actors_during_commit_loan(self):
        gate = self.prepare_gated_application()
        primary_identity = self.payload_identity()
        self.enroll_second()
        self.delegate([grant.WORKSPACE_ACCESS, *access.workspace.PROJECT, grant.PROCESS_EXECUTE,
                       *leases.access.APP_RIGHTS])
        revoked_app = self.launch_application(claims=self.second)
        revoked_lease = self.acquire(revoked_app, self.second)
        self.revoke_second_actor()
        self.until(lambda: (gate/'entered').exists(), bool)
        self.broker.event(revoked_lease)
        self.assert_payload_identity(primary_identity)

        denied = self.broker.request(control_acquire=broker.ApplicationControlRequest(
            principal=self.principal(self.second), application=revoked_app))
        self.assertTrue(denied.HasField('error'), denied)
        self.assertEqual(denied.error.code, core.UNAUTHORIZED, denied)

        active = self.broker.request(control_acquire=broker.ApplicationControlRequest(
            principal=self.principal(), application=self.app))
        self.assertTrue(active.HasField('error'), active)
        self.assertEqual(active.error.code, core.BUSY, active)
        self.assert_payload_identity(primary_identity)
        self.release(revoked_lease, self.second)
        self.assert_payload_identity(primary_identity)
        self.assertFalse(self._info_relay.errors, self._info_relay.errors)
        self.release_commit_gate()
        self.broker.close()
        self.assertEqual(self.broker.proc.returncode, 0, Path(self.broker.log.name).read_text())

    def release_commit_gate(self):
        gate = self.root/'revocation-commit-gate'
        if self._commit_gate_released:
            return
        try:
            fd = os.open(gate/'release', os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            self.fail('Durability worker did not wait at the real commit gate')
        try:
            self.assertEqual(os.write(fd, b'!'), 1)
            self._commit_gate_released = True
        finally:
            os.close(fd)

    def test_issuer_exit_while_commit_is_held_classifies_cached_requester(self):
        gate = self.prepare_gated_application()
        self.enroll_second()
        self.delegate([grant.WORKSPACE_ACCESS, *access.workspace.PROJECT, grant.PROCESS_EXECUTE,
                       *leases.access.APP_RIGHTS])
        primary_lease = self.acquire(self.app)
        revoked_app = self.launch_application(claims=self.second)
        revoked_lease = self.acquire(revoked_app, self.second)
        self.revoke_second_actor()
        self.until(lambda: (gate/'entered').exists(), bool)
        self.broker.event(revoked_lease)

        self.stop_daemons()
        self.assertTrue(all(proc.poll() == 0 for proc, _ in self.daemons))
        self.expected_issuer_shutdown = True
        started = time.monotonic()
        self.broker.event(primary_lease)
        self.assertLess(time.monotonic()-started, 1.5)
        active = self.broker.request(control_acquire=broker.ApplicationControlRequest(
            principal=self.principal(), application=self.app))
        self.assertTrue(active.HasField('error'), active)
        self.assertEqual(active.error.code, core.UNAVAILABLE, active)
        self.release(revoked_lease, self.second)
        self.release(primary_lease)
        self.release_commit_gate()
        self.broker.close()
        self.assertEqual(self.broker.proc.returncode, 2, Path(self.broker.log.name).read_text())


class BrokerRevocationReplyTests(workspace.WorkspaceAuthorityTests):
    """Race real root-wire scoped decisions, after dispatch reaches actor_status."""

    def checked_scoped_reply(self, principal):
        self.broker.sequence += 1
        request = broker.Request(protocol_version=1, request_id=self.broker.sequence,
            scoped_authority_check=broker.ScopedAuthorityCheckRequest(
                workspace='one', principal=principal, right=grant.FILE_READ))
        reply = self.broker.exchange(request)
        self.assertEqual(reply.protocol_version, request.protocol_version, reply)
        self.assertEqual(reply.request_id, request.request_id, reply)
        return reply

    def assert_only_rejection(self, reply, code):
        self.assertEqual(reply.WhichOneof('outcome'), 'error', reply)
        self.assertEqual(reply.error.code, code, reply)
        self.assertEqual({field.name for field, _ in reply.ListFields()},
                         {'protocol_version', 'request_id', 'error'}, reply)

    def scoped_revocation_race(self, target, kind):
        self.enroll_second()
        barrier = {}

        def revoke_pending_status():
            # RelayingIssuer invokes this only after decoding the actual
            # dispatched actor_status request. INFO and queued bytes cannot arm it.
            started = time.monotonic()
            claims = self.identity if target == 'ancestor' else self.second
            operation = issuer.IssuerOperation(identity_revoke=issuer.IdentityRevoke(
                target=issuer.IdentityReference(kind=kind,
                    id=claims.credential_id if kind == issuer.CREDENTIAL_TARGET else claims.actor_id),
                expected_security_revision=0, reason='root scoped reply classification regression'))
            peer = self.connect()
            try:
                result = self.execute(peer, self.challenge(peer, operation))
            finally:
                peer.close()
            self.assertTrue(result.HasField('identity_status'), result)
            revision = result.identity_status.security_revision
            barrier['consumed'] = self.wait_for_broker_revocation_consumed(
                revision, log_offset, started+2, relay)

        relay = world_authority.RelayingIssuer(self, revoke_pending_status,
                                               wait_for_revocation_event=False)
        # Both identities have independent roots. The ancestor case also adds a
        # real delegated grant, so losing that ancestor leaves usable authority.
        self.workspace(roots=lambda: [self.workspace_terms(), self.workspace_terms(self.second)],
                       issuer_port=relay.port, attach_world=False)
        self.addCleanup(self.broker.close)  # Retire participation before closing its relay.
        if target == 'ancestor':
            page = self.broker.request(grants=broker.GrantListRequest(
                workspace='one', principal=self.principal())).grants
            parent = next(record for record in page.grants if not record.parent_id)
            delegated = self.broker.request(delegate=broker.GrantDelegateRequest(
                workspace='one', principal=self.principal(), parent_id=parent.id,
                terms=self.workspace_terms(self.second, rights=[grant.FILE_READ], depth=0),
                expected_revision=page.revision))
            self.assertTrue(delegated.HasField('grant'), delegated)
        requester = self.principal(self.second if target != 'unrelated' else self.identity)
        before = self.checked_scoped_reply(requester)
        self.assertTrue(before.HasField('scoped_authority_check'), before)
        self.assertTrue(before.scoped_authority_check.allowed, before)
        project_before = {path.relative_to(self.project): path.read_bytes()
                          for path in self.project.rglob('*') if path.is_file()}

        def checked_status(request, reply):
            if request.HasField('actor_status'):
                self.assertIn(requester.actor_id, request.actor_status.actor_ids)
                self.assertTrue(reply.HasField('actor_status'), reply)
                self.assertEqual(relay.status_seen, 1)
                barrier['status_returned'] = True

        relay.before_reply = checked_status
        log_offset = Path(self.broker.log.name).stat().st_size
        relay.armed.set()
        denied = self.checked_scoped_reply(requester)
        self.assertTrue(relay.triggered.is_set(), 'Request never reached actor-status dispatch')
        self.assertTrue(relay.delivered.is_set(), 'Real watch event was not forwarded')
        self.assertIn('consumed', barrier, barrier)
        self.assertTrue(barrier.get('status_returned'), barrier)
        self.assertFalse(relay.errors, relay.errors)
        expected = core.UNAUTHORIZED if target == 'requester' else core.FORBIDDEN
        self.assert_only_rejection(denied, expected)
        relay.before_reply = None
        # A separate explicit request, never automatic replay, remains usable.
        independent = self.principal() if target == 'requester' else requester
        fresh = self.checked_scoped_reply(independent)
        self.assertTrue(fresh.HasField('scoped_authority_check'), fresh)
        self.assertTrue(fresh.scoped_authority_check.allowed, fresh)
        self.assertGreater(fresh.request_id, denied.request_id)
        if target == 'requester':
            inactive = self.checked_scoped_reply(requester)
            self.assertTrue(inactive.HasField('scoped_authority_check'), inactive)
            self.assertFalse(inactive.scoped_authority_check.allowed, inactive)
        self.assertEqual({path.relative_to(self.project): path.read_bytes()
                          for path in self.project.rglob('*') if path.is_file()}, project_before)
        self.broker.close()
        diagnostic = Path(self.broker.log.name).read_text()
        self.assertEqual(self.broker.proc.returncode, 0, diagnostic)
        for marker in ('AddressSanitizer', 'LeakSanitizer', 'runtime error:'):
            self.assertNotIn(marker, diagnostic)

    def test_root_scoped_reply_unrelated_revocation_is_forbidden_then_fresh_authority_works(self):
        self.scoped_revocation_race('unrelated', issuer.CREDENTIAL_TARGET)

    def test_root_scoped_reply_ancestor_revocation_preserves_independent_authority(self):
        self.scoped_revocation_race('ancestor', issuer.ACTOR_TARGET)

    def test_root_scoped_reply_requester_actor_revocation_is_unauthorized(self):
        self.scoped_revocation_race('requester', issuer.ACTOR_TARGET)

    def test_root_scoped_reply_actual_issuer_loss_after_dispatch_is_unavailable(self):
        barrier = {}

        def stop_pending_status():
            barrier['dispatched'] = True
            self.stop_daemons()
            self.assertTrue(all(proc.poll() == 0 for proc, _ in self.daemons))
            barrier['issuer_stopped'] = True

        relay = world_authority.RelayingIssuer(self, stop_pending_status,
                                               wait_for_revocation_event=False)
        self.workspace(issuer_port=relay.port, attach_world=False)
        self.addCleanup(self.broker.close)
        principal = self.principal()
        before = self.checked_scoped_reply(principal)
        self.assertTrue(before.HasField('scoped_authority_check'), before)
        self.assertTrue(before.scoped_authority_check.allowed, before)
        project_readme = (self.project/'README.txt').read_bytes()
        relay.armed.set()
        denied = self.checked_scoped_reply(principal)
        self.assertTrue(relay.triggered.is_set())
        self.assertEqual(relay.status_seen, 1)
        self.assertEqual(barrier, {'dispatched': True, 'issuer_stopped': True})
        self.assertFalse(relay.errors, relay.errors)
        self.assert_only_rejection(denied, core.UNAVAILABLE)
        self.assert_only_rejection(self.checked_scoped_reply(principal), core.UNAVAILABLE)
        self.assertEqual((self.project/'README.txt').read_bytes(), project_readme)
        self.broker.close()
        self.assertEqual(self.broker.proc.returncode, 2)
        diagnostic = Path(self.broker.log.name).read_text()
        self.assertIn('Workspace participant release remains unresolved', diagnostic)
        for marker in ('AddressSanitizer', 'LeakSanitizer', 'runtime error:'):
            self.assertNotIn(marker, diagnostic)


class BrokerPrivateRevocationReplyTests(owners.ControlOwnerTests):
    payload_identity = BrokerProgressWitness.payload_identity
    assert_payload_identity = BrokerProgressWitness.assert_payload_identity
    assert_only_rejection = BrokerRevocationReplyTests.assert_only_rejection
    wait_for_broker_revocation_consumed = workspace.WorkspaceAuthorityTests.wait_for_broker_revocation_consumed

    def config_file(self, endpoint_port=None, **overrides):
        relay = getattr(self, '_status_relay', None)
        return super().config_file(endpoint_port=endpoint_port or (relay.port if relay else None), **overrides)

    def setUp(self):
        # Provision the relay before the real OwnedBroker reads its configuration.
        WorldIdentityFixture.setUp(self)
        self.brokers = []
        self.sequence = self.admin_sequence = self.world_sequence = 0
        self.addCleanup(self.close_brokers)
        self._status_relay = world_authority.RelayingIssuer(self, self.revoke_pending_status,
                                                           wait_for_revocation_event=False)
        with patch.object(access, 'Broker', owners.OwnedBroker):
            self.app = access.ApplicationAccessTests.setup_application(self)

    def revoke_pending_status(self):
        started = time.monotonic()
        operation = issuer.IssuerOperation(identity_revoke=issuer.IdentityRevoke(
            target=issuer.IdentityReference(kind=issuer.ACTOR_TARGET, id=self.second.actor_id),
            expected_security_revision=0, reason='private pending access reply regression'))
        peer = self.connect()
        try:
            result = self.execute(peer, self.challenge(peer, operation))
        finally:
            peer.close()
        self.assertTrue(result.HasField('identity_status'), result)
        self.consumed = self.wait_for_broker_revocation_consumed(result.identity_status.security_revision,
            self.log_offset, started+2, self._status_relay)

    def test_private_application_access_reply_unrelated_revocation_suppresses_payload(self):
        self.enroll_second()
        owner, channel = self.register()
        query = broker.ApplicationAccessRequest(principal=self.principal(), applications=[self.app])
        before = self.request(channel, application_access=query)
        self.assertTrue(before.HasField('application_access'), before)
        self.assertEqual([(item.observe, item.control) for item in before.application_access.applications],
                         [(True, True)])
        identity = self.payload_identity()
        command_line = Path('/proc', str(identity['pid']), 'cmdline').read_bytes()
        observed = []

        def checked_status(request, reply):
            if request.HasField('actor_status'):
                self.assertIn(self.principal().actor_id, request.actor_status.actor_ids)
                self.assertTrue(reply.HasField('actor_status'), reply)
                self.assertEqual(self._status_relay.status_seen, 1)
                observed.append(request.request_id)

        self._status_relay.before_reply = checked_status
        self.log_offset = Path(self.broker.log.name).stat().st_size
        self._status_relay.armed.set()
        denied = self.request(channel, application_access=query)
        self.assertTrue(self._status_relay.triggered.is_set())
        self.assertTrue(self._status_relay.delivered.is_set())
        self.assertTrue(self.consumed)
        # application_access refreshes workspace permissions, then obtains its
        # authorization fence. Only the first actor-status request triggers revocation.
        self.assertEqual(len(observed), 2)
        self.assertFalse(self._status_relay.errors, self._status_relay.errors)
        self.assert_only_rejection(denied, core.FORBIDDEN)
        self._status_relay.before_reply = None
        self.assert_payload_identity(identity)
        fresh = self.request(channel, application_access=query)
        self.assertTrue(fresh.HasField('application_access'), fresh)
        self.assertEqual([(item.observe, item.control) for item in fresh.application_access.applications],
                         [(True, True)])
        self.assertGreater(fresh.request_id, denied.request_id)
        self.assertFalse(self.inspect(owner).channel_fenced)
        self.assert_payload_identity(identity)
        self.assertEqual(Path('/proc', str(identity['pid']), 'cmdline').read_bytes(), command_line)
        self.finish_application(self.app)


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
    for name in (
        'test_root_scoped_reply_unrelated_revocation_is_forbidden_then_fresh_authority_works',
        'test_root_scoped_reply_ancestor_revocation_preserves_independent_authority',
        'test_root_scoped_reply_requester_actor_revocation_is_unauthorized',
        'test_root_scoped_reply_actual_issuer_loss_after_dispatch_is_unavailable',
    ):
        suite.addTest(BrokerRevocationReplyTests(name))
    suite.addTest(BrokerPrivateRevocationReplyTests(
        'test_private_application_access_reply_unrelated_revocation_suppresses_payload'))
    suite.addTest(BrokerIssuerProgressTests("test_broker_actor_revocation_cancels_control_while_info_is_pending"))
    suite.addTest(BrokerIssuerProgressTests("test_broker_actual_issuer_exit_fails_closed_while_info_is_pending"))
    suite.addTest(BrokerDurabilityAcquirePrecedenceTests(
        'test_control_acquire_classifies_revoked_and_active_actors_during_commit_loan'))
    suite.addTest(BrokerDurabilityAcquirePrecedenceTests(
        'test_issuer_exit_while_commit_is_held_classifies_cached_requester'))
    suite.addTest(BrokerOwnerLossIssuerProgressTests("test_broker_control_owner_loss_is_fenced_while_info_is_pending"))
    suite.addTest(BrokerOwnerLossIssuerProgressTests("test_broker_half_closed_owner_with_queued_request_is_fenced_during_info"))
    suite.addTest(BrokerOwnerLossIssuerProgressTests("test_broker_live_owner_queued_request_is_not_dispatched_during_info"))
    return suite


if __name__ == "__main__":
    unittest.main(verbosity=2)
