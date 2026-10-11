"""Independent requests to real owner-bound brokers and confined native applications."""
import array
import copy
import os
import select
import socket
import subprocess
import unittest
from unittest.mock import patch
import broker_pb2 as pb
import core_pb2 as core
import issuer_pb2 as issuer
import native_scope_pb2 as native_scope
import security_pb2 as security
from harness.wire import frame, make_request
import test_application_access as access
import test_application_control_lease as leases
from test_world_identity import WorldIdentityFixture, receive


class OwnedBroker(leases.LeaseBroker):
    def __init__(self, *args, **kwargs):
        self.admin, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.admin.settimeout(5)
        original = subprocess.Popen

        def launch(command, **options):
            command = [*command, '--control-supervisor-fd', str(child.fileno()),
                       '--control-supervisor-pid', str(os.getpid())]
            options['pass_fds'] = (*options.get('pass_fds', ()), child.fileno())
            return original(command, **options)

        try:
            with patch.object(access.subprocess, 'Popen', launch):
                super().__init__(*args, **kwargs)
        finally:
            child.close()

    def close(self):
        if self.stream.fileno() < 0:
            return
        if self.proc.poll() is None:
            self.stream.shutdown(socket.SHUT_WR)
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=3)
                raise
        self.stream.close()
        self.admin.close()
        self.log.close()
        text = self.path.read_text()
        # Some tests deliberately retain fenced reservations without requesting checked recovery.
        if self.proc.returncode == 2:
            self.fixture.assertTrue(text.rstrip().endswith('Workspace control release remains unresolved'), text)
        else:
            self.fixture.assertEqual(self.proc.returncode, 0, text)
        for marker in ('AddressSanitizer', 'LeakSanitizer', 'runtime error:'):
            self.fixture.assertNotIn(marker, text)


