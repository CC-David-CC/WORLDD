"""Authenticated actors use real workspace grants and confined POSIX processes."""
import os
from pathlib import Path
import socket
import struct
import select
import sqlite3
import stat
import subprocess
import time
import threading
import unittest
from google.protobuf import text_format
from google.protobuf import descriptor_pb2
import broker_pb2 as broker
import core_pb2 as core
import copy_pb2 as workspace_copy
import grants_pb2 as grant
import issuer_pb2 as issuer
import world_session_pb2 as session
from harness.git_fixture import GitRepo
from harness.wire import frame, make_request, result_value, read_message
from harness.resource_broker import ResourceBroker
from unittest.mock import patch
from test_world_identity import WorldIdentityFixture, WorldProcess, WORLD, receive
import test_world_authority as world_authority

BROKER = WORLD.parent/'workspaced'
WRITE = [grant.FILE_CREATE, grant.FILE_EDIT, grant.FILE_RENAME, grant.FILE_MOVE, grant.FILE_COPY,
         grant.FILE_TRASH, grant.FILE_RESTORE, grant.FILE_PERMANENT_DELETE]
PROJECT = [grant.FILE_BROWSE, grant.FILE_READ, *WRITE, grant.PROCESS_EXECUTE]
RIGHTS = [grant.WORKSPACE_ACCESS, *PROJECT, grant.POLICY_ADMINISTER]
POLICY = ['workspace.select', 'files.read', 'files.write', 'process.launch']
FENCE_REVOCATION_MESSAGE = 'Workspace authority changed during the request'
# Setup must accommodate import_repository's existing five-minute clone limit.
# The native service and CTest bounds still cap the whole fixture.
GIT_IMPORT_SETUP_RESPONSE_SECONDS = 5 * 60


class BrokerChannel:
    """Give each worldd a private endpoint and serialize it with direct fixture requests."""
    def __init__(self, broker_process, stream):
        self.broker = broker_process; self.stream = stream
        self.reader = stream.makefile('rb'); self.send_lock = threading.Lock(); self.errors = []
        self.thread = threading.Thread(target=self.run, daemon=True); self.thread.start()

    def send(self, reply):
        with self.send_lock: self.stream.sendall(frame(reply))

    def run(self):
        try:
            while True:
                request = read_message(self.reader, broker.Request)
                if request.protocol_version != 1 or request.request_id <= 0:
                    raise AssertionError('Invalid world broker request identity')
                self.broker.world_request_count += 1
                self.send(self.broker.exchange(request))
        except (EOFError, OSError):
            pass
        except BaseException as error:
            self.errors.append(error)

    def close(self):
        if not getattr(self, 'closed', False):
            try: self.stream.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            self.stream.close(); self.thread.join(5)
            if self.thread.is_alive(): raise AssertionError('Serialized broker channel did not stop')
            if not self.reader.closed: self.reader.close()
            self.closed = True
            if self in self.broker.channels: self.broker.channels.remove(self)
        if self.errors: raise self.errors[0]


def initialize_broker_lifecycle(process, fixture, log_name):
    # Subclasses that own their launch sequence still use BrokerProcess.close().
    process.sequence = 0
    process.resources = process.proc = None
    process.owner_log = process.storage_owner = process.storage_owner_log = None
    process.storage_control = process.storage_gate_write = None
    process.storage_unit = None
    process.storage_cleanup_confirmed = None
    process.storage_owner_launch_path = None
    process.channels = []
    process.closed = False
    process.log_path = fixture.root/log_name
    process.storage_cleanup_path = process.log_path.with_suffix('.storage-cleanup.log')
    process.stream = process.log = None


