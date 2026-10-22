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

    def arm_context(self):
        # These native-only fixtures exercise pin validation. Real worldd principal
        # bindings remain a separate mandatory qualification requirement.
        request = self.setup_context()
        self.pins = self.send_setup(request)
        self.call(button=binding.inp.Button(seat=self.seat.seat,
                  target=self.target(self.window), x=4, y=5, button=1,
                  pressed=True, focus=True))
        self.key(38, True); self.key(50, True)
        import native_input_observer_v2_pb2 as observer
        arm = observer.ObserverArmV2(
            session_id=request.session_id, arm_id=request.arm_id,
            arm_monotonic_ns=time.monotonic_ns(), deadline_monotonic_ns=request.deadline_monotonic_ns,
            old_world_actor_id=1, peer_world_actor_id=2,
            old_native_seat_id=self.seat.seat, peer_native_seat_id=self.second.seat,
            presentation_generation=request.presentation_generation,
            expected_xi2_event_type=4, expected_detail=1,
            expected_held_keys=[38, 50], expected_held_buttons=[1], expected_modifier_keycode=50)
        arm.old_actor.issuer_id = arm.peer_actor.issuer_id = b'i' * 32
        arm.old_actor.actor_id = b'o' * 32; arm.peer_actor.actor_id = b'p' * 32
        arm.expected_application.CopyFrom(request.expected_application)
        arm.expected_window.CopyFrom(self.pins.app_target)
        arm.expected_window_owner.CopyFrom(self.pins.app_target_owner)
        arm.expected_seat_owner.CopyFrom(self.pins.compositor_seat_owner)
        arm.expected_recipient.CopyFrom(self.pins.probe_marker.privileged_owner)
        arm.expected_grab_owner.CopyFrom(arm.expected_recipient)
        arm.old_actor_devices.extend(self.pins.old_actor_devices)
        arm.peer_actor_devices.extend(self.pins.peer_actor_devices)
        return arm

    def send_arm(self, arm, suffix=b''):
        self.endpoint.sendall(control.ObserverControlEnvelopeV2(arm=arm).SerializeToString(deterministic=True) + suffix)

    def invalid_arm(self, mutate):
        arm = self.arm_context()
        mutate(arm)
        self.send_arm(arm)
        self.assert_rejected('invalid arm or native pin')
        # An evidence refusal cannot silently revoke the application's control.
        self.assertTrue(self.held(self.owner, 38))
        self.assertTrue(self.held(self.owner, 50))
        self.cancel()

    def test_live_arm_acknowledges_exact_command_without_changing_native_input(self):
        arm = self.arm_context()
        self.send_arm(arm)
        raw = self.endpoint.recv(4097)
        self.assertTrue(raw, self.log_path.read_text(errors='replace'))
        reply = control.ObserverEndpointReplyV2.FromString(raw)
        self.assertEqual(reply.WhichOneof('message'), 'arm_accepted', reply)
        self.assertEqual(reply.arm_accepted.accepted_command, arm)
        self.assertEqual(self.focus(self.owner), self.window)
        self.assertTrue(self.held(self.owner, 38)); self.assertTrue(self.held(self.owner, 50))
        status = self.call(status=binding.inp.Seat(id=self.seat.seat))
        self.assertEqual(status.held_buttons, 1 << 1)
        self.cancel()
        self.click(self.second); self.key(38, True, self.second)
        self.assertTrue(self.held(self.owner, 38)); self.cancel(self.second)

    def test_arm_replay_closes_only_private_channel(self):
        arm = self.arm_context(); self.send_arm(arm)
        self.assertTrue(self.endpoint.recv(4097))
        self.send_arm(arm); self.assert_rejected('one-shot arm replay')
        self.assertTrue(self.held(self.owner, 38)); self.cancel()

    def test_arm_wrong_session_rejects(self):
        self.invalid_arm(lambda a: setattr(a, 'session_id', os.urandom(16)))

    def test_arm_wrong_arm_id_rejects(self):
        self.invalid_arm(lambda a: setattr(a, 'arm_id', os.urandom(16)))

    def test_arm_actor_alias_rejects(self):
        self.invalid_arm(lambda a: a.peer_actor.CopyFrom(a.old_actor))

    def test_arm_short_principal_rejects(self):
        self.invalid_arm(lambda a: setattr(a.old_actor, 'actor_id', b'short'))

    def test_arm_world_actor_alias_rejects(self):
        self.invalid_arm(lambda a: setattr(a, 'peer_world_actor_id', a.old_world_actor_id))

    def test_arm_zero_world_actor_rejects(self):
        self.invalid_arm(lambda a: setattr(a, 'old_world_actor_id', 0))

    def test_arm_wrong_seat_rejects(self):
        self.invalid_arm(lambda a: setattr(a, 'old_native_seat_id', a.peer_native_seat_id))

    def test_arm_wrong_application_rejects(self):
        self.invalid_arm(lambda a: setattr(a.expected_application, 'application_id', b'wrong'))

    def test_arm_wrong_presentation_rejects(self):
        self.invalid_arm(lambda a: setattr(a, 'presentation_generation', a.presentation_generation + 1))

    def test_arm_wrong_window_epoch_rejects(self):
        self.invalid_arm(lambda a: setattr(a.expected_window, 'native_object_epoch', a.expected_window.native_object_epoch + 1))

    def test_arm_wrong_seat_owner_rejects(self):
        self.invalid_arm(lambda a: a.expected_seat_owner.CopyFrom(a.expected_recipient))

    def test_arm_wrong_window_owner_rejects(self):
        self.invalid_arm(lambda a: a.expected_window_owner.CopyFrom(a.expected_recipient))

    def test_arm_wrong_probe_rejects(self):
        self.invalid_arm(lambda a: a.expected_recipient.CopyFrom(a.expected_seat_owner))

    def test_arm_wrong_grab_owner_rejects(self):
        self.invalid_arm(lambda a: a.expected_grab_owner.CopyFrom(a.expected_window_owner))

    def test_arm_wrong_device_epoch_rejects(self):
        self.invalid_arm(lambda a: setattr(a.old_actor_devices[0], 'native_object_epoch', a.old_actor_devices[0].native_object_epoch + 1))

    def test_arm_wrong_attachment_rejects(self):
        self.invalid_arm(lambda a: setattr(a.peer_actor_devices[1], 'paired_or_attached', a.old_actor_devices[0].device_id))

    def test_arm_duplicate_device_rejects(self):
        self.invalid_arm(lambda a: a.old_actor_devices[1].CopyFrom(a.old_actor_devices[0]))

    def test_arm_missing_device_rejects(self):
        self.invalid_arm(lambda a: a.peer_actor_devices.pop())

    def test_arm_wrong_event_rejects(self):
        self.invalid_arm(lambda a: setattr(a, 'expected_xi2_event_type', 5))

    def test_arm_out_of_range_peer_button_rejects(self):
        self.invalid_arm(lambda a: setattr(a, 'expected_detail', 32))

    def test_arm_unsorted_keys_rejects(self):
        self.invalid_arm(lambda a: a.expected_held_keys.reverse())

    def test_arm_missing_modifier_rejects(self):
        self.invalid_arm(lambda a: setattr(a, 'expected_modifier_keycode', 39))

    def test_arm_wrong_held_keys_rejects(self):
        self.invalid_arm(lambda a: a.expected_held_keys.__setitem__(0, 39))

    def test_arm_wrong_held_buttons_rejects(self):
        self.invalid_arm(lambda a: a.expected_held_buttons.__setitem__(0, 2))

    def test_arm_deadline_change_rejects(self):
        self.invalid_arm(lambda a: setattr(a, 'deadline_monotonic_ns', a.deadline_monotonic_ns + 1))

    def test_arm_future_time_rejects(self):
        self.invalid_arm(lambda a: setattr(a, 'arm_monotonic_ns', a.deadline_monotonic_ns))

    def test_arm_before_setup_time_rejects(self):
        self.invalid_arm(lambda a: setattr(a, 'arm_monotonic_ns', 1))

    def test_arm_unknown_fields_rejects(self):
        arm = self.arm_context(); self.send_arm(arm, b'\x18\x01')
        self.assert_rejected('invalid arm or native pin'); self.cancel()

    def test_arm_retired_peer_seat_rejects(self):
        arm = self.arm_context(); self.call(close=binding.inp.Seat(id=self.second.seat))
        self.send_arm(arm); self.assert_rejected('invalid arm or native pin'); self.cancel()

    def test_arm_resized_target_after_setup_rejects(self):
        arm = self.arm_context()
        # The compositor owns SubstructureRedirect. Its own configure request
        # performs the resize; an application request would only be redirected.
        self.assertIsNone(self.compositor.operation(12, self.compositor.pack('IHHI', self.window, 4, 0, 130)))
        self.assertNotEqual(self.target(self.window).generation, arm.expected_window.spatial_generation)
        self.send_arm(arm); self.assert_rejected('invalid arm or native pin')

    def test_redirected_owner_resize_preserves_live_arm_target(self):
        arm = self.arm_context()
        self.assertIsNone(self.owner.operation(12, self.owner.pack('IHHI', self.window, 4, 0, 130)))
        self.assertEqual(self.target(self.window).generation, arm.expected_window.spatial_generation)
        self.send_arm(arm)
        raw = self.endpoint.recv(4097)
        self.assertTrue(raw, self.log_path.read_text(errors='replace'))
        reply = control.ObserverEndpointReplyV2.FromString(raw)
        self.assertEqual(reply.WhichOneof('message'), 'arm_accepted', reply)
        self.assertEqual(reply.arm_accepted.accepted_command, arm)
        self.cancel()

    def test_arm_destroyed_target_after_setup_rejects(self):
        arm = self.arm_context()
        self.assertIsNone(self.owner.operation(4, self.owner.pack('I', self.window)))
        self.send_arm(arm); self.assert_rejected('invalid arm or native pin')

    def test_arm_closed_probe_marker_rejects(self):
        arm = self.arm_context(); self.probe.close()
        self.supervisor.inspect()
        self.send_arm(arm); self.assert_rejected('invalid arm or native pin'); self.cancel()

    def test_arm_native_key_release_after_setup_rejects(self):
        arm = self.arm_context(); self.key(50, False)
        self.send_arm(arm); self.assert_rejected('invalid arm or native pin'); self.cancel()

    def test_arm_peer_already_controls_another_application_rejects(self):
        arm = self.arm_context(); self.click(self.second, self.other_window)
        self.send_arm(arm); self.assert_rejected('invalid arm or native pin')
        self.cancel(); self.cancel(self.second)

    def test_setup_deadline_expires_without_rearming_startup_budget(self):
        request = self.setup_context()
        request.deadline_monotonic_ns = time.monotonic_ns() + 250_000_000
        self.send_setup(request)
        start = time.monotonic()
        self.assert_rejected('deadline')
        self.assertLess(time.monotonic() - start, 1.5)

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
