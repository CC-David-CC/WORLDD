"""Reproduce issuer loss before fencing using real retained native ownership."""
import argparse
import json
import os
from pathlib import Path
import select
import signal
import socket
import sqlite3
import subprocess
import sys
import unittest

p = argparse.ArgumentParser()
p.add_argument('--source', type=Path, required=True)
p.add_argument('--build', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
options = p.parse_args(); options.output.mkdir(mode=0o700, parents=True, exist_ok=False)
sys.path[:0] = [str(options.build / 'python'), str(options.source),
    str(options.source / 'tests/native'), str(options.source / 'tests/core')]
sys.argv = [sys.argv[0], str(options.source), str(options.build / 'spatial-authority-launch-probe'),
    str(options.build / 'spatial-security-auth'), str(options.build / 'identityd'), str(options.build / 'spatial-issuer-client')]
os.environ['SPATIAL_SECURITY_EVIDENCE'] = str(options.output / 'native')
from google.protobuf import text_format
import test_stopped_broker_dependency as existing
import test_supervisor_registry as registry
from test_identity_issuer import BINARY
import issuer_pb2 as identity
import launch_pb2 as launch
import session_probe_pb2 as probe
from harness.wire import frame


class RestartableIssuer(registry.SupervisorRegistryTests):
    def launch(self, address='127.0.0.1'):
        if not hasattr(self, 'retained_listener'):
            self.retained_listener = socket.socket()
            self.retained_listener.bind((address, 0)); self.retained_listener.listen(32)
            self.port = self.retained_listener.getsockname()[1]
            self.addCleanup(self.retained_listener.close)
        self.config_path.write_text(text_format.MessageToString(self.config)); self.config_path.chmod(0o600)
        log = (self.root / ('tls-' + str(len(self.daemons)) + '.log')).open('wb')
        descriptor = self.retained_listener.fileno()
        proc = subprocess.Popen([str(BINARY), '--state-dir', str(self.state), '--listen-fd', str(descriptor),
            '--tls-config', str(self.config_path)], pass_fds=(descriptor,), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=log)
        self.daemons.append((proc, log)); return proc


class IssuerLossBaseline(existing.StoppedBrokerDependencyTests):
    def issuer_fixture(self):
        return RestartableIssuer('runTest')

    def preserve(self, name, message):
        (options.output / (name + '.pb')).write_bytes(message.SerializeToString())

    def barrier(self):
        peer = self.issuer.connect()
        try:
            operation = identity.IssuerOperation(barrier_read=identity.AuthorityBarrierRead(revision=1))
            reply = self.issuer.execute(peer, self.issuer.challenge(peer, operation))
            self.assertTrue(reply.HasField('authority_barrier'), reply)
            return reply.authority_barrier
        finally: peer.close()

    def observe(self, **operation):
        self.coordinator_sequence += 1
        request = probe.SessionProbeRequest(protocol_version=1, request_id=self.coordinator_sequence, **operation)
        self.coordinator.stdin.write(frame(request)); self.coordinator.stdin.flush()
        reply = self.message(self.coordinator.stdout, probe.SessionProbeReply)
        self.assertEqual(reply.protocol_version, 1); self.assertEqual(reply.request_id, request.request_id)
        return reply

    def test_issuer_restart_allows_exact_retirement_after_owned_cleanup(self):
        self.pin_broker(stop_world=False)
        self.preserve('world-before', self.world_record); self.preserve('broker-before', self.original)
        independent = self.supervisor.issue(workspace=b'issuer-loss-independent', application=b'survivor')
        survivor = self.connect(independent.cookie); survivor_identity = survivor.inspect()
        project_marker = self.project / 'issuer-loss-uncommitted.txt'; project_marker.write_text('preserved project data\n')
        world_pid = self.world_ids.worldd_pid
        supervisor_pid = int(next(row.split()[1] for row in Path('/proc/' + str(world_pid) + '/status').read_text().splitlines() if row.startswith('PPid:')))
        world_pins = {pid: os.pidfd_open(pid) for pid in (world_pid, self.world_ids.spatialwm_pid, supervisor_pid)}
        for pin in world_pins.values(): self.addCleanup(os.close, pin)
        issuer_proc = self.issuer.daemons[-1][0]
        issuer_pin = os.pidfd_open(issuer_proc.pid); self.addCleanup(os.close, issuer_pin)
        peer = self.issuer.connect()
        self.addCleanup(peer.close)
        operation = identity.IssuerOperation(identity_revoke=identity.IdentityRevoke(
            target=identity.IdentityReference(kind=identity.CREDENTIAL_TARGET, id=self.claims.credential_id),
            expected_security_revision=0, reason='issuer loss before native scope fencing'))
        challenge = self.issuer.challenge(peer, operation)
        for pin in (world_pins[world_pid], self.pins[self.broker_pid]): signal.pidfd_send_signal(pin, signal.SIGSTOP)
        try:
            committed = self.issuer.execute(peer, challenge)
            self.assertTrue(committed.HasField('identity_status'), committed)
            before = self.barrier(); self.preserve('pending-before', before)
            self.assertEqual((before.participant_count, before.pending_count, before.complete), (2, 2, False))
            self.assertTrue(all(item.applied_revision == 0 and not item.complete for item in before.obligations))
            signal.pidfd_send_signal(issuer_pin, signal.SIGKILL)
            self.assertEqual(issuer_proc.wait(timeout=3), -signal.SIGKILL)
        finally:
            # Resume only still-live fixture children; their supervisors own further teardown.
            for pin in (world_pins[world_pid], self.pins[self.broker_pid]):
                try: signal.pidfd_send_signal(pin, signal.SIGCONT)
                except ProcessLookupError: pass
        self.exited()
        for pin in world_pins.values(): self.assertEqual(select.select([pin], [], [], 8)[0], [pin])
        self.world_cleanup = self.request(stop_world=self.owner).cleanup; self.world_stopped = True
        self.cleanup = self.request(stop_broker=launch.AuthorityLaunchQuery()).cleanup
        for name, actual, original in (('world-cleanup', self.world_cleanup, self.world_record), ('broker-cleanup', self.cleanup, self.original)):
            self.assertEqual(actual.stage, launch.OWNED_SCOPES_STOPPED)
            self.assertEqual(actual.process_stopped.identity, original.process)
            self.assertEqual(actual.native_stopped.identity, original.native)
            self.assertFalse((self.cgroup / actual.process.scope_name).exists())
            self.preserve(name, actual)
        self.assertEqual(survivor.inspect(), survivor_identity)
        self.assertEqual(project_marker.read_text(), 'preserved project data\n')
        before_restart = self.issuer.snapshot_all()
        original_port = self.issuer.port
        self.issuer.peer = self.issuer.ready()
        self.assertEqual(self.issuer.port, original_port)
        self.assertEqual(self.issuer.snapshot_all(), before_restart)
        after = self.barrier(); self.preserve('pending-after-restart', after)
        self.assertEqual(after, before)
        self.issuer.closed(peer)
        dependent = self.dependency(); self.saved(dependent); self.preserve('dependent-cleanup', dependent)
        world_reply = self.observe(retire_world=self.owner)
        broker_reply = self.observe(retire_broker=launch.AuthorityLaunchQuery())
        self.preserve('world-retirement-reply', world_reply); self.preserve('broker-retirement-reply', broker_reply)
        final = self.barrier(); self.preserve('final-barrier', final)
        witness = dict(issuer_restarted=True, same_endpoint=True, pending_survived_restart=after == before,
            stopped_pinned_processes=len(self.pins) + len(world_pins), checked_world_cleanup=True, checked_broker_cleanup=True,
            world_fence=self.world_cleanup.HasField('fence'), broker_fence=self.cleanup.HasField('fence'),
            independent_native_credential_survived=survivor.inspect() == survivor_identity,
            project_data_preserved=project_marker.read_text() == 'preserved project data\n',
            world_retirement_outcome=world_reply.WhichOneof('outcome'), broker_retirement_outcome=broker_reply.WhichOneof('outcome'),
            final_pending=final.pending_count)
        (options.output / 'witness.json').write_text(json.dumps(witness, indent=2) + '\n')
        for path in self.issuer.root.glob('tls-*.log'):
            (options.output / path.name).write_bytes(path.read_bytes())
        self.finish_session()
        self.assertTrue(world_reply.HasField('retirement'), world_reply)
        self.assertTrue(broker_reply.HasField('retirement'), broker_reply)
        self.assertTrue(final.complete, final)


suite = unittest.TestSuite([IssuerLossBaseline('test_issuer_restart_allows_exact_retirement_after_owned_cleanup')])
raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