class BrokerProcess:
    def __init__(self, fixture, state, config=None, *, storage_options=None):
        initialize_broker_lifecycle(self, fixture, f'broker-{len(fixture.brokers)}.log')
        child = owner_channel = gate_read = None
        try:
            self.stream, child = socket.socketpair()
            self.log = self.log_path.open('wb')
            command = [str(BROKER), '--state-dir', str(state)]
            if config: command += ['--authority-config', str(config)]
            configuration = os.environ.get('SPATIAL_TEST_BROKER_CONFIG')
            if storage_options is not None:
                if not configuration:
                    raise RuntimeError('SETUP FAILURE: StorageOwner lane requires the production ResourceBroker template')
                storage_volume, storage_launch = storage_options
                if Path(state).resolve() != Path(storage_volume).resolve():
                    raise RuntimeError('SETUP FAILURE: broker state root must be the provisioned StorageOwner volume')
                self.storage_control, owner_channel = socket.socketpair()
                for endpoint in (self.storage_control, owner_channel):
                    endpoint.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
                gate_read, self.storage_gate_write = os.pipe()
                command += ['--storage-fd', str(self.storage_control.fileno())]
            if configuration:
                self.owner_log = (fixture.root/f'broker-owner-{len(fixture.brokers)}.log').open('wb')
                extra_fds = {'--storage-fd':self.storage_control.fileno()} if storage_options is not None else None
                self.resources = ResourceBroker(BROKER, command, child, self.log, self.owner_log, configuration,
                                                extra_fds=extra_fds, startup_gate_fd=gate_read)
                self.proc = self.resources.process
                self.pid = self.resources.pid
            else:
                self.proc = subprocess.Popen(command, stdin=child, stdout=child, stderr=self.log)
                self.pid = self.proc.pid
            if gate_read is not None:
                os.close(gate_read); gate_read = None
            child.close(); child = None
            if storage_options is not None:
                self.start_storage_owner(WORLD.parent/'spatial-storage-launch-probe', storage_launch,
                                         storage_volume, owner_channel)
                owner_channel = None
                if os.write(self.storage_gate_write, b'!') != 1:
                    raise OSError('Incomplete broker startup gate write')
                os.close(self.storage_gate_write); self.storage_gate_write = None
            self.initialize_transport(world_channel_opt_in=True)
            fixture.brokers.append(self)
        except BaseException as error:
            # Kill the gated wrapper before closing its gate. Never authorize exec on failure.
            cleanup_errors = []
            if self.proc is not None:
                try: self.proc.kill()
                except BaseException as cleanup: cleanup_errors.append(repr(cleanup))
            try: self.close()
            except BaseException as cleanup: cleanup_errors.append(repr(cleanup))
            if cleanup_errors:
                error.add_note('Partial broker cleanup: '+ '; '.join(cleanup_errors))
            raise
        finally:
            for endpoint in (child, owner_channel):
                if endpoint is not None: endpoint.close()
            if gate_read is not None: os.close(gate_read)
            if self.storage_gate_write is not None:
                os.close(self.storage_gate_write); self.storage_gate_write = None

    def storage_owner_launch_payload(self, launch_config, storage_volume):
        """Extract the typed owner launch config from the provisioned runtime envelope."""
        runtime_path = Path(launch_config)
        volume = Path(storage_volume)
        def refuse(reason):
            raise RuntimeError('SETUP FAILURE: invalid StorageOwner runtime configuration: '+reason)
        try:
            if not runtime_path.is_absolute() or runtime_path.resolve(strict=True) != runtime_path:
                refuse('runtime.pb must be a canonical absolute path')
            runtime_metadata = runtime_path.lstat()
            if (runtime_metadata.st_uid != 0 or runtime_metadata.st_mode & 0o022 or
                    not stat.S_ISREG(runtime_metadata.st_mode)):
                refuse('runtime.pb must be a root-owned protected regular file')
            if not volume.is_absolute() or volume.resolve(strict=True) != volume:
                refuse('selected storage volume must be canonical')
            runtime = broker.StorageVolumeRuntime.FromString(runtime_path.read_bytes())
        except RuntimeError:
            raise
        except Exception as error:
            raise RuntimeError('SETUP FAILURE: launch config must contain StorageVolumeRuntime') from error
        if runtime.protocol_version != 1:
            refuse('unsupported runtime protocol version')
        if (Path(runtime.volume_root) != volume or
                Path(runtime.volume_root).resolve(strict=True) != volume):
            refuse('runtime volume root does not match the selected storage volume')
        if not runtime.require_confinement:
            refuse('runtime must require confinement')
        if not runtime.HasField('storage_owner'):
            refuse('runtime has no StorageOwner launch config')
        owner = runtime.storage_owner
        if owner.protocol_version != 1:
            refuse('unsupported StorageOwner launch protocol version')
        if (Path(owner.volume_root) != volume or
                Path(owner.volume_root).resolve(strict=True) != volume):
            refuse('StorageOwner volume root does not match the selected storage volume')
        owner_configuration = Path(owner.owner_configuration)
        try:
            if (not owner_configuration.is_absolute() or
                    owner_configuration.resolve(strict=True) != owner_configuration):
                refuse('owner configuration must be a canonical absolute path')
            owner_metadata = owner_configuration.lstat()
            if (owner_metadata.st_uid != 0 or owner_metadata.st_nlink != 1 or
                    owner_metadata.st_mode & 0o077 or not stat.S_ISREG(owner_metadata.st_mode)):
                refuse('owner configuration must be a root-owned protected regular file')
            for parent in owner_configuration.parents:
                metadata = parent.lstat()
                if (metadata.st_uid != 0 or metadata.st_mode & 0o022 or
                        not stat.S_ISDIR(metadata.st_mode)):
                    refuse('owner configuration has an unprotected parent directory')
        except RuntimeError:
            raise
        except Exception as error:
            raise RuntimeError('SETUP FAILURE: owner configuration is unavailable or unprotected') from error
        if not owner.HasField('budget'):
            refuse('StorageOwner budget is missing')
        return owner.SerializeToString()

    def private_storage_owner_launch_file(self, launch_config, storage_volume):
        payload = self.storage_owner_launch_payload(launch_config, storage_volume)
        path = self.log_path.parent/f'{self.log_path.stem}.storage-owner-launch.pb'
        descriptor = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0: raise OSError('Incomplete private StorageOwner config write')
                view = view[written:]
            os.fsync(descriptor)
        except BaseException:
            os.close(descriptor)
            try: path.unlink()
            except OSError: pass
            raise
        else:
            os.close(descriptor)
        self.storage_owner_launch_path = path
        return path

    def retain_storage_owner_launch_file(self):
        path = self.storage_owner_launch_path
        if path is None or not path.exists(): return None
        evidence = os.environ.get('SPATIAL_SECURITY_EVIDENCE')
        if not evidence: return None
        directory = Path(evidence)
        if not directory.is_absolute() or not directory.is_dir() or directory.is_symlink():
            raise RuntimeError('Cannot retain private StorageOwner config under the evidence directory')
        target = directory/f'{self.log_path.stem}-storage-owner-launch-{self.pid}.pb'
        descriptor = os.open(target, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            with path.open('rb') as source:
                while True:
                    chunk = source.read(65536)
                    if not chunk: break
                    view = memoryview(chunk)
                    while view:
                        written = os.write(descriptor, view)
                        if written <= 0: raise OSError('Incomplete private StorageOwner evidence copy')
                        view = view[written:]
            os.fsync(descriptor)
        except BaseException:
            os.close(descriptor)
            try: target.unlink()
            except OSError: pass
            raise
        else:
            os.close(descriptor)
        return str(target)

    def start_storage_owner(self, probe, launch_config, storage_volume, owner_channel):
        try:
            private_config = self.private_storage_owner_launch_file(launch_config, storage_volume)
            pidfd = os.pidfd_open(self.pid)
            try:
                self.storage_owner_log = (self.log_path.parent/f'broker-storage-owner-{self.pid}.log').open('wb')
                self.storage_owner = subprocess.Popen([str(probe), str(private_config),
                    str(owner_channel.fileno()), str(pidfd)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=self.storage_owner_log, pass_fds=(owner_channel.fileno(),pidfd), bufsize=0)
            finally:
                os.close(pidfd)
        finally:
            owner_channel.close()
        self.storage_exchange(1, False)

    def storage_exchange(self, request_id, stop):
        import storage_pb2 as storage
        request=storage.StorageLaunchControl(protocol_version=1,request_id=request_id)
        if stop: request.stop=True
        payload=request.SerializeToString()
        self.storage_owner.stdin.write(struct.pack('!I',len(payload))+payload); self.storage_owner.stdin.flush()
        deadline=time.monotonic()+12
        def exact(count):
            data=b''
            while len(data)<count:
                remain=deadline-time.monotonic()
                if remain<=0 or not select.select([self.storage_owner.stdout],[],[],remain)[0]:
                    raise TimeoutError('StorageOwner response timed out')
                chunk=os.read(self.storage_owner.stdout.fileno(),count-len(data))
                if not chunk: raise EOFError('StorageOwner probe closed its response pipe')
                data+=chunk
            return data
        size=struct.unpack('!I',exact(4))[0]
        if not 0<size<=1024*1024: raise AssertionError('Invalid StorageOwner response frame')
        reply=storage.StorageLaunchResult.FromString(exact(size))
        if reply.unit: self.storage_unit = reply.unit
        if reply.HasField('startup_cleanup_confirmed'):
            self.storage_cleanup_confirmed = reply.startup_cleanup_confirmed
        if reply.protocol_version!=1 or reply.request_id!=request_id:
            raise AssertionError(f'StorageOwner response identity mismatch: {reply}')
        if stop and not reply.stopped: raise AssertionError(f'StorageOwner stop failed: {reply}')
        if not stop and not reply.running: raise AssertionError(f'StorageOwner start failed: {reply}')

    def stop_storage_owner(self):
        owner = self.storage_owner
        errors = []
        def attempt(action):
            try: return action()
            except BaseException as error: errors.append(error)
        if owner is not None:
            try:
                self.storage_exchange(2, True)
                self.storage_cleanup_confirmed = True
                owner.stdin.close()
                if owner.wait(timeout=20) != 0:
                    raise AssertionError('StorageOwner probe failed to exit')
            except BaseException as error:
                errors.append(error)
            # EOF asks the probe destructor to stop its exact service after a bad reply.
            if owner.stdin is not None: attempt(owner.stdin.close)
            if owner.poll() is None:
                try: owner.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    attempt(owner.terminate)
                    try: owner.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        attempt(owner.kill)
                        attempt(lambda: owner.wait(timeout=3))
                    except BaseException as error: errors.append(error)
                except BaseException as error: errors.append(error)
            if owner.stdout is not None: attempt(owner.stdout.close)
            self.storage_owner = None
        if self.storage_control is not None: attempt(self.storage_control.close)
        if self.storage_owner_log is not None: attempt(self.storage_owner_log.close)
        launch_config_evidence = None
        launch_config_copied = False
        if self.storage_owner_launch_path is not None:
            owner_alive = owner is not None and owner.poll() is None
            if errors:
                try:
                    launch_config_evidence = self.retain_storage_owner_launch_file()
                    launch_config_copied = (launch_config_evidence is not None and
                        Path(launch_config_evidence) != self.storage_owner_launch_path)
                except BaseException as error:
                    errors.append(error)
                if launch_config_evidence is None:
                    launch_config_evidence = str(self.storage_owner_launch_path)
            if not owner_alive and (not errors or launch_config_copied):
                try: self.storage_owner_launch_path.unlink()
                except FileNotFoundError: pass
                except OSError as error: errors.append(error)
            elif owner_alive:
                launch_config_evidence = str(self.storage_owner_launch_path)
        if errors:
            evidence = ('unit='+str(self.storage_unit)+'\nchecked_cleanup_confirmed='+
                        str(self.storage_cleanup_confirmed)+'\nerrors='+repr([str(e) for e in errors])+'\n')
            if launch_config_evidence:
                evidence += 'owner_launch_config_evidence='+launch_config_evidence+'\n'
            log_name = getattr(self.storage_owner_log, 'name', None)
            if isinstance(log_name, (str, bytes, os.PathLike)):
                try:
                    with open(log_name, 'rb') as diagnostics:
                        evidence += 'owner_diagnostics=' + diagnostics.read(65536).decode('utf-8', errors='replace') + '\n'
                except OSError as error: errors.append(error)
            attempt(lambda: self.storage_cleanup_path.write_text(evidence))
            errors[0].add_note(evidence)
            for error in errors[1:]: errors[0].add_note('Additional storage cleanup error: '+repr(error))
            raise errors[0]

    def initialize_transport(self, *, world_channel_opt_in):
        self.io_lock = threading.RLock()
        self.transport_error = None
        self.channels = []
        self.world_request_count = 0
        self.world_channel_opt_in = bool(world_channel_opt_in)
        self.world_channel_factory = self.open_world_channel if self.world_channel_opt_in else None

    def dispatch_control(self, reply):
        if not reply.HasField('control_revoked') or reply.request_id != 0:
            raise AssertionError('Invalid unsolicited broker control reply')
        if len(self.channels) != 1:
            raise AssertionError('Selected fixture relay supports one active world for control delivery')
        self.channels[0].send(reply)

    def exchange(self, request):
        with self.io_lock:
            if self.transport_error is not None:
                raise AssertionError('Broker transport is unusable: '+self.transport_error)
            deadline = (time.monotonic() + GIT_IMPORT_SETUP_RESPONSE_SECONDS
                        if request.HasField('management') and
                        request.management.HasField('git_repo_import') else None)
            self.stream.sendall(frame(request))
            while True:
                if deadline is None:
                    reply = receive(self.stream.recv, self.stream, broker.Reply)
                else:
                    reply = receive(self.stream.recv, self.stream, broker.Reply, deadline=deadline)
                if reply.protocol_version != 1: raise AssertionError('Invalid broker reply protocol')
                if reply.HasField('control_revoked'):
                    self.dispatch_control(reply)
                    continue
                if reply.request_id != request.request_id:
                    raise AssertionError(f'Broker reply {reply.request_id} did not match request {request.request_id}')
                return reply

    def open_world_channel(self):
        world, relay = socket.socketpair()
        channel = BrokerChannel(self, relay); self.channels.append(channel)
        return world, channel

    def call(self, method, **args):
        with self.io_lock:
            self.sequence += 1; request = make_request(method, args); request.request_id = self.sequence
            reply = self.exchange(broker.Request(protocol_version=1, request_id=self.sequence, management=request))
            if reply.HasField('error') or reply.management.HasField('error'): raise AssertionError(reply)
            return result_value(reply.management)

    def request(self, **operation):
        with self.io_lock:
            self.sequence += 1
            request = broker.Request(protocol_version=1, request_id=self.sequence, **operation)
            return self.exchange(request)

    def close(self):
        if self.closed: return
        errors = []
        def attempt(action):
            try: action()
            except BaseException as error: errors.append(error)
        for channel in tuple(self.channels): attempt(channel.close)
        if self.stream is not None:
            try: self.stream.shutdown(socket.SHUT_WR)
            except OSError: pass
        if self.resources is not None:
            attempt(self.resources.close)
        elif self.proc is not None and self.proc.poll() is None:
            try: self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                attempt(self.proc.kill)
                attempt(lambda: self.proc.wait(timeout=3))
                errors.append(TimeoutError('Broker did not exit after channel closure'))
        attempt(self.stop_storage_owner)
        for stream in (self.stream, self.log, self.owner_log):
            if stream is not None: attempt(stream.close)
        if errors:
            for error in errors[1:]: errors[0].add_note('Additional cleanup error: '+repr(error))
            raise errors[0]
        self.closed = True


class BrokerTransportCompatibilityTests(unittest.TestCase):
    def timed_exchange(self, method, *, notifications=(), reply_at=6.0, request_id=1,
                       split_reply=False):
        """Exercise actual protobuf framing with a controlled readiness clock."""
        clock = [0.0]
        command = make_request(method, {'id': 'repo-one', 'path': '/source'}
                               if method == 'git.repo.import' else {})
        command.request_id = 1
        request = broker.Request(protocol_version=1, request_id=1, management=command)
        management = (core.Reply(protocol_version=1, request_id=request_id,
            repository=core.RepositoryReply(id='repo-one', path='/execution/repositories/one.git', source='/source'))
            if method == 'git.repo.import' else core.Reply(protocol_version=1,
                request_id=request_id, hello=core.HelloReply(protocol='fixture', implementation='test')))
        reply = broker.Reply(protocol_version=1, request_id=request_id, management=management)
        packets = [(at, frame(broker.Reply(protocol_version=1,
            control_revoked=broker.ApplicationControlLease(id=bytes([i + 1]) * 32,
                principal=grant.ActorRef(issuer_id=b'i' * 32, actor_id=b'a' * 32),
                application=broker.ApplicationRef(workspace=b'one', application=b'p' * 32,
                                                  server_id=b's' * 16)))))
                   for i, at in enumerate(notifications)]
        raw = frame(reply)
        packets += ([(reply_at, raw[:4]), (reply_at + 2, raw[4:])]
                    if split_reply else [(reply_at, raw)])

        class Stream:
            def __init__(self):
                self.sent = []

            def sendall(self, data):
                self.sent.append(data)

            def recv(self, size):
                at, data = packets[0]
                self_case.assertLessEqual(at, clock[0])
                result = data[:size]
                if len(result) == len(data):
                    packets.pop(0)
                else:
                    packets[0] = (at, data[len(result):])
                return result

            def select(self, readers, writers, errors, timeout):
                self_case.assertEqual(readers, [self])
                at = packets[0][0]
                if at > clock[0] + timeout:
                    clock[0] += timeout
                    return [], [], []
                clock[0] = max(clock[0], at)
                return readers, [], []

        self_case = self
        stream = Stream()
        fixture = BrokerProcess.__new__(BrokerProcess)
        fixture.stream = stream
        fixture.initialize_transport(world_channel_opt_in=False)
        events = []

        class Sink:
            def send(self, value):
                events.append(value)

        fixture.channels.append(Sink())
        self.clock = clock
        self.transport_events = events
        self.transport_stream = stream
        with patch.object(time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(select, 'select', side_effect=stream.select):
            result = fixture.exchange(request)
        self.assertEqual(stream.sent, [frame(request)])
        self.assertEqual(result, reply)
        return result

    def test_repository_import_reply_can_exceed_interactive_frame_budget(self):
        result = self.timed_exchange('git.repo.import')
        self.assertEqual(self.clock[0], 6.0)
        self.assertEqual(result.management.repository.id, 'repo-one')

    def test_repository_import_notifications_do_not_renew_setup_deadline(self):
        with self.assertRaisesRegex(AssertionError, 'World response deadline expired'):
            self.timed_exchange('git.repo.import', notifications=range(4, 305, 4), reply_at=308)
        self.assertEqual(self.clock[0], 300.0)
        self.assertEqual(len(self.transport_events), 74)
        self.assertEqual(len(self.transport_stream.sent), 1)

    def test_repository_import_partial_reply_keeps_absolute_setup_deadline(self):
        with self.assertRaisesRegex(AssertionError, 'World response deadline expired'):
            self.timed_exchange('git.repo.import', reply_at=299, split_reply=True)
        self.assertEqual(self.clock[0], 300.0)
        self.assertEqual(len(self.transport_stream.sent), 1)

    def test_repository_import_delayed_wrong_request_identity_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, 'Broker reply 99 did not match request 1'):
            self.timed_exchange('git.repo.import', request_id=99)
        self.assertEqual(self.clock[0], 6.0)

    def test_ordinary_broker_reply_retains_five_second_frame_budget(self):
        with self.assertRaisesRegex(AssertionError, 'World response deadline expired'):
            self.timed_exchange('system.hello')
        self.assertEqual(self.clock[0], 5.0)

    def test_authorization_fence_custom_broker_transport_opt_out_preserves_callbacks(self):
        class CustomBroker(BrokerProcess):
            def exchange(self, request):
                self.exchanges.append(request)
                return broker.Reply(protocol_version=1, request_id=request.request_id,
                    management=core.Reply(hello=core.HelloReply(protocol='fixture', implementation='test')))

            def request(self, **operation):
                self.sequence += 1
                self.events.append(operation)
                return 'custom-response'

        stream, peer = socket.socketpair()
        self.addCleanup(stream.close)
        self.addCleanup(peer.close)
        custom = CustomBroker.__new__(CustomBroker)
        custom.stream = stream
        custom.sequence = 0
        custom.events = []
        custom.exchanges = []
        custom.initialize_transport(world_channel_opt_in=False)

        endpoint, channel, owned = WorldProcess.workspace_channel(custom)
        self.assertIs(endpoint, stream)
        self.assertIsNone(channel)
        self.assertFalse(owned)
        self.assertIsNone(custom.world_channel_factory)
        self.assertFalse(stream._closed)
        self.assertEqual(custom.request(control_acquire='fixture-event'), 'custom-response')
        self.assertEqual(custom.events, [{'control_acquire': 'fixture-event'}])
        self.assertEqual(custom.sequence, 1)
        self.assertEqual(BrokerProcess.call(custom, 'system.hello'),
            {'protocol': 'fixture', 'implementation': 'test', 'build_id': '', 'capabilities': []})
        self.assertEqual(len(custom.exchanges), 1)
        sent = custom.exchanges[0]
        self.assertTrue(sent.management.HasField('hello'), sent)
        self.assertEqual(sent.request_id, 2)
        self.assertEqual(sent.management.request_id, sent.request_id)
        self.assertEqual(sent.protocol_version, 1)
        self.assertEqual(custom.sequence, sent.request_id)
        self.assertGreater(custom.sequence, 0)
        self.assertFalse(stream._closed)


class WorkspaceAuthorityTests(WorldIdentityFixture):
    principal = world_authority.WorldAuthorityTests.principal
    terms = world_authority.WorldAuthorityTests.terms
    scoped_world = world_authority.WorldAuthorityTests.scoped_world
    enroll_second = world_authority.WorldAuthorityTests.enroll_second
    authenticate_second = world_authority.WorldAuthorityTests.authenticate_second

    def setUp(self):
        super().setUp(); self.brokers = []
        self.addCleanup(lambda: ([world.close() for world in reversed(self.worlds)],
                                 [item.close() for item in reversed(self.brokers)]))

    def workspace_terms(self, claims=None, rights=RIGHTS, depth=3, components=(), delegable=None):
        return grant.GrantTerms(subject=self.principal(claims),
            scope=grant.Scope(domain=grant.Domain(kind=grant.WORKSPACE, id='one'), components=components),
            rights=rights, delegable_rights=rights if delegable is None and depth else delegable or (), delegation_depth=depth)

    def make_broker(self, state, config=None):
        factory = getattr(self, 'broker_factory', BrokerProcess)
        return factory(self, state, config)

    def workspace(self, policy=POLICY, roots=None, issuer_port=None, registry=False, legacy=False, pair=False, attach_world=True, empty_initial_commit=False):
        self.execution = getattr(self,'execution_override',self.root/'execution'); first = self.make_broker(self.execution)
        repository = GitRepo(self.root, empty_initial_commit=empty_initial_commit)
        first.call('git.repo.import', id='repo-one', path=str(repository.path))
        work = first.call('git.worktree.create', repo='repo-one', id='tree-one', branch='branch-one', start='main', operation_id='tree')
        self.project = Path(work['path'])
        first.call('workspace.create', id='one', worktree='tree-one', policy=policy)
        if pair:
            second_repository = GitRepo(self.root/'second-repository')
            first.call('git.repo.import', id='repo-two', path=str(second_repository.path))
            second_work = first.call('git.worktree.create', repo='repo-two', id='tree-two', branch='branch-two',
                                     start='main', operation_id='tree-two')
            self.project_two = Path(second_work['path'])
            first.call('workspace.create', id='two', worktree='tree-two', policy=policy)
        if legacy: first.call('workspace.member.set', id='one', actor='legacy-member', rights=POLICY)
        first.close()
        if hasattr(self,'after_workspace_storage_setup'): self.after_workspace_storage_setup()
        directory = self.root/'workspace-authority'; directory.mkdir(mode=0o700)
        seed = broker.WorkspaceAuthoritySeed(protocol_version=1, workspace='one', authority_directory=str(directory),
            roots=roots() if roots else [self.workspace_terms()])
        seed_path = self.root/'workspace-seed.textproto'; seed_path.write_text(text_format.MessageToString(seed)); seed_path.chmod(0o600)
        result = subprocess.run([str(BROKER), '--state-dir', str(self.execution), '--init-authority', str(seed_path)], capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        bindings = [broker.WorkspaceAuthorityBinding(workspace='one', authority_directory=str(directory))]
        if pair:
            second_dir = self.root/'workspace-two-authority'; second_dir.mkdir(mode=0o700)
            second_terms = self.workspace_terms()
            second_terms.scope.domain.id = 'two'
            second_seed = broker.WorkspaceAuthoritySeed(protocol_version=1, workspace='two',
                authority_directory=str(second_dir), roots=[second_terms])
            second_seed_path = self.root/'workspace-two-seed.textproto'
            second_seed_path.write_text(text_format.MessageToString(second_seed)); second_seed_path.chmod(0o600)
            second_result = subprocess.run([str(BROKER), '--state-dir', str(self.execution),
                '--init-authority', str(second_seed_path)], capture_output=True, timeout=8)
            self.assertEqual(second_result.returncode, 0, second_result.stderr)
            bindings.append(broker.WorkspaceAuthorityBinding(workspace='two', authority_directory=str(second_dir)))
        if registry:
            registry_dir = self.root/'workspace-registry-authority'; registry_dir.mkdir(mode=0o700)
            create_root = grant.GrantTerms(subject=self.principal(),
                scope=grant.Scope(domain=grant.Domain(kind=grant.WORKSPACE, id='workspace-registry')),
                rights=[grant.WORKSPACE_CREATE], delegation_depth=0)
            registry_seed = broker.WorkspaceAuthoritySeed(protocol_version=1, workspace='workspace-registry',
                authority_directory=str(registry_dir), roots=[create_root])
            registry_seed_path = self.root/'registry-seed.textproto'
            registry_seed_path.write_text(text_format.MessageToString(registry_seed)); registry_seed_path.chmod(0o600)
            registry_result = subprocess.run([str(BROKER), '--state-dir', str(self.execution),
                '--init-authority', str(registry_seed_path)], capture_output=True, timeout=8)
            self.assertEqual(registry_result.returncode, 0, registry_result.stderr)
            bindings.append(broker.WorkspaceAuthorityBinding(workspace='workspace-registry',
                authority_directory=str(registry_dir)))
        client_path = self.root/'workspace-issuer.textproto'
        client_path.write_bytes(self.config_file(endpoint_port=issuer_port).read_bytes()); client_path.chmod(0o600)
        config = broker.WorkspaceAuthorityConfig(protocol_version=1, issuer_client_config=str(client_path),
            bindings=bindings)
        self.configure_participant(config,[grant.Domain(kind=grant.WORKSPACE,id=binding.workspace) for binding in config.bindings])
        path = self.root/'workspace-policy.textproto'; path.write_text(text_format.MessageToString(config)); path.chmod(0o600)
        self.authority_config = path
        self.broker = self.make_broker(self.execution) if legacy else self.make_broker(self.execution, path)
        if not attach_world:return None
        world = self.scoped_world(session.REGISTERED_ACTORS, broker=self.broker)
        world.management('workspace.available', id='one', available=True)
        return world

    def own(self, peer):
        reply = peer.call(grants=session.WorldGrantList(workspace='one'))
        self.assertTrue(reply.HasField('grants'), reply); return reply.grants

    def delegate(self, peer, terms, parent=None):
        page = self.own(peer); parent = parent or next(item for item in page.grants if not item.parent_id)
        peer.action_sequence += 1
        result = peer.call(delegate=session.WorldGrantDelegate(workspace='one', seq=peer.action_sequence,
            parent_id=parent.id, terms=terms, expected_revision=page.revision))
        return result

    def grant_second(self, world, rights, components=()):
        result = self.delegate(world.peers[0], self.workspace_terms(self.second, rights=rights, depth=0, components=components))
        self.assertTrue(result.HasField('grant'), result); return result.grant

    def revoke_grant(self, peer, record):
        revision = self.own(peer).revision; peer.action_sequence += 1
        result = peer.call(revoke=session.WorldGrantRevoke(workspace='one', seq=peer.action_sequence, id=record.id, expected_revision=revision))
        self.assertTrue(result.HasField('grant'), result); return result.grant

    def select(self, peer):
        result = peer.act(workspace_select=core.WorkspaceIdRequest(id='one'))
        self.assertTrue(result.HasField('action'), result)

    def launch_process(self, peer, code, operation='launch', profile='development'):
        result = peer.act(process_launch=core.ProcessLaunchRequest(argv=['/usr/bin/python3','-c',code], operation_id=operation, profile=profile))
        self.assertTrue(result.HasField('action'), result); return result.action.process

    def wait_job(self, world, process):
        self.assertEqual(world.management('process.wait', id=process.process_id, timeout_seconds=5), {'exit_code':0})

    def descendants(self):
        todo = [self.broker.pid]; found = set()
        while todo:
            parent = todo.pop()
            try: children = Path(f'/proc/{parent}/task/{parent}/children').read_text().split()
            except FileNotFoundError: continue
            for child in map(int, children):
                if child not in found: found.add(child); todo.append(child)
        return found

    @staticmethod
    def gone(pids):
        for pid in pids:
            try: state = Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[0]
            # A process can exit after open(), while procfs serves read().
            except (FileNotFoundError, ProcessLookupError): continue
            if state not in ('Z','X'): return False
        return True

    def running(self, world, peer):
        self.select(peer)
        process = self.launch_process(peer, "from pathlib import Path\nimport time\nPath('ready').touch()\nwhile not Path('release').exists():time.sleep(.01)\nPath('done').touch()")
        self.until(lambda:(self.project/'ready').exists(), bool)
        return process

    def fence_with_checked_credential_retry(self, principal):
        request = broker.AuthorizationFenceRequest(principal=principal)
        first = self.broker.request(authorization_fence=request)
        if first.HasField('authorization_fence'):
            self.assertFalse(first.HasField('error'), first)
            return first, None
        self.assertTrue(first.HasField('error'), f'Authorization fence reply has no typed outcome: {first!r}')
        self.assertEqual(first.error.code, core.FORBIDDEN, first)
        self.assertEqual(first.error.message, FENCE_REVOCATION_MESSAGE, first)
        retry = self.broker.request(authorization_fence=request)
        self.assertGreater(retry.request_id, first.request_id, f'Fence retry reused its request ID: {first!r}; {retry!r}')
        self.assertTrue(retry.HasField('authorization_fence'), f'Fresh authorized fence failed: {retry!r}; original: {first!r}')
        self.assertFalse(retry.HasField('error'), f'Fresh authorized fence returned an error: {retry!r}; original: {first!r}')
        return first, retry

    def process_pid_for_code(self, code):
        expected = [b'/usr/bin/python3', b'-c', code.encode()]
        matches = []
        for pid in self.descendants():
            try:
                command_line = Path(f'/proc/{pid}/cmdline').read_bytes()
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
            if command_line.split(bytes([0])) == [*expected, b'']:
                ticks = WorldProcess.process_start_ticks(pid)
                if ticks is not None:
                    matches.append((pid, ticks, command_line))
        self.assertEqual(len(matches), 1, f'Expected one live process for the exact launch argv, found {matches}')
        return matches[0]

    def wait_for_broker_revocation_consumed(self, revision, offset, deadline, relay):
        # Forwarding alone is not consumption. Require this broker's checked watcher revision.
        while True:
            text = Path(self.broker.log.name).read_bytes()[offset:].decode(errors='replace')
            for line in text.splitlines():
                if not line.startswith('authority_diag '):
                    continue
                fields = dict(item.split('=', 1) for item in line.split()[1:] if '=' in item)
                if (fields.get('pid') == str(self.broker.pid) and fields.get('role') == 'workspace'
                        and fields.get('stage') == 'revocation_received'
                        and fields.get('target_revision') == str(revision)
                        and fields.get('consumed_revision') == str(revision)
                        and relay.delivered.is_set()):
                    self.assertLess(time.monotonic(), deadline, f'Broker consumption barrier exceeded its deadline: {line}')
                    return line
            remaining = deadline-time.monotonic()
            self.assertGreater(remaining, 0, f'Broker did not consume revision {revision} while status was pending: {text}')
            self.assertFalse(relay.stop.wait(min(.005, remaining)), 'Issuer relay stopped during consumption barrier')

    def test_m07_actor_schema_tags_and_copy_status_privacy(self):
        action_fields = core.ActorActionRequest.DESCRIPTOR.oneofs_by_name['action'].fields
        self.assertEqual({field.name: field.number for field in action_fields if 25 <= field.number <= 29}, {
            'presentation_create': 25, 'presentation_move': 26, 'presentation_remove': 27,
            'workspace_copy': 28, 'workspace_copy_status': 29,
        })
        create = core.PresentationCreateRequest.DESCRIPTOR
        self.assertEqual(create.fields_by_name['transform'].message_type.full_name, 'spatial.core.v1.Pose')
        self.assertTrue(create.fields_by_name['room_id'].has_presence)

        file_descriptor = descriptor_pb2.FileDescriptorProto()
        file_descriptor.ParseFromString(workspace_copy.DESCRIPTOR.serialized_pb)
        status = next(message for message in file_descriptor.message_type if message.name == 'WorkspaceCopyStatus')
        self.assertEqual([(entry.start, entry.end) for entry in status.reserved_range], [(5, 6)])
        self.assertEqual({field.number for field in status.field}, {1, 2, 3, 4, 6})
        self.assertFalse(any(any(term in field.name for term in ('snapshot', 'storage', 'path', 'receipt'))
                             for field in status.field))
        public_actor_fields = {field.message_type.full_name for field in action_fields if field.message_type}
        self.assertNotIn('spatial.workspace.copy.v1.SnapshotReceipt', public_actor_fields)
        reply_fields = core.ActorActionReply.DESCRIPTOR.fields_by_name
        self.assertEqual(reply_fields['presentation'].number, 6)
        self.assertEqual(reply_fields['copy_status'].number, 7)
        self.assertEqual(reply_fields['presentation'].message_type.full_name, 'spatial.core.v1.PresentationView')
        self.assertEqual(reply_fields['copy_status'].message_type.full_name, 'spatial.workspace.copy.v1.WorkspaceCopyStatus')
        self.assertEqual(set(broker.ScopedAuthorityCheckReply.DESCRIPTOR.fields_by_name), {'allowed','revision','incarnation','epoch'})
        self.assertEqual(broker.ScopedAuthorityCheckReply.DESCRIPTOR.fields_by_name['incarnation'].number, 3)
        self.assertEqual(broker.ScopedAuthorityCheckReply.DESCRIPTOR.fields_by_name['epoch'].number, 4)
        self.assertEqual(broker.ApplicationAccessReply.DESCRIPTOR.fields_by_name['incarnation'].number, 2)
        self.assertEqual(broker.ApplicationAccessReply.DESCRIPTOR.fields_by_name['epoch'].number, 3)
        self.assertEqual(broker.Request.DESCRIPTOR.fields_by_name['authorization_fence'].number, 114)
        self.assertEqual(broker.Reply.DESCRIPTOR.fields_by_name['authorization_fence'].number, 113)
        self.assertEqual(broker.ScopedAuthorityCheckRequest.DESCRIPTOR.fields_by_name['components'].number, 4)
        self.assertEqual(broker.ScopedAuthorityCheckRequest.DESCRIPTOR.fields_by_name['components'].type,
                         descriptor_pb2.FieldDescriptorProto.TYPE_BYTES)
        self.assertEqual(broker.Request.DESCRIPTOR.fields_by_name['scoped_authority_check'].number, 113)
        self.assertEqual(broker.Reply.DESCRIPTOR.fields_by_name['scoped_authority_check'].number, 112)
        public_view = core.PresentationView.DESCRIPTOR
        self.assertEqual({field.name for field in public_view.fields},
                         {'representation_id', 'room_id', 'transform', 'generation', 'locked', 'kind', 'cabinet_locked'})
        self.assertEqual(public_view.fields_by_name['transform'].message_type.full_name, 'spatial.core.v1.Pose')

    def test_m08_presentation_view_defaults_preserve_legacy_generic_wire(self):
        descriptor = core.PresentationView.DESCRIPTOR
        self.assertEqual(descriptor.fields_by_name['kind'].number, 6)
        self.assertEqual(descriptor.fields_by_name['cabinet_locked'].number, 7)
        legacy = core.PresentationView(representation_id=bytes(range(16)),
            room_id='legacy', generation=7, locked=True)
        legacy.transform.SetInParent()
        legacy_wire = legacy.SerializeToString(deterministic=True)
        expected_legacy_five_field_payload = (
            b'\x0a\x10' + bytes(range(16)) + b'\x12\x06legacy\x1a\x00\x20\x07\x28\x01')
        self.assertEqual(legacy_wire, expected_legacy_five_field_payload)
        extended = core.PresentationView()
        extended.CopyFrom(legacy)
        extended.kind = core.PRESENTATION_KIND_GENERIC
        extended.cabinet_locked = False
        self.assertEqual(extended.SerializeToString(deterministic=True), legacy_wire)

    def test_private_scoped_authority_check_uses_exact_scope_and_fresh_revocation(self):
        self.enroll_second()
        world = self.workspace(roots=lambda: [self.workspace_terms(
            rights=[*RIGHTS, grant.WORKSPACE_PRESENTATION_ATTACH])])
        components = (b'presentations\x00', b'\xffrep-1')
        record = self.grant_second(world, [grant.WORKSPACE_PRESENTATION_ATTACH], components)
        query = broker.ScopedAuthorityCheckRequest(workspace='one', principal=self.principal(self.second),
            right=grant.WORKSPACE_PRESENTATION_ATTACH, components=list(components))
        reply = self.broker.request(scoped_authority_check=query)
        self.assertTrue(reply.HasField('scoped_authority_check'), reply)
        self.assertTrue(reply.scoped_authority_check.allowed)
        self.assertGreater(reply.scoped_authority_check.revision, 0)
        self.assertEqual(len(reply.scoped_authority_check.incarnation), 16)
        self.assertGreater(reply.scoped_authority_check.epoch, 0)
        query.components[-1] = b'\xffother-representation'
        denied = self.broker.request(scoped_authority_check=query)
        self.assertFalse(denied.scoped_authority_check.allowed)
        for right in (grant.WORLD_PLACEMENT_CREATE, 99, grant.WORKSPACE_CREATE):
            invalid = broker.ScopedAuthorityCheckRequest(workspace='one', principal=self.principal(self.second), right=right)
            self.denied(self.broker.request(scoped_authority_check=invalid), core.INVALID_ARGUMENT)
        query.components[:] = components
        before = self.broker.request(scoped_authority_check=query).scoped_authority_check.revision
        self.revoke_grant(world.peers[0], record)
        after = self.broker.request(scoped_authority_check=query)
        self.assertFalse(after.scoped_authority_check.allowed)
        self.assertGreater(after.scoped_authority_check.revision, before)
        self.assertGreater(after.scoped_authority_check.epoch, reply.scoped_authority_check.epoch)

    def test_application_access_fails_closed_without_authority_fence(self):
        self.workspace(legacy=True)
        request = broker.ApplicationAccessRequest(principal=self.principal(),
            applications=[broker.ApplicationRef(workspace=b'one', application=b'missing-app', server_id=b's' * 32)])
        self.denied(self.broker.request(application_access=request), core.UNIMPLEMENTED)

    def test_authorization_fence_rejects_later_workspace_revocation_in_application_batch(self):
        relay = world_authority.RelayingIssuer(self, lambda: self.revoke(kind=issuer.ACTOR_TARGET))
        self.workspace(issuer_port=relay.port, pair=True)
        principal = self.principal()
        self.owner_peer = self.connect()
        relay.trigger_after_status = 2
        relay.armed.set()
        request = broker.ApplicationAccessRequest(principal=principal, applications=[
            broker.ApplicationRef(workspace=b'one', application=b'missing-one', server_id=b's' * 32),
            broker.ApplicationRef(workspace=b'two', application=b'missing-two', server_id=b's' * 32)])
        self.denied(self.broker.request(application_access=request), core.UNAUTHORIZED)
        self.assertEqual(relay.status_seen, 2)
        self.assertTrue(relay.delivered.is_set())
        self.assertFalse(relay.errors)

    def test_authorization_fence_tracks_delegation_revision(self):
        self.enroll_second(); world = self.workspace()
        principal = self.principal()
        request = broker.AuthorizationFenceRequest(principal=principal)
        before = self.broker.request(authorization_fence=request).authorization_fence
        self.grant_second(world, [grant.WORKSPACE_ACCESS])
        after = self.broker.request(authorization_fence=request).authorization_fence
        self.assertEqual(after.incarnation, before.incarnation)
        self.assertGreater(after.epoch, before.epoch)
        repeated = self.broker.request(authorization_fence=request).authorization_fence
        self.assertEqual((repeated.incarnation, repeated.epoch), (after.incarnation, after.epoch))

    def test_authorization_fence_is_stable_and_changes_incarnation_on_restart(self):
        world = self.workspace()
        principal = self.principal()
        request = broker.AuthorizationFenceRequest(principal=principal)
        first_reply = self.broker.request(authorization_fence=request)
        self.assertTrue(first_reply.HasField('authorization_fence'), first_reply)
        first = first_reply.authorization_fence
        self.assertEqual(len(first.incarnation), 16); self.assertGreater(first.epoch, 0)
        second = self.broker.request(authorization_fence=request).authorization_fence
        self.assertEqual((second.incarnation, second.epoch), (first.incarnation, first.epoch))
        baseline_world_requests = self.broker.world_request_count
        direct_results = []; direct_errors = []; start = threading.Barrier(2)
        def query_fence_repeatedly():
            try:
                start.wait()
                for _ in range(6):
                    direct_results.append(self.broker.request(authorization_fence=request).authorization_fence)
            except BaseException as error:
                direct_errors.append(error)
        query_thread = threading.Thread(target=query_fence_repeatedly, daemon=True)
        query_thread.start(); start.wait()
        for _ in range(3):
            selected = world.peers[0].act(workspace_select=core.WorkspaceIdRequest(id='one'))
            self.assertTrue(selected.HasField('action'), selected)
            cleared = world.peers[0].act(workspace_clear=core.ActorStopRequest())
            self.assertTrue(cleared.HasField('action'), cleared)
        query_thread.join(5)
        self.assertFalse(query_thread.is_alive(), 'direct broker requests exceeded the existing 5-second bound')
        self.assertFalse(direct_errors, direct_errors)
        self.assertEqual(len(direct_results), 6)
        self.assertGreater(self.broker.world_request_count, baseline_world_requests)
        self.assertIsNone(world.proc.poll(), 'worldd must remain live through serialized broker traffic')
        scoped = self.broker.request(scoped_authority_check=broker.ScopedAuthorityCheckRequest(
            workspace='one', principal=principal, right=grant.WORKSPACE_ACCESS)).scoped_authority_check
        app_batch = self.broker.request(application_access=broker.ApplicationAccessRequest(principal=principal,
            applications=[broker.ApplicationRef(workspace=b'one', application=b'missing-app', server_id=b's' * 32)]))
        self.assertTrue(app_batch.HasField('application_access'), app_batch)
        self.assertEqual(len(app_batch.application_access.incarnation), 16)
        fence = self.broker.request(authorization_fence=request).authorization_fence
        self.assertEqual((scoped.incarnation, scoped.epoch), (fence.incarnation, fence.epoch))
        self.assertEqual((app_batch.application_access.incarnation, app_batch.application_access.epoch),
                         (fence.incarnation, fence.epoch))
        initial_epoch = fence.epoch
        second_repo = GitRepo(self.root/'second-repository')
        self.broker.call('git.repo.import', id='repo-two', path=str(second_repo.path))
        self.broker.call('git.worktree.create', repo='repo-two', id='tree-two', branch='branch-two',
                         start='main', operation_id='tree-two')
        self.broker.call('workspace.create', id='two', worktree='tree-two', policy=['workspace.select'])
        created = self.broker.request(authorization_fence=request).authorization_fence
        self.assertGreater(created.epoch, initial_epoch)
        self.broker.call('workspace.member.set', id='two', actor='test-member', rights=['workspace.select'])
        changed = self.broker.request(authorization_fence=request).authorization_fence
        self.assertGreater(changed.epoch, created.epoch)
        self.broker.call('workspace.member.set', id='two', actor='test-member', rights=['workspace.select'])
        unchanged = self.broker.request(authorization_fence=request).authorization_fence
        self.assertEqual(unchanged.epoch, changed.epoch)
        self.assertLess(scoped.epoch, changed.epoch)
        self.assertLess(app_batch.application_access.epoch, changed.epoch)
        world.close()
        self.assertEqual(world.proc.returncode, 0, 'worldd must close before its broker restarts')
        self.broker.close()
        self.broker = self.make_broker(self.execution, self.authority_config)
        restarted = self.broker.request(authorization_fence=request).authorization_fence
        self.assertNotEqual(restarted.incarnation, first.incarnation)
        self.assertGreater(restarted.epoch, 0)

    def test_registry_authority_binding_needs_no_execution_workspace(self):
        self.workspace(registry=True)
        query = broker.ScopedAuthorityCheckRequest(workspace='workspace-registry', principal=self.principal(),
            right=grant.WORKSPACE_CREATE)
        reply = self.broker.request(scoped_authority_check=query)
        self.assertTrue(reply.HasField('scoped_authority_check'), reply)
        self.assertTrue(reply.scoped_authority_check.allowed)
        with self.assertRaisesRegex(AssertionError, 'INVALID_ARGUMENT'):
            self.broker.call('workspace.create', id='workspace-registry', worktree='tree-one', policy=POLICY)
        ordinary = broker.ScopedAuthorityCheckRequest(workspace='workspace-registry', principal=self.principal(),
            right=grant.FILE_READ)
        self.denied(self.broker.request(scoped_authority_check=ordinary), core.INVALID_ARGUMENT)
        self.denied(self.broker.request(access=broker.AccessRequest(workspace='workspace-registry', actor='x')),
                    core.INVALID_ARGUMENT)

    def test_scoped_authority_obeys_workspace_policy_ceiling(self):
        self.workspace(policy=['workspace.select'])
        query = broker.ScopedAuthorityCheckRequest(workspace='one', principal=self.principal(), right=grant.FILE_READ)
        reply = self.broker.request(scoped_authority_check=query)
        self.assertTrue(reply.HasField('scoped_authority_check'), reply)
        self.assertFalse(reply.scoped_authority_check.allowed)

    def test_project_copy_requires_project_root_read_not_object_id_filename(self):
        self.enroll_second()
        object_id = 'a' * 32
        root = self.workspace_terms(self.second,
            rights=[grant.WORKSPACE_COPY_PROJECT, grant.FILE_BROWSE], depth=0)
        forged_path = self.workspace_terms(self.second, rights=[grant.FILE_READ], depth=0,
            components=(object_id.encode(),))
        self.workspace(roots=lambda: [root, forged_path])
        scoped_file = self.broker.request(scoped_authority_check=broker.ScopedAuthorityCheckRequest(
            workspace='one', principal=self.principal(self.second), right=grant.FILE_READ,
            components=[object_id.encode()]))
        self.assertTrue(scoped_file.scoped_authority_check.allowed)
        root_read = self.broker.request(scoped_authority_check=broker.ScopedAuthorityCheckRequest(
            workspace='one', principal=self.principal(self.second), right=grant.FILE_READ))
        self.assertFalse(root_read.scoped_authority_check.allowed)
        # Copy admission checks empty root components, so the filename-shaped grant cannot admit it.
        self.assertGreater(root_read.scoped_authority_check.revision, 0)

    def test_scoped_authority_does_not_fall_back_to_legacy_membership(self):
        self.workspace(legacy=True)
        request = broker.ScopedAuthorityCheckRequest(workspace='one', principal=self.principal(), right=grant.WORKSPACE_ACCESS)
        self.denied(self.broker.request(scoped_authority_check=request), core.UNIMPLEMENTED)

    def test_scoped_authority_fails_closed_after_participant_transport_loss(self):
        relay = world_authority.RelayingIssuer(self, lambda: None)
        self.workspace(issuer_port=relay.port)
        relay.close()
        query = broker.ScopedAuthorityCheckRequest(workspace='one', principal=self.principal(),
            right=grant.WORKSPACE_ACCESS)
        self.denied(self.broker.request(scoped_authority_check=query), core.UNAVAILABLE)

    def test_scoped_authority_denies_after_issuer_actor_revocation(self):
        def revoke_actor():
            self.owner_peer = self.connect()
            self.revoke(kind=issuer.ACTOR_TARGET)

        relay = world_authority.RelayingIssuer(self, revoke_actor)
        world = self.workspace(issuer_port=relay.port)
        query = broker.ScopedAuthorityCheckRequest(workspace='one', principal=self.principal(),
            right=grant.WORKSPACE_ACCESS)
        before = self.broker.request(scoped_authority_check=query)
        self.assertTrue(before.HasField('scoped_authority_check'), before)
        self.assertTrue(before.scoped_authority_check.allowed)
        self.assertEqual(len(before.scoped_authority_check.incarnation), 16)

        # Revoke during the next issuer status refresh. The cancellation guard may
        # replace this in-flight result with its typed fail-closed error.
        relay.armed.set()
        in_flight = self.broker.request(scoped_authority_check=query)
        self.assertTrue(relay.delivered.is_set(), 'issuer revocation was not consumed during dispatch')
        if in_flight.HasField('error'):
            self.assertEqual(in_flight.error.code, core.UNAUTHORIZED)
            self.assertEqual(in_flight.error.message,
                'Workspace actor identity was revoked during the request')
            self.assertFalse(in_flight.HasField('scoped_authority_check'))
        else:
            self.assertTrue(in_flight.HasField('scoped_authority_check'), in_flight)
            self.assertFalse(in_flight.scoped_authority_check.allowed)
            self.assertEqual(in_flight.scoped_authority_check.incarnation,
                before.scoped_authority_check.incarnation)
            self.assertGreater(in_flight.scoped_authority_check.epoch,
                before.scoped_authority_check.epoch)

        settled = self.broker.request(scoped_authority_check=query)
        self.assertFalse(settled.HasField('error'), settled)
        self.assertTrue(settled.HasField('scoped_authority_check'), settled)
        self.assertFalse(settled.scoped_authority_check.allowed)
        self.assertEqual(settled.scoped_authority_check.incarnation,
            before.scoped_authority_check.incarnation)
        self.assertGreater(settled.scoped_authority_check.epoch,
            before.scoped_authority_check.epoch)

    def test_m07_actor_actions_reject_malformed_requests(self):
        world = self.workspace(); peer = world.peers[0]
        actions = (
            ({'presentation_create': core.PresentationCreateRequest(protocol_version=1, operation_id=b'a' * 16)},
             'Invalid presentation create request'),
            ({'presentation_move': core.PresentationMoveRequest(protocol_version=1, operation_id=b'b' * 16)},
             'Invalid presentation action request'),
            ({'presentation_remove': core.PresentationRemoveRequest(protocol_version=1, operation_id=b'c' * 16)},
             'Invalid presentation action request'),
            ({'workspace_copy': workspace_copy.WorkspaceCopyRequest(protocol_version=1, operation_id=b'd' * 16, source_workspace_id='one')},
             'Invalid workspace copy request'),
            ({'workspace_copy_status': workspace_copy.WorkspaceCopyStatusRequest(protocol_version=0, operation_id=b'd' * 16)},
             'Invalid workspace copy status identity'),
        )
        initial_revision = world.management('world.info')['world_revision']
        for action, expected_message in actions:
            with self.subTest(action=next(iter(action))):
                reply = peer.act(**action)
                self.assertEqual(reply.WhichOneof('outcome'), 'error', reply)
                self.assertEqual(reply.error.code, core.INVALID_ARGUMENT, reply)
                self.assertEqual(reply.error.message, expected_message, reply)
        state = peer.call(inspect=session.WorldSessionInspect()).state
        self.assertFalse(state.HasField('workspace'))
        self.assertEqual(world.management('world.info')['world_revision'], initial_revision)

    def test_authenticated_workspace_selection_and_confined_process(self):
        world = self.workspace(); peer = world.peers[0]; self.select(peer)
        process = self.launch_process(peer, "from pathlib import Path\nimport os\nassert Path.cwd()==Path('/project')\nassert 'NoNewPrivs:\\t1' in Path('/proc/self/status').read_text()\nassert 'DISPLAY' not in os.environ\nPath('actor-file').write_text('real')")
        self.wait_job(world, process); self.assertEqual((self.project/'actor-file').read_text(), 'real')
        self.assertEqual(peer.call(inspect=session.WorldSessionInspect()).state.workspace, 'one')

    def test_world_admission_and_availability_do_not_grant_workspace_access(self):
        self.enroll_second(); world = self.workspace(); peer = world.peers[1]
        self.assertTrue(self.authenticate_second(peer).HasField('opened'))
        self.denied(peer.act(workspace_select=core.WorkspaceIdRequest(id='one')), core.FORBIDDEN)
        self.assertFalse(self.own(peer).grants)
        self.grant_second(world, [grant.WORKSPACE_ACCESS]); self.select(peer)
        self.denied(peer.act(process_launch=core.ProcessLaunchRequest(argv=['/usr/bin/true'], operation_id='denied', profile='development')), core.FORBIDDEN)

    def test_grants_do_not_make_workspace_available_implicitly(self):
        world = self.workspace(); world.management('workspace.available', id='one', available=False)
        self.denied(world.peers[0].act(workspace_select=core.WorkspaceIdRequest(id='one')), core.UNAVAILABLE)

    def test_readonly_profile_keeps_project_and_git_readonly(self):
        world = self.workspace(); peer = world.peers[0]; self.select(peer)
        code = "from pathlib import Path\nassert Path('README.txt').read_text()=='base\\n'\nadmin=Path('.git').read_text().strip().split('gitdir: ',1)[1]\nfor name in ['README.txt',admin+'/HEAD']:\n try:Path(name).write_text('escape')\n except OSError:pass\n else:raise SystemExit(20)\nPath('/state/home/own-state').write_text('ok')"
        self.wait_job(world, self.launch_process(peer, code, profile='read-only'))
        self.assertEqual((self.project/'README.txt').read_text(), 'base\n')

    def test_partial_file_write_grants_do_not_mount_entire_project_writable(self):
        self.enroll_second(); world = self.workspace(); peer = world.peers[1]
        self.grant_second(world, [grant.WORKSPACE_ACCESS,grant.FILE_READ,grant.FILE_BROWSE,grant.FILE_EDIT,grant.PROCESS_EXECUTE])
        self.assertTrue(self.authenticate_second(peer).HasField('opened')); self.select(peer)
        code = "from pathlib import Path\ntry:Path('new').write_text('escape')\nexcept OSError:pass\nelse:raise SystemExit(21)"
        self.wait_job(world, self.launch_process(peer, code)); self.assertFalse((self.project/'new').exists())

    def test_workspace_policy_limits_a_broader_actor_grant(self):
        world = self.workspace(policy=['workspace.select','files.read']); peer = world.peers[0]; self.select(peer)
        self.denied(peer.act(process_launch=core.ProcessLaunchRequest(argv=['/usr/bin/true'], operation_id='denied', profile='development')), core.FORBIDDEN)

    def test_grant_revocation_kills_confined_process_without_recipient_request(self):
        self.enroll_second(); world = self.workspace(); peer = world.peers[1]
        child = self.grant_second(world, [grant.WORKSPACE_ACCESS,*PROJECT])
        self.assertTrue(self.authenticate_second(peer).HasField('opened')); process = self.running(world, peer)
        pids = self.descendants(); self.assertTrue(pids)
        self.revoke_grant(world.peers[0], child)
        self.until(lambda:self.gone(pids), bool)
        with self.assertRaisesRegex(AssertionError, 'UNAVAILABLE'):
            world.management('process.wait', id=process.process_id, timeout_seconds=0)
        self.assertFalse((self.project/'done').exists())

    def test_access_revocation_keeps_valid_process_execution(self):
        self.enroll_second(); world = self.workspace(); peer = world.peers[1]
        access = self.grant_second(world, [grant.WORKSPACE_ACCESS]); self.grant_second(world, PROJECT)
        self.assertTrue(self.authenticate_second(peer).HasField('opened')); process = self.running(world, peer)
        self.revoke_grant(world.peers[0], access)
        self.assertFalse(peer.call(inspect=session.WorldSessionInspect()).state.HasField('workspace'))
        (self.project/'release').touch(); self.wait_job(world, process); self.assertTrue((self.project/'done').exists())

    def test_actor_revocation_stops_execution_while_broker_is_idle(self):
        world = self.workspace(); process = self.running(world, world.peers[0]); pids = self.descendants()
        self.owner_peer = self.connect(); self.revoke(kind=issuer.ACTOR_TARGET)
        self.until(lambda:self.gone(pids), bool)
        with self.assertRaisesRegex(AssertionError, 'UNAVAILABLE'):
            world.management('process.wait', id=process.process_id, timeout_seconds=0)

    def test_credential_revocation_preserves_valid_workspace_process(self):
        world = self.workspace(); process = self.running(world, world.peers[0])
        principal = self.principal()
        before_reply = self.broker.request(authorization_fence=broker.AuthorizationFenceRequest(principal=principal))
        self.assertTrue(before_reply.HasField('authorization_fence'), before_reply)
        self.assertFalse(before_reply.HasField('error'), before_reply)
        before = before_reply.authorization_fence
        self.owner_peer = self.connect(); self.revoke(kind=issuer.CREDENTIAL_TARGET)
        first_reply, retry_reply = self.fence_with_checked_credential_retry(principal)
        after_reply = retry_reply or first_reply
        self.assertTrue(after_reply.HasField('authorization_fence'), after_reply)
        self.assertFalse(after_reply.HasField('error'), after_reply)
        after = after_reply.authorization_fence
        self.assertEqual(after.incarnation, before.incarnation)
        self.assertGreater(after.epoch, before.epoch)
        (self.project/'release').touch(); self.wait_job(world, process)
        self.assertTrue((self.project/'done').exists())

    def test_credential_revocation_during_fence_returns_checked_refusal_and_preserves_process(self):
        # Only the broker uses this relay. The world uses its ordinary direct issuer connection.
        barrier = {}
        def revoke_pending_status():
            started = time.monotonic()
            deadline = started + 2
            operation = issuer.IssuerOperation(identity_revoke=issuer.IdentityRevoke(
                target=issuer.IdentityReference(kind=issuer.CREDENTIAL_TARGET, id=self.identity.credential_id),
                expected_security_revision=0, reason='credential fence dispatch regression'))
            result = self.execute(self.owner_peer, self.challenge(self.owner_peer, operation))
            self.assertTrue(result.HasField('identity_status'), result)
            self.assertFalse(result.identity_status.actor_revoked, result)
            self.assertTrue(result.identity_status.credential_revoked, result)
            revision = result.identity_status.security_revision
            self.assertGreater(revision, 0, result)
            # The production tick logs consumption before incrementing cancellation_sequence.
            # That same tick must finish before the pending actor-status call can read its reply.
            diagnostic = self.wait_for_broker_revocation_consumed(revision, log_offset, deadline, relay)
            barrier.update(revision=revision, diagnostic=diagnostic,
                           elapsed_seconds=time.monotonic()-started)
        relay = world_authority.RelayingIssuer(self, revoke_pending_status, wait_for_revocation_event=False)
        world = self.workspace(issuer_port=relay.port)
        peer = world.peers[0]
        code = ("from pathlib import Path\nimport time\n"
                "Path('credential-race-ready').touch()\n"
                "while not Path('credential-race-release').exists():time.sleep(.01)\n"
                "Path('credential-race-done').touch()")
        self.select(peer)
        process = self.launch_process(peer, code, operation='credential-race-process')
        self.until(lambda: (self.project/'credential-race-ready').exists(), bool)
        process_id = process.process_id
        process_pid, process_start_ticks, command_line = self.process_pid_for_code(code)

        principal = self.principal()
        before_reply = self.broker.request(authorization_fence=broker.AuthorizationFenceRequest(principal=principal))
        self.assertTrue(before_reply.HasField('authorization_fence'), before_reply)
        self.assertFalse(before_reply.HasField('error'), before_reply)
        before = before_reply.authorization_fence
        # Verify the existing five-second policy. Do not extend deadlines for this interleaving.
        authority = text_format.Parse(self.authority_config.read_text(), broker.WorkspaceAuthorityConfig())
        client = text_format.Parse(Path(authority.issuer_client_config).read_text(), issuer.IssuerTlsClientConfig())
        self.assertEqual(client.io_timeout_ms, 5000, client)
        self.owner_peer = self.connect()
        log_offset = Path(self.broker.log.name).stat().st_size
        relay.armed.set()
        first_reply, retry_reply = self.fence_with_checked_credential_retry(principal)
        self.assertIsNotNone(retry_reply, f'Expected the deterministic in-flight refusal; got {first_reply!r}')
        self.assertFalse(first_reply.HasField('authorization_fence'), first_reply)
        self.assertTrue(first_reply.HasField('error'), first_reply)
        self.assertEqual(first_reply.error.code, core.FORBIDDEN, first_reply)
        self.assertEqual(first_reply.error.message, FENCE_REVOCATION_MESSAGE, first_reply)
        self.assertTrue(retry_reply.HasField('authorization_fence'), retry_reply)
        self.assertFalse(retry_reply.HasField('error'), retry_reply)
        self.assertGreater(retry_reply.request_id, first_reply.request_id)
        self.assertTrue(relay.triggered.is_set())
        self.assertEqual(relay.status_seen, 1)
        self.assertTrue(relay.delivered.is_set())
        self.assertFalse(relay.errors, relay.errors)
        self.assertIn('diagnostic', barrier, f'No broker consumption barrier: {first_reply!r}; {retry_reply!r}')
        self.assertLess(barrier['elapsed_seconds'], 2, barrier)
        print(f'Credential fence broker-consumption barrier: {barrier}', flush=True)
        print(f'Credential fence replies: in_flight={first_reply!r}; fresh={retry_reply!r}', flush=True)

        after = retry_reply.authorization_fence
        self.assertEqual(after.incarnation, before.incarnation)
        self.assertGreater(after.epoch, before.epoch)
        self.assertIn(process_pid, self.descendants(), 'Existing payload left the broker process tree')
        self.assertEqual(WorldProcess.process_start_ticks(process_pid), process_start_ticks,
                         f'Credential revocation replaced or stopped the live process: {first_reply!r}; {retry_reply!r}')
        self.assertEqual(Path(f'/proc/{process_pid}/cmdline').read_bytes(), command_line,
                         f'Live process argv changed: {first_reply!r}; {retry_reply!r}')
        (self.project/'credential-race-release').touch()
        self.assertEqual(world.management('process.wait', id=process_id, timeout_seconds=5), {'exit_code':0})
        self.assertTrue((self.project/'credential-race-done').exists())

    def test_enrollment_credential_id_comes_from_nested_actor_credential_payload(self):
        self.enroll_second()
        signed_claims = issuer.ActorCredential.FromString(self.second_record.credential.content.payload)
        self.assertEqual(signed_claims, self.second)
        self.assertEqual(signed_claims.credential_id, self.second.credential_id)

    def test_unrelated_revocation_refuses_inflight_fence_then_allows_retry_and_preserves_process(self):
        self.enroll_second()
        barrier = {}
        def revoke_unrelated_status():
            started = time.monotonic()
            deadline = started + 2
            operation = issuer.IssuerOperation(identity_revoke=issuer.IdentityRevoke(
                target=issuer.IdentityReference(kind=issuer.CREDENTIAL_TARGET,
                                                id=self.second.credential_id),
                expected_security_revision=0, reason='unrelated fence cancellation regression'))
            result = self.execute(self.owner_peer, self.challenge(self.owner_peer, operation))
            self.assertTrue(result.HasField('identity_status'), result)
            self.assertFalse(result.identity_status.actor_revoked, result)
            self.assertTrue(result.identity_status.credential_revoked, result)
            revision = result.identity_status.security_revision
            diagnostic = self.wait_for_broker_revocation_consumed(revision, log_offset, deadline, relay)
            barrier.update(revision=revision, diagnostic=diagnostic,
                           elapsed_seconds=time.monotonic()-started)
        relay = world_authority.RelayingIssuer(self, revoke_unrelated_status,
                                                wait_for_revocation_event=False)
        world = self.workspace(issuer_port=relay.port)
        peer = world.peers[0]
        code = ("from pathlib import Path\nimport time\n"
                "Path('unrelated-race-ready').touch()\n"
                "while not Path('unrelated-race-release').exists():time.sleep(.01)\n"
                "Path('unrelated-race-done').touch()")
        self.select(peer)
        process = self.launch_process(peer, code, operation='unrelated-race-process')
        self.until(lambda:(self.project/'unrelated-race-ready').exists(), bool)
        process_id = process.process_id
        process_pid, process_start_ticks, command_line = self.process_pid_for_code(code)
        principal = self.principal()
        before_reply = self.broker.request(authorization_fence=broker.AuthorizationFenceRequest(principal=principal))
        self.assertTrue(before_reply.HasField('authorization_fence'), before_reply)
        self.owner_peer = self.connect()
        log_offset = Path(self.broker.log.name).stat().st_size
        relay.armed.set()
        first_reply, retry_reply = self.fence_with_checked_credential_retry(principal)
        self.assertIsNotNone(retry_reply, f'Expected unrelated in-flight cancellation refusal: {first_reply!r}')
        self.assertEqual(first_reply.error.message, FENCE_REVOCATION_MESSAGE, first_reply)
        self.assertTrue(retry_reply.HasField('authorization_fence'), retry_reply)
        self.assertTrue(relay.delivered.is_set())
        self.assertFalse(relay.errors, relay.errors)
        self.assertIn('diagnostic', barrier)
        self.assertLess(barrier['elapsed_seconds'], 2, barrier)
        self.assertIn(process_pid, self.descendants(), 'Unrelated revocation stopped the authorized process')
        self.assertEqual(WorldProcess.process_start_ticks(process_pid), process_start_ticks)
        self.assertEqual(Path(f'/proc/{process_pid}/cmdline').read_bytes(), command_line)
        (self.project/'unrelated-race-release').touch()
        self.assertEqual(world.management('process.wait', id=process_id, timeout_seconds=5), {'exit_code':0})
        self.assertTrue((self.project/'unrelated-race-done').exists())

    def test_revoked_ancestor_without_world_session_stops_process(self):
        self.enroll_second(); world = self.workspace(); peer = world.peers[1]
        self.grant_second(world, [grant.WORKSPACE_ACCESS,*PROJECT])
        self.assertTrue(self.authenticate_second(peer).HasField('opened')); self.running(world, peer)
        pids = self.descendants(); world.peers[0].close()
        self.owner_peer = self.connect(); self.revoke(kind=issuer.ACTOR_TARGET)
        self.until(lambda:self.gone(pids), bool); self.assertFalse((self.project/'done').exists())

    def test_independent_execution_grant_survives_other_grant_revocation(self):
        self.enroll_second(); world = self.workspace(); peer = world.peers[1]
        child = self.grant_second(world, [grant.WORKSPACE_ACCESS,*PROJECT])
        self.grant_second(world, [grant.WORKSPACE_ACCESS,*PROJECT])
        self.assertTrue(self.authenticate_second(peer).HasField('opened')); process = self.running(world, peer)
        self.revoke_grant(world.peers[0], child)
        (self.project/'release').touch(); self.wait_job(world, process)
        self.assertTrue((self.project/'done').exists())

    def test_file_scope_does_not_authorize_a_whole_project_mount(self):
        self.enroll_second(); world = self.workspace(); peer = world.peers[1]
        self.grant_second(world, [grant.WORKSPACE_ACCESS,grant.PROCESS_EXECUTE])
        self.grant_second(world, PROJECT, components=[b'README.txt'])
        self.assertTrue(self.authenticate_second(peer).HasField('opened')); self.select(peer)
        self.denied(peer.act(process_launch=core.ProcessLaunchRequest(argv=['/usr/bin/true'],operation_id='scoped',profile='development')), core.FORBIDDEN)

    def test_cross_domain_unknown_and_foreign_grants_are_rejected(self):
        self.enroll_second(); world = self.workspace(); peer = world.peers[0]
        for fault in ('world','workspace','unknown','foreign'):
            with self.subTest(fault=fault):
                terms = self.workspace_terms(self.second, rights=[grant.POLICY_ADMINISTER], depth=0)
                if fault == 'world': terms.scope.domain.kind = grant.WORLD; terms.scope.domain.id = self.world_info['world_id']
                elif fault == 'workspace': terms.scope.domain.id = 'another'
                elif fault == 'unknown': terms.subject.actor_id = b'u'*32
                else: terms.subject.issuer_id = b'f'*32
                self.denied(self.delegate(peer, terms), core.FORBIDDEN)
        self.assertTrue(self.authenticate_second(world.peers[1]).HasField('opened'))
        self.assertFalse(self.own(world.peers[1]).grants)

    def test_bound_workspace_rejects_static_authority_edits(self):
        world = self.workspace(); peer = world.peers[0]
        actor = peer.call(inspect=session.WorldSessionInspect()).state.id
        with self.assertRaisesRegex(AssertionError, 'FORBIDDEN'):
            world.management('workspace.member.set',id='one',actor=actor,rights=POLICY)
        world.close()
        self.denied(self.broker.request(access=broker.AccessRequest(workspace='one',actor=actor)), core.FORBIDDEN)
        wrong = broker.AccessRequest(workspace='one',actor='chosen-name',principal=self.principal())
        self.denied(self.broker.request(access=wrong), core.INVALID_ARGUMENT)

    def test_persistent_grants_and_selection_survive_broker_and_world_restart(self):
        self.enroll_second(); world = self.workspace(); peer = world.peers[1]
        child = self.grant_second(world, [grant.WORKSPACE_ACCESS,*PROJECT])
        self.assertTrue(self.authenticate_second(peer).HasField('opened')); self.select(peer)
        process = self.launch_process(peer,"from pathlib import Path\nPath('durable').write_text('saved')")
        self.wait_job(world,process); world.close(); self.broker.close()
        self.broker = self.make_broker(self.execution,self.root/'workspace-policy.textproto')
        restarted = WorldProcess(self,world.config_path,world.state_path,runtime_config=world.runtime_path,broker=self.broker)
        self.worlds.append(restarted); restarted.management('system.hello'); peer = restarted.peers[0]
        self.assertTrue(self.authenticate_second(peer).HasField('opened'))
        self.assertEqual(peer.call(inspect=session.WorldSessionInspect()).state.workspace,'one')
        self.assertIn(child.id,[record.id for record in self.own(peer).grants])
        self.assertEqual((self.project/'durable').read_text(),'saved'); self.wait_job(restarted,process)

    def test_actor_revocation_interrupts_a_blocked_process_wait(self):
        world = self.workspace(); process = self.running(world,world.peers[0]); pids = self.descendants()
        request = make_request('process.wait',dict(id=process.process_id,timeout_seconds=5))
        world.sequence += 1; request.request_id = world.sequence
        world.proc.stdin.write(frame(request)); world.proc.stdin.flush(); time.sleep(.05)
        self.owner_peer = self.connect(); started = time.monotonic(); self.revoke(kind=issuer.ACTOR_TARGET)
        reply = receive(lambda n:os.read(world.proc.stdout.fileno(),n),world.proc.stdout,core.Reply)
        self.denied(reply,core.UNAVAILABLE); self.assertLess(time.monotonic()-started,2)
        self.until(lambda:self.gone(pids),bool)

    def test_partial_private_frame_does_not_block_revocation(self):
        world = self.workspace(); self.running(world,world.peers[0]); pids = self.descendants(); world.close()
        self.broker.stream.sendall(b'\x00\x00')
        self.owner_peer = self.connect(); self.revoke(kind=issuer.ACTOR_TARGET)
        self.until(lambda:self.gone(pids),bool); self.assertFalse((self.project/'done').exists())

    def test_retry_cannot_acknowledge_a_process_revoked_during_status_refresh(self):
        self.retry_revoked_process_after_idle(0)

    def test_retry_revocation_uses_live_owner_after_setup_exceeds_idle_timeout(self):
        self.retry_revoked_process_after_idle(self.config.idle_timeout_ms / 1000 + .2)

    def retry_revoked_process_after_idle(self, idle_seconds):
        self.enroll_second(); self.owner_peer = self.connect()
        relay = world_authority.RelayingIssuer(self,lambda:self.revoke(kind=issuer.ACTOR_TARGET))
        read = [grant.WORKSPACE_ACCESS,grant.FILE_READ,grant.FILE_BROWSE,grant.PROCESS_EXECUTE]
        world = self.workspace(roots=lambda:[self.workspace_terms(),self.workspace_terms(self.second,rights=read,depth=0)],issuer_port=relay.port)
        peer = world.peers[1]; self.grant_second(world,WRITE)
        self.assertTrue(self.authenticate_second(peer).HasField('opened')); process = self.running(world,peer)
        with sqlite3.connect(self.execution/'execution.db') as database:
            payload, = database.execute('SELECT payload FROM processes WHERE id=?',(process.process_id,)).fetchone()
        request = broker.LaunchRequest.FromString(payload); pids = self.descendants()
        if idle_seconds: time.sleep(idle_seconds)
        world.close()
        # Setup and sanitizer teardown can exceed the issuer's owner idle timeout.
        # Authenticate the revocation on a fresh connection before arming the relay.
        self.owner_peer = self.connect()
        relay.armed.set(); result = self.broker.request(launch=request)
        self.denied(result,core.UNAVAILABLE); self.assertTrue(relay.delivered.is_set())
        self.until(lambda:self.gone(pids),bool); self.assertFalse((self.project/'done').exists())


class GitFixtureEmptyProjectTests(unittest.TestCase):
    def test_empty_initial_commit_is_opt_in_and_real(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="spatial-empty-git-fixture-") as tmp:
            empty = GitRepo(Path(tmp) / "empty", empty_initial_commit=True)
            self.assertEqual(empty.run("ls-tree", "-r", "--name-only", "HEAD"), "")
            self.assertEqual(empty.run("rev-parse", "--verify", "HEAD"), empty.head())
            seeded = GitRepo(Path(tmp) / "seeded")
            self.assertEqual(seeded.run("ls-tree", "-r", "--name-only", "HEAD"), "README.txt")
            self.assertEqual((seeded.path / "README.txt").read_text(encoding="utf-8"), "base\n")


if __name__ == '__main__': unittest.main(verbosity=2)
