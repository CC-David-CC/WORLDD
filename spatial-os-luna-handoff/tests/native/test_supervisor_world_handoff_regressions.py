"""Prepared correction witnesses for sealed world/compositor startup and cleanup."""
import fcntl
import errno
import json
import os
from pathlib import Path
import select
import signal
import socket
import struct
import subprocess
import time
import unittest
from unittest.mock import patch

import test_supervisor_world_handoff as baseline
import core_pb2 as core
import launch_pb2 as launch
import process_scope_pb2 as process
import security_pb2 as security
from harness.oracles import PPM, pose
from harness.probe import Probe
from harness.wire import frame, make_request, result_value


class InputAwareNativeClient(baseline.supervisor.owned.existing.native.NativeClient):
    """Keep the same native connection across legitimate keymap notifications."""
    def request(self, opcode, payload=b'', minor=0):
        self.send(opcode, payload, minor)
        for _ in range(65):
            header = self.exact(32)
            if header[0] == 34:  # Core MappingNotify; never discard errors or other event types.
                if header[4] not in (0, 1, 2):
                    raise AssertionError('Invalid MappingNotify request')
                continue
            if header[0] != 1:
                raise AssertionError('Native request returned error/event: ' + str(header[:4]))
            if struct.unpack(self.endian + 'H', header[2:4])[0] != self.sequence % 65536:
                raise AssertionError('Native reply sequence mismatch')
            size = struct.unpack(self.endian + 'I', header[4:8])[0] * 4
            if size > 1048576:
                raise AssertionError('Excessive native reply')
            return header, self.exact(size)
        raise AssertionError('Excessive MappingNotify events before native reply')


