"""Real issuer-backed world participation through its owning native supervisor."""
import io
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
import test_supervisor_world_handoff_regressions as handoff
import core_pb2 as core
import grants_pb2 as grants
import issuer_pb2 as identity
import launch_pb2 as launch
import world_session_pb2 as session
from harness.wire import frame, make_request, read_message, result_value


class SupervisedWorldParticipationTests(handoff.WorldHandoffRegressionTests):
    def setUp(self):
        def register_world(fixture):
            # Create the real stopped world before registering its persistent identity.
            executable = handoff.baseline.supervisor.owned.existing.PROBE.with_name('worldd')
            result = subprocess.run([str(executable), '--state-dir', str(self.root / 'world')],
                input=frame(make_request('world.info', {})), capture_output=True, timeout=8)
            self.assertEqual(result.returncode, 0, result.stderr)
            reply = read_message(io.BytesIO(result.stdout), core.Reply)
            self.assertFalse(reply.HasField('error'), reply)
            self.world_id = result_value(reply)['world_id']
            participant = identity.AuthorityParticipant(id=b'w' * 32, public_key=fixture.public,
                domains=[grants.Domain(kind=grants.WORLD, id=self.world_id)])
            operation = identity.IssuerOperation(participant_register=identity.AuthorityParticipantRegister(
                participant=participant, expected_registry_revision=0))
            registered = fixture.execute(fixture.peer, fixture.challenge(fixture.peer, operation))
            self.assertTrue(registered.HasField('participant'), registered)
            return registered.participant
        registry = handoff.baseline.supervisor.owned.registry.SupervisorRegistryTests
        with patch.object(registry, 'register', register_world):
            super().setUp()
        peer = self.issuer.peer
        enrolled = self.issuer.decide(peer, self.issuer.enroll(peer))
        self.assertTrue(enrolled.HasField('enrollment'), enrolled)
        self.enrollment = enrolled.enrollment
        self.actor_identity = self.issuer.claims(self.enrollment, self.issuer.info)
        self.actor_sequence = 0
        self.session_sequence = 0
        self.actor_client, self.actor_server = socket.socketpair()
        self.addCleanup(self.actor_client.close)
        self.addCleanup(self.actor_server.close)

    def write_config(self, name, message):
        path = self.root / (name + '.textproto')
        path.write_text(text_format.MessageToString(message)); path.chmod(0o600)
        return path

    def configuration(self, target=38):
        config = super().configuration(target)
        directory = self.root / 'world-authority'; directory.mkdir(mode=0o700)
        seed = session.WorldAuthoritySeed(protocol_version=1, authority_directory=str(directory),
            roots=[grants.GrantTerms(subject=grants.ActorRef(issuer_id=self.actor_identity.issuer_id,
                actor_id=self.actor_identity.actor_id), scope=grants.Scope(domain=grants.Domain(
                    kind=grants.WORLD, id=self.world_id)),
                rights=[grants.WORLD_ENTER, grants.WORLD_NAVIGATE, grants.WORLD_OBSERVE, grants.WORLD_MANIPULATE])])
        result = subprocess.run([config.launch.executable, '--state-dir', str(self.root / 'world'),
            '--init-authority', str(self.write_config('world-seed', seed))], capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        query = self.root / 'world-query.textproto'
        query.write_bytes(self.issuer.config_file().read_bytes()); query.chmod(0o600)
        settings = session.WorldIdentityConfig(protocol_version=1, issuer_client_config=str(query),
            admission=session.EXPLICIT_ACTORS, authority_directory=str(directory),
            participant_runtime_config=config.child_participant_config)
        runtime = core.RuntimeConfig()
        text_format.Parse((SOURCE / 'config/conformance.textproto').read_text(), runtime)
        runtime.input_timeout = 10
        config.launch.arguments.extend(['--identity-config', str(self.write_config('world-identity', settings)),
            '--config', str(self.write_config('world-runtime', runtime)), '--actor-fd', '40'])
        config.inherited_source_fds.append(self.actor_server.fileno())
        config.launch.inherited_fds.append(40)
        return config

    def launch_world(self, *args, **kwargs):
        original = subprocess.Popen
        executable = str(handoff.baseline.supervisor.owned.existing.PROBE.with_name('spatial-authority-supervisor'))
        def spawn(arguments, *other, **options):
            if str(arguments[0]) == executable:
                options['pass_fds'] = (*options.get('pass_fds', ()), self.actor_server.fileno())
            return original(arguments, *other, **options)
        with patch.object(subprocess, 'Popen', side_effect=spawn):
            result = super().launch_world(*args, **kwargs)
        self.actor_server.close()
        return result

    def actor_call(self, **operation):
        self.session_sequence += 1
        self.actor_client.sendall(frame(session.WorldSessionRequest(protocol_version=1,
            request_id=self.session_sequence, **operation)))
        reply = self.message(self.actor_client, session.WorldSessionReply)
        self.assertEqual(reply.request_id, self.session_sequence)
        return reply

    def actor_action(self, **operation):
        self.actor_sequence += 1
        return self.actor_call(action=core.ActorActionRequest(seq=self.actor_sequence, **operation))

    def authenticate_actor(self):
        reply = self.actor_call(begin=session.WorldAuthenticationBegin(credential=self.enrollment.credential))
        self.assertTrue(reply.HasField('challenge'), reply)
        proof = self.issuer.proof(reply.challenge, self.issuer.actor_key, self.issuer.actor_public)
        opened = self.actor_call(complete=identity.AuthenticationComplete(nonce=reply.challenge.nonce, proof=proof))
        self.assertTrue(opened.HasField('opened'), opened)
        self.assertEqual(opened.opened.identity.actor_id, self.actor_identity.actor_id)
        self.actor_id = opened.opened.actor_id
        return opened

    def activation_state(self):
        with sqlite3.connect('file:' + str(self.issuer.state / 'identity.db') + '?mode=ro', uri=True) as db:
            return db.execute('SELECT generation,applied_revision,active FROM authority_activation ORDER BY generation').fetchall()

    def revoke_actor(self):
        peer = self.issuer.connect()
        try:
            operation = identity.IssuerOperation(identity_revoke=identity.IdentityRevoke(
                target=identity.IdentityReference(kind=identity.ACTOR_TARGET, id=self.actor_identity.actor_id),
                expected_security_revision=0, reason='supervised world participation regression'))
            result = self.issuer.execute(peer, self.issuer.challenge(peer, operation))
            self.assertTrue(result.HasField('identity_status'), result)
        finally:
            peer.close()

    def issuer_barrier(self):
        peer = self.issuer.connect()
        try:
            operation = identity.IssuerOperation(barrier_read=identity.AuthorityBarrierRead(revision=1))
            result = self.issuer.execute(peer, self.issuer.challenge(peer, operation))
            self.assertTrue(result.HasField('authority_barrier'), result)
            return result.authority_barrier
        finally:
            peer.close()

    def graceful_close(self, revision):
        self.world.stdin.close()
        final = self.message(self.channel, launch.AuthorityLaunchReply)
        self.assertTrue(final.HasField('view'), final)
        self.assertEqual(final.view.record.stage, launch.OWNED_SCOPES_STOPPED)
        self.assertEqual(self.world.wait(timeout=5), 0)
        self.checked_cleanup()
        for pin in self.pins.values():
            self.assertEqual(select.select([pin], [], [], 0)[0], [pin])
        self.assertEqual(self.activation_state(), [(1, revision, 0)])

    def moving_actor(self):
        self.authenticate_actor()
        initial = self.world_call('world.get', id=self.actor_id)['pose']['p'][0]
        reply = self.actor_action(input=core.ActorInputRequest(axes=[1, 0, 0, 0, 0, 0]))
        self.assertTrue(reply.HasField('action'), reply)
        self.until(lambda: self.world_call('world.get', id=self.actor_id)['pose']['p'][0],
            lambda x: x > initial + .02)

    def test_owned_world_activates_authenticates_moves_and_releases_on_eof(self):
        self.started_world()
        self.assertEqual(self.world_call('world.info')['world_id'], self.world_id)
        self.assertEqual(self.activation_state(), [(1, 0, 1)])
        config = text_format.Parse(self.child_config.read_text(), identity.AuthorityParticipantRuntimeConfig())
        self.assertEqual(config.supervisor_channel_fd, 37)
        self.assertEqual(config.private_key_path, '')
        self.assertEqual(config.issuer_client_config, '')
        self.moving_actor()
        self.graceful_close(0)

    def test_anonymous_actor_cannot_use_supervised_world_authority(self):
        self.started_world()
        reply = self.actor_action(input=core.ActorInputRequest(axes=[1, 0, 0, 0, 0, 0]))
        self.assertTrue(reply.HasField('error'), reply)
        self.assertEqual(reply.error.code, core.UNAUTHORIZED)
        self.assertEqual(self.activation_state(), [(1, 0, 1)])
        self.assertEqual(len(self.world_call('world.list')['entities']), 1)
        self.graceful_close(0)

    def test_revocation_waits_for_world_cancellation_and_denies_further_actor_actions(self):
        self.started_world(); self.moving_actor()
        world_pid = self.ids['worldd_pid']
        os.kill(world_pid, signal.SIGSTOP)
        try:
            self.revoke_actor()
            time.sleep(.15)
            pending = self.issuer_barrier()
            self.assertEqual((pending.participant_count, pending.pending_count, pending.complete), (1, 1, False))
        finally:
            os.kill(world_pid, signal.SIGCONT)
        complete = self.until(self.issuer_barrier, lambda value: value.complete)
        self.assertEqual((complete.participant_count, complete.pending_count), (1, 0))
        stopped = self.world_call('world.get', id=self.actor_id)['pose']['p'][0]
        time.sleep(.08)
        still = self.world_call('world.get', id=self.actor_id)['pose']['p'][0]
        self.assertAlmostEqual(stopped, still, places=8)
        reply = self.actor_action(input=core.ActorInputRequest(axes=[1, 0, 0, 0, 0, 0]))
        self.assertTrue(reply.HasField('error'), reply)
        self.assertEqual(reply.error.code, core.UNAUTHORIZED)
        self.graceful_close(1)

    def test_world_crash_cleans_native_processes_but_retains_issuer_obligation(self):
        self.started_world(); self.moving_actor()
        independent = self.supervisor.issue(workspace=b'independent', application=b'survivor')
        witness = handoff.InputAwareNativeClient(self.display, independent.cookie)
        self.clients.append(witness)
        original = witness.inspect()
        os.kill(self.ids['worldd_pid'], signal.SIGKILL)
        reply = self.message(self.channel, launch.AuthorityLaunchReply)
        self.assertTrue(reply.HasField('error'), reply)
        self.assertEqual(self.world.wait(timeout=5), 2)
        self.checked_cleanup()
        for pin in self.pins.values():
            self.assertEqual(select.select([pin], [], [], 0)[0], [pin])
        self.assertEqual(witness.inspect(), original)
        self.assertEqual(self.activation_state(), [(1, 0, 1)])
        self.revoke_actor()
        pending = self.issuer_barrier()
        self.assertEqual((pending.participant_count, pending.pending_count, pending.complete), (1, 1, False))


if __name__ == '__main__':
    suite = unittest.TestSuite(SupervisedWorldParticipationTests(name)
        for name in SupervisedWorldParticipationTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
