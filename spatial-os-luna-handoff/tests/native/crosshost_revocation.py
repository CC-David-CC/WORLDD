"""Real two-host cancellation with one issuer and independent native witnesses."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import socket
import sqlite3
import ssl
import struct
import sys
import tempfile
import threading
import time

parser = argparse.ArgumentParser()
parser.add_argument('mode', choices=('controller', 'worker'))
parser.add_argument('--source', type=Path, required=True)
parser.add_argument('--build', type=Path, required=True)
parser.add_argument('--bindings', type=Path)
parser.add_argument('--scenario', choices=('delay', 'crash'), default='delay')
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--bundle', type=Path)
parser.add_argument('--address')
parser.add_argument('--delayed-host', choices=('sb16', 'sb16-2'))
options = parser.parse_args()
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
os.umask(0o077)
options.output.mkdir(mode=0o700, parents=True, exist_ok=False)
os.environ['SPATIAL_SECURITY_EVIDENCE'] = str(options.output / 'native')
sys.path[:0] = [str(options.build / 'python'), str(options.source),
    str(options.source / 'tests/native'), str(options.source / 'tests/core')]
if options.bindings: sys.path.insert(0, str(options.bindings))
sys.argv = [sys.argv[0], str(options.source), str(options.build / 'spatial-authority-launch-probe'),
    str(options.build / 'spatial-security-auth'), str(options.build / 'identityd'),
    str(options.build / 'spatial-issuer-client')]
import test_session_application as application
import test_storage_session as storage
import test_supervisor_registry as registry
from test_identity_tls import TlsFixture, Peer
from test_issuer_client import ClientFixture
from google.protobuf import text_format
import crosshost_revocation_pb2 as wire
import core_pb2 as core
import grants_pb2 as grants
import issuer_pb2 as identity
import launch_pb2 as launch
import world_session_pb2 as session
from harness.wire import frame

HOSTS = ('sb16', 'sb16-2')
MATERIAL = ('server.pem', 'server.crt', 'service.pem', 'service.crt', 'owner.pem', 'actor.pem')

def receive(stream, kind):
    def exact(size):
        data = b''
        while len(data) < size:
            part = stream.recv(size-len(data))
            if not part: raise EOFError('Cross-host fixture connection closed')
            data += part
        return data
    size, = struct.unpack('!I', exact(4))
    assert 0 < size <= 1048576
    message = kind.FromString(exact(size))
    assert message.protocol_version == 1
    return message

def connect_issuer(fixture, address):
    raw = socket.create_connection((address, fixture.port), timeout=5)
    try: peer = Peer(fixture.context().wrap_socket(raw, server_hostname='issuer.test'))
    except BaseException: raw.close(); raise
    fixture.addCleanup(peer.close)
    return peer

class OpaqueHoldRelay:
    """Delay encrypted server bytes. TLS and issuer proofs remain end to end."""
    def __init__(self, address, port):
        self.address = address; self.port = port
        self.lock = threading.Lock(); self.hold = False; self.pending = bytearray()
        self.closed = threading.Event(); self.errors = []; self.connections = 0
        self.client = self.server = None
        self.listener = socket.socket(); self.listener.bind(('127.0.0.1', 0)); self.listener.listen(1)
        self.listener.settimeout(.1); self.local_port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self.run, daemon=True); self.thread.start()

    def set_hold(self, enabled):
        with self.lock:
            self.hold = enabled
            if not enabled and self.pending:
                self.client.sendall(self.pending); self.pending.clear()

    def snapshot(self):
        with self.lock: return self.hold, len(self.pending)

    def run(self):
        try:
            while not self.closed.is_set():
                try: self.client, _ = self.listener.accept(); break
                except socket.timeout: continue
            else: return
            self.connections += 1
            self.client.settimeout(3)
            self.server = socket.create_connection((self.address, self.port), timeout=3)
            while not self.closed.is_set():
                ready, _, _ = select.select([self.client, self.server], [], [], .05)
                for stream in ready:
                    data = stream.recv(65536)
                    if not data: return
                    if stream is self.client:
                        self.server.sendall(data)
                    else:
                        with self.lock:
                            if self.hold:
                                self.pending.extend(data)
                                assert len(self.pending) <= 1048576, 'Excessive withheld TLS bytes'
                            else: self.client.sendall(data)
        except ConnectionError:
            pass
        except (OSError, ValueError) as error:
            if not self.closed.is_set(): self.errors.append(error)
        except BaseException as error: self.errors.append(error)
        finally:
            for stream in (self.client, self.server):
                if stream: stream.close()

    def close(self):
        self.closed.set(); self.listener.close()
        for stream in (self.client, self.server):
            if stream:
                try: stream.shutdown(socket.SHUT_RDWR)
                except OSError: pass
        self.thread.join(4)
        assert not self.thread.is_alive(), 'TLS relay did not stop'
        if self.errors: raise self.errors[0]

class SharedIssuer(registry.SupervisorRegistryTests):
    def __init__(self, bootstrap, bundle):
        super().__init__('runTest'); self.bootstrap = bootstrap; self.bundle = bundle

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='spatial-crosshost-issuer-client-')
        self.addCleanup(self.temp.cleanup); self.root = Path(self.temp.name)
        self.connections = []; self.addCleanup(self.close_all)
        for name in MATERIAL:
            shutil.copyfile(self.bundle / name, self.root / name); (self.root / name).chmod(0o600)
        self.server_key = self.root / 'server.pem'; self.server_cert = self.root / 'server.crt'
        self.service_key = self.root / 'service.pem'; self.service_cert = self.root / 'service.crt'
        self.owner = self.root / 'owner.pem'; self.owner_public = self.bootstrap.owner_public_key
        self.actor_key = self.root / 'actor.pem'; self.actor_public = self.bootstrap.actor_public_key
        self.info = self.bootstrap.issuer; self.port = self.bootstrap.issuer_port
        self.signer, self.public = self.key('registered-authority')
        self.supervisor_key, self.supervisor_public = self.key('supervisor')
        self.peer = self.connect()
        self.registration = self.register_domain(os.urandom(32), self.public, grants.WORKSPACE, application.WORKSPACE)

    def connect(self, **unused):
        return connect_issuer(self, self.bootstrap.issuer_address)

    def config_file(self, endpoint_port=None, **overrides):
        values = dict(address=self.bootstrap.issuer_address, io_timeout_ms=5000)
        values.update(overrides)
        return ClientFixture.config_file(self, endpoint_port, **values)

    def register_domain(self, participant, public, kind, domain):
        peer = self.connect()
        page = self.execute(peer, self.challenge(peer, identity.IssuerOperation(
            registry_read=identity.AuthorityRegistryRead(limit=16))))
        self.assertTrue(page.HasField('registry'), page)
        operation = identity.IssuerOperation(participant_register=identity.AuthorityParticipantRegister(
            participant=identity.AuthorityParticipant(id=participant, public_key=public,
                domains=[grants.Domain(kind=kind, id=domain)]),
            expected_registry_revision=page.registry.registry_revision))
        reply = self.execute(peer, self.challenge(peer, operation))
        self.assertTrue(reply.HasField('participant'), reply)
        return reply.participant

    def register(self, identity=None, public=None, domain=None, revision=None):
        # The shared registry assigns the real next revision. Hostnames are fixture labels only.
        return self.register_domain(os.urandom(32), public, grants.WORLD, domain)

    def binding_operation(self, expected=0, public=None, registration=None, participant=None):
        return super().binding_operation(expected=expected, public=public,
            registration=self.registration.registered_revision if registration is None else registration,
            participant=participant)

class SharedSession(storage.NativeStorageSessionTests):
    def __init__(self, bootstrap, bundle):
        super().__init__('runTest'); self.bootstrap = bootstrap; self.bundle = bundle

    def setUp(self):
        self._testMethodName = 'test_native_storage_crosshost_' + host
        super().setUp()

    def issuer_fixture(self):
        return SharedIssuer(self.bootstrap, self.bundle)

    def enrolled_actor(self):
        return self.bootstrap.actor

    def world_config(self):
        config = super().world_config()
        self.feed = OpaqueHoldRelay(self.bootstrap.issuer_address, self.bootstrap.issuer_port)
        self.addCleanup(self.feed.close)
        path = Path(config.participant.runtime_config)
        runtime = text_format.Parse(path.read_text(), identity.AuthorityParticipantRuntimeConfig())
        runtime.issuer_client_config = str(self.issuer.config_file(self.feed.local_port, address='127.0.0.1'))
        path.write_text(text_format.MessageToString(runtime))
        return config

    def check_active_participants(self):
        # The controller checks all four exact records against the shared real issuer database.
        # No local shadow database or fabricated activation row is used here.
        for record in (self.world_record, self.read_record()):
            self.assertEqual(record.stage, launch.START_ATTEMPTED)
            self.assertTrue(record.HasField('authorization'))
            self.assertEqual(record.binding.issuer_id, self.bootstrap.issuer.issuer_id)
        self.assertNotEqual(self.world_record.binding.participant_id, self.read_record().binding.participant_id)

    def state(self):
        rows = self.events()
        seen = lambda event, pressed: any(row.get('event') == event and row.get('pressed') == pressed for row in rows)
        held, count = self.feed.snapshot()
        return wire.CrossHostState(host=host, world=self.world_domain, workspace=application.WORKSPACE,
            world_launch=self.world_record, broker_launch=self.read_record(), application=self.app,
            native_key_press=seen('key', True), native_button_press=seen('button', True),
            native_key_release=seen('key', False), native_button_release=seen('button', False),
            feed_held=held, held_bytes=count)

    def cancelled(self):
        self.released()
        current = self.request(inspect=self.owner).owner
        self.assertFalse(current.channel_fenced)
        self.assertEqual(list(current.reservations), [])
        self.reject_old_actor()
        value = self.state()
        self.assertTrue(value.native_key_release and value.native_button_release)
        value.application_responded_after_cancellation = True
        value.reservation_released = True; value.old_actor_rejected = True
        return value

    def reject_old_actor(self):
        try:
            reply = self.actor_call(self.stream, inspect=session.WorldSessionInspect())
            self.assertEqual(reply.error.code, core.UNAUTHORIZED, reply)
        except (EOFError, ConnectionError): pass

    def crashed(self):
        self.assertTrue(self.feed.snapshot()[0])
        self.crash_cleanup = self.crash_world()
        self.assertTrue(self.crash_cleanup.HasField('fence'))
        # A checked process/native receipt alone cannot retire the retained reservation.
        self.request(expect_error=True, retire_world=self.owner)
        self.reject_old_actor()
        value = self.state()
        value.world_cleanup.CopyFrom(self.crash_cleanup)
        value.control_state.CopyFrom(self.request(inspect=self.owner).owner)
        self.assertTrue(value.control_state.channel_fenced)
        self.assertEqual(list(value.control_state.reservations), [self.lease])
        value.application_responded_after_cancellation = True
        value.old_actor_rejected = True; value.premature_retirement_rejected = True
        return value

    def recovered(self):
        self.assertTrue(hasattr(self, 'crash_cleanup'))
        recovered = self.request(recover_world=self.owner).owner
        self.assertTrue(recovered.channel_fenced); self.assertFalse(recovered.reservations)
        self.assertEqual(list(recovered.recovery.released), [self.lease])
        with sqlite3.connect('file:' + str(self.world_journal / 'launch.db') + '?mode=ro', uri=True) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)
            saved, = db.execute('SELECT record FROM dependent_cleanup').fetchall()
        self.dependent_bytes = saved[0]
        receipt = launch.AuthorityDependentCleanup.FromString(self.dependent_bytes)
        self.assertEqual(receipt.schema_version, 1)
        self.assertEqual(receipt.world, self.crash_cleanup)
        self.assertEqual(receipt.broker, self.read_record())
        self.assertEqual(receipt.controls, recovered.recovery)
        (options.output / 'dependent-cleanup.pb').write_bytes(self.dependent_bytes)
        self.released(); self.reject_old_actor()
        value = self.state()
        value.world_cleanup.CopyFrom(self.crash_cleanup); value.control_state.CopyFrom(recovered)
        value.dependent_cleanup_sha256 = hashlib.sha256(self.dependent_bytes).digest()
        value.application_responded_after_cancellation = True
        value.old_actor_rejected = True; value.reservation_released = True
        return value

    def retired(self):
        self.assertTrue(hasattr(self, 'dependent_bytes'))
        receipt = self.request(retire_world=self.owner).retirement
        self.assertEqual(receipt.request.owner, self.owner.launch)
        self.assertEqual(receipt.request.fence_id, self.crash_cleanup.fence.fence_id)
        self.assertEqual(receipt.request.cleanup_sha256, hashlib.sha256(self.dependent_bytes).digest())
        self.assertEqual(receipt.applied_revision, 0)
        with sqlite3.connect('file:' + str(self.world_journal / 'launch.db') + '?mode=ro', uri=True) as db:
            self.assertEqual(db.execute('SELECT record FROM issuer_retirement').fetchall(), [(receipt.SerializeToString(),)])
            self.assertEqual(db.execute('SELECT record FROM dependent_cleanup').fetchall(), [(self.dependent_bytes,)])
        self.assertEqual(self.request(retire_world=self.owner).retirement, receipt)
        self.released(); self.reject_old_actor()
        value = self.state(); value.retirement.CopyFrom(receipt)
        value.world_cleanup.CopyFrom(self.crash_cleanup)
        value.control_state.CopyFrom(self.request(inspect=self.owner).owner)
        value.dependent_cleanup_sha256 = hashlib.sha256(self.dependent_bytes).digest()
        self.assertTrue(value.control_state.channel_fenced); self.assertFalse(value.control_state.reservations)
        value.application_responded_after_cancellation = True
        value.old_actor_rejected = True; value.reservation_released = True
        return value

def worker():
    assert options.bundle and not options.address
    bootstrap = wire.CrossHostBootstrap.FromString((options.bundle / 'bootstrap.pb').read_bytes())
    assert bootstrap.protocol_version == 1 and len(bootstrap.run_id) == 32
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.load_verify_locations(cafile=str(options.bundle / 'server.crt'))
    context.load_cert_chain(str(options.bundle / 'service.crt'), str(options.bundle / 'service.pem'))
    raw = socket.create_connection((bootstrap.issuer_address, bootstrap.control_port), timeout=60)
    stream = context.wrap_socket(raw, server_hostname='issuer.test'); stream.settimeout(45)
    fixture = None; completed = False
    report = dict(status='starting', host=host, scenario=options.scenario, production_issuer=bootstrap.issuer.issuer_id.hex())
    try:
        stream.sendall(frame(wire.CrossHostCommand(protocol_version=1, request_id=0,
            join=wire.CrossHostJoin(run_id=bootstrap.run_id, host=host))))
        last = 0
        while True:
            command = receive(stream, wire.CrossHostCommand)
            assert command.request_id > last; last = command.request_id
            reply = wire.CrossHostReply(protocol_version=1, request_id=last)
            try:
                operation = command.WhichOneof('operation')
                if operation == 'start':
                    assert fixture is None
                    application.WORKSPACE = 'crosshost-workspace-' + host
                    fixture = SharedSession(bootstrap, options.bundle)
                    fixture.setUp(); fixture.held_application()
                    reply.state.CopyFrom(fixture.state())
                    report['ready'] = True
                elif operation == 'hold_world_feed':
                    assert fixture is not None
                    fixture.feed.set_hold(command.hold_world_feed.enabled)
                    reply.state.CopyFrom(fixture.state())
                elif operation == 'cancelled':
                    reply.state.CopyFrom(fixture.cancelled()); report['cancelled'] = True
                elif operation == 'crash_world':
                    reply.state.CopyFrom(fixture.crashed()); report['crashed'] = True
                elif operation == 'recover_world':
                    reply.state.CopyFrom(fixture.recovered()); report['recovered'] = True
                elif operation == 'retire_world':
                    reply.state.CopyFrom(fixture.retired()); report['retired'] = True
                elif operation == 'stop':
                    if fixture:
                        if hasattr(fixture, 'crash_cleanup'):
                            stopped = fixture.crash_cleanup
                        else:
                            fixture.feed.set_hold(False)
                            stopped = fixture.request(stop_world=fixture.owner).cleanup
                        fixture.assertEqual(stopped.stage, launch.OWNED_SCOPES_STOPPED)
                        fixture.assertEqual(stopped.process_stopped.identity, fixture.world_record.process)
                        fixture.assertEqual(stopped.native_stopped.identity, fixture.world_record.native)
                        fixture.assertFalse((fixture.cgroup / stopped.process.scope_name).exists())
                        fixture.finish_session()
                    reply.state.host = host
                    stream.sendall(frame(reply)); completed = True; break
                else: raise AssertionError('Unknown fixture command')
            except BaseException as error:
                reply.error = type(error).__name__ + ': ' + str(error)
                stream.sendall(frame(reply)); raise
            (options.output / ('reply-' + str(last) + '.pb')).write_bytes(reply.SerializeToString())
            stream.sendall(frame(reply))
    finally:
        stream.close()
        cleanup_ok = True
        if fixture:
            fixture.tearDown(); cleanup_ok = fixture.doCleanups()
        report['status'] = 'passed' if completed and cleanup_ok else 'failed'
        (options.output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        assert cleanup_ok, 'Worker cleanup failed'
    assert completed

class ControllerIssuer(TlsFixture):
    def launch(self, address=None):
        return super().launch(options.address)

    def connect(self, **unused):
        return connect_issuer(self, options.address)

def controller():
    assert options.address and options.delayed_host
    fixture = ControllerIssuer('runTest'); fixture.setUp()
    fixture.config.io_timeout_ms = 5000; fixture.config.idle_timeout_ms = 60000
    fixture.config.connection_limit = 32
    peers = {}; states = {}; commands = 0; completed = False
    report = dict(status='starting', issuer_host=host, delayed_host=options.delayed_host, scenario=options.scenario)
    listener = socket.socket(); listener.bind((options.address, 0)); listener.listen(2); listener.settimeout(120)
    def call(target, **operation):
        nonlocal commands
        commands += 1
        request = wire.CrossHostCommand(protocol_version=1, request_id=commands, **operation)
        peers[target].sendall(frame(request))
        reply = receive(peers[target], wire.CrossHostReply)
        assert reply.request_id == commands and reply.HasField('state'), reply
        (options.output / (target + '-reply-' + str(commands) + '.pb')).write_bytes(reply.SerializeToString())
        return reply.state
    def barrier():
        peer = fixture.connect()
        operation = identity.IssuerOperation(barrier_read=identity.AuthorityBarrierRead(revision=1))
        result = fixture.execute(peer, fixture.challenge(peer, operation)); peer.close()
        assert result.HasField('authority_barrier'), result
        return result.authority_barrier
    try:
        peer = fixture.ready()
        actor = fixture.decide(peer, fixture.enroll(peer)).enrollment
        claims = fixture.claims(actor, fixture.info)
        bootstrap = wire.CrossHostBootstrap(protocol_version=1, run_id=os.urandom(32),
            issuer_address=options.address, issuer_port=fixture.port, control_port=listener.getsockname()[1],
            issuer=fixture.info, actor=actor, owner_public_key=fixture.owner_public, actor_public_key=fixture.actor_public)
        bundle = options.output / 'private-bundle'; bundle.mkdir(mode=0o700)
        for name in MATERIAL:
            shutil.copyfile(fixture.root / name, bundle / name); (bundle / name).chmod(0o600)
        (bundle / 'bootstrap.pb').write_bytes(bootstrap.SerializeToString())
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
        context.load_cert_chain(str(fixture.server_cert), str(fixture.server_key))
        context.load_verify_locations(cafile=str(fixture.service_cert)); context.verify_mode = ssl.CERT_REQUIRED
        report.update(status='waiting_for_workers', bundle=str(bundle), control_port=bootstrap.control_port,
            issuer_port=fixture.port, issuer_id=fixture.info.issuer_id.hex())
        (options.output / 'ready.json').write_text(json.dumps(report, indent=2) + '\n')
        print('Ready for workers', flush=True)
        for _ in HOSTS:
            raw, address = listener.accept(); raw.settimeout(30)
            stream = context.wrap_socket(raw, server_side=True)
            join = receive(stream, wire.CrossHostCommand)
            assert join.request_id == 0 and join.HasField('join') and join.join.run_id == bootstrap.run_id
            assert join.join.host in HOSTS and join.join.host not in peers
            peers[join.join.host] = stream
        for name in HOSTS:
            state = call(name, start=wire.CrossHostEmpty()); states[name] = state
            assert state.host == name and state.native_key_press and state.native_button_press
            assert not state.native_key_release and not state.native_button_release
        assert states['sb16'].world != states['sb16-2'].world
        assert states['sb16'].workspace != states['sb16-2'].workspace
        expected = {}
        for state in states.values():
            for record in (state.world_launch, state.broker_launch):
                key = record.binding.participant_id
                assert key not in expected and record.binding.issuer_id == fixture.info.issuer_id
                expected[key] = (record.authorization.request.previous_generation + 1,
                    record.binding.service_incarnation, 1, 0)
        with sqlite3.connect('file:' + str(fixture.state / 'identity.db') + '?mode=ro', uri=True) as db:
            actual = {row[0]: row[1:] for row in db.execute('SELECT participant_id,generation,service_incarnation,active,applied_revision FROM authority_activation')}
        assert actual == expected and len(actual) == 4, actual
        report['active_participants'] = len(actual)
        target = options.delayed_host; other = next(name for name in HOSTS if name != target)
        held = call(target, hold_world_feed=wire.CrossHostHold(enabled=True)); assert held.feed_held
        peer = fixture.connect()
        operation = identity.IssuerOperation(identity_revoke=identity.IdentityRevoke(
            target=identity.IdentityReference(kind=identity.CREDENTIAL_TARGET, id=claims.credential_id),
            expected_security_revision=0, reason='cross-host native cancellation qualification'))
        revoked = fixture.execute(peer, fixture.challenge(peer, operation)); peer.close()
        assert revoked.HasField('identity_status') and revoked.identity_status.security_revision == 1
        pending = barrier(); assert pending.participant_count == 4 and pending.pending_count > 0 and not pending.complete
        target_id = states[target].world_launch.binding.participant_id
        obligation, = [value for value in pending.obligations if value.registration.participant.id == target_id]
        assert not obligation.complete and obligation.applied_revision == 0
        assert obligation.service_incarnation == states[target].world_launch.binding.service_incarnation
        (options.output / 'pending-barrier.pb').write_bytes(pending.SerializeToString())
        cancelled = call(other, cancelled=wire.CrossHostEmpty())
        assert cancelled.application_responded_after_cancellation and cancelled.reservation_released and cancelled.old_actor_rejected
        pending = barrier()
        assert not pending.complete and any(value.registration.participant.id == target_id and not value.complete for value in pending.obligations)
        held = call(target, hold_world_feed=wire.CrossHostHold(enabled=True))
        assert held.feed_held and held.held_bytes > 0, 'The relay did not withhold actual encrypted reply bytes'
        assert not held.native_key_release and not held.native_button_release
        if options.scenario == 'delay':
            released = call(target, hold_world_feed=wire.CrossHostHold(enabled=False)); assert not released.feed_held
            cancelled = call(target, cancelled=wire.CrossHostEmpty())
            assert cancelled.application_responded_after_cancellation and cancelled.reservation_released and cancelled.old_actor_rejected
        else:
            def still_pending(label):
                value = barrier()
                outstanding, = [item for item in value.obligations if item.registration.participant.id == target_id]
                assert not value.complete and not outstanding.complete and outstanding.applied_revision == 0
                assert not outstanding.HasField('retirement')
                (options.output / (label + '-barrier.pb')).write_bytes(value.SerializeToString())
            crashed = call(target, crash_world=wire.CrossHostEmpty())
            assert crashed.native_key_release and crashed.native_button_release and crashed.old_actor_rejected
            assert crashed.application_responded_after_cancellation and crashed.premature_retirement_rejected
            assert crashed.world_cleanup.stage == launch.OWNED_SCOPES_STOPPED
            assert crashed.control_state.channel_fenced and len(crashed.control_state.reservations) == 1
            still_pending('crashed')
            recovered = call(target, recover_world=wire.CrossHostEmpty())
            assert recovered.control_state.channel_fenced and not recovered.control_state.reservations
            assert recovered.reservation_released and recovered.application_responded_after_cancellation
            still_pending('recovered')
            retired = call(target, retire_world=wire.CrossHostEmpty())
            assert retired.HasField('retirement') and retired.retirement.applied_revision == 0
            assert retired.retirement.request.cleanup_sha256 == recovered.dependent_cleanup_sha256
            assert retired.application_responded_after_cancellation and retired.old_actor_rejected
        deadline = time.monotonic()+8
        while True:
            complete = barrier()
            if complete.complete: break
            assert time.monotonic() < deadline, complete
            time.sleep(.02)
        assert complete.participant_count == 4 and complete.pending_count == 0 and len(complete.obligations) == 4
        assert {item.registration.participant.id for item in complete.obligations} == set(expected)
        for item in complete.obligations:
            assert item.complete
            if options.scenario == 'crash' and item.registration.participant.id == target_id:
                assert item.applied_revision == 0 and item.retirement == retired.retirement
            else: assert item.applied_revision == 1 and not item.HasField('retirement')
        # Recovery on one host must not change the other's launch identity or live application.
        survivor = call(other, cancelled=wire.CrossHostEmpty()) if options.scenario == 'crash' else cancelled
        if options.scenario == 'crash':
            assert survivor.world_launch == states[other].world_launch and survivor.broker_launch == states[other].broker_launch
            assert survivor.application == states[other].application and survivor.application_responded_after_cancellation
        (options.output / 'complete-barrier.pb').write_bytes(complete.SerializeToString())
        for name in HOSTS: call(name, stop=wire.CrossHostEmpty())
        completed = True
        report.update(status='passed', exact_active_generations=4, committed_revision=1, pending_obligation_observed=True,
            native_cancellation_hosts=list(HOSTS), applications_survived=2,
            complete_by_acknowledgement=options.scenario == 'delay',
            retired_worlds=1 if options.scenario == 'crash' else 0)
    finally:
        listener.close()
        for stream in peers.values(): stream.close()
        fixture.stop_daemons()
        for path in fixture.root.glob('tls-*.log'):
            data = path.read_bytes(); (options.output / path.name).write_bytes(data)
            assert not any(marker in data for marker in (b'AddressSanitizer', b'LeakSanitizer', b'runtime error:'))
        fixture.tearDown(); cleanup_ok = fixture.doCleanups()
        if not completed or not cleanup_ok: report['status'] = 'failed'
        (options.output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        assert cleanup_ok, 'Issuer fixture cleanup failed'
    assert completed

if options.mode == 'worker': worker()
else: controller()
