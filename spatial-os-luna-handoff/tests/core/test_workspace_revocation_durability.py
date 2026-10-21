"""Hold the real revoked-row transaction. Exercise the production broker and issuer."""
import os
from pathlib import Path
import select
import socket
import sqlite3
import time
import unittest
from unittest.mock import patch

import broker_pb2 as broker
import core_pb2 as core
import issuer_pb2 as issuer
from harness.resource_broker import ResourceBroker
from test_workspace_participant import WorkspaceParticipantFixture, BrokerProcess, RUN
import test_workspace_authority as workspace


class FaultBroker(BrokerProcess):
    def __init__(self, fixture, state, config=None):
        def launch(executable, command, *args, **kwargs):
            return ResourceBroker(executable, [*command, '--fixture-faults'], *args, **kwargs)
        # Qualification always uses the real delegated production process boundary.
        fixture.assertTrue(os.environ.get('SPATIAL_TEST_BROKER_CONFIG'))
        with patch.object(workspace, 'ResourceBroker', launch):
            super().__init__(fixture, state, config)


class WorkspaceRevocationDurabilityTests(WorkspaceParticipantFixture):
    enroll_second = workspace.WorkspaceAuthorityTests.enroll_second

    def gated_broker(self):
        self.gate = self.root/'revocation-commit-gate'
        self.gate.mkdir(mode=0o700)
        os.mkfifo(self.gate/'release', 0o600)
        self.released = False
        self.addCleanup(lambda: self.release_commit(b'x') if not self.released else None)
        with patch.dict(os.environ, SPATIAL_TEST_REVOCATION_COMMIT_GATE=str(self.gate)):
            item = self.start_broker(factory=FaultBroker)
        item.call('system.hello')
        self.assertEqual(self.activations(), [(1, 0, 1)])
        return item

    def release_commit(self, command=b'!'):
        if self.released:
            return
        # Nonblocking setup cannot deadlock cleanup when a test fails before entry.
        try:
            fd = os.open(self.gate/'release', os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            if not (self.gate/'entered').exists():
                return
            raise
        try:
            self.assertEqual(os.write(fd, command), 1)
            self.released = True
        finally:
            os.close(fd)

    def entered(self, poll, count):
        self.until(lambda: (self.gate/'entered').exists(), bool)
        self.assertEqual(len(poll.poll(0)), count, 'Commit started before all process scopes exited')
        self.assertFalse(self.barrier().complete)
        with sqlite3.connect(self.execution/'execution.db', timeout=.1) as db:
            # The uncommitted updates have not escaped the worker transaction.
            self.assertTrue(all(row[0] == 'running' for row in db.execute('SELECT state FROM processes')))

    def rows(self):
        with sqlite3.connect(self.execution/'execution.db') as db:
            return dict(db.execute('SELECT id,state FROM processes'))

    def wait_process(self, item, process_id):
        request_id = item.sequence + 1
        management = core.Request(protocol_version=1, request_id=request_id,
            process_wait=core.ProcessWaitRequest(id=process_id, timeout_seconds=0))
        return item.request(management=management)

    def test_gate_environment_is_ignored_without_fixture_faults(self):
        gate = self.root/'disabled-revocation-gate'
        gate.mkdir(mode=0o700)
        os.mkfifo(gate/'release', 0o600)
        with patch.dict(os.environ, SPATIAL_TEST_REVOCATION_COMMIT_GATE=str(gate)):
            item = self.start_broker()
        item.call('system.hello')
        poll, count = self.launch_application()
        self.revoke_now()
        self.until(self.barrier, lambda value: value.complete)
        self.assertFalse((gate/'entered').exists())
        self.assertEqual(len(poll.poll(0)), count)
        item.close()
        self.assertEqual(item.proc.returncode, 0)

    def test_stalled_commit_keeps_issuer_and_safe_requests_live_without_ack(self):
        item = self.gated_broker()
        poll, count = self.launch_application()
        application = self.application
        self.revoke_now()
        self.entered(poll, count)
        # Exceed the unchanged issuer idle window. Every response uses production dispatch.
        limit = time.monotonic()+6.2
        while time.monotonic() < limit:
            self.assertEqual(item.call('system.hello')['implementation'], 'production')
            refused = item.request(access=broker.AccessRequest(workspace='one', actor='busy'))
            self.assertEqual(refused.error.code, core.BUSY, refused)
            pending = self.barrier()
            self.assertEqual((pending.pending_count, pending.complete), (1, False))
            self.assertEqual(self.activations(), [(1, 0, 1)])
            self.assertIsNone(item.proc.poll())
            # Readiness waiting drives observation cadence without changing any deadline.
            self.assertFalse(select.select([item.stream], [], [], .05)[0])
        self.release_commit()
        self.until(self.barrier, lambda value: value.complete)
        self.assertEqual(self.rows(), {application: 'revoked'})
        item.close()
        self.assertEqual(item.proc.returncode, 0)
        self.assertEqual(self.activations(), [(1, 1, 0)])

    def test_commit_failure_rolls_back_and_never_acknowledges(self):
        item = self.gated_broker()
        poll, count = self.launch_application()
        application = self.application
        self.revoke_now()
        self.entered(poll, count)
        self.release_commit(b'x')
        self.assertEqual(item.proc.wait(timeout=6), 2)
        self.assertEqual(self.rows(), {application: 'running'})
        self.assertFalse(self.barrier().complete)
        self.assertEqual(self.activations(), [(1, 0, 1)])
        self.assertIn('Execution registry write or read failed', Path(item.log.name).read_text())
        item.close()

    def revoke_actor(self, actor_id, revision):
        peer = self.connect()
        try:
            operation = issuer.IssuerOperation(identity_revoke=issuer.IdentityRevoke(
                target=issuer.IdentityReference(kind=issuer.ACTOR_TARGET, id=actor_id),
                expected_security_revision=revision, reason='durability progress regression'))
            result = self.execute(peer, self.challenge(peer, operation))
            self.assertTrue(result.HasField('identity_status'), result)
        finally:
            peer.close()

    def test_second_revocation_stops_other_actor_during_first_commit(self):
        old_peer = self.owner_peer
        self.owner_peer = self.connect()
        try:
            self.enroll_second()
        finally:
            self.owner_peer.close()
            self.owner_peer = old_peer
        item = self.gated_broker()
        grant_reply = item.request(grants=broker.GrantListRequest(
            workspace='one', principal=self.principal(), limit=16))
        self.assertTrue(grant_reply.HasField('grants'), grant_reply)
        page = grant_reply.grants
        self.assertEqual(len(page.grants), 1)
        delegated = item.request(delegate=broker.GrantDelegateRequest(
            workspace='one', principal=self.principal(), parent_id=page.grants[0].id,
            terms=self.workspace_terms(self.second, depth=0), expected_revision=page.revision))
        self.assertTrue(delegated.HasField('grant'), delegated)
        self.launch_application('first-actor')
        initial_application = self.application
        principal = self.principal(self.second)
        result = item.request(launch=broker.LaunchRequest(
            workspace='one', actor='identity:'+principal.issuer_id.hex()+':'+principal.actor_id.hex(),
            principal=principal, command=core.ProcessLaunchRequest(operation_id='second-actor',
                profile='development', argv=['/usr/bin/python3', '-c', RUN])))
        self.assertTrue(result.HasField('launched'), result)
        second_application = result.launched.process_id
        pids = self.until(self.descendants, lambda value: len(value) >= 10)
        poll = select.poll()
        for pid in pids:
            fd = os.pidfd_open(pid)
            self.addCleanup(os.close, fd)
            poll.register(fd, select.POLLIN)
        self.revoke_actor(self.second.actor_id, 0)
        self.until(lambda: (self.gate/'entered').exists(), bool)
        self.assertLess(len(poll.poll(0)), len(pids), 'Independent actor stopped before its revocation')
        revoked_wait = self.wait_process(item, second_application)
        self.assertTrue(revoked_wait.HasField('management'), revoked_wait)
        self.assertEqual(revoked_wait.management.error.code, core.UNAVAILABLE, revoked_wait)
        live_wait = self.wait_process(item, initial_application)
        self.assertTrue(live_wait.HasField('error'), live_wait)
        self.assertEqual(live_wait.error.code, core.BUSY, live_wait)
        self.assertFalse(self.barrier().complete)
        self.assertEqual(self.activations(), [(1, 0, 1)])
        before = item.request(authorization_fence=broker.AuthorizationFenceRequest(principal=self.principal()))
        self.assertTrue(before.HasField('authorization_fence'), before)
        self.revoke_actor(self.identity.actor_id, 1)
        self.until(lambda: len(poll.poll(0)), lambda count: count == len(pids))
        after = item.request(authorization_fence=broker.AuthorizationFenceRequest(principal=self.principal()))
        self.assertTrue(after.HasField('error'), after)
        self.assertFalse(self.barrier().complete)
        self.assertEqual(self.activations(), [(1, 0, 1)])
        self.release_commit()
        self.until(lambda: self.activations()[0][1], lambda revision: revision == 2)
        self.assertEqual(self.rows(), {initial_application: 'revoked', second_application: 'revoked'})
        item.close()
        self.assertEqual(item.proc.returncode, 0)
        self.assertEqual(self.activations(), [(1, 2, 0)])

    def test_shutdown_waits_for_owned_commit_before_closing_database(self):
        item = self.gated_broker()
        poll, count = self.launch_application()
        application = self.application
        self.revoke_now()
        self.entered(poll, count)
        item.stream.shutdown(socket.SHUT_WR)
        self.assertFalse(select.select([item.proc.pidfd], [], [], .15)[0])
        self.assertFalse(self.barrier().complete)
        self.release_commit()
        self.assertEqual(item.proc.wait(timeout=6), 0)
        self.assertEqual(self.rows(), {application: 'revoked'})
        self.assertTrue(self.barrier().complete)
        self.assertEqual(self.activations(), [(1, 1, 0)])
        item.close()


# This fixture reuses the real NativeLaunch/X server boundary and production leases.
# Its runtime remains confined to qualification hosts.
import test_application_control_lease as leases
import grants_pb2 as grants


class IndependentControllerDuringDurabilityTests(leases.ApplicationControlLeaseTests):
    release_commit = WorkspaceRevocationDurabilityTests.release_commit
    revoke_actor = WorkspaceRevocationDurabilityTests.revoke_actor
    barrier = WorkspaceParticipantFixture.barrier
    activations = WorkspaceParticipantFixture.activations

    def test_unaffected_controller_survives_another_actors_stalled_commit(self):
        self.enroll_second()
        self.gate = self.root/'native-revocation-commit-gate'
        self.gate.mkdir(mode=0o700)
        os.mkfifo(self.gate/'release', 0o600)
        self.released = False
        def launch(executable, command, *args, **kwargs):
            return ResourceBroker(executable, [*command, '--fixture-faults'], *args, **kwargs)
        with patch.dict(os.environ, SPATIAL_TEST_REVOCATION_COMMIT_GATE=str(self.gate)), \
                patch.object(workspace, 'ResourceBroker', launch):
            unaffected = self.application()
        self.addCleanup(lambda: self.release_commit(b'x') if not self.released else None)
        first_lease = self.acquire(unaffected)
        self.delegate([grants.WORKSPACE_ACCESS, *workspace.PROJECT, *leases.access.APP_RIGHTS])
        other = self.launch_application(claims=self.second)
        second_lease = self.acquire(other, self.second)
        self.revoke_actor(self.second.actor_id, 0)
        self.until(lambda: (self.gate/'entered').exists(), bool)
        self.broker.event(second_lease)
        for _ in range(5):
            self.assertEqual(self.application_access(unaffected), [(True, True)])
            self.broker.call('system.hello')
            self.assertFalse(any(event.id == first_lease.id for event in self.broker.events))
        # The revoked controller can complete native-input cancellation during the loan.
        self.release(second_lease, self.second)
        self.assertFalse(self.barrier().complete)
        self.release_commit()
        self.until(self.barrier, lambda value: value.complete)
        self.assertEqual(self.acquire(unaffected), first_lease)
        self.assertFalse(any(event.id == first_lease.id for event in self.broker.events))


def load_tests(loader, standard_tests, pattern):
    # Select only additive methods. Do not rerun inherited lease tests here.
    suite = loader.loadTestsFromTestCase(WorkspaceRevocationDurabilityTests)
    suite.addTest(IndependentControllerDuringDurabilityTests(
        'test_unaffected_controller_survives_another_actors_stalled_commit'))
    return suite


if __name__ == '__main__':
    unittest.main(verbosity=2)
