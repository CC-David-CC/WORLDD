"""Fresh supervised sessions recover durable data without replaying interrupted work."""
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import unittest
from google.protobuf import text_format
import test_stopped_broker_dependency as existing
import broker_pb2 as broker
import core_pb2 as core
import issuer_pb2 as identity
import launch_pb2 as launch
import native_scope_pb2 as native
import security_pb2 as security
import world_session_pb2 as session
from harness.oracles import pose


class BrokerReplacementTests(existing.StoppedBrokerDependencyTests):
    def act(self, **action):
        if 'process_launch' in action and not hasattr(self, 'original_command'):
            self.original_command = core.ProcessLaunchRequest()
            self.original_command.CopyFrom(action['process_launch'])
        return super().act(**action)

    def process_rows(self):
        with sqlite3.connect('file:' + str(self.execution / 'execution.db') + '?mode=ro', uri=True) as db:
            return db.execute('SELECT id,actor,operation,payload,workspace,rights,state,exit_code FROM processes ORDER BY id').fetchall()

    def activation_rows(self):
        with sqlite3.connect('file:' + str(self.issuer.state / 'identity.db') + '?mode=ro', uri=True) as db:
            return db.execute('SELECT participant_id,generation,active,applied_revision FROM authority_activation '
                'ORDER BY participant_id,generation').fetchall()

    def prepare_recovery(self, retire_broker=True):
        self.pin_broker(stop_world=False)
        self.management('world.create', entity=dict(id='broker-recovery-object', kind='object',
            room=self.management('world.info')['entry_room'], pose=pose((1, 0, -2))))
        self.management('world.checkpoint')
        self.saved_world = self.management('world.info')
        self.saved_object = self.management('world.get', id='broker-recovery-object')
        self.saved_actor = self.actor_call(self.stream, inspect=session.WorldSessionInspect()).state
        self.project_marker = self.project / 'broker-recovery-uncommitted.txt'
        self.project_marker.write_text('original workspace data\n')
        self.old_app = self.app; self.old_app_data = self.app_data
        self.old_owner = self.owner; self.old_world_record = self.world_record
        self.original_process_rows = self.process_rows()
        self.assertEqual(len(self.original_process_rows), 1)
        self.assertEqual(self.original_process_rows[0][6], 'running')
        self.world_cleanup = self.crash_world(); self.world_stopped = True
        signal.pidfd_send_signal(self.pins[self.broker_pid], signal.SIGKILL); self.exited()
        self.checked()
        encoded = self.saved(self.dependency())
        self.world_retirement = self.retire_world(); self.world_retired(self.world_retirement, encoded)
        if retire_broker:
            self.broker_retirement = self.retired(); self.completed(self.broker_retirement)
        self.old_application_events = (self.old_app_data / 'probe.jsonl').read_bytes()
        target = os.environ.get('SPATIAL_SECURITY_EVIDENCE')
        if target:
            Path(target).mkdir(parents=True, exist_ok=True)
            for name in ('probe.jsonl', 'confinement.json'):
                (Path(target) / (self._testMethodName + '-original-' + name + '.log')).write_bytes(
                    (self.old_app_data / name).read_bytes())
        self.assertEqual(self.process_rows(), self.original_process_rows)
        self.assertIsNone(self.server.poll())

    def finish_original(self):
        self.finish_session()
        self.assertEqual(self.coordinator.returncode, 0)
        self.original_journals = {path: path.read_bytes() for path in
            (self.journal / 'launch.db', self.world_journal / 'launch.db')}
        self.assertEqual([(generation, active) for _, generation, active, _ in self.activation_rows()], [(1, 0), (1, 0)])

    def replacement_config(self, kind, attempt):
        config = launch.AuthoritySupervisorConfig()
        text_format.Parse((self.root / ('session-' + kind + '.textproto')).read_text(), config)
        config.binding.service_incarnation = os.urandom(32)
        config.authorization.previous_generation = 1
        journal = self.root / ('replacement-' + kind + '-journal-' + str(attempt)); journal.mkdir(mode=0o700)
        config.journal_directory = str(journal)
        config.child_participant_config = str(self.root / ('replacement-' + kind + '-child-' + str(attempt) + '.textproto'))
        runtime = identity.AuthorityParticipantRuntimeConfig()
        text_format.Parse(Path(config.participant.runtime_config).read_text(), runtime)
        runtime.service_incarnation = config.binding.service_incarnation
        config.participant.runtime_config = str(self.config_file('replacement-' + kind + '-runtime-' + str(attempt), runtime))
        arguments = list(config.launch.arguments)
        flag = '--authority-config' if kind == 'broker' else '--identity-config'
        index = arguments.index(flag)
        policy = broker.WorkspaceAuthorityConfig() if kind == 'broker' else session.WorldIdentityConfig()
        text_format.Parse(Path(arguments[index + 1]).read_text(), policy)
        policy.participant_runtime_config = config.child_participant_config
        arguments[index + 1] = str(self.config_file('replacement-' + kind + '-policy-' + str(attempt), policy))
        del config.launch.arguments[:]; config.launch.arguments.extend(arguments)
        return self.config_file('replacement-' + kind + '-' + str(attempt), config), config

    def start_fresh(self, attempt=0, expect_failure=False):
        arguments = []; descriptors = []; configs = {}
        for kind, role in [('broker', security.BROKER), ('world', security.COMPOSITOR)]:
            path, config = self.replacement_config(kind, attempt); configs[kind] = config
            value = native.NativeScopeConfig(protocol_version=1, display_number=self.display, supervisor_cookie=self.cookie,
                server_id=self.supervisor.inspect().server_id, server_pid=self.server.pid, server_uid=os.getuid(),
                launch_id=os.urandom(32), role=role)
            descriptor = os.memfd_create('replacement-session-native', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
            descriptors.append(descriptor)
            self.assertEqual(os.write(descriptor, value.SerializeToString()), value.ByteSize())
            fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS,
                fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
            arguments += [str(path), str(descriptor)]
        listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen(8)
        receiver, delivery = socket.socketpair(type=socket.SOCK_SEQPACKET)
        self.addCleanup(receiver.close)
        arguments += [str(listener.fileno()), str(delivery.fileno())]
        binary = Path(configs['world'].launch.executable).with_name('spatial-session-application-probe')
        logpath = self.root / ('replacement-session-' + str(attempt) + '.log'); log = logpath.open('wb')
        try:
            proc = subprocess.Popen([str(binary), *arguments], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=log, pass_fds=tuple(descriptors) + (listener.fileno(), delivery.fileno()))
        finally:
            for descriptor in descriptors: os.close(descriptor)
            listener.close(); delivery.close()
        def cleanup():
            if proc.poll() is None:
                proc.stdin.close()
                try: proc.wait(timeout=20)
                except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=3)
            if not proc.stdin.closed: proc.stdin.close()
            proc.stdout.close(); log.close()
            data = logpath.read_bytes()
            target = os.environ.get('SPATIAL_SECURITY_EVIDENCE')
            if target:
                Path(target).mkdir(parents=True, exist_ok=True)
                (Path(target) / (self._testMethodName + '-replacement-' + str(attempt) + '.log')).write_bytes(data)
            for marker in (b'AddressSanitizer', b'LeakSanitizer', b'runtime error:'): self.assertNotIn(marker, data)
        self.addCleanup(cleanup)
        if expect_failure:
            self.assertEqual(proc.wait(timeout=10), 2, logpath.read_text())
            self.assertEqual(proc.stdout.read(), b'')
            path = Path(configs['broker'].journal_directory) / 'launch.db'
            with sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True) as db:
                record = launch.AuthorityLaunchRecord.FromString(db.execute('SELECT record FROM launch').fetchone()[0])
            self.assertEqual(record.stage, launch.OWNED_SCOPES_STOPPED)
            self.assertFalse(record.HasField('authorization'))
            self.assertFalse((self.cgroup / record.process.scope_name).exists())
            receiver.close()
            return
        new_broker = self.message(proc.stdout, launch.AuthorityLaunchReply).view.record
        new_world = self.message(proc.stdout, launch.AuthorityLaunchReply).view.record
        new_owner = self.message(proc.stdout, launch.AuthorityLaunchReply).control_owner
        identities = self.message(proc.stdout, core.Reply).identities
        for old, new in [(self.original, new_broker), (self.old_world_record, new_world)]:
            self.assertEqual(new.binding.participant_id, old.binding.participant_id)
            self.assertEqual(new.binding.issuer_id, old.binding.issuer_id)
            self.assertEqual(new.authorization.request.previous_generation, 1)
            self.assertNotEqual(new.binding.service_incarnation, old.binding.service_incarnation)
            self.assertNotEqual(new.authorization.authorization_id, old.authorization.authorization_id)
            self.assertNotEqual(new.process.launch_id, old.process.launch_id)
            self.assertNotEqual(new.native.credential_id, old.native.credential_id)
            self.assertEqual(new.native.server_id, old.native.server_id)
        self.assertEqual(new_owner.native, new_world.native); self.assertEqual(new_owner.launch.generation, 2)
        self.coordinator = proc; self.owner = new_owner; self.world_record = new_world
        self.world_ids = identities; self.new_broker_record = new_broker
        self.new_broker_journal = Path(configs['broker'].journal_directory)
        self.new_world_journal = Path(configs['world'].journal_directory)
        self.actor_receiver = receiver; self.actor_delivery = delivery
        self.coordinator_sequence = self.action_sequence = self.probe_sequence = 0
        self.stream = self.connect_actor()
        self.until(self.activation_rows, lambda rows: [(generation, active) for _, generation, active, _ in rows]
            == [(1, 0), (2, 1), (1, 0), (2, 1)])
        self.assert_private_channels_stay_in_coordinator(proc)
        self.assertEqual(self.management('world.info'), self.saved_world)
        self.assertEqual(self.management('world.get', id='broker-recovery-object'), self.saved_object)
        self.assertEqual(self.project_marker.read_text(), 'original workspace data\n')
        self.assertEqual(self.process_rows(), [row[:6] + ('uncertain', row[7]) for row in self.original_process_rows])
        self.assertEqual((self.old_app_data / 'probe.jsonl').read_bytes(), self.old_application_events)
        for path, data in self.original_journals.items(): self.assertEqual(path.read_bytes(), data)

    def authenticate_fresh(self):
        challenge = self.actor_call(self.stream, begin=session.WorldAuthenticationBegin(credential=self.actor.credential)).challenge
        proof = self.issuer.proof(challenge, self.issuer.actor_key, self.issuer.actor_public)
        reply = self.actor_call(self.stream, complete=identity.AuthenticationComplete(nonce=challenge.nonce, proof=proof))
        self.assertTrue(reply.HasField('opened'), reply)
        state = self.actor_call(self.stream, inspect=session.WorldSessionInspect()).state
        self.assertEqual((state.id, state.room, state.pose, state.workspace),
            (self.saved_actor.id, self.saved_actor.room, self.saved_actor.pose, self.saved_actor.workspace))
        self.assertFalse(any(state.axes)); self.assertEqual(list(state.held_keys), [])
        self.assertEqual(list(state.held_buttons), []); self.assertFalse(state.HasField('focus'))
        self.assertEqual(list(self.request(inspect=self.owner).owner.reservations), [])

    def finish_fresh(self):
        world = self.request(stop_world=self.owner).cleanup
        self.assertEqual(world.stage, launch.OWNED_SCOPES_STOPPED)
        self.assertEqual(world.process, self.world_record.process)
        self.assertEqual(world.native, self.world_record.native)
        self.request(recover_world=self.owner)
        stopped = self.request(stop_session=launch.AuthorityLaunchQuery()).cleanup
        self.assertEqual(stopped.stage, launch.OWNED_SCOPES_STOPPED)
        self.assertEqual(stopped.process_stopped.identity, self.new_broker_record.process)
        self.assertEqual(stopped.native_stopped.identity, self.new_broker_record.native)
        self.assertFalse((self.cgroup / stopped.process.scope_name).exists())
        self.assertEqual(self.coordinator.wait(timeout=10), 0)

    def test_fresh_session_preserves_data_and_requires_explicit_application_launch(self):
        self.prepare_recovery(); self.finish_original(); self.start_fresh(); self.authenticate_fresh()
        before = self.process_rows()
        self.action_sequence += 1
        replay = self.actor_call(self.stream,
            action=core.ActorActionRequest(seq=self.action_sequence, process_launch=self.original_command))
        self.assertEqual(replay.error.code, core.UNAVAILABLE, replay)
        self.assertEqual(self.process_rows(), before)
        command = core.ProcessLaunchRequest(); command.CopyFrom(self.original_command)
        command.operation_id = 'explicit-replacement-application'
        result = self.act(process_launch=command).process
        self.assertNotEqual(result.process_id.encode(), self.old_app.application)
        self.app = broker.ApplicationRef(workspace=result.workspace.encode(), application=result.process_id.encode(),
            server_id=self.owner.native.server_id)
        self.app_data = self.execution / 'applications' / result.process_id / 'data'
        surface = self.until(lambda: self.management('surface.find', probe_id='coordinator-application'),
            lambda value: value['surface'] is not None)['surface']
        confinement = json.loads((self.app_data / 'confinement.json').read_text())
        self.assertEqual(confinement['cwd'], '/project'); self.assertEqual(confinement['fds'], [])
        self.assertTrue(confinement['no_new_privs']); self.assertFalse(confinement['host_file_visible'])
        self.assertNotEqual(confinement['mnt'], os.readlink('/proc/self/ns/mnt'))
        self.assertFalse(any(row.get('pressed') for row in self.events() if row.get('event') in ('key', 'button')))
        self.management('surface.place', id=surface['id'], room=self.saved_world['entry_room'], pose=pose((0, 0, -2)), size=[1, 1])
        self.act(aim=core.ActorAimRequest(direction=core.Vec3(z=-1)))
        self.act(pointer=core.ActorPointerRequest(button=1, pressed=True))
        self.act(key=core.ActorKeyRequest(keysym='Shift_L', pressed=True))
        self.until(self.events, lambda rows: all(any(row.get('event') == name and row.get('pressed') for row in rows)
            for name in ('key', 'button')))
        controlled = self.request(inspect=self.owner).owner
        self.assertEqual(len(controlled.reservations), 1)
        self.assertEqual(controlled.reservations[0].application, self.app)
        self.assertNotEqual(controlled.reservations[0], self.lease)
        self.act(stop=core.ActorStopRequest()); self.released()
        self.assertEqual([row for row in self.process_rows() if row[0] == self.old_app.application.decode()], before)
        self.assertEqual((self.old_app_data / 'probe.jsonl').read_bytes(), self.old_application_events)
        self.finish_fresh()

    def test_fresh_session_synchronizes_revocation_before_actor_admission(self):
        self.prepare_recovery(); self.finish_original()
        peer = self.issuer.connect()
        operation = identity.IssuerOperation(identity_revoke=identity.IdentityRevoke(
            target=identity.IdentityReference(kind=identity.CREDENTIAL_TARGET, id=self.claims.credential_id),
            expected_security_revision=0, reason='broker replacement regression'))
        try: revoked = self.issuer.execute(peer, self.issuer.challenge(peer, operation))
        finally: peer.close()
        self.assertTrue(revoked.HasField('identity_status'), revoked)
        self.start_fresh()
        for _, generation, active, revision in self.activation_rows():
            if generation == 2:
                self.assertEqual(active, 1); self.assertGreaterEqual(revision, revoked.identity_status.security_revision)
        denied = self.actor_call(self.stream, begin=session.WorldAuthenticationBegin(credential=self.actor.credential))
        self.assertEqual(denied.error.code, core.UNAUTHORIZED, denied)
        self.finish_fresh()

    def test_pending_broker_retirement_prevents_new_session_execution(self):
        self.prepare_recovery(retire_broker=False)
        before = self.activation_rows()
        self.start_fresh(expect_failure=True)
        self.assertEqual(self.activation_rows(), before)
        self.assertEqual(self.process_rows(), self.original_process_rows)
        self.broker_retirement = self.retired(); self.completed(self.broker_retirement)
        self.finish_original(); self.start_fresh(attempt=1); self.authenticate_fresh(); self.finish_fresh()


if __name__ == '__main__':
    names = ['BrokerReplacementTests.' + name for name in BrokerReplacementTests.__dict__ if name.startswith('test_')]
    unittest.main(defaultTest=names, verbosity=2)