class WorldHandoffRegressionTests(baseline.WorldNativeHandoffTests):
    def configuration(self, target=38):
        config = self.config()
        config.compositor_native_target_fd = target
        config.launch.CopyFrom(process.AuthorityProcessLaunch(
            executable=str(baseline.supervisor.owned.existing.PROBE.with_name('worldd')),
            arguments=['--state-dir', str(self.root / 'world'), '--native-security-fd', str(target)]))
        return config

    def launch_world(self, config=None, role=security.COMPOSITOR, start_server=True):
        config = config or self.configuration()
        original = baseline.supervisor.native_pb.NativeScopeConfig
        def native(*args, **kwargs):
            kwargs['role'] = role
            return original(*args, **kwargs)
        with patch.object(baseline.supervisor.native_pb, 'NativeScopeConfig', side_effect=native):
            return self.supervisor_process(config, start_server=start_server)

    def world_reply(self, method, **arguments):
        self.sequence += 1
        request = make_request(method, arguments)
        request.request_id = self.sequence
        self.world.stdin.write(frame(request)); self.world.stdin.flush()
        reply = self.message(self.world.stdout, core.Reply)
        self.assertEqual(reply.request_id, self.sequence)
        return reply

    def world_call(self, method, **arguments):
        reply = self.world_reply(method, **arguments)
        self.assertFalse(reply.HasField('error'), reply)
        return result_value(reply)

    def started_world(self):
        self.world, self.channel = self.launch_world()
        self.sequence = 0
        reply = self.message(self.channel, launch.AuthorityLaunchReply)
        self.assertTrue(reply.HasField('view'), reply)
        self.assertEqual(reply.view.record.stage, launch.START_ATTEMPTED)
        self.assertEqual(reply.view.record.native.role, security.COMPOSITOR)
        self.assertFalse(reply.view.HasField('ready'))
        self.ids = self.world_call('runtime.identities')
        self.assertEqual(self.ids['display'], ':' + str(self.display))
        pids = {self.ids['worldd_pid'], self.ids['spatialwm_pid']}
        self.assertEqual(len(pids), 2)
        self.assertTrue(all(pid > 1 for pid in pids))
        members = (self.cgroup / reply.view.record.process.scope_name / 'cgroup.procs').read_text().split()
        self.assertEqual(set(map(int, members)), pids)
        self.pins = {pid: os.pidfd_open(pid) for pid in pids}
        for pin in self.pins.values():
            self.addCleanup(os.close, pin)
            self.assertEqual(select.select([pin], [], [], 0)[0], [])
        return reply.view.record

    def stop_world(self):
        reply = self.control(self.channel, stop=launch.AuthorityLaunchQuery())
        self.assertEqual(reply.view.record.stage, launch.OWNED_SCOPES_STOPPED)
        self.assertEqual(self.world.wait(timeout=5), 0)
        self.checked_cleanup()
        for pin in self.pins.values():
            self.assertEqual(select.select([pin], [], [], 0)[0], [pin])

    def until(self, function, predicate):
        deadline = time.monotonic() + 5
        while True:
            value = function()
            if predicate(value):
                return value
            self.assertLess(time.monotonic(), deadline, repr(value))
            time.sleep(.01)

    def application_scene(self, actor_grants=('interact',)):
        grant = self.supervisor.issue(workspace=b'one', application=b'independent-witness')
        data = b'\xff\xff'
        for part in (b'', str(self.display).encode(), b'MIT-MAGIC-COOKIE-1', grant.cookie):
            data += struct.pack('!H', len(part)) + part
        auth = self.root / 'independent-application.auth'
        auth.write_bytes(data); auth.chmod(0o600)
        # This authority belongs only to the independently launched application witness.
        # The already-running supervisor/world/compositor never receives this environment.
        with patch.dict(os.environ, XAUTHORITY=str(auth),
                        SPATIAL_XPROBE=str(baseline.supervisor.owned.existing.PROBE.with_name('spatial-xprobe'))):
            self.probe = Probe(self.root, display=':' + str(self.display))
        self.addCleanup(self.probe.close)
        surface = self.until(lambda: self.world_call('surface.find', probe_id=self.probe.id),
                             lambda value: value['surface'] is not None)['surface']
        self.assertEqual(surface['pixels'], [320, 240])
        self.assertTrue(surface['mapped'])
        room = self.world_call('world.info')['entry_room']
        self.world_call('surface.place', id=surface['id'], room=room, pose=pose([0, 0, -2]), size=[2, 2])
        actor = self.world_call('actor.open', id='witness-controller', grants=list(actor_grants))
        self.actor = actor['token']; self.actor_sequence = 0
        self.application_cookie = grant.cookie
        self.native_witness = InputAwareNativeClient(self.display, grant.cookie)
        self.clients.append(self.native_witness)
        native = self.native_witness.inspect()
        self.native_identity = native
        self.assertEqual(native.role, security.APPLICATION)
        self.assertEqual((native.workspace, native.application), (b'one', b'independent-witness'))
        return surface

    def act(self, operation, **arguments):
        self.actor_sequence += 1
        return self.world_call('actor.act', token=self.actor, seq=self.actor_sequence, op=operation, args=arguments)

    def hold_input(self, surface):
        self.assertEqual(self.act('aim', direction=[0, 0, -1])['hit']['id'], surface['id'])
        self.act('pointer', button=1, pressed=True)
        self.act('key', keysym='a', pressed=True)
        self.world_call('runtime.fence')
        events = self.probe.input_events()
        self.assertEqual([event['pressed'] for event in events if event['event'] == 'key'], [True])
        self.assertEqual([event['pressed'] for event in events if event['event'] == 'button'], [True])

    def released_input(self):
        # Checked native cleanup has already completed. No sleep grants extra cleanup time.
        events = self.probe.input_events()
        self.assertEqual([event['pressed'] for event in events if event['event'] == 'key'], [True, False])
        self.assertEqual([event['pressed'] for event in events if event['event'] == 'button'], [True, False])
        self.assertEqual(self.native_witness.inspect(), self.native_identity)
        self.assertIsNone(self.probe.proc.poll(), 'Independent application must survive world cleanup')

    def test_owned_world_renders_real_application_pixels_and_releases_held_input(self):
        self.started_world()
        surface = self.application_scene()
        color = [19, 117, 231]
        self.probe.command('paint', *color)
        self.world_call('runtime.fence')
        capture = self.world_call('render.capture', token=self.actor, width=128, height=128)
        path = Path(capture['path']).resolve()
        self.assertTrue(path.is_relative_to((self.root / 'world').resolve()))
        image = PPM(path)
        self.assertEqual((image.width, image.height), (128, 128))
        self.assertTrue(all(abs(a-b) <= 3 for a, b in zip(image.mean_patch(64, 64), color)))
        self.hold_input(surface)
        self.stop_world()
        self.released_input()

    def test_diagnostic_interact_actor_can_capture_and_aim_unlabelled_surface(self):
        self.started_world()
        surface = self.application_scene(actor_grants=('interact',))

        capture = self.world_call('render.capture', token=self.actor, width=128, height=128)
        self.assertGreater(capture['frame_id'], 0)
        self.assertTrue(os.path.isfile(capture['path']))

        self.actor_sequence += 1
        aim = self.world_call('actor.act', token=self.actor, seq=self.actor_sequence,
                              op='aim', args={'direction': [0, 0, -1]})
        self.assertEqual(aim['hit']['id'], surface['id'])
        self.assertIsNone(self.world.poll(), 'Diagnostic observation must keep worldd alive')
        self.assertIsNone(self.probe.proc.poll(), 'Diagnostic observation must preserve the independent application')

    def test_world_death_cleans_compositor_and_held_input_without_revoking_independent_application(self):
        self.started_world()
        surface = self.application_scene(); self.hold_input(surface)
        os.kill(self.ids['worldd_pid'], signal.SIGKILL)
        reply = self.message(self.channel, launch.AuthorityLaunchReply)
        self.assertTrue(reply.HasField('error'), reply)
        self.assertEqual(self.world.wait(timeout=5), 2)
        self.checked_cleanup()
        for pin in self.pins.values():
            self.assertEqual(select.select([pin], [], [], 0)[0], [pin])
        self.released_input()

    def test_owned_native_credential_revocation_disconnects_compositor_and_preserves_cleanup_uncertainty(self):
        record = self.started_world()
        issuer_before = self.activation_rows()
        surface = self.application_scene()
        self.hold_input(surface)
        controller = InputAwareNativeClient(self.display, self.cookie)
        self.clients.append(controller)
        result = controller.call(revoke=security.Revoke(credential_id=record.native.credential_id))
        self.assertEqual(result.code, security.OK)
        self.assertTrue(result.HasField('revoked') and result.revoked)
        compositor_pin = self.pins[self.ids['spatialwm_pid']]
        self.assertEqual(select.select([compositor_pin], [], [], 5)[0], [compositor_pin])
        reply = self.message(self.channel, launch.AuthorityLaunchReply)
        self.assertTrue(reply.HasField('error'), reply)
        self.assertEqual(self.world.wait(timeout=5), 2)
        # Another supervisor revoked the credential. NOT_FOUND is not a native cleanup receipt.
        final = self.read_record()
        self.assertEqual(final.stage, launch.CLEANUP_PENDING)
        self.assertEqual(final.native, record.native)
        self.assertTrue(final.HasField('process_stopped'))
        self.assertEqual(final.process_stopped.identity, record.process)
        self.assertFalse(final.HasField('native_stopped'))
        self.assertFalse((self.cgroup / record.process.scope_name).exists())
        self.assertEqual(self.activation_rows(), issuer_before)
        for pin in self.pins.values():
            self.assertEqual(select.select([pin], [], [], 0)[0], [pin])
        self.released_input()

    def test_world_and_compositor_consume_the_native_configuration_descriptor(self):
        import compositor_native_pb2 as compositor
        self.start()
        credential = self.supervisor.issue(security.COMPOSITOR, b'', b'')
        config = compositor.NativeCompositorConfig(protocol_version=1, display_number=self.display,
            compositor_cookie=credential.cookie, server_id=self.supervisor.inspect().server_id,
            server_pid=self.server.pid, server_uid=os.getuid())
        fd = os.memfd_create('world-config-observer', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        self.addCleanup(os.close, fd)
        os.write(fd, config.SerializeToString())
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
        info = os.fstat(fd); identity = [info.st_dev, info.st_ino]
        logpath = self.root / 'direct-world.log'; log = logpath.open('wb')
        self.world = subprocess.Popen([
            str(baseline.supervisor.owned.existing.PROBE.with_name('worldd')),
            '--state-dir', str(self.root / 'world'), '--native-security-fd', str(fd)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, pass_fds=(fd,),
            env={name: os.environ[name] for name in ('PATH', 'LANG', 'ASAN_OPTIONS', 'UBSAN_OPTIONS') if name in os.environ})
        def cleanup():
            if self.world.stdin is not None:
                self.world.stdin.close(); self.world.stdin = None
            if self.world.poll() is None:
                try:
                    self.world.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.world.kill(); self.world.wait(timeout=3)
            self.world.stdout.close(); log.close()
            text = logpath.read_text(errors='replace')
            if os.environ.get('SPATIAL_SECURITY_EVIDENCE'):
                target = Path(os.environ['SPATIAL_SECURITY_EVIDENCE']); target.mkdir(parents=True, exist_ok=True)
                (target / (self._testMethodName + '-direct-world.log')).write_text(text)
            for marker in ('AddressSanitizer', 'LeakSanitizer', 'runtime error:'):
                self.assertNotIn(marker, text)
        self.addCleanup(cleanup)
        self.sequence = 0
        ids = self.world_call('runtime.identities')
        self.assertEqual(ids['worldd_pid'], self.world.pid)
        self.assertGreater(ids['spatialwm_pid'], 1)
        self.assertNotEqual(ids['spatialwm_pid'], self.world.pid)
        observer_code = '''import json,os,sys
root='/proc/'+str(int(sys.argv[1]))+'/fd/'
rows={}
for name in os.listdir(root):
 try:
  info=os.stat(root+name)
 except FileNotFoundError:
  continue
 rows[name]=[info.st_dev,info.st_ino]
print(json.dumps(rows))
'''
        pins = {}
        for pid in (os.getpid(), ids['worldd_pid'], ids['spatialwm_pid']):
            pin = os.pidfd_open(pid); self.addCleanup(os.close, pin); pins[pid] = pin
            self.assertEqual(select.select([pin], [], [], 0)[0], [])
            observer = subprocess.run(['sudo', '-n', 'python3', '-c', observer_code,
                str(pid)], capture_output=True, text=True, timeout=5, check=True)
            self.assertEqual(select.select([pin], [], [], 0)[0], [])
            descriptors = json.loads(observer.stdout)
            self.assertTrue({'0', '1', '2'}.issubset(descriptors))
            if pid == os.getpid():
                self.assertIn(identity, descriptors.values(), 'Observer must detect the retained parent control descriptor')
            else:
                self.assertNotIn(identity, descriptors.values(), 'Native configuration survived in a child under some descriptor number')
        self.world.stdin.close(); self.world.stdin = None
        self.assertEqual(self.world.wait(timeout=5), 0)
        for pid in (ids['worldd_pid'], ids['spatialwm_pid']):
            self.assertEqual(select.select([pins[pid]], [], [], 0)[0], [pins[pid]])

    def test_invalid_compositor_targets_fail_before_scope_ownership(self):
        for target in (0, 1, 2, 37, 256):
            with self.subTest(target=target):
                proc, _ = self.launch_world(self.configuration(target), start_server=not hasattr(self, 'server'))
                self.assertEqual(proc.wait(timeout=5), 2)
                self.assertFalse((self.journal / 'launch.db').exists())
                self.assertFalse(self.child_config.exists())

    def native_configuration(self):
        import compositor_native_pb2 as compositor
        credential = self.supervisor.issue(security.COMPOSITOR, b'', b'')
        return compositor.NativeCompositorConfig(protocol_version=1, display_number=self.display,
            compositor_cookie=credential.cookie, server_id=self.supervisor.inspect().server_id,
            server_pid=self.server.pid, server_uid=os.getuid())

    def direct_compositor(self, payload, *, sealed=True, display=None, ready=False):
        fd = os.memfd_create('compositor-validation', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        os.write(fd, payload)
        if sealed:
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS,
                fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
        argv = [str(baseline.supervisor.owned.existing.PROBE.with_name('spatialwm')),
                '--state-dir', str(self.root / 'direct-renderer'), '--native-security-fd', str(fd)]
        if display is not None:
            argv += ['--display', display]
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, pass_fds=(fd,),
                env={name: os.environ[name] for name in ('PATH', 'LANG', 'ASAN_OPTIONS', 'UBSAN_OPTIONS') if name in os.environ})
        finally:
            os.close(fd)
        try:
            if ready:
                self.assertTrue(self.message(proc.stdout, core.RenderResult).HasField('ready'))
        finally:
            try:
                stdout, stderr = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill(); stdout, stderr = proc.communicate(timeout=3)
            diagnostic = stderr.decode(errors='replace')
            if os.environ.get('SPATIAL_SECURITY_EVIDENCE'):
                target = Path(os.environ['SPATIAL_SECURITY_EVIDENCE']); target.mkdir(parents=True, exist_ok=True)
                with (target / (self._testMethodName + '-direct-compositor.log')).open('a') as log:
                    log.write(diagnostic + '\n')
            for marker in ('AddressSanitizer', 'LeakSanitizer', 'runtime error:'):
                self.assertNotIn(marker, diagnostic)
        self.assertEqual(proc.returncode, 0 if ready else 2, diagnostic)
        self.assertEqual(stdout, b'', 'Rejected configuration must not publish renderer readiness')
        if not ready:
            self.assertIn('Native compositor configuration', diagnostic)

    def direct_control(self):
        self.start()
        (self.root / 'direct-renderer').mkdir()
        self.direct_compositor(self.native_configuration().SerializeToString(), ready=True)

    def test_compositor_rejects_malformed_and_incomplete_native_configurations(self):
        self.direct_control()
        valid = self.native_configuration()
        payloads = [('empty', b''), ('truncated', b'\x0a\xff'),
                    ('oversized', b'\x00' * 1048577),
                    ('unknown-field', valid.SerializeToString() + b'\xf8\x07\x01')]
        for field in ('protocol_version', 'display_number', 'compositor_cookie', 'server_id', 'server_pid', 'server_uid'):
            item = type(valid)(); item.CopyFrom(valid); item.ClearField(field)
            payloads.append(('missing-' + field, item.SerializeToString()))
        for field, value in (('protocol_version', 2), ('compositor_cookie', b'x'),
                             ('server_id', b'x'), ('server_pid', 0)):
            item = type(valid)(); item.CopyFrom(valid); setattr(item, field, value)
            payloads.append(('invalid-' + field, item.SerializeToString()))
        for name, payload in payloads:
            with self.subTest(case=name):
                self.direct_compositor(payload)

    def test_compositor_rejects_unsealed_native_configuration(self):
        self.direct_control()
        self.direct_compositor(self.native_configuration().SerializeToString(), sealed=False)

    def test_compositor_rejects_wrong_server_pins_and_native_roles(self):
        self.direct_control()
        valid = self.native_configuration()
        for field, value in (('server_id', os.urandom(32)), ('server_pid', os.getpid()),
                             ('server_uid', os.getuid() + 1)):
            with self.subTest(field=field):
                item = type(valid)(); item.CopyFrom(valid); setattr(item, field, value)
                self.direct_compositor(item.SerializeToString())
        for role in (security.BROKER, security.SUPERVISOR, security.APPLICATION):
            with self.subTest(role=role):
                item = type(valid)(); item.CopyFrom(valid)
                item.compositor_cookie = self.cookie if role == security.SUPERVISOR else self.supervisor.issue(
                    role, b'one' if role == security.APPLICATION else b'',
                    b'witness' if role == security.APPLICATION else b'').cookie
                self.direct_compositor(item.SerializeToString())

    def test_compositor_rejects_display_conflict(self):
        self.direct_control()
        self.direct_compositor(self.native_configuration().SerializeToString(), display=':' + str(self.display + 1))

    def test_wrong_server_receives_no_authentication_bytes(self):
        self.direct_control()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.settimeout(5)
        for number in range(60000, 65536):
            path = Path('/tmp/.X11-unix') / ('X' + str(number))
            try:
                listener.bind(str(path))
                break
            except OSError as error:
                if error.errno != errno.EADDRINUSE:
                    raise
        else:
            self.fail('No free disposable fake-server socket')
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        listener.listen(1)
        config = self.native_configuration()
        config.display_number = number
        # Keep the actual X server PID and UID. This listener belongs to the witness.
        self.direct_compositor(config.SerializeToString())
        peer, _ = listener.accept()
        with peer:
            peer.settimeout(5)
            self.assertEqual(peer.recv(4096), b'', 'Credentials reached the wrong native server')

    def test_input_aware_reader_preserves_reply_validation(self):
        notification = bytes([34, 0]) + b'\0' * 30
        reply = b'\x01\0' + struct.pack('<H', 1) + b'\0' * 28
        cases = [('valid', notification + reply, False),
                 ('error', b'\0' * 32, True),
                 ('unrelated-event', bytes([2]) + b'\0' * 31, True),
                 ('bad-sequence', reply[:2] + struct.pack('<H', 2) + reply[4:], True),
                 ('bad-mapping-kind', bytes([34, 0, 0, 0, 3]) + b'\0' * 27, True),
                 ('excessive-events', notification * 65, True),
                 ('truncated', notification + reply[:3], True)]
        for name, data, reject in cases:
            with self.subTest(case=name):
                parent, peer = socket.socketpair()
                with parent, peer:
                    parent.settimeout(1)
                    client = InputAwareNativeClient.__new__(InputAwareNativeClient)
                    client.stream = parent; client.endian = '<'; client.sequence = 0
                    peer.sendall(data); peer.shutdown(socket.SHUT_WR)
                    if reject:
                        with self.assertRaises((AssertionError, EOFError)):
                            client.request(43)
                    else:
                        self.assertEqual(client.request(43), (reply, b''))

    def test_broker_and_compositor_handoffs_cannot_share_one_native_scope(self):
        config = self.configuration(); config.broker_native_target_fd = 39
        proc, _ = self.launch_world(config)
        self.assertEqual(proc.wait(timeout=5), 2)
        self.assertFalse((self.journal / 'launch.db').exists())
        self.assertFalse(self.child_config.exists())

    def test_wrong_native_roles_fail_before_scope_ownership(self):
        for role in (security.BROKER, security.SUPERVISOR, security.APPLICATION):
            with self.subTest(role=role):
                proc, _ = self.launch_world(role=role, start_server=not hasattr(self, 'server'))
                self.assertEqual(proc.wait(timeout=5), 2)
                self.assertFalse((self.journal / 'launch.db').exists())
                self.assertFalse(self.child_config.exists())
        diagnostic = '\n'.join(path.read_text(errors='replace') for path in self.root.glob('supervisor-*.log'))
        self.assertEqual(diagnostic.count('Compositor handoff requires a compositor native scope'), 3)


if __name__ == '__main__':
    suite = unittest.TestSuite(WorldHandoffRegressionTests(name)
        for name in WorldHandoffRegressionTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
