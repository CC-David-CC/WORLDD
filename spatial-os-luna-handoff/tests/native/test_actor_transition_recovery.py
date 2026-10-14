"""Crash transition exchanges through production clients and retained native owners."""
import io
import fcntl
import os
from pathlib import Path
import select
import sqlite3
import subprocess
import time
import unittest
from unittest.mock import patch
import native_scope_pb2 as native
import security_pb2 as security
from google.protobuf import text_format
import test_session_replacement as existing
import test_session_world as worlds
import actor_transition_recovery_probe_pb2 as recovery
import actor_transition_pb2 as transition
import core_pb2 as core
import grants_pb2 as grants
import issuer_pb2 as identity
import launch_pb2 as launch
import session_probe_pb2 as probe
import world_session_pb2 as session
from harness.wire import frame, make_request, read_message


class ActorTransitionRecoveryTests(existing.SessionReplacementTests):
    coordinator_probe = 'spatial-actor-transition-recovery-probe'
    shared_presentation = True

    def setUp(self):
        super().setUp()
        register = self.issuer.register
        def fresh_registration(**arguments):
            # Provisioning can exceed the issuer's idle deadline between transactions.
            # Start a new TLS connection before obtaining a new signed challenge.
            self.issuer.peer.close()
            self.issuer.peer = self.issuer.connect()
            return register(**arguments)
        self.issuer.register = fresh_registration

    def world_config(self):
        config = super().world_config()
        config.launch.arguments.extend(['--fixture-faults', '--fixture-clock'])
        self.original_world_config.CopyFrom(config)
        for _ in range(2):
            value = native.NativeScopeConfig(protocol_version=1, display_number=self.display,
                supervisor_cookie=self.cookie, server_id=self.supervisor.inspect().server_id,
                server_pid=self.server.pid, server_uid=os.getuid(), launch_id=os.urandom(32), role=security.COMPOSITOR)
            fd = os.memfd_create('negative-restart-native', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
            self.addCleanup(os.close, fd)
            self.assertEqual(os.write(fd, value.SerializeToString()), value.ByteSize())
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS,
                fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
            self.replacement_fds.append(fd)
            self.coordinator_fds += (fd,)
        return config

    def exchange(self, **operation):
        self.coordinator_sequence += 1
        request = recovery.RecoveryProbeRequest(protocol_version=1,
            request_id=self.coordinator_sequence, **operation)
        self.coordinator.stdin.write(frame(request)); self.coordinator.stdin.flush()
        reply = self.message(self.coordinator.stdout, recovery.RecoveryProbeReply)
        self.assertEqual((reply.protocol_version, reply.request_id), (1, request.request_id))
        self.assertFalse(reply.HasField('error'), reply)
        return reply

    def request(self, expect_error=False, **operation):
        if 'start_world' in operation:
            start = operation['start_world']
            config = launch.AuthoritySupervisorConfig()
            text_format.Parse(Path(start.configuration_path).read_text(), config)
            args = list(config.launch.arguments)
            directory = Path(args[args.index('--state-dir') + 1])
            with sqlite3.connect('file:' + str(directory / 'world.db') + '?mode=ro', uri=True) as db:
                world_id = db.execute('SELECT world_id FROM metadata').fetchone()[0]
            output = self.exchange(start=recovery.RecoveryWorldStart(start=start, world_id=world_id))
        else:
            output = self.exchange(session=probe.SessionProbeRequest(protocol_version=1,
                request_id=self.coordinator_sequence + 1, **operation))
        self.assertTrue(output.HasField('session'), output)
        reply = output.session
        self.assertEqual(reply.request_id, self.coordinator_sequence)
        self.assertEqual(reply.protocol_version, 1)
        self.assertEqual(reply.HasField('error'), expect_error, reply)
        return reply

    def actor_op(self, owner=None, world=None, **operation):
        output = self.exchange(actor=recovery.RecoveryActor(owner=owner or self.source_owner,
            world_id=world or self.source_world, credential=self.actor.credential,
            key_path=str(self.issuer.actor_key), **operation))
        self.assertTrue(output.HasField('actor'), output)
        reply = output.actor
        self.assertFalse(reply.HasField('error'), reply)
        return reply

    def owned(self, owner, method, **arguments):
        reply = self.request(owned_world=probe.SessionWorldCall(owner=owner,
            request=make_request(method, arguments))).result
        self.assertFalse(reply.HasField('error'), reply)
        return reply

    def witness(self, directory):
        with sqlite3.connect('file:' + str(directory / 'world.db') + '?mode=ro', uri=True) as db:
            revision = db.execute('SELECT world_revision FROM metadata').fetchone()[0]
            rows = db.execute('SELECT id,data FROM entities ORDER BY id').fetchall()
        return revision, {key: core.Entity.FromString(value) for key, value in rows}

    def start_pair(self):
        self.coordinator, self.owner, self.world_record = self.ready()
        self.source_owner = self.owner
        self.source_world = self.world_domain
        self.source_config = launch.AuthoritySupervisorConfig()
        self.source_config.CopyFrom(self.original_world_config)
        # Consume the fixture's observation channel without authenticating it.
        self.connect_actor().close()
        self.owned(self.source_owner, 'world.create', entity=dict(id='saved-room', kind='room', label='Saved room'))
        spawn = core.Pose(position=core.Vec3(x=.25, z=-.5), orientation=core.Quaternion(w=1))
        self.actor_op(authenticate=session.WorldAuthenticationBegin(room='saved-room', pose=spawn))
        self.saved = self.actor_op(inspect=session.WorldSessionInspect()).state
        self.assertEqual((self.saved.room, self.saved.pose), ('saved-room', spawn))
        self.saved_source = self.witness(self.root / 'world')
        self.actor_op(action=core.ActorActionRequest(seq=1,
            input=core.ActorInputRequest(axes=[1, 0, 0, 0, 0, 0])))
        self.owned(self.source_owner, 'clock.advance', seconds=.15)
        self.actor_op(action=core.ActorActionRequest(seq=2, stop=core.ActorStopRequest()))
        self.live = self.actor_op(inspect=session.WorldSessionInspect()).state
        self.assertNotEqual(self.live.pose, self.saved.pose)
        self.assertEqual(self.live.room, self.saved.room)
        # Seed only durable world identity, using the existing real-world fixture pattern.
        directory = self.root / 'world-destination'
        seeded = subprocess.run([self.source_config.launch.executable, '--state-dir', str(directory)],
            input=frame(make_request('world.info', {})), capture_output=True, timeout=8)
        self.assertEqual(seeded.returncode, 0, seeded.stderr)
        info = read_message(io.BytesIO(seeded.stdout), core.Reply).world_info
        self.destination_world = info.world_id
        self.destination_room = info.entry_room
        self.world_instance_suffix = '-destination'
        self.world_domain = self.destination_world
        register = self.issuer.register
        def exact_domain(**arguments):
            self.assertEqual(arguments['domain'], self.destination_world + '-destination')
            arguments['domain'] = self.destination_world
            return register(**arguments)
        with patch.object(self.issuer, 'register', exact_domain):
            config = worlds.SessionWorldTests.world_config(self)
        self.assertEqual(config.participant.domains[0].id, self.destination_world)
        self.destination_config = config
        self.world_instance_suffix = ''
        self.world_domain = self.source_world
        self.world_journal = Path(self.source_config.journal_directory)
        policy = session.WorldIdentityConfig()
        args = list(self.source_config.launch.arguments)
        text_format.Parse(Path(args[args.index('--identity-config') + 1]).read_text(), policy)
        policy.participant_runtime_config = config.child_participant_config
        authority = self.root / 'destination-authority'; authority.mkdir(mode=0o700)
        seed = session.WorldAuthoritySeed(protocol_version=1, authority_directory=str(authority),
            roots=[grants.GrantTerms(subject=self.principal,
                scope=grants.Scope(domain=grants.Domain(kind=grants.WORLD, id=self.destination_world)),
                rights=[grants.WORLD_ENTER, grants.WORLD_NAVIGATE, grants.WORLD_OBSERVE, grants.WORLD_MANIPULATE])])
        initialized = subprocess.run([config.launch.executable, '--state-dir', str(directory),
            '--init-authority', str(self.config_file('destination-seed', seed))], capture_output=True, timeout=8)
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        policy.authority_directory = str(authority)
        config.launch.arguments.extend(['--identity-config', str(self.config_file('destination-identity', policy)), '--fixture-faults'])
        started = self.request(start_world=probe.SessionWorldStart(
            configuration_path=str(self.config_file('destination-world', config)), native_fd=self.replacement_fds[0])).started
        self.destination_owner = started.owner
        self.destination_record = started.record
        self.owner = self.destination_owner
        self.connect_actor().close()
        self.owner = self.source_owner
        self.before_destination = self.witness(directory)
        self.assertNotIn(self.saved.id, self.before_destination[1])
        # Issuer must refuse another generation while the source owner is live.
        before = self.activation()
        self.request(expect_error=True, start_world=self.next_generation(self.source_config, self.source_owner, 'negative-live', 2))
        self.assertEqual(self.activation(), before)
        self.assertEqual(self.witness(self.root / 'world'), self.saved_source)
        self.assertEqual(self.witness(directory), self.before_destination)

    def next_generation(self, original, owner, name, descriptor_index):
        config = launch.AuthoritySupervisorConfig()
        config.CopyFrom(original)
        config.binding.service_incarnation = os.urandom(32)
        config.authorization.previous_generation = owner.launch.generation
        config.journal_directory = str(self.root / (name + '-journal'))
        Path(config.journal_directory).mkdir(mode=0o700)
        config.child_participant_config = str(self.root / (name + '-child.textproto'))
        runtime = identity.AuthorityParticipantRuntimeConfig()
        text_format.Parse(Path(config.participant.runtime_config).read_text(), runtime)
        runtime.service_incarnation = config.binding.service_incarnation
        config.participant.runtime_config = str(self.config_file(name + '-runtime', runtime))
        args = list(config.launch.arguments)
        if '--actor-tls-config' in args:
            index = args.index('--actor-tls-config'); del args[index:index + 2]
        index = args.index('--identity-config')
        policy = session.WorldIdentityConfig()
        text_format.Parse(Path(args[index + 1]).read_text(), policy)
        policy.participant_runtime_config = config.child_participant_config
        args[index + 1] = str(self.config_file(name + '-identity', policy))
        del config.launch.arguments[:]; config.launch.arguments.extend(args)
        return probe.SessionWorldStart(configuration_path=str(self.config_file(name + '-world', config)),
            native_fd=self.replacement_fds[descriptor_index])

    def restart(self, owner, config):
        cleanup = self.request(stop_world=owner).cleanup
        self.assertEqual(cleanup.stage, launch.OWNED_SCOPES_STOPPED)
        self.assertEqual(cleanup.native, owner.native)
        self.assertEqual(cleanup.process_stopped.identity, cleanup.process)
        self.assertEqual(cleanup.native_stopped.identity, owner.native)
        with sqlite3.connect('file:' + str(Path(config.journal_directory) / 'launch.db') + '?mode=ro', uri=True) as db:
            journal = launch.AuthorityLaunchRecord.FromString(db.execute('SELECT record FROM launch WHERE singleton=1').fetchone()[0])
        self.assertEqual(cleanup, journal)
        changed = type(owner)(); changed.CopyFrom(owner); changed.launch.generation += 1
        self.request(expect_error=True, recover_world=changed)
        recovered = self.request(recover_world=owner).owner
        self.assertEqual(recovered.recovery.cleanup.owner, owner)
        self.assertEqual(recovered.recovery.cleanup.process_stopped, cleanup.process_stopped)
        self.assertEqual(recovered.recovery.cleanup.native_stopped, cleanup.native_stopped)
        self.assertTrue(recovered.channel_fenced)
        self.assertEqual(list(recovered.reservations), [])
        self.assertEqual(self.request(recover_world=owner).owner, recovered)
        self.owner = owner
        active = self.activation()
        self.request(expect_error=True, start_world=self.next_generation(config, owner, 'negative-unretired', 3))
        self.assertEqual(self.activation(), active)
        retired = self.request(retire_world=owner).retirement
        self.assertEqual(retired.request.owner, owner.launch)
        self.assertEqual(self.activation()[-1][:2], (owner.launch.generation, 0))
        started = self.request(start_world=self.next_generation(config, owner, 'fresh', 1)).started
        fresh = started.owner
        self.assertEqual(fresh.launch.participant_id, owner.launch.participant_id)
        self.assertEqual(fresh.launch.generation, owner.launch.generation + 1)
        for field in ('launch_id', 'authorization_id', 'service_incarnation'):
            self.assertNotEqual(getattr(fresh.launch, field), getattr(owner.launch, field))
        self.assertNotEqual(fresh.native.credential_id, owner.native.credential_id)
        self.owner = fresh
        self.until(self.activation, lambda rows: rows[-1][:2] == (owner.launch.generation + 1, 1))
        channel = self.connect_actor()
        denied = self.actor_call(channel, inspect=session.WorldSessionInspect())
        self.assertEqual(denied.error.code, core.UNAUTHORIZED)
        channel.close()
        self.request(expect_error=True, owned_world=probe.SessionWorldCall(owner=owner,
            request=make_request('world.info', {})))
        return fresh

    def crash_transition(self, point, activation=False):
        self.start_pair()
        destination_committed = activation and point == core.FAULT_WORLD_AFTER_COMMIT_BEFORE_REPLY
        destination_spawn = core.Pose(position=core.Vec3(x=55), orientation=core.Quaternion(w=1))
        spawn_arguments = dict(destination_room=self.destination_room, destination_pose=destination_spawn) if destination_committed else {}
        crashed = self.destination_owner if activation else self.source_owner
        config = self.destination_config if activation else self.source_config
        # Existing checked recovery rejects live owners before cleanup.
        denied = self.request(expect_error=True, recover_world=crashed)
        self.assertIn('requires checked world cleanup', denied.error.message)
        hello = self.owned(crashed, 'system.hello').hello
        self.assertEqual(hello.implementation, 'production')
        self.assertIn('fixture.faults/v1', hello.capabilities)
        identities = self.owned(crashed, 'runtime.identities').identities
        pin = os.pidfd_open(identities.worldd_pid)
        self.addCleanup(os.close, pin)
        arm = core.Request(protocol_version=1, request_id=91,
            fault_arm=core.FaultArmRequest(point=point, action=core.FAULT_EXIT))
        reply = self.request(owned_world=probe.SessionWorldCall(owner=crashed, request=arm)).result
        self.assertFalse(reply.HasField('error'), reply)
        self.assertTrue(reply.fault_armed.armed)
        transition_id = os.urandom(32)
        result = self.exchange(transition=recovery.RecoveryTransition(source=self.source_owner,
            destination=self.destination_owner, credential=self.actor.credential,
            key_path=str(self.issuer.actor_key), source_world=self.source_world,
            destination_world=self.destination_world, transition_id=transition_id, sequence=3, **spawn_arguments)).transition
        self.assertEqual(result.transition_id, transition_id)
        self.assertEqual((result.actor_id, result.source_world_id, result.destination_world_id),
            (self.saved.id, self.source_world, self.destination_world))
        self.assertTrue(result.host_presentation_selection_pending)
        self.assertTrue(result.source_inspection_confirmed)
        self.assertTrue(result.destination_preparation_confirmed)
        self.assertTrue(result.effect_uncertain)
        self.assertFalse(result.destination_activation_confirmed)
        self.assertEqual(result.source_departure_confirmed, activation)
        self.assertEqual(result.HasField('departure_world_revision'), activation)
        self.assertEqual(result.phase, transition.ACTIVATION_UNCERTAIN if activation else transition.DEPARTURE_UNCERTAIN)
        self.assertFalse(result.HasField('server_error'))
        self.assertEqual(select.select([pin], [], [], 6)[0], [pin])
        committed = activation or point == core.FAULT_WORLD_AFTER_COMMIT_BEFORE_REPLY
        durable_source = self.witness(self.root / 'world')
        expected = self.live if committed else self.saved
        entity = durable_source[1][self.saved.id]
        self.assertEqual((entity.room, entity.pose), (expected.room, expected.pose))
        durable_destination = self.witness(self.root / 'world-destination')
        if destination_committed:
            self.assertEqual(durable_destination[0], self.before_destination[0] + 1)
            self.assertEqual(set(durable_destination[1]), set(self.before_destination[1]) | {self.saved.id})
            inserted = durable_destination[1][self.saved.id]
            self.assertEqual((inserted.room, inserted.pose), (self.destination_room, destination_spawn))
            self.assertEqual({key: value for key, value in durable_destination[1].items() if key != self.saved.id},
                self.before_destination[1])
        else:
            self.assertEqual(durable_destination, self.before_destination)
        if committed: self.assertGreater(durable_source[0], self.saved_source[0])
        else: self.assertEqual(durable_source, self.saved_source)
        if activation: self.assertEqual(result.departure_world_revision, durable_source[0])
        # Reusing a consumed or failed client must stop before another departure or activation.
        repeated = self.exchange(transition=recovery.RecoveryTransition(source=self.source_owner,
            destination=self.destination_owner, credential=self.actor.credential,
            key_path=str(self.issuer.actor_key), source_world=self.source_world,
            destination_world=self.destination_world, transition_id=transition_id, sequence=3, **spawn_arguments)).transition
        self.assertEqual(repeated.phase, transition.SOURCE_INSPECTION_UNCERTAIN)
        self.assertFalse(repeated.source_departure_confirmed)
        self.assertFalse(repeated.destination_preparation_confirmed)
        self.assertFalse(repeated.HasField('departure_world_revision'))
        self.assertEqual(self.witness(self.root / 'world'), durable_source)
        self.assertEqual(self.witness(self.root / 'world-destination'), durable_destination)
        fresh = self.restart(crashed, config)
        directory = self.root / ('world-destination' if activation else 'world')
        before_auth = self.witness(directory)
        self.assertEqual(before_auth, durable_destination if activation else durable_source)
        world_id = self.destination_world if activation else self.source_world
        opened = self.actor_op(owner=fresh, world=world_id,
            authenticate=session.WorldAuthenticationBegin()).opened
        self.assertEqual(opened.world_id, world_id)
        state = self.actor_op(owner=fresh, world=world_id, inspect=session.WorldSessionInspect()).state
        self.assertEqual(state.id, self.saved.id)
        self.assertFalse(state.workspace)
        if not activation:
            self.assertEqual((state.room, state.pose), (expected.room, expected.pose))
            self.assertEqual(self.witness(directory), before_auth)
        elif destination_committed:
            self.assertEqual((state.room, state.pose), (self.destination_room, destination_spawn))
        else:
            self.assertEqual(state.room, self.owned(fresh, 'world.info').world_info.entry_room)
            self.assertEqual(state.pose, core.Pose(position=core.Vec3(), orientation=core.Quaternion(w=1)))
        self.assertFalse(any(state.axes)); self.assertEqual(list(state.held_keys), [])
        self.assertEqual(list(state.held_buttons), []); self.assertFalse(state.HasField('focus'))
        after_auth = self.witness(directory)
        self.assertEqual(self.witness(self.root / 'world'), durable_source)
        if destination_committed:
            self.assertEqual(after_auth, durable_destination)
        elif activation:
            self.assertEqual(after_auth[0], self.before_destination[0] + 1)
            self.assertEqual(set(after_auth[1]), set(self.before_destination[1]) | {self.saved.id})
            self.assertEqual({key: value for key, value in after_auth[1].items() if key != self.saved.id},
                self.before_destination[1])
        else:
            self.assertEqual(self.witness(self.root / 'world-destination'), self.before_destination)
        time.sleep(.3)
        self.actor_op(owner=fresh, world=world_id, inspect=session.WorldSessionInspect())
        self.assertEqual(self.witness(directory), after_auth)
        survivor = self.source_owner if activation else self.destination_owner
        # Healthy stopped owners have no failure fence and require no issuer retirement.
        for healthy in (fresh, survivor):
            cleanup = self.request(stop_world=healthy).cleanup
            self.assertEqual(cleanup.stage, launch.OWNED_SCOPES_STOPPED)
            self.assertEqual(cleanup.native, healthy.native)
            self.assertEqual(cleanup.process.launch_id, healthy.launch.launch_id)
            self.assertEqual(cleanup.process_stopped.identity, cleanup.process)
            self.assertEqual(cleanup.native_stopped.identity, healthy.native)
            recovered = self.request(recover_world=healthy).owner
            self.assertEqual(recovered.recovery.cleanup.owner, healthy)
            self.assertEqual(recovered.recovery.cleanup.process_stopped, cleanup.process_stopped)
            self.assertEqual(recovered.recovery.cleanup.native_stopped, cleanup.native_stopped)
            self.assertTrue(recovered.channel_fenced)
            self.assertEqual(list(recovered.reservations), [])
            self.assertEqual(self.request(recover_world=healthy).owner, recovered)
        self.finish_session()

    def test_departure_before_durable_commit(self):
        self.crash_transition(core.FAULT_WORLD_BEFORE_COMMIT)

    def test_departure_after_durable_commit(self):
        self.crash_transition(core.FAULT_WORLD_AFTER_COMMIT_BEFORE_REPLY)

    def test_destination_activation_crash(self):
        self.crash_transition(core.FAULT_WORLD_BEFORE_COMMIT, activation=True)

    def test_destination_activation_after_durable_commit(self):
        self.crash_transition(core.FAULT_WORLD_AFTER_COMMIT_BEFORE_REPLY, activation=True)


if __name__ == '__main__':
    names = ['ActorTransitionRecoveryTests.' + name for name in ActorTransitionRecoveryTests.__dict__ if name.startswith('test_')]
    unittest.main(defaultTest=names, verbosity=2)
