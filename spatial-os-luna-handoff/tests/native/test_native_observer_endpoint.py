"""Independent native proof for the inherited, parent-owned observer endpoint."""
import array
import fcntl
import hashlib
import json
import os
import select
import struct
import subprocess
import time
from unittest.mock import Mock, patch
from pathlib import Path
import socket
import unittest
import native_input_observer_control_v2_pb2 as control
import test_actor_binding as binding


class NativeObserverEndpointTests(binding.ActorBindingTests):
    def start_endpoint(self):
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        parent.settimeout(3)
        self.addCleanup(parent.close)
        self.endpoint = parent
        self.endpoint_inode = os.fstat(child.fileno()).st_ino
        self.endpoint_number = child.fileno()
        try:
            self.start(observer_fd=child.fileno())
        finally:
            child.close()
        raw = parent.recv(4097)
        self.assertLessEqual(len(raw), 4096)
        reply = control.ObserverEndpointReplyV2.FromString(raw)
        self.assertEqual(reply.WhichOneof('message'), 'hello', reply)
        self.hello = reply.hello
        return reply.hello

    def test_actual_server_receives_parent_owned_cloexec_endpoint(self):
        hello = self.start_endpoint()
        self.assertEqual(hello.protocol_version, 2)
        self.assertEqual(len(hello.session_id), 16)
        self.assertGreater(hello.native_session_epoch, 0)
        self.assertEqual((hello.server_pid, hello.server_uid, hello.server_gid),
                         (self.server.pid, os.getuid(), os.getgid()))
        actual = hashlib.sha256(Path(f'/proc/{self.server.pid}/exe').read_bytes()).hexdigest()
        self.assertEqual(actual, os.environ['SPATIAL_TEST_XSERVER_SHA256'])
        status = Path(f'/proc/{self.server.pid}/status').read_text()
        self.assertEqual(next(int(line.split()[1]) for line in status.splitlines()
                              if line.startswith('PPid:')), os.getpid())
        fd = Path(f'/proc/{self.server.pid}/fd/{self.endpoint_number}')
        self.assertEqual(fd.readlink().as_posix(), f'socket:[{self.endpoint_inode}]')
        info = Path(f'/proc/{self.server.pid}/fdinfo/{self.endpoint_number}').read_text()
        flags = next(int(line.split()[1], 8) for line in info.splitlines()
                     if line.startswith('flags:'))
        self.assertTrue(flags & os.O_CLOEXEC)
        self.assertTrue(flags & os.O_NONBLOCK)
        self.assertEqual(self.supervisor.inspect().role, binding.sec.SUPERVISOR)


    @staticmethod
    def native_client_index(client):
        base, mask = struct.unpack_from(client.endian + 'II', client.setup, 4)
        return base >> mask.bit_length()

    @staticmethod
    def descriptor_targets(pid):
        targets = set()
        for path in Path(f'/proc/{pid}/fd').iterdir():
            try:
                targets.add(path.readlink().as_posix())
            except FileNotFoundError:
                # A descriptor can close between directory enumeration and readlink.
                continue
        return targets

    def setup_context(self):
        self.start_endpoint()
        with patch.object(self, 'start', return_value=self.supervisor):
            self.setup_scene()
        self.probe = self.connect(self.supervisor.issue(binding.sec.COMPOSITOR, b'', b'').cookie)
        marker = self.probe.create_window()
        self.assertIsNone(self.compositor.operation(8, self.compositor.pack('I', marker)))
        target = self.target(self.window)
        request = control.ObserverSetupRequestV2(
            session_id=self.hello.session_id, arm_id=os.urandom(16),
            old_native_seat_id=self.seat.seat, peer_native_seat_id=self.second.seat,
            target_xid=self.window, probe_marker_xid=marker,
            presentation_generation=1, target_spatial_generation=target.generation,
            expected_probe_role=binding.sec.COMPOSITOR,
            deadline_monotonic_ns=time.monotonic_ns() + 3_000_000_000)
        request.expected_application.workspace_id = b'one'
        request.expected_application.application_id = b'editor'
        self.request = request
        return request

    def send_setup(self, request):
        data = control.ObserverControlEnvelopeV2(setup_request=request).SerializeToString(deterministic=True)
        self.endpoint.sendall(data)
        raw = self.endpoint.recv(4097)
        self.assertTrue(raw, self.log_path.read_text(errors='replace'))
        self.assertLessEqual(len(raw), 4096)
        reply = control.ObserverEndpointReplyV2.FromString(raw)
        self.assertEqual(reply.WhichOneof('message'), 'setup_reply', reply)
        return reply.setup_reply

    def assert_rejected(self, reason='invalid setup or native pin'):
        self.assertEqual(self.endpoint.recv(4097), b'')
        # A private observer refusal does not revoke unrelated X connections.
        self.assertEqual(self.supervisor.inspect().role, binding.sec.SUPERVISOR)
        end = time.monotonic() + 2
        while reason not in self.log_path.read_text(errors='replace') and time.monotonic() < end:
            time.sleep(.01)
        self.assertIn(reason, self.log_path.read_text(errors='replace'))
        self.assertIsNone(self.server.poll())

    def invalid_setup(self, mutate):
        request = self.setup_context()
        mutate(request)
        self.endpoint.sendall(control.ObserverControlEnvelopeV2(setup_request=request).SerializeToString(deterministic=True))
        self.assert_rejected()

    def test_setup_resolves_real_native_seats_and_distinct_owners(self):
        request = self.setup_context()
        reply = self.send_setup(request)
        self.assertEqual(reply.accepted_request, request)
        self.assertEqual(reply.session_id, self.hello.session_id)
        self.assertEqual(reply.native_session_epoch, self.hello.native_session_epoch)
        self.assertEqual((reply.old_native_seat_id, reply.peer_native_seat_id),
                         (self.seat.seat, self.second.seat))
        for devices, seat in [(reply.old_actor_devices, self.seat),
                              (reply.peer_actor_devices, self.second)]:
            self.assertEqual(len(devices), 4)
            by_role = {(d.kind, d.role): d for d in devices}
            self.assertEqual(set(by_role), {(1, 1), (1, 2), (2, 1), (2, 2)})
            self.assertEqual(by_role[1, 1].device_id, seat.pointer_device)
            self.assertEqual(by_role[2, 1].device_id, seat.keyboard_device)
            self.assertTrue(all(d.native_object_epoch > 0 for d in devices))
            self.assertEqual(by_role[1, 2].paired_or_attached, seat.pointer_device)
            self.assertEqual(by_role[2, 2].paired_or_attached, seat.keyboard_device)
        devices = list(reply.old_actor_devices) + list(reply.peer_actor_devices)
        self.assertEqual(len({d.device_id for d in devices}), 8)
        self.assertEqual(reply.app_target.xid, self.window)
        self.assertEqual(reply.app_target.spatial_generation, request.target_spatial_generation)
        self.assertGreater(reply.app_target.native_object_epoch, 0)
        self.assertEqual(reply.app_target.application, request.expected_application)
        self.assertEqual(reply.app_target.owner, reply.app_target_owner)
        self.assertEqual(reply.app_target_owner.client_id, self.native_client_index(self.owner))
        self.assertEqual(reply.app_target_owner.role, binding.sec.APPLICATION)
        self.assertEqual(reply.compositor_seat_owner.client_id, self.native_client_index(self.compositor))
        self.assertEqual(reply.compositor_seat_owner.role, binding.sec.COMPOSITOR)
        self.assertEqual(reply.probe_marker.xid, request.probe_marker_xid)
        self.assertGreater(reply.probe_marker.window_epoch, 0)
        self.assertEqual(reply.probe_marker.privileged_owner.client_id, self.native_client_index(self.probe))
        self.assertEqual(reply.probe_marker.privileged_owner.role, binding.sec.COMPOSITOR)
        self.assertFalse(reply.probe_marker.privileged_owner.HasField('application'))
        # The read-only setup did not acquire control or alter ordinary native routing.
        self.click(); self.key(38, True); self.assertTrue(self.held(self.owner, 38))
        self.cancel(); self.assertFalse(self.held(self.owner, 38))

    def test_setup_is_one_shot_and_replay_does_not_affect_native_clients(self):
        request = self.setup_context()
        self.send_setup(request)
        self.endpoint.sendall(control.ObserverControlEnvelopeV2(setup_request=request).SerializeToString(deterministic=True))
        self.assert_rejected('one-shot setup replay')

    def test_wrong_session_rejects(self):
        self.invalid_setup(lambda r: setattr(r, 'session_id', os.urandom(16)))

    def test_stale_target_generation_rejects(self):
        self.invalid_setup(lambda r: setattr(r, 'target_spatial_generation', r.target_spatial_generation + 1))

    def test_wrong_application_rejects(self):
        self.invalid_setup(lambda r: setattr(r.expected_application, 'application_id', b'another-app'))

    def test_wrong_probe_role_rejects(self):
        self.invalid_setup(lambda r: setattr(r, 'expected_probe_role', binding.sec.SUPERVISOR))

    def test_application_owned_marker_rejects(self):
        self.invalid_setup(lambda r: setattr(r, 'probe_marker_xid', self.other_window))

    def test_seat_alias_rejects(self):
        self.invalid_setup(lambda r: setattr(r, 'peer_native_seat_id', r.old_native_seat_id))

    def test_expired_deadline_rejects(self):
        self.invalid_setup(lambda r: setattr(r, 'deadline_monotonic_ns', time.monotonic_ns() - 1))

    def test_unknown_fields_reject_without_native_side_effects(self):
        request = self.setup_context()
        raw = control.ObserverControlEnvelopeV2(setup_request=request).SerializeToString(deterministic=True)
        self.endpoint.sendall(raw + b'\x18\x01')
        self.assert_rejected()

    def test_oversized_packet_closes_endpoint(self):
        self.start_endpoint()
        self.endpoint.sendall(b'x' * 4097)
        self.assert_rejected('sender or ancillary data')

    def test_unexpected_arm_before_setup_rejects(self):
        self.start_endpoint()
        message = control.ObserverControlEnvelopeV2()
        message.arm.session_id = self.hello.session_id
        message.arm.arm_id = os.urandom(16)
        self.endpoint.sendall(message.SerializeToString(deterministic=True))
        self.assert_rejected()

    def test_extra_descriptors_close_without_leaking_received_rights(self):
        request = self.setup_context()
        raw = control.ObserverControlEnvelopeV2(setup_request=request).SerializeToString(deterministic=True)
        before = self.descriptor_targets(self.server.pid)
        fd = os.memfd_create('observer-unexpected-right', os.MFD_CLOEXEC)
        try:
            self.endpoint.sendmsg([raw], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [fd]))])
        finally:
            os.close(fd)
        self.assert_rejected('sender or ancillary data')
        after = self.descriptor_targets(self.server.pid)
        self.assertFalse(any('observer-unexpected-right' in value for value in after - before))

    def test_forked_sender_cannot_use_parent_endpoint_authority(self):
        request = self.setup_context()
        raw = control.ObserverControlEnvelopeV2(setup_request=request).SerializeToString(deterministic=True)
        pid = os.fork()
        if pid == 0:
            try:
                self.endpoint.sendall(raw)
            except BaseException:
                os._exit(1)
            os._exit(0)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0)
        self.assert_rejected('sender or ancillary data')

    def rejected_launch(self, fd, diagnostic, extra=()):
        self.start(observer_fd=fd, extra_server_args=extra, expect_ready=False)
        self.assertIn(diagnostic, self.log_path.read_text(errors='replace'))

    def test_regular_file_cannot_be_endpoint(self):
        fd = os.memfd_create('observer-wrong-class', os.MFD_CLOEXEC)
        try:
            self.rejected_launch(fd, 'must be a connected Unix seqpacket socket')
        finally:
            os.close(fd)

    def test_stream_socket_cannot_be_endpoint(self):
        parent, child = socket.socketpair()
        try:
            self.rejected_launch(child.fileno(), 'must be a connected Unix seqpacket socket')
        finally:
            parent.close(); child.close()

    def test_duplicate_endpoint_argument_rejects(self):
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            self.rejected_launch(child.fileno(), 'requires one inherited endpoint',
                                 ('-spatial-observer-fd', str(child.fileno())))
        finally:
            parent.close(); child.close()

    def test_default_build_refuses_endpoint(self):
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            with patch.dict(os.environ, SPATIAL_TEST_XSERVER=os.environ['SPATIAL_TEST_DEFAULT_XSERVER']):
                self.rejected_launch(child.fileno(), 'requires an enabled qualification build')
        finally:
            parent.close(); child.close()

    def test_eof_removes_server_endpoint_and_preserves_native_connection(self):
        self.start_endpoint()
        self.endpoint.close()
        end = time.monotonic() + 3
        target = f'socket:[{self.endpoint_inode}]'
        def present():
            return target in self.descriptor_targets(self.server.pid)
        while present() and time.monotonic() < end:
            self.supervisor.inspect(); time.sleep(.01)
        self.assertFalse(present())
        self.assertEqual(self.supervisor.inspect().role, binding.sec.SUPERVISOR)

    def test_unarmed_endpoint_expires_without_stopping_native_server(self):
        self.start_endpoint()
        self.endpoint.settimeout(12)
        self.assert_rejected('deadline')

    def test_endpoint_is_absent_from_independent_application_exec(self):
        self.start_endpoint()
        cookie = self.supervisor.issue(workspace=b'endpoint', application=b'exec-probe').cookie
        auth = b'\xff\xff'
        for part in (b'', str(self.display).encode(), b'MIT-MAGIC-COOKIE-1', cookie):
            auth += struct.pack('!H', len(part)) + part
        path = self.root / 'probe.auth'; path.write_bytes(auth); path.chmod(0o600)
        env = {**os.environ, 'DISPLAY': ':' + str(self.display), 'XAUTHORITY': str(path)}
        proc = subprocess.Popen([str(binding.resources.native.PROBE), '--id', 'endpoint-exec'],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        try:
            self.assertTrue(select.select([proc.stdout], [], [], 3)[0])
            ready = json.loads(proc.stdout.readline())
            self.assertEqual(ready['event'], 'ready')
            forbidden = {self.endpoint_inode, os.fstat(self.endpoint.fileno()).st_ino}
            fds = Path(f'/proc/{proc.pid}/fd')
            self.assertFalse(any(p.readlink().as_posix() in {f'socket:[{value}]' for value in forbidden}
                                 for p in fds.iterdir()))
            proc.stdin.write(b'quit 1\n'); proc.stdin.flush()
            self.assertEqual(proc.wait(timeout=3), 0)
        finally:
            if proc.poll() is None:
                proc.kill(); proc.wait(timeout=3)
            proc.stdin.close(); proc.stdout.close()
            diagnostic = proc.stderr.read().decode(errors='replace'); proc.stderr.close()
            self.assertFalse(diagnostic, diagnostic)


    def test_malformed_protobuf_closes_endpoint(self):
        self.start_endpoint()
        self.endpoint.sendall(b'\xff')
        self.assert_rejected()

    def test_uninherited_descriptor_number_rejects_at_native_startup(self):
        self.start(expect_ready=False, extra_server_args=('-spatial-observer-fd', str((1 << 31) - 1)))
        self.assertIn('must be a connected Unix seqpacket socket', self.log_path.read_text(errors='replace'))

    def test_endpoint_created_by_another_process_is_not_parent_authority(self):
        incoming, outgoing = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        pid = os.fork()
        if pid == 0:
            incoming.close()
            try:
                left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
                outgoing.sendmsg([b'pair'], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                                            array.array('i', [left.fileno(), right.fileno()]))])
                left.close(); right.close(); outgoing.close()
            except BaseException:
                os._exit(1)
            os._exit(0)
        outgoing.close()
        incoming.settimeout(3)
        fds = []
        try:
            data, ancillary, flags, _ = incoming.recvmsg(4, socket.CMSG_SPACE(2 * array.array('i').itemsize))
            self.assertEqual(data, b'pair'); self.assertFalse(flags & socket.MSG_CTRUNC)
            for level, kind, raw in ancillary:
                self.assertEqual((level, kind), (socket.SOL_SOCKET, socket.SCM_RIGHTS))
                values = array.array('i'); values.frombytes(raw); fds.extend(values)
            _, status = os.waitpid(pid, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 0)
            self.assertEqual(len(fds), 2)
            self.rejected_launch(fds[1], 'must belong to the launching parent')
        finally:
            incoming.close()
            for fd in fds: os.close(fd)


    def test_launch_supervisor_cannot_mint_another_supervisor_for_probe(self):
        self.start_endpoint()
        denied = self.supervisor.call(issue=binding.sec.Issue(role=binding.sec.SUPERVISOR))
        self.assertEqual(denied.code, binding.sec.FORBIDDEN)
        self.assertFalse(denied.HasField('credential'))
        allowed = self.connect(self.supervisor.issue(binding.sec.COMPOSITOR, b'', b'').cookie)
        self.assertEqual(allowed.inspect().role, binding.sec.COMPOSITOR)

    def test_descriptor_snapshot_ignores_only_descriptors_that_vanish(self):
        vanished = Mock(); vanished.readlink.side_effect = FileNotFoundError()
        live = Mock(); live.readlink.return_value = Path('socket:[123]')
        with patch.object(Path, 'iterdir', return_value=iter([vanished, live])):
            self.assertEqual(self.descriptor_targets(123), {'socket:[123]'})
        denied = Mock(); denied.readlink.side_effect = PermissionError('unreadable descriptor')
        with patch.object(Path, 'iterdir', return_value=iter([denied])):
            with self.assertRaises(PermissionError):
                self.descriptor_targets(123)


if __name__ == '__main__':
    suite = unittest.TestSuite(NativeObserverEndpointTests(name)
        for name in NativeObserverEndpointTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