class ControlOwnerTests(WorldIdentityFixture):
    close_brokers = access.ApplicationAccessTests.close_brokers
    launch_application = access.ApplicationAccessTests.launch_application
    finish_application = access.ApplicationAccessTests.finish_application
    data = access.ApplicationAccessTests.data
    principal = access.ApplicationAccessTests.principal
    workspace_terms = access.ApplicationAccessTests.workspace_terms
    enroll_second = access.ApplicationAccessTests.enroll_second
    delegate = access.ApplicationAccessTests.delegate
    revoke_record = access.ApplicationAccessTests.revoke_record

    def setUp(self):
        super().setUp()
        self.brokers = []
        self.sequence = self.admin_sequence = self.world_sequence = 0
        self.addCleanup(self.close_brokers)
        with patch.object(access, 'Broker', OwnedBroker):
            self.app = access.ApplicationAccessTests.setup_application(self)

    def owner_binding(self):
        launch_id = os.urandom(32)
        return pb.ControlOwner(issuer_id=self.principal().issuer_id,
            launch=issuer.AuthorityFenceBegin(participant_id=os.urandom(32), registered_revision=1,
                generation=1, service_incarnation=os.urandom(32), launch_id=launch_id,
                supervision_revision=1, authorization_id=os.urandom(32)),
            native=native_scope.NativeScopeIdentity(launch_id=launch_id, server_id=self.server_id,
                credential_id=os.urandom(32), role=security.COMPOSITOR))

    def admin(self, operation, owner, fds=()):
        self.admin_sequence += 1
        request = pb.ControlSupervisorRequest(protocol_version=1, request_id=self.admin_sequence,
                                               **{operation: owner})
        ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', fds))] if fds else []
        self.broker.admin.sendmsg([request.SerializeToString()], ancillary)
        reply = pb.ControlSupervisorReply.FromString(self.broker.admin.recv(65536))
        self.assertEqual(reply.protocol_version, 1)
        self.assertEqual(reply.request_id, self.admin_sequence)
        return reply

    def inspect(self, owner):
        reply = self.admin('inspect', owner)
        self.assertTrue(reply.HasField('state'), reply)
        self.assertEqual(reply.state.owner, owner)
        return reply.state

    def register(self, owner=None):
        owner = owner or self.owner_binding()
        world, endpoint = socket.socketpair()
        world.settimeout(5)
        self.addCleanup(world.close)
        try:
            reply = self.admin('register', owner, [endpoint.fileno()])
        finally:
            endpoint.close()
        self.assertTrue(reply.HasField('state'), reply)
        self.assertEqual(reply.state.owner, owner)
        self.assertFalse(reply.state.channel_fenced)
        return owner, world

    def request(self, world, **operation):
        self.world_sequence += 1
        world.sendall(frame(pb.Request(protocol_version=1, request_id=self.world_sequence, **operation)))
        reply = receive(world.recv, world, pb.Reply)
        self.assertEqual(reply.protocol_version, 1)
        self.assertEqual(reply.request_id, self.world_sequence)
        return reply

    def acquire(self, world, app=None, claims=None):
        return self.request(world, control_acquire=pb.ApplicationControlRequest(
            principal=self.principal(claims), application=app or self.app))

    def lease(self, world, app=None):
        reply = self.acquire(world, app)
        self.assertTrue(reply.HasField('control_lease'), reply)
        return reply.control_lease

    def release(self, world, lease):
        return self.request(world, control_release=pb.ApplicationControlRelease(id=lease.id, principal=lease.principal))

    def cleanup_receipt(self, owner):
        # This component fixture is the configured private coordinator, not a cleanup witness.
        # The native session tests prove actual process and native cleanup before admission here.
        recovery = pb.ControlOwnerRecovery(owner=owner)
        recovery.native_stopped.identity.CopyFrom(owner.native)
        process = recovery.process_stopped.identity
        process.launch_id = owner.launch.launch_id
        process.scope_name = 'authority-' + process.launch_id.hex()
        process.boot_id = '12345678-1234-1234-1234-123456789abc'
        process.device = 23; process.inode = 42
        return recovery

    def test_recovery_releases_only_fenced_owner_and_preserves_applications(self):
        owner, world = self.register(); other, second = self.register()
        first = self.lease(world); other_app = self.launch_application(); other_lease = self.lease(second, other_app)
        self.admin('fence', owner)
        recovered = self.admin('recover', self.cleanup_receipt(owner)).state
        self.assertTrue(recovered.channel_fenced); self.assertFalse(recovered.reservations)
        self.assertEqual(list(recovered.recovery.released), [first])
        self.assertFalse(self.inspect(other).channel_fenced)
        self.assertEqual(self.lease(second, other_app), other_lease)
        replacement = self.lease(second)
        self.assertNotEqual(replacement.id, first.id)
        self.assertTrue(self.release(second, replacement).HasField('control_released'))
        self.assertTrue(self.release(second, other_lease).HasField('control_released'))
        self.finish_application(self.app); self.finish_application(other_app)

    def test_recovery_is_idempotent_and_changed_receipt_is_rejected(self):
        owner, world = self.register(); lease = self.lease(world); self.admin('fence', owner)
        cleanup = self.cleanup_receipt(owner)
        first = self.admin('recover', cleanup)
        self.assertTrue(first.HasField('state'), first)
        self.assertEqual(self.admin('recover', cleanup).state, first.state)
        self.assertEqual(self.inspect(owner), first.state)
        self.assertEqual(list(first.state.recovery.released), [lease])
        changed = copy.deepcopy(cleanup); changed.process_stopped.identity.inode += 1
        self.denied(self.admin('recover', changed), core.INVALID_ARGUMENT)
        self.assertEqual(self.inspect(owner), first.state)

    def test_recovery_rejects_unfenced_owner_without_effect(self):
        owner, world = self.register(); lease = self.lease(world)
        self.denied(self.admin('recover', self.cleanup_receipt(owner)), core.INVALID_ARGUMENT)
        state = self.inspect(owner)
        self.assertFalse(state.channel_fenced); self.assertFalse(state.HasField('recovery'))
        self.assertEqual(self.lease(world), lease)
        self.release(world, lease)

    def test_recovery_rejects_missing_or_mismatched_cleanup_without_releasing(self):
        owner, world = self.register(); lease = self.lease(world); self.admin('fence', owner)
        cleanup = self.cleanup_receipt(owner)
        changes = [lambda x: x.ClearField('process_stopped'), lambda x: x.ClearField('native_stopped'),
            lambda x: setattr(x.native_stopped.identity, 'credential_id', os.urandom(32)),
            lambda x: setattr(x.native_stopped.identity, 'server_id', os.urandom(32)),
            lambda x: setattr(x.process_stopped.identity, 'launch_id', os.urandom(32)),
            lambda x: setattr(x.process_stopped.identity, 'scope_name', 'unrelated'),
            lambda x: setattr(x.process_stopped.identity, 'inode', 0),
            lambda x: x.native_stopped.MergeFromString(b'\xf8\x07\x01')]
        for change in changes:
            invalid = copy.deepcopy(cleanup); change(invalid)
            self.denied(self.admin('recover', invalid), core.INVALID_ARGUMENT)
            self.assertEqual(list(self.inspect(owner).reservations), [lease])
        self.assertTrue(self.admin('recover', cleanup).HasField('state'))

    def test_recovery_rejects_unknown_owner_and_transferred_descriptors(self):
        owner, world = self.register(); lease = self.lease(world); self.admin('fence', owner)
        self.denied(self.admin('recover', self.cleanup_receipt(self.owner_binding())), core.NOT_FOUND)
        a, b = socket.socketpair()
        try:
            self.denied(self.admin('recover', self.cleanup_receipt(owner), [b.fileno()]), core.INVALID_ARGUMENT)
        finally:
            a.close(); b.close()
        self.assertEqual(list(self.inspect(owner).reservations), [lease])
        self.assertTrue(self.admin('recover', self.cleanup_receipt(owner)).HasField('state'))

    def test_recovery_covers_acquisition_whose_reply_was_lost(self):
        owner, world = self.register()
        world.sendall(frame(pb.Request(protocol_version=1, request_id=1,
            control_acquire=pb.ApplicationControlRequest(principal=self.principal(), application=self.app))))
        self.assertTrue(select.select([world], [], [], 5)[0])
        world.close()
        state = self.until(lambda: self.inspect(owner), lambda value: value.channel_fenced)
        self.assertEqual(len(state.reservations), 1)
        recovered = self.admin('recover', self.cleanup_receipt(owner)).state
        self.assertEqual(list(recovered.recovery.released), list(state.reservations))
        self.assertFalse(recovered.reservations)

    def test_recovery_reply_loss_is_reconciled_by_inspection(self):
        owner, world = self.register(); lease = self.lease(world); self.admin('fence', owner)
        cleanup = self.cleanup_receipt(owner)
        request = pb.ControlSupervisorRequest(protocol_version=1, request_id=999, recover=cleanup)
        self.broker.admin.send(request.SerializeToString())
        self.broker.admin.recv(65536)  # Deliberately discard the committed reply.
        state = self.inspect(owner)
        self.assertEqual(state.recovery.cleanup, cleanup)
        self.assertEqual(list(state.recovery.released), [lease])
        self.assertFalse(state.reservations)
        self.assertEqual(self.admin('recover', cleanup).state, state)

    def test_empty_recovery_retains_fence_and_does_not_enable_rebinding(self):
        owner, world = self.register(); self.admin('fence', owner)
        state = self.admin('recover', self.cleanup_receipt(owner)).state
        self.assertTrue(state.channel_fenced); self.assertTrue(state.HasField('recovery'))
        self.assertFalse(state.reservations); self.assertFalse(state.recovery.released)
        a, b = socket.socketpair()
        try:
            self.denied(self.admin('register', owner, [b.fileno()]), core.ALREADY_EXISTS)
        finally:
            a.close(); b.close()

    def test_world_channel_cannot_invoke_private_recovery(self):
        owner, world = self.register(); lease = self.lease(world)
        world.sendall(frame(pb.ControlSupervisorRequest(protocol_version=1, request_id=999,
            recover=self.cleanup_receipt(owner))))
        reply = receive(world.recv, world, pb.Reply)
        self.assertTrue(reply.HasField('error'), reply)
        state = self.inspect(owner)
        self.assertFalse(state.channel_fenced); self.assertFalse(state.HasField('recovery'))
        self.assertEqual(list(state.reservations), [lease])
        self.assertTrue(self.release(world, lease).HasField('control_released'))

    def test_two_owner_channels_have_distinct_reservations(self):
        owner, world = self.register()
        other, second = self.register()
        a = self.lease(world)
        b = self.lease(second, self.launch_application())
        self.assertNotEqual(a.id, b.id)
        self.assertEqual(list(self.inspect(owner).reservations), [a])
        self.assertEqual(list(self.inspect(other).reservations), [b])
        self.assertTrue(self.release(world, a).HasField('control_released'))
        self.assertTrue(self.release(second, b).HasField('control_released'))

    def test_same_actor_cannot_reuse_or_release_other_owner(self):
        owner, world = self.register()
        other, second = self.register()
        lease = self.lease(world)
        self.denied(self.acquire(second), core.BUSY)
        self.denied(self.release(second, lease), core.FORBIDDEN)
        self.denied(self.broker.request(control_release=pb.ApplicationControlRelease(
            id=lease.id, principal=lease.principal)), core.FORBIDDEN)
        self.assertEqual(self.lease(world), lease)
        self.assertEqual(list(self.inspect(owner).reservations), [lease])
        self.assertFalse(self.inspect(other).reservations)

    def test_lost_acquisition_reply_remains_inspectable_after_eof(self):
        owner, world = self.register()
        world.sendall(frame(pb.Request(protocol_version=1, request_id=1,
            control_acquire=pb.ApplicationControlRequest(principal=self.principal(), application=self.app))))
        self.assertTrue(select.select([world], [], [], 5)[0], 'No committed acquisition response')
        # Do not consume the acquisition reply. Its lease ID is learned only from broker inspection.
        world.close()
        state = self.until(lambda: self.inspect(owner), lambda value: value.channel_fenced)
        self.assertEqual(len(state.reservations), 1)
        self.assertEqual(state.reservations[0].application, self.app)
        self.denied(self.broker.request(control_acquire=pb.ApplicationControlRequest(
            principal=self.principal(), application=self.app)), core.BUSY)
        self.finish_application(self.app)

    def test_fence_preserves_other_owner_and_live_applications(self):
        owner, world = self.register()
        other, second = self.register()
        a = self.lease(world)
        other_app = self.launch_application()
        b = self.lease(second, other_app)
        fenced = self.admin('fence', owner)
        self.assertTrue(fenced.state.channel_fenced)
        self.assertEqual(list(fenced.state.reservations), [a])
        self.assertEqual(world.recv(1), b'')
        self.assertEqual(self.admin('fence', owner).state, fenced.state)
        self.assertFalse(self.inspect(other).channel_fenced)
        self.assertEqual(self.lease(second, other_app), b)
        self.assertTrue(self.release(second, b).HasField('control_released'))
        self.finish_application(self.app)
        self.finish_application(other_app)

    def test_exact_binding_required_for_inspection_and_fence(self):
        owner, world = self.register()
        for field in ('participant_id', 'launch_id', 'authorization_id', 'service_incarnation',
                      'generation', 'registered_revision', 'supervision_revision'):
            changed = pb.ControlOwner()
            changed.CopyFrom(owner)
            old = getattr(changed.launch, field)
            setattr(changed.launch, field, old+1 if isinstance(old, int) else os.urandom(32))
            if field == 'launch_id':
                changed.native.launch_id = changed.launch.launch_id
            for operation in ('inspect', 'fence'):
                self.denied(self.admin(operation, changed), core.NOT_FOUND)
        self.assertFalse(self.inspect(owner).channel_fenced)
        self.lease(world)

    def test_registration_cannot_rebind_launch_or_endpoint(self):
        owner, world = self.register()
        for field in ('launch_id', 'authorization_id', 'generation'):
            changed = self.owner_binding()
            if field == 'generation':
                changed.launch.participant_id = owner.launch.participant_id
            else:
                setattr(changed.launch, field, getattr(owner.launch, field))
            if field == 'launch_id':
                changed.native.launch_id = changed.launch.launch_id
            a, b = socket.socketpair()
            try:
                self.denied(self.admin('register', changed, [b.fileno()]), core.ALREADY_EXISTS)
            finally:
                a.close(); b.close()
        self.assertFalse(self.inspect(owner).channel_fenced)
        self.lease(world)

    def test_invalid_descriptors_and_ancillary_truncation_do_not_leak(self):
        def count():
            self.assertIsNone(self.broker.proc.poll())
            # NativeLaunch deliberately disables same-user /proc inspection.
            # A read-only root witness observes this exact unreaped fixture child.
            witness = subprocess.run(['sudo', '-n', 'python3', '-c',
                'import os,sys; print(len(os.listdir("/proc/"+sys.argv[1]+"/fd")))',
                str(self.broker.proc.pid)], capture_output=True, timeout=5)
            self.assertEqual(witness.returncode, 0, witness.stderr)
            self.assertIsNone(self.broker.proc.poll())
            return int(witness.stdout)
        before = count()
        fd = os.open('/dev/null', os.O_RDONLY)
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            for descriptors in ([], [fd], [b.fileno()], [fd]*2, [fd]*40):
                reply = self.admin('register', self.owner_binding(), descriptors)
                self.assertTrue(reply.HasField('error'), reply)
            # Synchronize after all rejected descriptor destructors have run.
            self.broker.call('system.hello')
            self.assertEqual(count(), before)
        finally:
            os.close(fd); a.close(); b.close()
        self.register()

    def test_query_cannot_transfer_a_descriptor(self):
        owner, world = self.register()
        a, b = socket.socketpair()
        try:
            for operation in ('inspect', 'fence'):
                self.denied(self.admin(operation, owner, [b.fileno()]), core.INVALID_ARGUMENT)
        finally:
            a.close(); b.close()
        self.assertFalse(self.inspect(owner).channel_fenced)

    def test_revocation_event_goes_only_to_owning_world(self):
        owner, world = self.register()
        other, second = self.register()
        lease = self.lease(world)
        self.revoke(kind=issuer.ACTOR_TARGET)
        event = receive(world.recv, world, pb.Reply)
        self.assertEqual(event.request_id, 0)
        self.assertEqual(event.control_revoked, lease)
        self.assertFalse(select.select([second, self.broker.stream], [], [], .1)[0])
        self.assertTrue(self.release(world, lease).HasField('control_released'))

    def test_owner_binding_cannot_grant_actor_control(self):
        self.enroll_second()
        owner, world = self.register()
        self.denied(self.acquire(world, claims=self.second), core.FORBIDDEN)
        changed = pb.ApplicationRef()
        changed.CopyFrom(self.app)
        changed.server_id = os.urandom(32)
        self.denied(self.acquire(world, changed), core.FORBIDDEN)
        self.assertFalse(self.inspect(owner).reservations)

    def test_administration_is_not_accepted_on_world_channel(self):
        owner, world = self.register()
        world.sendall(frame(pb.ControlSupervisorRequest(protocol_version=1, request_id=42, fence=owner)))
        reply = receive(world.recv, world, pb.Reply)
        self.assertTrue(reply.HasField('error'), reply)
        self.assertFalse(self.inspect(owner).channel_fenced)
        self.lease(world)

    def test_admin_disconnect_fences_owners_without_stopping_application(self):
        owner, world = self.register()
        self.lease(world)
        self.broker.admin.close()
        self.assertEqual(world.recv(1), b'')
        self.denied(self.broker.request(control_acquire=pb.ApplicationControlRequest(
            principal=self.principal(), application=self.app)), core.BUSY)
        self.finish_application(self.app)
        self.denied(self.broker.request(control_acquire=pb.ApplicationControlRequest(
            principal=self.principal(), application=self.app)), core.FORBIDDEN)

    def test_tombstones_reject_rebinding_and_bound_owner_table(self):
        first, world = self.register()
        self.assertTrue(self.admin('fence', first).state.channel_fenced)
        a, b = socket.socketpair()
        try:
            self.denied(self.admin('register', first, [b.fileno()]), core.ALREADY_EXISTS)
        finally:
            a.close(); b.close()
        for _ in range(255):
            owner, channel = self.register()
            self.assertTrue(self.admin('fence', owner).state.channel_fenced)
            channel.close()
        a, b = socket.socketpair()
        try:
            self.denied(self.admin('register', self.owner_binding(), [b.fileno()]), core.RESOURCE_EXHAUSTED)
        finally:
            a.close(); b.close()
        self.assertTrue(self.inspect(first).channel_fenced)
        self.finish_application(self.app)

    def test_duplicate_endpoint_cannot_receive_another_owner(self):
        a, b = socket.socketpair()
        try:
            self.assertTrue(self.admin('register', self.owner_binding(), [b.fileno()]).HasField('state'))
            self.denied(self.admin('register', self.owner_binding(), [b.fileno()]), core.ALREADY_EXISTS)
        finally:
            a.close(); b.close()

    def test_invalid_bindings_cannot_create_owner(self):
        for change in ('issuer', 'native', 'role', 'revision', 'generation'):
            owner = self.owner_binding()
            if change == 'issuer': owner.issuer_id = b'bad'
            elif change == 'native': owner.native.launch_id = os.urandom(32)
            elif change == 'role': owner.native.role = security.BROKER
            elif change == 'revision': owner.launch.registered_revision = 0
            else: owner.launch.generation = 2**64-1
            a, b = socket.socketpair()
            try:
                self.denied(self.admin('register', owner, [b.fileno()]), core.INVALID_ARGUMENT)
            finally:
                a.close(); b.close()
        self.register()

    def test_malformed_unknown_and_oversized_packets_leave_admin_usable(self):
        owner = self.owner_binding()
        a, b = socket.socketpair()
        try:
            valid = pb.ControlSupervisorRequest(protocol_version=1, request_id=99, **{'register': owner}).SerializeToString()
            for payload in (b'\xff', valid+b'\xf8\x07\x01', valid+b'x'*65536):
                self.broker.admin.sendmsg([payload], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [b.fileno()]))])
                reply = pb.ControlSupervisorReply.FromString(self.broker.admin.recv(65536))
                self.denied(reply, core.INVALID_ARGUMENT)
            wrong = pb.ControlSupervisorRequest(protocol_version=2, request_id=99, **{'register': owner})
            self.broker.admin.sendmsg([wrong.SerializeToString()], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [b.fileno()]))])
            self.denied(pb.ControlSupervisorReply.FromString(self.broker.admin.recv(65536)), core.UNSUPPORTED_VERSION)
        finally:
            a.close(); b.close()
        self.register()

    def test_transferred_channel_with_different_creator_is_rejected(self):
        receive_fd, send_fd = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        child = os.fork()
        if child == 0:
            try:
                a, b = socket.socketpair()
                send_fd.sendmsg([b'channel'], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [b.fileno()]))])
            finally:
                os._exit(0)
        try:
            _, ancillary, flags, _ = receive_fd.recvmsg(64, socket.CMSG_SPACE(4))
            self.assertFalse(flags & socket.MSG_CTRUNC)
            descriptors = array.array('i')
            descriptors.frombytes(ancillary[0][2])
            self.assertEqual(len(descriptors), 1)
            try:
                reply = self.admin('register', self.owner_binding(), descriptors)
                self.assertTrue(reply.HasField('error'), reply)
            finally:
                for fd in descriptors: os.close(fd)
        finally:
            receive_fd.close(); send_fd.close()
            self.assertEqual(os.waitpid(child, 0)[1], 0)
        self.register()

    def test_changed_admin_sender_fences_channels(self):
        owner, world = self.register()
        self.lease(world)
        child = os.fork()
        if child == 0:
            try:
                self.broker.admin.send(pb.ControlSupervisorRequest(protocol_version=1, request_id=1, inspect=owner).SerializeToString())
            finally:
                os._exit(0)
        self.assertEqual(os.waitpid(child, 0)[1], 0)
        self.assertEqual(world.recv(1), b'')
        self.assertEqual(self.broker.admin.recv(1), b'')
        self.denied(self.broker.request(control_acquire=pb.ApplicationControlRequest(
            principal=self.principal(), application=self.app)), core.BUSY)
        self.finish_application(self.app)

    def test_fence_is_processed_during_root_process_wait(self):
        owner, world = self.register()
        lease = self.lease(world)
        self.broker.sequence += 1
        sequence = self.broker.sequence
        command = make_request('process.wait', {'id': self.app.application.decode(), 'timeout_seconds': 5})
        command.request_id = sequence
        self.broker.stream.sendall(frame(pb.Request(protocol_version=1, request_id=sequence, management=command)))
        self.assertFalse(select.select([self.broker.stream], [], [], .1)[0])
        try:
            reply = self.admin('fence', owner)
            self.assertTrue(reply.state.channel_fenced)
            self.assertEqual(list(reply.state.reservations), [lease])
        finally:
            (self.data(self.app)/'release').touch()
        self.assertEqual(self.broker.response(sequence).management.process_wait.exit_code, 0)


    def check_validation_precedes_attribution(self, version, extra, expected):
        owner, world = self.register()
        application = pb.ApplicationRef()
        application.CopyFrom(self.app)
        application.server_id = os.urandom(32)
        self.assertNotEqual(application.server_id, self.server_id)
        request = pb.Request(protocol_version=version, request_id=101,
            control_acquire=pb.ApplicationControlRequest(principal=self.principal(), application=application))
        payload = request.SerializeToString()+extra
        world.sendall(len(payload).to_bytes(4, 'big')+payload)
        reply = receive(world.recv, world, pb.Reply)
        self.assertEqual(reply.protocol_version, 1)
        self.assertEqual(reply.request_id, 101)
        self.assertTrue(reply.HasField('error'), reply)
        self.assertEqual(reply.error.code, expected,
            f'Expected {core.ErrorCode.Name(expected)} before native attribution: {reply}')
        state = self.inspect(owner)
        self.assertFalse(state.channel_fenced)
        self.assertFalse(state.reservations)
        self.lease(world)

    def test_unsupported_version_precedes_native_attribution(self):
        self.check_validation_precedes_attribution(2, b'', core.UNSUPPORTED_VERSION)

    def test_unknown_fields_precede_native_attribution(self):
        self.check_validation_precedes_attribution(1, b'\xf8\x07\x01', core.INVALID_ARGUMENT)


if __name__ == '__main__':
    unittest.main(defaultTest='ControlOwnerTests', verbosity=2)
