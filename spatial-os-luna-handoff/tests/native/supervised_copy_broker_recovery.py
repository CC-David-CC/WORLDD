"""Adapter for supervised FileActionBroker crash/restart integration fixtures.

The adapter owns no replacement authority. Its caller must provide the genuine
CopyFaultStorageBrokerProcess and configured workspace execution path.
"""
import os
import select
import signal
import time
import hashlib
import launch_pb2 as launch
from harness.issuer_launch_denial import (describe_issuer_trace,
    require_active_generation_denial_trace)
from harness.issuer_relay import IssuerRelay
import issuer_pb2 as issuer

def finish_checked_retirement(fixture, broker, resources, handle):
    """Close supervised resources, validate the retained failure proof, then retire once."""
    fixture.assertIsNone(handle.retirement, "Generation already retired")
    broker.close()
    fixture.assertEqual(broker.owner.poll(), 0, 'Production storage owner did not finish EOF cleanup')
    fixture.assertTrue(resources.closed)
    if handle.proc is not None and handle.proc.returncode != 0:
        fixture.assertIsNotNone(handle.control, 'Failed supervisor control reply must stay available')
        fixture.assertFalse(handle.control.closed, 'Resource close discarded failed cleanup reply')
    record = handle.failed_cleanup()
    fixture.assertEqual(record.binding, handle.binding)
    fixture.assertEqual(record.process, handle.initial.view.record.process)
    fixture.assertEqual(record.native, handle.initial.view.record.native)
    fixture.assertEqual(record.stage, launch.OWNED_SCOPES_STOPPED)
    fixture.assertTrue(record.HasField('fence'))
    fixture.assertEqual(record.process_stopped.identity, record.process)
    fixture.assertEqual(record.native_stopped.identity, record.native)
    fixture.assertEqual(record.authorization, handle.initial.view.record.authorization)
    digest = hashlib.sha256(record.SerializeToString(deterministic=True)).digest()
    handle.retire(record, digest)
    retirement = handle.retirement
    fixture.assertEqual(retirement.request.cleanup_sha256, digest)
    fence = record.fence
    expected_owner = issuer.AuthorityFenceBegin(
        participant_id=handle.binding.participant_id,
        registered_revision=handle.binding.registered_revision,
        generation=fence.generation, service_incarnation=fence.service_incarnation,
        launch_id=fence.launch_id, supervision_revision=fence.supervision_revision,
        authorization_id=fence.authorization_id)
    fixture.assertEqual(retirement.request.owner, expected_owner)
    fixture.assertEqual(retirement.request.fence_id, fence.fence_id)
    return record, retirement


def launch_journal_bytes(directory):
    """Include committed WAL bytes. Ignore volatile shared-memory reader locks."""
    return {name: (directory/name).read_bytes() for name in ('launch.db', 'launch.db-wal')
            if (directory/name).exists()}


def require_candidate_supervisor_denial(fixture, reply):
    fixture.assertTrue(reply.HasField('error'), reply)
    fixture.assertEqual(reply.error.message, 'Issuer did not approve the retained participant connection', reply)
    fixture.assertTrue(reply.error.HasField('cleanup_record'), 'Rejected candidate lacks checked cleanup')
    fixture.assertEqual(reply.error.cleanup_record.stage, launch.OWNED_SCOPES_STOPPED)


def wait_for_active_generation(fixture, handle, timeout_seconds=5):
    """Wait for the exact issuer activation row while the real supervisor lives."""
    deadline = time.monotonic() + timeout_seconds
    participant = handle.binding.participant_id
    generation = handle.previous_generation + 1
    incarnation = handle.binding.service_incarnation
    registration = handle.supervision.registration.SerializeToString(deterministic=True)
    while True:
        rows = handle.owner.activation_rows([participant])
        process_state = handle.proc.poll()
        pidfd_ready = bool(select.select([handle.pidfd], [], [], 0)[0]) if handle.pidfd is not None else True
        if handle.initial is not None and handle.initial.HasField('error'):
            fixture.fail('Supervisor startup failed before active generation readiness: %r '
                         'rows=%r process=%r pidfd_ready=%r' %
                         (handle.initial.error, rows, process_state, pidfd_ready))
        if process_state is not None or pidfd_ready:
            fixture.fail('Supervisor failed before active generation readiness: rows=%r process=%r pidfd_ready=%r' %
                         (rows, process_state, pidfd_ready))
        active = [row for row in rows if row[-1]]
        if active:
            fixture.assertTrue(
                len(active) == 1 and active[0][:4] == (participant, generation, incarnation, registration),
                'Unexpected active generation: rows=%r process=%r pidfd_ready=%r' %
                (rows, process_state, pidfd_ready))
            return active[0]
        now = time.monotonic()
        if now >= deadline:
            fixture.fail('Timed out waiting for exact active generation: rows=%r process=%r pidfd_ready=%r' %
                         (rows, process_state, pidfd_ready))
        time.sleep(min(.01, deadline - now))

