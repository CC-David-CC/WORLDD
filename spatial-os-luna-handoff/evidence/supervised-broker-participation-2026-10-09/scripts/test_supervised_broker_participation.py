"""Real issuer-backed broker participation through the production supervisor."""
import os
from pathlib import Path
import select
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from google.protobuf import text_format

SOURCE = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(SOURCE / 'tests/native'))
import test_supervisor_broker_handoff_regressions as handoff
import broker_pb2 as broker
import core_pb2 as core
import grants_pb2 as grants
import issuer_pb2 as identity
import launch_pb2 as launch
import security_pb2 as security
from harness.git_fixture import GitRepo

WORKSPACE = 'workspace-one'
RIGHTS = [grants.WORKSPACE_ACCESS, grants.FILE_BROWSE, grants.FILE_READ,
          grants.FILE_CREATE, grants.FILE_EDIT, grants.FILE_RENAME, grants.FILE_MOVE,
          grants.FILE_COPY, grants.FILE_TRASH, grants.FILE_RESTORE,
          grants.FILE_PERMANENT_DELETE, grants.PROCESS_EXECUTE]


def register_workspace(fixture):
    participant = identity.AuthorityParticipant(id=b'p' * 32, public_key=fixture.public,
        domains=[grants.Domain(kind=grants.WORKSPACE, id=WORKSPACE)])
    operation = identity.IssuerOperation(participant_register=identity.AuthorityParticipantRegister(
        participant=participant, expected_registry_revision=0))
    reply = fixture.execute(fixture.peer, fixture.challenge(fixture.peer, operation))
    fixture.assertTrue(reply.HasField('participant'), reply)
    return reply.participant


