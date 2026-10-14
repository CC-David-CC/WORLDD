"""Exercise actual replacement generations through the retained production session."""
import array
import fcntl
import os
from pathlib import Path
import select
import socket
import sqlite3
import unittest
from google.protobuf import text_format
import test_session_application as existing
import core_pb2 as core
import issuer_pb2 as identity
import launch_pb2 as launch
import native_scope_pb2 as native
import security_pb2 as security
import session_probe_pb2 as probe
import world_session_pb2 as session
from harness.oracles import pose
from harness.wire import make_request


class SessionReplacementTests(existing.SessionApplicationTests):
    def world_config(self):
        config = super().world_config()
        self.original_world_config = launch.AuthoritySupervisorConfig()
        self.original_world_config.CopyFrom(config)
        self.replacement_fds = []
        for _ in range(2):
            value = native.NativeScopeConfig(protocol_version=1, display_number=self.display,
                supervisor_cookie=self.cookie, server_id=self.supervisor.inspect().server_id,
                server_pid=self.server.pid, server_uid=os.getuid(), launch_id=os.urandom(32), role=security.COMPOSITOR)
            fd = os.memfd_create('replacement-native-fixture', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
            self.addCleanup(os.close, fd)
            self.assertEqual(os.write(fd, value.SerializeToString()), value.ByteSize())
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS,
                fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
            self.replacement_fds.append(fd)
        self.coordinator_fds += tuple(self.replacement_fds)
        return config

    def connect_actor(self):
        self.actor_delivery.close()
        self.actor_receiver.settimeout(3)
        packet, control, flags, _ = self.actor_receiver.recvmsg(65536, socket.CMSG_SPACE(4), socket.MSG_CMSG_CLOEXEC)
        self.assertFalse(flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC))
        binding = launch.AuthorityLaunchReply.FromString(packet)
        self.assertEqual(binding.protocol_version, 1)
        self.assertEqual(binding.control_owner, self.owner)
        self.assertEqual(len(control), 1)
        level, kind, data = control[0]
        self.assertEqual((level, kind), (socket.SOL_SOCKET, socket.SCM_RIGHTS))
        descriptors = array.array('i'); descriptors.frombytes(data)
        self.assertEqual(len(descriptors), 1)
        stream = socket.socket(fileno=descriptors[0]); stream.settimeout(3)
        self.addCleanup(stream.close)
        return stream

    def replacement(self, index=0):
        config = launch.AuthoritySupervisorConfig()
        config.CopyFrom(self.original_world_config)
        config.binding.service_incarnation = os.urandom(32)
        config.authorization.previous_generation = self.owner.launch.generation
        journal = self.root / ('replacement-journal-' + str(index)); journal.mkdir(mode=0o700)
        config.journal_directory = str(journal)
        config.child_participant_config = str(self.root / ('replacement-child-' + str(index) + '.textproto'))
        runtime = identity.AuthorityParticipantRuntimeConfig()
        text_format.Parse(Path(config.participant.runtime_config).read_text(), runtime)
        runtime.service_incarnation = config.binding.service_incarnation
        config.participant.runtime_config = str(self.config_file('replacement-runtime-' + str(index), runtime))
        arguments = list(config.launch.arguments)
        position = arguments.index('--actor-tls-config'); del arguments[position:position + 2]
        position = arguments.index('--identity-config')
        policy = session.WorldIdentityConfig()
        text_format.Parse(Path(arguments[position + 1]).read_text(), policy)
        policy.participant_runtime_config = config.child_participant_config
        arguments[position + 1] = str(self.config_file('replacement-identity-' + str(index), policy))
        del config.launch.arguments[:]; config.launch.arguments.extend(arguments)
        path = self.config_file('replacement-world-' + str(index), config)
        return probe.SessionWorldStart(configuration_path=str(path), native_fd=self.replacement_fds[index])

    def activation(self):
        with sqlite3.connect('file:' + str(self.issuer.state / 'identity.db') + '?mode=ro', uri=True) as db:
            return db.execute('SELECT generation,active,applied_revision FROM authority_activation '
                'WHERE participant_id=? ORDER BY generation', (self.owner.launch.participant_id,)).fetchall()

    def retire_old_world(self):
        self.old_owner = self.owner
        self.old_record = self.world_record
        self.old_stream = self.stream
        self.cleanup = self.crash_world()
        recovered = self.request(recover_world=self.owner).owner
        self.assertEqual(list(recovered.reservations), [])
        self.retirement = self.request(retire_world=self.owner).retirement
        self.assertEqual(self.retirement.request.owner, self.owner.launch)
        self.assertEqual(self.activation(), [(1, 0, self.retirement.applied_revision)])

    def start_replacement(self, request):
        reply = self.request(start_world=request)
        self.assertTrue(reply.HasField('started'), reply)
        self.owner = reply.started.owner; self.world_record = reply.started.record
        self.assertEqual(self.owner.launch.participant_id, self.old_owner.launch.participant_id)
        self.assertEqual(self.owner.launch.generation, self.old_owner.launch.generation + 1)
        for field in ('authorization_id', 'launch_id', 'service_incarnation'):
            self.assertNotEqual(getattr(self.owner.launch, field), getattr(self.old_owner.launch, field))
        self.assertEqual(self.owner.native, self.world_record.native)
        self.assertNotEqual(self.owner.native.credential_id, self.old_owner.native.credential_id)
        self.stream = self.connect_actor(); self.action_sequence = 0
        # Launch registration precedes asynchronous authority synchronization and activation.
        self.until(self.activation, lambda rows: rows[-1][:2] == (2, 1))
        return reply.started

    def authenticate(self):
        challenge = self.actor_call(self.stream, begin=session.WorldAuthenticationBegin(credential=self.actor.credential)).challenge
        proof = self.issuer.proof(challenge, self.issuer.actor_key, self.issuer.actor_public)
        reply = self.actor_call(self.stream, complete=identity.AuthenticationComplete(nonce=challenge.nonce, proof=proof))
        self.assertTrue(reply.HasField('opened'), reply)
        return reply.opened

    def stop_replacement(self):
        self.act(stop=core.ActorStopRequest())
        self.request(stop_world=self.owner)
        self.request(recover_world=self.owner)
        self.finish_session()

    def test_replacement_preserves_world_and_application_without_old_control(self):
        self.held_application()
        self.management('world.create', entity=dict(id='saved-object', kind='object',
            room=self.management('world.info')['entry_room'], pose=pose((1, 0, -2))))
        self.management('world.checkpoint')
        before = self.actor_call(self.stream, inspect=session.WorldSessionInspect()).state
        world = self.management('world.info'); saved = self.management('world.get', id='saved-object')
        project = self.project / 'uncommitted-recovery.txt'; project.write_text('durable workspace content\n')
        request = self.replacement()
        self.retire_old_world()
        self.assertEqual(select.select([self.old_stream], [], [], 3)[0], [self.old_stream])
        self.assertEqual(self.old_stream.recv(1), b'')
        self.start_replacement(request)
        denied = self.actor_call(self.stream, inspect=session.WorldSessionInspect())
        self.assertEqual(denied.error.code, core.UNAUTHORIZED)
        opened = self.authenticate(); self.assertEqual(opened.world_id, self.world_domain)
        after = self.actor_call(self.stream, inspect=session.WorldSessionInspect()).state
        self.assertEqual((after.id, after.room, after.pose, after.workspace),
            (before.id, before.room, before.pose, before.workspace))
        self.assertFalse(any(after.axes)); self.assertEqual(list(after.held_keys), [])
        self.assertEqual(list(after.held_buttons), []); self.assertFalse(after.HasField('focus'))
        self.assertEqual(self.management('world.info'), world)
        self.assertEqual(self.management('world.get', id='saved-object'), saved)
        self.assertEqual(project.read_text(), 'durable workspace content\n')
        self.released()
        self.request(expect_error=True, owned_world=probe.SessionWorldCall(owner=self.old_owner,
            request=make_request('world.info', {})))
        surface = self.until(lambda: self.management('surface.find', probe_id='coordinator-application'),
            lambda value: value['surface'] is not None)['surface']
        # Explicitly present the surviving application. Automatic surface restoration is a later gate.
        self.management('surface.place', id=surface['id'], room=world['entry_room'], pose=pose((0, 0, -2)), size=[1, 1])
        self.act(aim=core.ActorAimRequest(direction=core.Vec3(z=-1)))
        self.act(pointer=core.ActorPointerRequest(button=1, pressed=True))
        controlled = self.request(inspect=self.owner).owner
        self.assertEqual(len(controlled.reservations), 1)
        self.assertEqual(controlled.reservations[0].application, self.app)
        self.assertNotEqual(controlled.reservations[0], self.lease)
        self.assertEqual(self.request(retire_world=self.old_owner).retirement, self.retirement)
        self.assertEqual(self.request(inspect=self.owner).owner, controlled)
        self.assertEqual(self.activation()[-1][:2], (2, 1))
        self.stop_replacement()

    def test_unretired_generation_rejects_replacement_before_execution(self):
        self.held_application(); request = self.replacement()
        self.cleanup = self.crash_world(); self.request(recover_world=self.owner)
        before = self.activation()
        self.request(expect_error=True, start_world=request)
        self.assertEqual(self.activation(), before)
        self.released()
        # An explicit issuer retirement is still required after failed replacement.
        receipt = self.request(retire_world=self.owner).retirement
        self.assertEqual(receipt.request.owner, self.owner.launch)
        self.finish_session()

    def test_crash_replacement_keeps_committed_edit_and_discards_preview(self):
        self.held_application()
        world = self.management('world.info')
        self.management('world.create', entity=dict(id='crash-object', kind='object',
            room=world['entry_room'], pose=pose((.25, 0, -1))))
        self.act(object_grab=core.ObjectGrabRequest(id='crash-object'))
        committed = core.Pose(position=core.Vec3(x=.35, z=-1), orientation=core.Quaternion(w=1))
        self.act(object_move=core.ObjectMoveRequest(pose=committed))
        self.act(object_release=core.ObjectReleaseRequest(commit=True))
        def observed():
            visible = self.act(observe=core.ActorObserveRequest(width=127, height=95)).observation.visible
            return next(item.pose for item in visible if item.id == 'crash-object')
        self.assertEqual(observed(), committed)
        self.act(object_grab=core.ObjectGrabRequest(id='crash-object'))
        preview = core.Pose(position=core.Vec3(x=-.35, z=-1), orientation=core.Quaternion(w=1))
        self.act(object_move=core.ObjectMoveRequest(pose=preview))
        self.assertEqual(observed(), preview)
        request = self.replacement()
        # No checkpoint or graceful world shutdown occurs before SIGKILL.
        self.retire_old_world()
        self.assertEqual(select.select([self.old_stream], [], [], 3)[0], [self.old_stream])
        self.assertEqual(self.old_stream.recv(1), b'')
        self.start_replacement(request)
        denied = self.actor_call(self.stream, inspect=session.WorldSessionInspect())
        self.assertEqual(denied.error.code, core.UNAUTHORIZED)
        self.assertEqual(self.authenticate().world_id, world['world_id'])
        self.assertEqual(observed(), committed)
        self.act(object_grab=core.ObjectGrabRequest(id='crash-object'))
        self.act(object_release=core.ObjectReleaseRequest(commit=False))
        self.assertEqual(self.request(retire_world=self.old_owner).retirement, self.retirement)
        self.released()
        self.stop_replacement()

    def test_replacement_synchronizes_revocation_before_actor_admission(self):
        self.held_application(); request = self.replacement(); self.retire_old_world()
        operation = identity.IssuerOperation(identity_revoke=identity.IdentityRevoke(
            target=identity.IdentityReference(kind=identity.CREDENTIAL_TARGET, id=self.claims.credential_id),
            expected_security_revision=0, reason='replacement recovery regression'))
        # Recovery can exceed the administrator fixture's three-second idle timeout.
        peer = self.issuer.connect()
        try:
            revoked = self.issuer.execute(peer, self.issuer.challenge(peer, operation))
        finally:
            peer.close()
        self.assertTrue(revoked.HasField('identity_status'), revoked)
        self.start_replacement(request)
        self.assertGreaterEqual(self.activation()[-1][2], revoked.identity_status.security_revision)
        denied = self.actor_call(self.stream, begin=session.WorldAuthenticationBegin(credential=self.actor.credential))
        self.assertTrue(denied.HasField('error'), denied)
        self.assertEqual(denied.error.code, core.UNAUTHORIZED)
        self.request(stop_world=self.owner); self.request(recover_world=self.owner)
        self.finish_session()


if __name__ == '__main__':
    names = ['SessionReplacementTests.' + name for name in SessionReplacementTests.__dict__ if name.startswith('test_')]
    unittest.main(defaultTest=names, verbosity=2)