class SupervisedCopyBrokerRecovery:
    """Kill, validate, retire, and replace one real supervised broker generation."""

    def __init__(self, fixture, broker, authority_config):
        self.fixture = fixture
        self.broker = broker
        self.authority_config = authority_config
        self.resources = broker.resources
        if self.resources is None or not hasattr(self.resources, 'handle'):
            raise RuntimeError('SETUP FAILURE: production supervised broker resources are required')
        self.handle = self.resources.handle
        self.execution = broker.fixture.execution
        self.copy_journal = self.execution / 'file-operations' / 'file-operations.db'
        self.identity = dict(self.handle.identity)
        self.death_observed_monotonic_ns = None
        self.cleanup_record = None
        self.retirement = None

    def reject_live_replacement_at_issuer(self, constructor):
        """Launch an authentic replacement and require issuer denial.

        This bypasses only the fixture owner's local preflight guard. It reuses
        the active generation's real supervisor key and registration, then lets
        the production supervisor make a fresh activation request.
        """
        owner = self.handle.owner
        participant = self.handle.binding.participant_id
        rows_before = owner.activation_rows([participant])
        self.fixture.assertTrue(rows_before and any(row[-1] for row in rows_before))
        journal_before = launch_journal_bytes(self.handle.journal)
        original_service = owner.service

        def reuse_authenticated_supervisor(runtime):
            self.fixture.assertEqual(runtime.participant_id, participant)
            return self.handle.key, self.handle.public, self.handle.supervision, self.handle.previous_generation

        issuer_relay = IssuerRelay(owner.fixture, owner.fixture)
        prior_override = getattr(owner.fixture, '_supervised_copy_issuer_client_config_override', None)
        owner.service = reuse_authenticated_supervisor
        try:
            owner.fixture._supervised_copy_issuer_client_config_override = str(
                owner.fixture.config_file(issuer_relay.port))
            with self.fixture.assertRaises(AssertionError):
                constructor(self.fixture, self.execution, self.authority_config)
        finally:
            owner.service = original_service
            if prior_override is None:
                del owner.fixture._supervised_copy_issuer_client_config_override
            else:
                owner.fixture._supervised_copy_issuer_client_config_override = prior_override
            issuer_relay.close()
        candidate = owner.handles[-1]
        self.fixture.assertIsNot(candidate, self.handle)
        self.fixture.assertIsNotNone(candidate.initial)
        self.fixture.assertTrue(candidate.initial.HasField('error'), candidate.initial)
        self.fixture.assertTrue(candidate.initial.error.HasField('cleanup_record'),
                                'Rejected candidate must include its checked cleanup record')
        candidate_cleanup = candidate.initial.error.cleanup_record
        self.fixture.assertEqual(candidate_cleanup.binding.participant_id, participant)
        try:
            require_active_generation_denial_trace(issuer_relay.observations, participant,
                candidate_cleanup.binding.service_incarnation, candidate_cleanup.process.launch_id,
                self.handle.supervision.revision, self.handle.public,
                self.handle.supervision.registration.issuer_id)
        except Exception as error:
            detail = str(error) if isinstance(error, AssertionError) else type(error).__name__
            raise AssertionError(detail + '; ' +
                describe_issuer_trace(issuer_relay.observations)) from error
        require_candidate_supervisor_denial(self.fixture, candidate.initial)
        self.fixture.assertEqual(issuer_relay.connections, 1, 'Candidate issuer relay must have one TLS client')
        self.fixture.assertFalse(issuer_relay.errors, issuer_relay.errors)
        cleanup = candidate.initial.error.cleanup_record
        self.fixture.assertEqual(cleanup, candidate.record(),
                                 'Cleanup receipt must match the durable launch row')
        self.fixture.assertEqual(cleanup.stage, launch.OWNED_SCOPES_STOPPED)
        self.fixture.assertTrue(cleanup.HasField('process_stopped'), cleanup)
        self.fixture.assertTrue(cleanup.HasField('native_stopped'), cleanup)
        self.fixture.assertEqual(cleanup.process_stopped.identity, cleanup.process)
        self.fixture.assertEqual(cleanup.native_stopped.identity, cleanup.native)
        self.fixture.assertEqual(cleanup.process.launch_id, cleanup.native.launch_id)
        scope_name = cleanup.process.scope_name
        self.fixture.assertTrue(scope_name)
        self.fixture.assertNotIn('/', scope_name)
        self.fixture.assertNotIn('\\', scope_name)
        self.fixture.assertNotIn(scope_name, ('.', '..'))
        self.fixture.assertEqual(os.path.basename(scope_name), scope_name)
        self.fixture.assertTrue(cleanup.process.launch_id)
        self.fixture.assertGreater(cleanup.process.inode, 0)
        candidate_scope = candidate.cgroup / scope_name
        self.fixture.assertFalse(candidate_scope.exists(),
                                 'Denied candidate ProcessScope cgroup remains present')
        self.fixture.assertEqual(owner.activation_rows([participant]), rows_before,
                                 'Rejected replacement changed issuer activation history')
        self.fixture.assertEqual(launch_journal_bytes(self.handle.journal), journal_before,
                                 'Rejected replacement changed the active launch journal')
        candidate.close()
        self.fixture.assertTrue(candidate.initial.HasField('error'),
                                'Closing rejected candidate discarded its typed denial')
        candidate.close_control()
        self.fixture.assertIsNotNone(candidate.proc.poll(), 'Rejected supervisor remains live')
        self.fixture.assertIsNone(candidate.identity, 'Rejected replacement launched an authority child')
        self.handle.verify_active()
        return candidate.initial.error

    def kill_actual_broker(self, timeout_seconds=5):
        pid = self.identity['pid']
        self.fixture.assertEqual(self.handle.pid, pid)
        self.fixture.assertEqual(self.fixture.start_time(pid), self.identity['start_ticks'])
        self.fixture.assertEqual(os.readlink('/proc/' + str(pid) + '/exe'), self.identity['executable'])
        self.fixture.assertEqual(self.identity['start_ticks'], self.fixture.start_time(pid))
        self.fixture.assertIsNotNone(self.handle.pidfd, "Retained broker pidfd is required")
        descriptor = os.dup(self.handle.pidfd)
        try:
            self.fixture.assertEqual(select.select([descriptor], [], [], 0)[0], [],
                                     'Broker exited before SIGKILL')
            signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            deadline = time.monotonic() + timeout_seconds
            while not select.select([descriptor], [], [], max(0, deadline - time.monotonic()))[0]:
                self.fixture.assertLess(time.monotonic(), deadline, 'Broker pidfd did not signal exit')
            self.death_observed_monotonic_ns = time.monotonic_ns()
        finally:
            os.close(descriptor)

    def checked_cleanup_and_retire(self):
        self.fixture.assertIsNotNone(self.death_observed_monotonic_ns,
                                     'SIGKILL death must precede cleanup')
        self.fixture.assertIsNone(self.retirement, 'Generation already retired')
        rows_before = self.handle.owner.activation_rows([self.handle.binding.participant_id])
        self.cleanup_record, self.retirement = finish_checked_retirement(
            self.fixture, self.broker, self.resources, self.handle)
        expected = [row[:-1]+(0,) if row[1] == self.cleanup_record.fence.generation else row
                    for row in rows_before]
        self.fixture.assertEqual(self.handle.owner.activation_rows([self.handle.binding.participant_id]), expected)
        return self.cleanup_record, self.retirement

    def restart(self, constructor):
        self.fixture.assertIsNotNone(self.retirement, 'Issuer retirement must precede replacement')
        replacement = constructor(self.fixture, self.broker.fixture.execution, self.authority_config)
        current = replacement.resources.handle
        self.fixture.assertEqual(replacement.fixture.execution, self.execution)
        self.fixture.assertEqual(replacement.fixture.execution / 'file-operations' / 'file-operations.db', self.copy_journal)
        self.fixture.assertNotEqual(current.journal, self.handle.journal)
        self.fixture.assertEqual(current.supervision, self.handle.supervision)
        self.fixture.assertEqual(current.previous_generation, self.handle.previous_generation + 1)
        self.fixture.assertNotEqual(current.binding.service_incarnation,
                                     self.handle.binding.service_incarnation)
        return replacement
