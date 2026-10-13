"""Production host entrypoint against the real issuer, X server, broker, worlds, and compositor."""
import fcntl
import os
from pathlib import Path
import select
import signal
import socket
import sqlite3
import struct
import subprocess
import sys
import time
import unittest
from google.protobuf import text_format

# Imported native fixtures consume the five arguments before the host binary.
# Preserve the original CLI entry before loading those fixtures.
if len(sys.argv) != 7:
    raise SystemExit('Expected SOURCE, launch probe, security auth, identityd, issuer client, and host session')
HOST_SESSION_BINARY = Path(sys.argv[6]).resolve()
if not HOST_SESSION_BINARY.is_file() or not os.access(HOST_SESSION_BINARY, os.X_OK):
    raise SystemExit('Production host-session executable is missing or not executable')

import test_host_actor_controller as existing
import actor_session_client_pb2 as actor_client
import compositor_native_pb2 as compositor
import core_pb2 as core
import host_session_pb2 as host
import host_actor_controller_pb2 as host_actor
import launch_pb2 as launch
import native_scope_pb2 as native
import security_pb2 as security
import world_session_pb2 as world_session
from harness.wire import frame


def sealed(data, name):
    fd = os.memfd_create(name, os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    os.write(fd, data)
    fcntl.fcntl(fd, fcntl.F_ADD_SEALS,
                fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
    return fd


def exact(sock, length):
    result = bytearray()
    while len(result) < length:
        data = sock.recv(length - len(result))
        if not data:
            raise EOFError('production host closed a partial reply')
        result.extend(data)
    return bytes(result)


class ProductionHostSessionArgumentTests(unittest.TestCase):
    def test_production_binary_survives_native_fixture_argument_consumption(self):
        self.assertEqual(len(sys.argv), 2)
        self.assertEqual(HOST_SESSION_BINARY, Path(sys.argv[1]).resolve())
        self.assertTrue(os.access(HOST_SESSION_BINARY, os.X_OK))


class ProductionHostSessionTests(existing.HostActorControllerNativeTests):
    """Reuse genuine host fixtures; replace their controller executable with the entrypoint."""

    def world_id(self, config):
        result = subprocess.run([config.launch.executable, '--state-dir',
                                 config.launch.arguments[1]],
                                input=frame(core.Request(protocol_version=1, request_id=1,
                                                         world_info=core.WorldInfoRequest())),
                                capture_output=True, timeout=10, check=True)
        size, = struct.unpack('!I', result.stdout[:4])
        return core.Reply.FromString(result.stdout[4:4 + size]).world_info.world_id

    def make_native(self, role):
        value = native.NativeScopeConfig(protocol_version=1, display_number=self.display,
            supervisor_cookie=self.cookie, server_id=self.supervisor.inspect().server_id,
            server_pid=self.server.pid, server_uid=os.getuid(), launch_id=os.urandom(32), role=role)
        return sealed(value.SerializeToString(), 'production-host-native')

    def make_world(self, suffix, admitted=True):
        self.world_instance_suffix = suffix
        had_world_admitted = hasattr(self, 'world_admitted')
        previous_world_admitted = getattr(self, 'world_admitted', None)
        self.world_admitted = admitted
        try:
            config = self.world_config()
        finally:
            if had_world_admitted:
                self.world_admitted = previous_world_admitted
            else:
                del self.world_admitted
        world_id = self.world_id(config)
        server_tls = world_session.WorldActorTlsConfig(protocol_version=1,
            certificate_path=str(self.issuer.server_cert), private_key_path=str(self.issuer.server_key),
            client_ca_path=str(self.issuer.service_cert))
        server_tls_path = self.root / ('host-server-tls' + suffix + '.textproto')
        server_tls_path.write_text(text_format.MessageToString(server_tls)); server_tls_path.chmod(0o600)
        config.launch.arguments.extend(['--actor-tls-config', str(server_tls_path)])
        if not admitted:
            args = list(config.launch.arguments)
            policy_path = Path(args[args.index('--identity-config') + 1])
            policy = text_format.Parse(policy_path.read_text(), world_session.WorldIdentityConfig())
            del policy.admitted_actor_ids[:]
            del policy.actor_grants[:]
            policy_path.write_text(text_format.MessageToString(policy))
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0)); listener.listen(8)
        tls = actor_client.ActorSessionClientTlsConfig(protocol_version=1,
            address='127.0.0.1', port=listener.getsockname()[1], server_name='issuer.test',
            expected_world_id=world_id, certificate_path=str(self.issuer.service_cert),
            private_key_path=str(self.issuer.service_key), server_ca_path=str(self.issuer.server_cert),
            actor_signing_key_path=str(self.issuer.actor_key), actor_credential=self.actor.credential)
        tls_path = self.root / ('host-client' + suffix + '.textproto')
        tls_path.write_text(text_format.MessageToString(tls)); tls_path.chmod(0o600)
        native_fd = self.make_native(security.COMPOSITOR)
        private = self.root / ('host-world' + suffix + '.owned')
        return host.HostWorld(world_id=world_id, private_config_path=str(private), supervisor=config,
                              native_fd=native_fd, actor_listener_fd=listener.fileno(),
                              actor_tls_config=str(tls_path)), listener, native_fd

    def prepare(self, second=False, denied=False, failed_second=False):
        self.start()
        broker_config = self.broker_config()
        self.host_broker_journal = Path(broker_config.journal_directory)
        worlds = [self.make_world('')]
        if second or failed_second: worlds.append(self.make_world('-b'))
        if denied: worlds.append(self.make_world('-denied', admitted=False))
        if failed_second:
            worlds[1][0].world_id = 'wrong-world'
            tls = text_format.Parse(Path(worlds[1][0].actor_tls_config).read_text(),
                                    actor_client.ActorSessionClientTlsConfig())
            tls.expected_world_id = 'wrong-world'
            Path(worlds[1][0].actor_tls_config).write_text(text_format.MessageToString(tls))
        broker_native = self.make_native(security.BROKER)
        grant = self.supervisor.issue(security.COMPOSITOR, b'', b'')
        graphics = compositor.NativeCompositorConfig(protocol_version=1, display_number=self.display,
            compositor_cookie=grant.cookie, server_id=self.supervisor.inspect().server_id,
            server_pid=self.server.pid, server_uid=os.getuid())
        graphics_fd = sealed(graphics.SerializeToString(), 'production-host-graphics')
        graphics_state = self.root / 'production-host-graphics'; graphics_state.mkdir(mode=0o700)
        config = host.HostSessionConfig(protocol_version=1,
            supervisor_executable=str(existing.fixture.base.baseline.supervisor.owned.existing.PROBE.with_name('spatial-authority-supervisor')),
            compositor_executable=str(existing.fixture.base.baseline.supervisor.owned.existing.PROBE.with_name('spatialwm')),
            compositor_state_path=str(graphics_state), broker_config_path=str(self.root / 'host-broker.owned'),
            broker=broker_config, broker_native_fd=broker_native, compositor_native_fd=graphics_fd,
            initial_world_id=worlds[0][0].world_id, initial_actor_tls_config=worlds[0][0].actor_tls_config)
        config.worlds.extend(value for value, _, _ in worlds)
        descriptors = [broker_native, graphics_fd]
        for _, listener, fd in worlds: descriptors.extend([fd, listener.fileno()])
        self.worlds = worlds
        return config, descriptors

    def launch_host(self, config, descriptors, *, endpoint=None, config_fd=None, parent_pid=None):
        if config_fd is None: config_fd = sealed(text_format.MessageToString(config).encode(), 'production-host-config')
        if endpoint is None: parent, child = socket.socketpair()
        else: parent, child = endpoint
        self.host_log = self.root / 'production-host-session.log'
        log = self.host_log.open('wb')
        environment = dict(os.environ)
        for name in ('SPATIAL_HOST_ACTOR_CREDENTIAL', 'SPATIAL_HOST_ACTOR_KEY',
                     'SPATIAL_HOST_ACTOR_SERVER_PID'):
            environment.pop(name, None)
        process = subprocess.Popen([str(HOST_SESSION_BINARY), str(config_fd), str(child.fileno()),
                                    str(os.getpid() if parent_pid is None else parent_pid)],
            pass_fds=tuple(set(descriptors + [config_fd, child.fileno()])),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
            env=environment)
        child.close(); log.close()
        listener_fds = {listener.fileno() for _, listener, _ in self.worlds}
        for fd in set(descriptors + [config_fd]):
            if fd >= 3 and fd not in listener_fds: os.close(fd)
        for _, listener, _ in self.worlds:
            listener.close()
        self.host_process = process; self.control = parent
        parent.settimeout(90)
        def cleanup():
            parent.close()
            if process.poll() is None:
                try: process.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try: process.wait(timeout=15)
                    except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
            data = self.host_log.read_bytes()
            for marker in (b'AddressSanitizer', b'LeakSanitizer', b'runtime error:'):
                self.assertNotIn(marker, data)
        self.addCleanup(cleanup)
        return process, parent

    def request(self, operation='inspect', generation=0, request_id=None, **fields):
        if request_id is None:
            self.host_request_id = getattr(self, 'host_request_id', 0) + 1
            request_id = self.host_request_id
        request = host.HostControlRequest(protocol_version=1, request_id=request_id, generation=generation)
        if operation == 'inspect': request.inspect = True
        elif operation == 'action': request.action.CopyFrom(fields['action'])
        elif operation == 'transition': request.transition.CopyFrom(fields['transition'])
        payload = request.SerializeToString()
        self.control.sendall(struct.pack('!I', len(payload)) + payload)
        size, = struct.unpack('!I', exact(self.control, 4))
        self.assertGreater(size, 0); self.assertLessEqual(size, 1024 * 1024)
        reply = host.HostControlReply.FromString(exact(self.control, size))
        self.assertEqual((reply.protocol_version, reply.request_id), (1, request_id))
        return reply

    def ready_host(self, **options):
        config, descriptors = self.prepare(**options)
        self.launch_host(config, descriptors)
        first = self.request()
        self.assertTrue(first.accepted, (first, self.host_log.read_text()))
        self.assertTrue(first.HasField('inspection'), first)
        return first

    def process_start(self, pid):
        line = Path(f'/proc/{pid}/stat').read_text()
        return line.rsplit(')', 1)[1].split()[19]

    def compositor_pin(self):
        children = Path(f'/proc/{self.host_process.pid}/task/{self.host_process.pid}/children').read_text().split()
        matches = [int(pid) for pid in children if b'spatialwm' in Path(f'/proc/{pid}/cmdline').read_bytes()]
        self.assertEqual(len(matches), 1)
        pin = os.pidfd_open(matches[0]); self.addCleanup(os.close, pin)
        return pin

    def live_compositor_pids(self):
        state = str(self.root / 'production-host-graphics').encode()
        found = []
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit(): continue
            try: arguments = (entry / 'cmdline').read_bytes().split(b'\0')
            except (FileNotFoundError, PermissionError, ProcessLookupError): continue
            if arguments and b'spatialwm' in arguments[0] and state in arguments:
                found.append(int(entry.name))
        return found

    def retained_pids(self):
        result = {}
        for value, _, _ in self.worlds:
            journal = Path(value.supervisor.journal_directory) / 'launch.db'
            if not journal.exists(): continue
            with sqlite3.connect(f'file:{journal}?mode=ro', uri=True) as db:
                record = launch.AuthorityLaunchRecord.FromString(
                    db.execute('SELECT record FROM launch WHERE singleton=1').fetchone()[0])
            group = self.cgroup / record.process.scope_name / 'cgroup.procs'
            result[value.world_id] = tuple(int(pid) for pid in group.read_text().split()) if group.exists() else ()
        return result

    def checked_record(self, journal):
        with sqlite3.connect(f'file:{journal / "launch.db"}?mode=ro', uri=True) as db:
            return launch.AuthorityLaunchRecord.FromString(
                db.execute('SELECT record FROM launch WHERE singleton=1').fetchone()[0])

    def assert_checked_cleanup(self):
        journals = [self.host_broker_journal] + [Path(value.supervisor.journal_directory)
                                                  for value, _, _ in self.worlds]
        for journal in journals:
            record = self.checked_record(journal)
            self.assertEqual(record.stage, launch.OWNED_SCOPES_STOPPED, journal)
            self.assertTrue(record.HasField('process_stopped'), journal)
            self.assertTrue(record.HasField('native_stopped'), journal)

    def terminal_cleanup_reply(self):
        size, = struct.unpack('!I', exact(self.control, 4))
        self.assertGreater(size, 0)
        self.assertLessEqual(size, 1024 * 1024)
        reply = host.HostControlReply.FromString(exact(self.control, size))
        self.assertEqual(reply.request_id, (1 << 64) - 1)
        return reply

    def test_initial_presentation_selection_and_authenticated_actor_readiness(self):
        first = self.ready_host()
        self.assertEqual(first.generation, 0)
        self.assertTrue(first.inspection.HasField('state'))
        self.assertEqual(self.retained_pids()[self.worlds[0][0].world_id].__len__(), 1)

    def test_native_a_b_a_fresh_channels_context_and_retained_pid_starts(self):
        first = self.ready_host(second=True)
        before = self.retained_pids()
        starts = {pid: self.process_start(pid) for group in before.values() for pid in group}
        starts[self.server.pid] = self.process_start(self.server.pid)
        children = Path(f'/proc/{self.host_process.pid}/task/{self.host_process.pid}/children').read_text().split()
        compositor_pids = [int(pid) for pid in children if b'spatialwm' in
                           Path(f'/proc/{pid}/cmdline').read_bytes()]
        self.assertEqual(len(compositor_pids), 1)
        starts[compositor_pids[0]] = self.process_start(compositor_pids[0])
        a, b = [value.world_id for value, _, _ in self.worlds]
        for generation, destination in ((0, b), (1, a)):
            transition = host_actor.HostActorTransitionRequest(protocol_version=1,
                transition_id=os.urandom(32), departure_sequence=10 + generation,
                destination_world_id=destination)
            reply = self.request('transition', generation, transition=transition)
            self.assertTrue(reply.accepted, reply)
            self.assertTrue(reply.transition.actor_transition.destination_activation_confirmed, reply)
            self.assertEqual(reply.generation, generation + 1)
            self.assertEqual(before, self.retained_pids())
            self.assertEqual(starts, {pid: self.process_start(pid) for pid in starts})
            if destination == b:
                destination_state = self.request(generation=1).inspection.state
                self.assertEqual(destination_state.id, first.inspection.state.id)
                self.assertEqual(destination_state.workspace, '')
        resumed = self.request(generation=2)
        self.assertEqual(resumed.inspection.state.id, first.inspection.state.id)
        self.assertEqual(resumed.inspection.state.room, first.inspection.state.room)
        self.assertEqual(resumed.inspection.state.pose, first.inspection.state.pose)
        self.assertEqual(resumed.inspection.state.workspace, first.inspection.state.workspace)

    def test_private_destination_denial_keeps_source_usable(self):
        initial = self.ready_host(denied=True)
        destination = self.worlds[1][0].world_id
        transition = host_actor.HostActorTransitionRequest(protocol_version=1,
            transition_id=os.urandom(32), departure_sequence=10, destination_world_id=destination)
        denial = self.request('transition', transition=transition)
        self.assertFalse(denial.transition.actor_transition.destination_activation_confirmed)
        self.assertTrue(denial.transition.actor_control_available)
        resumed = self.request(generation=denial.generation)
        self.assertTrue(resumed.accepted)
        self.assertEqual(resumed.inspection.state.id, initial.inspection.state.id)
        self.assertEqual(resumed.inspection.state.room, initial.inspection.state.room)
        self.assertEqual(resumed.inspection.state.workspace, initial.inspection.state.workspace)
        action = self.request('action', generation=denial.generation,
            action=core.ActorActionRequest(seq=1, stop=core.ActorStopRequest()))
        self.assertTrue(action.accepted, action)
        self.assertTrue(action.action.HasField('action'), action)
        self.assertTrue(action.action.action.accepted, action)

    def test_stale_control_generation_rejected_before_effects(self):
        self.ready_host(second=True)
        transition = host_actor.HostActorTransitionRequest(protocol_version=1,
            transition_id=os.urandom(32), departure_sequence=10,
            destination_world_id=self.worlds[1][0].world_id)
        action = core.ActorActionRequest(seq=1, stop=core.ActorStopRequest())
        for operation, fields in [('inspect', {}), ('action', {'action': action}),
                                  ('transition', {'transition': transition})]:
            stale = self.request(operation, generation=99, **fields)
            self.assertFalse(stale.accepted, stale)
            self.assertEqual(stale.generation, 0)
            self.assertTrue('stale host actor context' in stale.detail.lower() or
                            'stale host actor generation' in stale.detail.lower(), stale)
        valid_action = self.request('action', action=action)
        self.assertTrue(valid_action.accepted, valid_action)
        self.assertTrue(valid_action.action.action.accepted, valid_action)
        valid = self.request('transition', transition=host_actor.HostActorTransitionRequest(
            protocol_version=1, transition_id=os.urandom(32), departure_sequence=11,
            destination_world_id=self.worlds[1][0].world_id))
        self.assertTrue(valid.accepted, valid)
        self.assertTrue(valid.transition.actor_transition.destination_activation_confirmed, valid)
        self.assertEqual(valid.generation, 1)

    def test_idle_fragmented_frames_and_slow_partial_absolute_deadline(self):
        self.ready_host()
        time.sleep(11)
        self.assertTrue(self.request().accepted)
        payload = host.HostControlRequest(protocol_version=1, request_id=100, generation=0, inspect=True).SerializeToString()
        wire = struct.pack('!I', len(payload)) + payload
        for part in wire:
            self.control.sendall(bytes([part])); time.sleep(.01)
        self.assertEqual(host.HostControlReply.FromString(exact(self.control, struct.unpack('!I', exact(self.control, 4))[0])).request_id, 100)
        self.control.sendall(b'\x00')
        time.sleep(11)
        self.assertNotEqual(self.host_process.wait(timeout=60), 0)

    def test_eof_during_frame_has_bounded_checked_cleanup(self):
        self.ready_host()
        self.control.sendall(b'\x00\x00')
        self.control.shutdown(socket.SHUT_WR)
        self.assertNotEqual(self.host_process.wait(timeout=60), 0)
        self.assertFalse(any(self.retained_pids().values()))

    def test_eof_during_body_has_bounded_checked_cleanup(self):
        self.ready_host()
        self.control.sendall(struct.pack('!I', 20) + b'\x08')
        self.control.shutdown(socket.SHUT_WR)
        self.assertNotEqual(self.host_process.wait(timeout=60), 0)
        self.assertFalse(any(self.retained_pids().values()))

    def test_oversized_control_frame_rejected_with_checked_cleanup(self):
        self.ready_host()
        self.control.sendall(struct.pack('!I', 1024 * 1024 + 1))
        self.assertNotEqual(self.host_process.wait(timeout=60), 0)
        self.assertFalse(any(self.retained_pids().values()))

    def test_slow_body_progress_does_not_extend_absolute_deadline(self):
        self.ready_host()
        payload = host.HostControlRequest(protocol_version=1, request_id=2, generation=0,
                                          inspect=True).SerializeToString()
        wire = struct.pack('!I', len(payload)) + payload
        started = time.monotonic()
        self.control.sendall(wire[:1])
        for offset, index in ((4.0, 2), (6.0, 4), (8.0, 5)):
            time.sleep(max(0, started + offset - time.monotonic()))
            self.control.sendall(wire[index:index + 1] if index != 2 else wire[1:4])
        # The host writes this diagnostic at the read failure, before checked cleanup.
        # A reset per fragment expires after 18 seconds; a fresh body clock after 14 seconds.
        boundary = started + 12.5
        diagnostic = 'Control channel read timed out or failed'
        observed = None
        while time.monotonic() < boundary:
            if diagnostic in self.host_log.read_text():
                observed = time.monotonic(); break
            time.sleep(.05)
        self.assertIsNotNone(observed, f'no deadline diagnostic by 12.5s; log={self.host_log.read_text()}')
        print(f'absolute frame deadline observed after {observed - started:.3f}s', flush=True)
        self.assertNotEqual(self.host_process.wait(timeout=60), 0)
        self.assertFalse(any(self.retained_pids().values()))

    def test_control_disconnect_and_signal_stop_every_owned_process(self):
        self.ready_host(second=True)
        compositor = self.compositor_pin()
        self.control.close()
        self.assertEqual(self.host_process.wait(timeout=40), 0, self.host_log.read_text())
        self.assertFalse(any(self.retained_pids().values()))
        self.assertEqual(select.select([compositor], [], [], 0)[0], [compositor])
        self.assert_checked_cleanup()

    def test_signal_cleanup_checks_every_owned_process(self):
        self.ready_host(second=True)
        compositor = self.compositor_pin()
        self.host_process.send_signal(signal.SIGTERM)
        self.assertEqual(self.host_process.wait(timeout=40), 0, self.host_log.read_text())
        self.assertFalse(any(self.retained_pids().values()))
        self.assertEqual(select.select([compositor], [], [], 0)[0], [compositor])
        self.assert_checked_cleanup()

    def test_native_descriptor_open_file_alias_rejected_before_start(self):
        config, descriptors = self.prepare()
        duplicate = os.dup(config.compositor_native_fd)
        descriptors.append(duplicate)
        config.broker_native_fd = duplicate
        self.launch_host(config, descriptors)
        self.assertNotEqual(self.host_process.wait(timeout=15), 0)
        self.assertFalse((self.root / 'host-broker.owned').exists())

    def test_wrong_parent_peer_rejected_before_start(self):
        config, descriptors = self.prepare()
        self.launch_host(config, descriptors, parent_pid=os.getpid() + 1)
        self.assertNotEqual(self.host_process.wait(timeout=15), 0)
        self.assertFalse((self.root / 'host-broker.owned').exists())

    def test_control_descriptor_alias_rejected_before_start(self):
        config, descriptors = self.prepare()
        parent, child = socket.socketpair()
        config.worlds[0].native_fd = child.fileno()
        self.launch_host(config, descriptors, endpoint=(parent, child))
        self.assertNotEqual(self.host_process.wait(timeout=15), 0)
        self.assertFalse((self.root / 'host-broker.owned').exists())

    def test_configuration_descriptor_alias_rejected_before_start(self):
        config, descriptors = self.prepare()
        config_fd = os.memfd_create('production-host-config', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        alias = os.dup(config_fd)
        config.worlds[0].native_fd = alias
        os.write(config_fd, text_format.MessageToString(config).encode())
        fcntl.fcntl(config_fd, fcntl.F_ADD_SEALS,
                    fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
        descriptors.append(alias)
        self.launch_host(config, descriptors, config_fd=config_fd)
        self.assertNotEqual(self.host_process.wait(timeout=15), 0)
        self.assertFalse((self.root / 'host-broker.owned').exists())

    def test_delegation_source_descriptor_alias_rejected_before_start(self):
        config, descriptors = self.prepare()
        config.broker.inherited_source_fds.append(config.worlds[0].native_fd)
        self.launch_host(config, descriptors)
        self.assertNotEqual(self.host_process.wait(timeout=15), 0)
        self.assertFalse((self.root / 'host-broker.owned').exists())

    def test_world_startup_source_descriptor_alias_rejected_before_start(self):
        config, descriptors = self.prepare()
        config.worlds[0].supervisor.inherited_source_fds.append(config.broker_native_fd)
        self.launch_host(config, descriptors)
        self.assertNotEqual(self.host_process.wait(timeout=15), 0)
        self.assertFalse((self.root / 'host-broker.owned').exists())

    def test_named_unix_endpoint_rejected_before_start(self):
        config, descriptors = self.prepare()
        path = str(self.root / 'named-control.sock')
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(path); listener.listen(1)
        parent = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        parent.connect(path)
        child, _ = listener.accept(); listener.close()
        self.launch_host(config, descriptors, endpoint=(parent, child))
        self.assertNotEqual(self.host_process.wait(timeout=15), 0)
        self.assertFalse((self.root / 'host-broker.owned').exists())

    def test_named_unix_peer_endpoint_rejected_before_start(self):
        config, descriptors = self.prepare()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.root / 'named-listener.sock')); listener.listen(1)
        parent = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        parent.bind(str(self.root / 'named-peer.sock'))
        parent.connect(str(self.root / 'named-listener.sock'))
        child, _ = listener.accept(); listener.close()
        self.launch_host(config, descriptors, endpoint=(parent, child))
        self.assertNotEqual(self.host_process.wait(timeout=15), 0)
        self.assertFalse((self.root / 'host-broker.owned').exists())

    def test_prestart_world_profile_rejection_has_no_service_attempt(self):
        config, descriptors = self.prepare()
        config.worlds[0].world_id = 'wrong-world-before-start'
        self.launch_host(config, descriptors)
        self.assertNotEqual(self.host_process.wait(timeout=15), 0)
        self.assertIn('Actor TLS profile does not match its configured world', self.host_log.read_text())
        self.assertTrue(self.terminal_cleanup_reply().cleanup_complete)
        self.assertFalse((self.root / 'host-broker.owned').exists())
        self.assertFalse((self.host_broker_journal / 'launch.db').exists())

    def test_presentation_constructor_failure_reports_unretained_owner_uncertainty(self):
        config, descriptors = self.prepare()
        receipt = self.root / 'compositor-constructor-process.txt'
        wrapper = self.root / 'record-compositor-exec.py'
        compositor_binary = config.compositor_executable
        wrapper.write_text('#!/usr/bin/python3\nimport os, sys\n'
            'pid = os.getpid()\n'
            'start = open("/proc/%d/stat" % pid).read().rsplit(")", 1)[1].split()[19]\n'
            f'open({str(receipt)!r}, "w").write("%d %s\\n" % (pid, start))\n'
            f'os.execv({compositor_binary!r}, [{compositor_binary!r}] + sys.argv[1:])\n')
        wrapper.chmod(0o700)
        config.compositor_executable = str(wrapper)
        old = config.compositor_native_fd
        descriptors.remove(old); os.close(old)
        invalid = compositor.NativeCompositorConfig(protocol_version=1,
            display_number=self.display, compositor_cookie=os.urandom(32),
            server_id=self.supervisor.inspect().server_id, server_pid=self.server.pid,
            server_uid=os.getuid())
        config.compositor_native_fd = sealed(invalid.SerializeToString(), 'invalid-compositor-cookie')
        descriptors.append(config.compositor_native_fd)
        def cleanup_exact_child():
            if not receipt.is_file(): return
            pid, start = map(int, receipt.read_text().split())
            stat = Path(f'/proc/{pid}/stat')
            try:
                if not stat.exists() or int(stat.read_text().rsplit(')', 1)[1].split()[19]) != start:
                    return
                pin = os.pidfd_open(pid)
            except (FileNotFoundError, ProcessLookupError):
                return
            try:
                try: signal.pidfd_send_signal(pin, signal.SIGKILL)
                except ProcessLookupError: pass
            finally: os.close(pin)
        self.addCleanup(cleanup_exact_child)
        self.launch_host(config, descriptors)
        self.assertNotEqual(self.host_process.wait(timeout=45), 0)
        self.assertFalse(self.terminal_cleanup_reply().cleanup_complete)
        log = self.host_log.read_text()
        self.assertIn('presentation construction cleanup remains uncertain', log)
        self.assertIn('spatialwm:', log)
        self.assertTrue(receipt.is_file(), log)
        pid, start = map(int, receipt.read_text().split())
        self.assertGreater(pid, 1)
        self.assertGreater(start, 0)
        stat = Path(f'/proc/{pid}/stat')
        if stat.exists():
            self.assertNotEqual(int(stat.read_text().rsplit(')', 1)[1].split()[19]), start,
                                'the exact compositor child remains live')
        broker = self.checked_record(self.host_broker_journal)
        self.assertEqual(broker.stage, launch.OWNED_SCOPES_STOPPED)
        self.assertTrue(broker.HasField('process_stopped'))
        self.assertTrue(broker.HasField('native_stopped'))
        self.assertFalse((Path(self.worlds[0][0].supervisor.journal_directory) / 'launch.db').exists())
        self.assertFalse(self.live_compositor_pids(), log)

    def test_partial_start_retained_internal_entry_checked_cleanup(self):
        config, descriptors = self.prepare(failed_second=True)
        self.launch_host(config, descriptors)
        self.assertNotEqual(self.host_process.wait(timeout=60), 0)
        self.assertFalse(any(self.retained_pids().values()), self.host_log.read_text())
        self.assertFalse(self.live_compositor_pids(), self.host_log.read_text())
        self.assert_checked_cleanup()
        self.assertTrue(self.terminal_cleanup_reply().cleanup_complete)

    def test_one_cleanup_error_still_attempts_other_retained_owner(self):
        self.ready_host(second=True)
        compositor = self.compositor_pin()
        signal.pidfd_send_signal(compositor, signal.SIGKILL)
        self.control.close()
        self.assertNotEqual(self.host_process.wait(timeout=45), 0)
        self.assertFalse(any(self.retained_pids().values()), self.host_log.read_text())
        self.assertIn('uncertain', self.host_log.read_text())


if __name__ == '__main__':
    names = [name for name in dir(ProductionHostSessionTests) if name.startswith('test_') and
             name in ProductionHostSessionTests.__dict__]
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ProductionHostSessionArgumentTests)
    suite.addTests(ProductionHostSessionTests(name) for name in names)
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