class SupervisedBrokerParticipationTests(handoff.BrokerHandoffRegressionTests):
    def setUp(self):
        # Register only the broker's real domain. No world authority is granted.
        registry = handoff.baseline.supervisor.owned.registry.SupervisorRegistryTests
        with patch.object(registry, 'register', register_workspace):
            super().setUp()
        peer = self.issuer.peer
        reply = self.issuer.decide(peer, self.issuer.enroll(peer))
        self.assertTrue(reply.HasField('enrollment'), reply)
        self.actor = self.issuer.claims(reply.enrollment, self.issuer.info)
        self.principal = grants.ActorRef(issuer_id=self.actor.issuer_id, actor_id=self.actor.actor_id)
        self.actor_name = 'identity:' + self.actor.issuer_id.hex() + ':' + self.actor.actor_id.hex()
        def preserve_logs():
            if os.environ.get('SPATIAL_SECURITY_EVIDENCE'):
                target = Path(os.environ['SPATIAL_SECURITY_EVIDENCE'])
                target.mkdir(parents=True, exist_ok=True)
                for index, path in enumerate(sorted(self.root.glob('supervisor-*.log'))):
                    (target / (self._testMethodName + '-supervisor-' + str(index) + '.log')).write_bytes(path.read_bytes())
        self.addCleanup(preserve_logs)

    def write_config(self, name, message):
        path = self.root / (name + '.textproto')
        path.write_text(text_format.MessageToString(message))
        path.chmod(0o600)
        return path

    def broker_config(self, target=38):
        config = super().broker_config(target)
        executable = config.launch.executable
        bootstrap = subprocess.Popen([executable, '--state-dir', str(self.root / 'execution')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def cleanup():
            if bootstrap.poll() is None:
                bootstrap.kill()
            bootstrap.communicate(timeout=5)
        self.addCleanup(cleanup)
        pipe = handoff.BrokerPipe(self, bootstrap)
        repository = GitRepo(self.root)
        pipe.call('git.repo.import', id='repo', path=str(repository.path))
        pipe.call('git.worktree.create', repo='repo', id='tree', branch='app', start='main', operation_id='tree')
        pipe.call('workspace.create', id=WORKSPACE, worktree='tree', policy=handoff.native_launch.POLICY)
        bootstrap.stdin.close()
        self.assertEqual(bootstrap.wait(timeout=5), 0)
        bootstrap.stdin = None
        directory = self.root / 'workspace-authority'
        directory.mkdir(mode=0o700)
        seed = broker.WorkspaceAuthoritySeed(protocol_version=1, workspace=WORKSPACE,
            authority_directory=str(directory), roots=[grants.GrantTerms(subject=self.principal,
                scope=grants.Scope(domain=grants.Domain(kind=grants.WORKSPACE, id=WORKSPACE)), rights=RIGHTS)])
        result = subprocess.run([executable, '--state-dir', str(self.root / 'execution'),
            '--init-authority', str(self.write_config('workspace-seed', seed))], capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        authority = broker.WorkspaceAuthorityConfig(protocol_version=1,
            issuer_client_config=str(self.issuer.config_file()),
            participant_runtime_config=config.child_participant_config,
            bindings=[broker.WorkspaceAuthorityBinding(workspace=WORKSPACE, authority_directory=str(directory))])
        config.launch.arguments.extend(['--authority-config', str(self.write_config('workspace-authority', authority))])
        return config

    def activation_state(self):
        with sqlite3.connect('file:' + str(self.issuer.state / 'identity.db') + '?mode=ro', uri=True) as db:
            return db.execute('SELECT generation,applied_revision,active FROM authority_activation ORDER BY generation').fetchall()

    def until(self, operation, predicate):
        deadline = time.monotonic() + 6
        while True:
            value = operation()
            if predicate(value):
                return value
            self.assertLess(time.monotonic(), deadline, repr(value))
            time.sleep(.01)

    def launch_request(self, operation='application', principal=True):
        request = broker.LaunchRequest(workspace=WORKSPACE, actor=self.actor_name,
            command=core.ProcessLaunchRequest(operation_id=operation, profile='development',
                argv=['/usr/bin/python3', '-c', handoff.native_launch.APP]))
        if principal:
            request.principal.CopyFrom(self.principal)
        return request

    def application(self):
        reply = self.broker.request(launch=self.launch_request())
        self.assertTrue(reply.HasField('launched'), reply)
        observed = self.record(reply.launched)
        client = self.connect(bytes.fromhex(observed['cookie']))
        native = client.inspect()
        self.assertEqual(native.role, security.APPLICATION)
        self.assertEqual(native.workspace, WORKSPACE.encode())
        self.assertEqual(native.application, reply.launched.process_id.encode())
        self.assertEqual(native.server_id, self.supervisor.inspect().server_id)
        self.assertEqual(observed['inherited_fds'], [])
        self.assertTrue(observed['auth_readonly'])
        return observed, client

    def revoke_actor(self):
        peer = self.issuer.connect()
        try:
            operation = identity.IssuerOperation(identity_revoke=identity.IdentityRevoke(
                target=identity.IdentityReference(kind=identity.ACTOR_TARGET, id=self.actor.actor_id),
                expected_security_revision=0, reason='supervised broker authority regression'))
            reply = self.issuer.execute(peer, self.issuer.challenge(peer, operation))
            self.assertTrue(reply.HasField('identity_status'), reply)
        finally:
            peer.close()

    def issuer_barrier(self):
        peer = self.issuer.connect()
        try:
            operation = identity.IssuerOperation(barrier_read=identity.AuthorityBarrierRead(revision=1))
            reply = self.issuer.execute(peer, self.issuer.challenge(peer, operation))
            self.assertTrue(reply.HasField('authority_barrier'), reply)
            return reply.authority_barrier
        finally:
            peer.close()

    def graceful_close(self, proc, channel, revision):
        proc.stdin.close()
        result = self.message(channel, launch.AuthorityLaunchReply)
        self.assertTrue(result.HasField('view'), result)
        self.assertEqual(result.view.record.stage, launch.OWNED_SCOPES_STOPPED)
        self.assertEqual(proc.wait(timeout=5), 0)
        self.checked_cleanup()
        self.assertEqual(self.activation_state(), [(1, revision, 0)])

    def test_real_authority_broker_activates_launches_native_app_and_releases_on_eof(self):
        proc, channel, _, _, _ = self.started_broker()
        self.assertEqual(self.activation_state(), [(1, 0, 1)])
        child = text_format.Parse(self.child_config.read_text(), identity.AuthorityParticipantRuntimeConfig())
        self.assertEqual(child.supervisor_channel_fd, 37)
        self.assertEqual(child.private_key_path, '')
        self.assertEqual(child.issuer_client_config, '')
        observed, client = self.application()
        self.graceful_close(proc, channel, 0)
        self.assert_closed(client)
        with self.assertRaises(handoff.native_launch.native.Denied):
            self.connect(bytes.fromhex(observed['cookie']))

    def test_missing_actor_principal_cannot_use_supervised_broker_launch_authority(self):
        proc, channel, _, _, _ = self.started_broker()
        reply = self.broker.request(launch=self.launch_request(principal=False))
        self.assertTrue(reply.HasField('error'), reply)
        self.assertEqual(reply.error.code, core.FORBIDDEN)
        self.assertEqual(self.activation_state(), [(1, 0, 1)])
        self.graceful_close(proc, channel, 0)

    def test_actor_revocation_closes_native_app_before_barrier_acknowledgement(self):
        proc, channel, _, _, _ = self.started_broker()
        observed, client = self.application()
        self.revoke_actor()
        completed = self.until(self.issuer_barrier, lambda value: value.complete)
        self.assertEqual((completed.participant_count, completed.pending_count), (1, 0))
        # Do not give the server a new grace period after the barrier completes.
        self.assertTrue(select.select([client.stream], [], [], 0)[0], 'Barrier acknowledged before native disconnection')
        self.assertEqual(client.stream.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT), b'')
        self.assert_closed(client)
        with self.assertRaises(handoff.native_launch.native.Denied):
            self.connect(bytes.fromhex(observed['cookie']))
        reply = self.broker.request(launch=self.launch_request(operation='after-revocation'))
        self.assertTrue(reply.HasField('error'), reply)
        self.assertEqual(reply.error.code, core.UNAUTHORIZED)
        self.graceful_close(proc, channel, 1)

    def test_broker_death_cleans_native_scope_but_preserves_unretired_issuer_obligation(self):
        proc, channel, _, child_pid, pin = self.started_broker()
        observed, client = self.application()
        os.kill(child_pid, signal.SIGKILL)
        reply = self.message(channel, launch.AuthorityLaunchReply)
        self.assertTrue(reply.HasField('error'), reply)
        self.assertEqual(proc.wait(timeout=5), 2)
        self.assertEqual(select.select([pin], [], [], 0)[0], [pin])
        self.checked_cleanup()
        self.assert_closed(client)
        with self.assertRaises(handoff.native_launch.native.Denied):
            self.connect(bytes.fromhex(observed['cookie']))
        self.assertEqual(self.activation_state(), [(1, 0, 1)])
        self.revoke_actor()
        pending = self.issuer_barrier()
        self.assertEqual((pending.participant_count, pending.pending_count, pending.complete), (1, 1, False))


if __name__ == '__main__':
    suite = unittest.TestSuite(SupervisedBrokerParticipationTests(name)
        for name in SupervisedBrokerParticipationTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
