#!/usr/bin/env python3
"""Provision and clean a bounded two-authority production TLS transition fixture.

Run only on sb16/sb16-2 after primary deploys this bundle to both hosts.
Python provisions trusted management/issuer state. Every semantic actor operation
runs in the existing C++ TLS probe and production transition coordinator.
"""
import argparse
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

from google.protobuf import text_format
import actor_session_client_pb2 as tls_pb
import actor_transition_crosshost_pb2 as plan_pb
import core_pb2 as core
import grants_pb2 as grants
import issuer_pb2 as issuer
import key_pb2 as keys
import world_session_pb2 as session
from harness.wire import frame, make_request, read_message, result_value
import actor_transition_crosshost_host as host_support
import test_actor_transition_crosshost as harness

HOSTS = ('sb16', 'sb16-2')

def deployed_source(bundle):
    return bundle / 'source' if (bundle / 'source').is_dir() else bundle

def deployed_profile(bundle, profile):
    name = {'debug': 'Debug', 'asan': 'ASan-UBSan'}[profile]
    candidate = bundle / 'build' / name
    if (bundle / 'source').is_dir() or (bundle / 'provenance.json').is_file():
        if not candidate.is_dir():
            raise RuntimeError('Final deployment is missing the requested build profile: ' + name)
        return candidate
    return candidate if candidate.is_dir() else bundle / profile

def fixture_path(bundle, name):
    source = deployed_source(bundle)
    candidates = (source / 'tests' / 'core' / name, source / name)
    return next((path for path in candidates if path.is_file()), candidates[0])


def write_private(path, message):
    path.write_text(text_format.MessageToString(message), encoding='utf-8')
    path.chmod(0o600)
    return path


def import_issuer_fixture(bundle, profile):
    # Existing test modules consume production executable arguments at import.
    previous = sys.argv[:]
    artifacts = deployed_profile(bundle, profile)
    sys.argv = [sys.argv[0], str(artifacts / 'identityd'),
                str(artifacts / 'spatial-issuer-client'), str(artifacts / 'worldd')]
    try:
        from test_world_identity import WorldIdentityFixture
        from test_identity_tls import TlsFixture
    finally:
        sys.argv = previous

    class OwnedIssuer(WorldIdentityFixture):
        def setup_owned(self, root, address, evidence, fail_after=None):
            self.root, self.address, self.evidence = root, address, evidence
            self.fail_after = fail_after
            self.bundle_binary = deployed_profile(bundle, profile) / 'identityd'
            self.state = root / 'issuer'
            self.state.mkdir(mode=0o700)
            self.connections, self.daemons, self.worlds = [], [], []
            self.registered_services, self.registry_revision = {}, 0
            self.owner, self.owner_public = self.key('owner')
            self.actor_key, self.actor_public = self.key('actor')
            self.public_file = root / 'owner.pb'
            self.public_file.write_bytes(frame(keys.KeyReply(protocol_version=1,
                public_key=keys.PublicKey(ed25519=self.owner_public))))
            self.info = self.init()
            self.server_key, self.server_cert = self.certificate('server', 'issuer.test')
            self.service_key, self.service_cert = self.certificate('service', 'service.test')
            self.config = issuer.IssuerTlsServerConfig(protocol_version=1,
                certificate_path=str(self.server_cert), private_key_path=str(self.server_key),
                client_ca_path=str(self.service_cert), io_timeout_ms=5000,
                idle_timeout_ms=30000, connection_limit=32)
            self.config_path = root / 'tls.textproto'
            self.owner_peer = self.ready()
            self.record = self.decide(self.owner_peer, self.enroll(self.owner_peer)).enrollment
            self.identity = self.claims(self.record, self.info)

        def launch(self, address='0.0.0.0'):
            proc = TlsFixture.launch(self, '0.0.0.0')
            self.daemon_record = None
            host_support.atomic_json(self.evidence / 'issuer-process-attempt.json', dict(
                pid=proc.pid, port=self.port, address=self.address,
                database=str(self.state / 'identity.db'), identity_record='pending'))
            if self.fail_after == 'issuer-record-after-spawn':
                raise RuntimeError('Injected process identity record acquisition failure after issuer spawn')
            self.daemon_record = host_support.identity(proc.pid)
            host_support.atomic_json(self.root / 'issuer-process.json', dict(
                process=self.daemon_record, port=self.port, address=self.address,
                database=str(self.state / 'identity.db')))
            return proc

        def stop_owned_issuer(self, proc):
            if proc.poll() is not None:
                return proc.returncode
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
            try:
                return proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                record = getattr(self, 'daemon_record', None)
                if record is None:
                    # Reacquire verified identity before any signal. Popen still retains
                    # the unreaped direct child, so its PID cannot be reused here.
                    record = host_support.identity(proc.pid)
                    self.daemon_record = record
                    host_support.atomic_json(self.root / 'issuer-process.json', dict(
                        process=record, port=self.port, address=self.address,
                        database=str(self.state / 'identity.db')))
                return host_support.stop_child(proc, record)

        def connect(self, *args, **kwargs):
            peer = super().connect(*args, **kwargs)
            original = peer.request
            def logged_request(request):
                reply = original(request)
                with (self.evidence / 'issuer-management.jsonl').open('a') as audit:
                    audit.write(json.dumps(dict(operation=request.WhichOneof('operation'),
                        request_id=reply.request_id, outcome=reply.WhichOneof('outcome'))) + '\n')
                return reply
            peer.request = logged_request
            return peer

        def config_file(self, endpoint_port=None, **overrides):
            overrides.setdefault('address', self.address)
            return super().config_file(endpoint_port, **overrides)

        def openssl(self, *args):
            # Keys/signatures stay private. Log argv/exit, never payload or key bytes.
            with (self.evidence / 'issuer-commands.jsonl').open('a') as audit:
                audit.write(json.dumps(dict(argv=['openssl', *map(str, args)])) + '\n')
            return super().openssl(*args)

        def initialize(self, state=None, public=None):
            host_support.atomic_json(self.evidence / 'issuer-init-command.json', dict(argv=[
                str(self.bundle_binary), '--init', '--state-dir', str(state or self.state),
                '--owner-public', str(public or self.public_file)]))
            result = super().initialize(state, public)
            (self.evidence / 'issuer-init.stderr').write_bytes(result.stderr)
            (self.evidence / 'issuer-init.exit').write_text(str(result.returncode) + '\n')
            return result

        def cleanup_owned(self):
            # Preserve issuer rows and coherent durable state before process shutdown.
            try:
                if (self.state / 'identity.db').exists():
                    with sqlite3.connect((self.state / 'identity.db').as_uri() + '?mode=ro', uri=True) as source:
                        rows = {}
                        tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                        for table in ('actor', 'credential', 'enrollment', 'authority_participant', 'authority_participant_domain'):
                            values = source.execute('SELECT * FROM ' + table).fetchall() if table in tables else []
                            rows[table] = [[value.hex() if isinstance(value, bytes) else value for value in row]
                                           for row in values]
                        host_support.atomic_json(self.evidence / 'issuer-owned-rows.json', rows)
                        with sqlite3.connect(self.evidence / 'identity.db') as destination:
                            source.backup(destination)
            finally:
                peers_clean = self.doCleanups()  # Existing registered TLS peer closures only.
                for proc, log in reversed(getattr(self, 'daemons', [])):
                    code = None
                    try:
                        code = self.stop_owned_issuer(proc)
                    finally:
                        log.flush()
                        (self.evidence / 'issuer.stderr').write_bytes(Path(log.name).read_bytes())
                        if code is None and proc.poll() is None:
                            (self.evidence / 'issuer.exit').write_text('UNREAPED\n')
                        else:
                            (self.evidence / 'issuer.exit').write_text(str(code if code is not None else proc.poll()) + '\n')
                        if proc.stdout and not proc.stdout.closed:
                            proc.stdout.close()
                        log.close()
                    if code:
                        raise RuntimeError('Owned issuer shutdown was not clean')
                if not peers_clean:
                    raise RuntimeError('Issuer peer cleanup failed')

    return OwnedIssuer()


class Run:
    def __init__(self, args):
        self.bundle, self.profile, self.output = args.bundle.resolve(), args.profile, args.output.resolve()
        self.source = deployed_source(self.bundle)
        self.artifacts = deployed_profile(self.bundle, self.profile)
        self.python = self.artifacts / 'python'
        self.token = uuid.uuid4().hex
        self.resources = []
        self.issuer = None
        self.local_root = None
        self.addresses = {}
        self.worlds = {}
        self.command_number = 0
        self.deadline = time.monotonic() + 1000
        self.fail_after = args.fail_after

    def checkpoint(self, name):
        with (self.output / 'setup-checkpoints.txt').open('a') as output:
            output.write(name + '\n')
        if self.fail_after == name:
            raise RuntimeError('Requested partial setup failure at ' + name)

    def command(self, argv, payload=None, timeout=25, private_output=False):
        self.command_number += 1
        number = f'{self.command_number:04d}'
        host_support.atomic_json(self.output / (number + '-command.json'), dict(argv=list(map(str, argv)), timeout=timeout))
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Fixture total deadline expired')
        with subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
            host_support.atomic_json(self.output / (number + '.process.json'), dict(pid=proc.pid, argv=list(map(str, argv))))
            try:
                stdout, stderr = proc.communicate(payload, timeout=min(timeout, remaining))
            except subprocess.TimeoutExpired:
                proc.kill()  # Retained unreaped Popen child. No PID lookup or broad kill.
                stdout, stderr = proc.communicate(timeout=3)
                (self.output / (number + '.stderr')).write_bytes(stderr)
                (self.output / (number + '.stdout')).write_bytes(b'' if private_output else stdout)
                (self.output / (number + '.exit')).write_text('TIMEOUT\n')
                raise
            except BaseException:
                if proc.poll() is None:
                    proc.kill()
                stdout, stderr = proc.communicate(timeout=3)
                (self.output / (number + '.stderr')).write_bytes(stderr)
                (self.output / (number + '.stdout')).write_bytes(b'' if private_output else stdout)
                (self.output / (number + '.exit')).write_text('CANCELLED\n')
                raise
            result = subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
        (self.output / (number + '.stderr')).write_bytes(result.stderr)
        (self.output / (number + '.stdout')).write_bytes(b'' if private_output else result.stdout)
        (self.output / (number + '.exit')).write_text(str(result.returncode) + '\n')
        if result.returncode:
            raise RuntimeError(f'Command {number} failed ({result.returncode}); inspect command logs')
        return result.stdout

    def remote(self, host, argv, payload=None, timeout=25, private_output=False):
        if host not in HOSTS:
            raise ValueError('Unauthorized runtime host')
        return self.command(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', host,
                             shlex.join(list(map(str, argv)))], payload, timeout, private_output)

    def helper(self, host, operation, root=None, payload=None, **options):
        command = ['env', 'PYTHONPATH=' + str(self.python) + ':' + str(self.source / 'tests' / 'core') + ':' + str(self.source),
                   'LD_LIBRARY_PATH=' + str(self.artifacts / 'lib'),
                   'PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python',
                   'ASAN_OPTIONS=detect_leaks=1:halt_on_error=1', 'UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1',
                   'python3', str(fixture_path(self.bundle, 'actor_transition_crosshost_host.py')), operation, self.token]
        if root:
            command += ['--root', str(root)]
        for name, value in options.items():
            command += ['--' + name, str(value)]
        return self.remote(host, command, payload, timeout=40 if operation in ('stop', 'snapshot') else 25)

    def put(self, host, root, name, data):
        self.helper(host, 'put', root, data, name=name)

    def management(self, host, method, **arguments):
        output = self.helper(host, 'management', self.worlds[host]['root'], frame(make_request(method, arguments)))
        reply = harness.read_frame(output, core.Reply)
        if reply.protocol_version != 1 or reply.HasField('error'):
            raise AssertionError('Production management failed: ' + str(reply))
        return result_value(reply)

    def verify_remote_source(self, host):
        provenance = self.bundle / 'provenance.json'
        if not provenance.is_file():
            self.remote(host, ['sh', '-c', 'cd ' + shlex.quote(str(self.source)) + ' && sha256sum -c SHA256SUMS'], timeout=60)
            return
        record = json.loads(provenance.read_text())
        payload = json.dumps(dict(root=str(self.bundle / 'source'), files=record['source_sha256'])).encode()
        script = "import hashlib,json,pathlib,sys; d=json.load(sys.stdin); r=pathlib.Path(d['root']); b=[n for n,h in d['files'].items() if hashlib.sha256((r/n).read_bytes()).hexdigest()!=h]; print(json.dumps({'ok':not b,'bad':b[:8]})); sys.exit(bool(b))"
        result = self.remote(host, ['python3', '-c', script], payload=payload, timeout=60)
        if not json.loads(result).get('ok'):
            raise AssertionError('Remote deployed source does not match pinned provenance')

    def preflight(self):
        identities = {}
        for host in HOSTS:
            raw = self.remote(host, ['python3', '-c',
                "import json,socket,pathlib; print(json.dumps(dict(hostname=socket.gethostname(),machine_id=pathlib.Path('/etc/machine-id').read_text().strip())))"])
            identity = json.loads(raw)
            if identity['hostname'].split('.')[0] != host:
                raise AssertionError('Observed hostname does not match runtime selector')
            identities[host] = identity
            raw = self.remote(host, ['hostname', '-I'])
            addresses = [ipaddress.ip_address(item) for item in raw.decode().split()]
            self.addresses[host] = str(next(item for item in addresses if item.version == 4 and not item.is_loopback))
            self.verify_remote_source(host)
            self.remote(host, ['sh', '-c', 'command -v tmux && command -v openssl'])
        if identities[HOSTS[0]]['machine_id'] == identities[HOSTS[1]]['machine_id']:
            raise AssertionError('Two selectors refer to one machine')
        host_support.atomic_json(self.output / 'hosts.json', identities)

    def enroll(self, name):
        key, public = self.issuer.key(name)
        pending = self.issuer.enroll(self.issuer.owner_peer, self.issuer.intent(public=public, label=name), key, public)
        result = self.issuer.decide(self.issuer.owner_peer, pending)
        if not result.HasField('enrollment'):
            raise AssertionError('Manual issuer approval failed')
        record = result.enrollment
        claims = self.issuer.claims(record, self.issuer.info)
        return key, record, claims

    def setup(self):
        self.preflight()
        driver_host = socket.gethostname().split('.')[0]
        # Dedicated issuer: every created row belongs to this fixture's isolated DB.
        self.local_root = Path(tempfile.mkdtemp(prefix='spatial-m06-run03-'))
        info = self.local_root.stat()
        host_support.atomic_json(self.local_root / 'owner.json', dict(token=self.token,
                                      device=info.st_dev, inode=info.st_ino, uid=os.getuid()))
        host_support.atomic_json(self.output / 'ownership.json', dict(token=self.token,
                                    issuer_root=str(self.local_root), resources=[]))
        self.issuer = import_issuer_fixture(self.bundle, self.profile)
        self.issuer.setup_owned(self.local_root, self.addresses[driver_host], self.output, self.fail_after)
        self.actors = {'shared': (self.issuer.actor_key, self.issuer.record, self.issuer.identity)}
        self.actors.update({host: self.enroll('source-only-' + host) for host in HOSTS})
        self.checkpoint('issuer')
        for host in HOSTS:
            root = Path('/tmp') / ('spatial-m06-run03-' + self.token + '-' + host)
            # Record before sending create, including an uncertain SSH acknowledgement.
            self.resources.append((host, root))
            self.worlds[host] = dict(root=root)
            host_support.atomic_json(self.output / 'ownership.json', dict(token=self.token,
                issuer_root=str(self.local_root), resources=[dict(host=h, root=str(p)) for h, p in self.resources]))
            created = self.helper(host, 'create', root).decode().strip()
            if created != str(root):
                raise AssertionError('Created path differs from recorded owned root')
            self.checkpoint(host + '-root')
            binary = self.artifacts / 'worldd'
            raw = self.helper(host, 'initialize', root, binary=binary)
            stream = io.BytesIO(raw)
            world_reply = read_message(stream, core.Reply)
            room_reply = read_message(stream, core.Reply)
            if world_reply.HasField('error') or room_reply.HasField('error') or stream.read(1):
                raise AssertionError('World initialization outputs invalid')
            world_id = result_value(world_reply)['world_id']
            if not world_id:
                raise AssertionError('Production world creation returned no identity')
            self.worlds[host]['world_id'] = world_id
            self.checkpoint(host + '-world')
            # Reuse real issuer registration for the ACTUAL persisted WORLD domain.
            identity_config = session.WorldIdentityConfig(protocol_version=1, admission=session.EXPLICIT_ACTORS,
                issuer_client_config=str(self.issuer.config_file()), authority_directory=str(root / 'authority'))
            self.issuer.configure_participant(identity_config, [grants.Domain(kind=grants.WORLD, id=world_id)])
            participant = text_format.Parse(Path(identity_config.participant_runtime_config).read_text(),
                                            issuer.AuthorityParticipantRuntimeConfig())
            issuer_client = text_format.Parse(Path(identity_config.issuer_client_config).read_text(), issuer.IssuerTlsClientConfig())
            issuer_client.server_ca_path = str(root / 'issuer.crt')
            issuer_client.certificate_path = str(root / 'service.crt')
            issuer_client.private_key_path = str(root / 'service.pem')
            service_key = Path(participant.private_key_path).read_bytes()
            participant.private_key_path = str(root / 'participant.pem')
            participant.issuer_client_config = str(root / 'issuer.textproto')
            identity_config.issuer_client_config = str(root / 'issuer.textproto')
            identity_config.participant_runtime_config = str(root / 'participant.textproto')
            self.worlds[host]['participant_id'] = participant.participant_id.hex()
            # Explicit production persistent authority, no static actor bypass.
            seed = session.WorldAuthoritySeed(protocol_version=1, authority_directory=str(root / 'authority'))
            for name in ('shared', host):
                claims = self.actors[name][2]
                seed.roots.add(subject=grants.ActorRef(issuer_id=claims.issuer_id, actor_id=claims.actor_id),
                    scope=grants.Scope(domain=grants.Domain(kind=grants.WORLD, id=world_id)),
                    rights=[grants.WORLD_ENTER, grants.WORLD_NAVIGATE])
            server_key, server_cert = self.issuer.certificate('world-' + host, host + '.run03.test')
            self.worlds[host]['certificate'] = server_cert
            for name, data in {
                'participant.pem': service_key, 'service.pem': self.issuer.service_key.read_bytes(),
                'service.crt': self.issuer.service_cert.read_bytes(), 'issuer.crt': self.issuer.server_cert.read_bytes(),
                'server.pem': server_key.read_bytes(), 'server.crt': server_cert.read_bytes(),
                'runtime.textproto': (self.artifacts / 'runtime.textproto').read_bytes(),
            }.items():
                self.put(host, root, name, data)
            tls = session.WorldActorTlsConfig(protocol_version=1, certificate_path=str(root / 'server.crt'),
                    private_key_path=str(root / 'server.pem'), client_ca_path=str(root / 'service.crt'),
                    io_timeout_ms=5000, idle_timeout_ms=10000, connection_limit=16)
            for name, message in {'seed.textproto': seed, 'issuer.textproto': issuer_client,
                                  'participant.textproto': participant, 'identity.textproto': identity_config,
                                  'tls.textproto': tls}.items():
                self.put(host, root, name, text_format.MessageToString(message).encode())
            self.helper(host, 'authority', root, binary=binary)
            self.checkpoint(host + '-authority')
            process = json.loads(self.helper(host, 'start', root, binary=binary, address=self.addresses[host]))
            self.worlds[host].update(process=process, port=process['port'], unavailable_port=process['unavailable_port'])
            self.checkpoint(host + '-started')
            hello = self.management(host, 'system.hello')
            if hello['implementation'] != 'production' or self.management(host, 'world.info')['world_id'] != world_id:
                raise AssertionError('Live authority does not match created world')
        if self.worlds[HOSTS[0]]['world_id'] == self.worlds[HOSTS[1]]['world_id']:
            raise AssertionError('Worlds do not have independent identities')
        observations = {}
        binary_hash = hashlib.sha256((self.artifacts / 'worldd').read_bytes()).hexdigest()
        for index, host in enumerate(HOSTS):
            world = self.worlds[host]
            observed = harness.host_evidence(host, world['port'], str(world['root'] / 'state/world.db'), binary_hash)
            if observed['owners'][0]['pid'] != world['process']['worldd']['pid']:
                raise AssertionError('Independent listener owner differs from started process')
            observations[host] = observed
            for name in ('shared', host):
                config = self.actor_config(host, name)
                pose = core.Pose(position=core.Vec3(x=index + 1, z=-2), orientation=core.Quaternion(w=1))
                world['pose'] = pose
                request = plan_pb.ActorTransitionCrosshostRequest(protocol_version=1,
                    source_tls_config_path=str(config), source_world_id=world['world_id'],
                    seed_context=plan_pb.ActorTransitionCrosshostRequest.SeedContext(room='saved-room',
                                                                                   pose=pose, departure_id=os.urandom(32)))
                output = self.command([str(self.artifacts / 'spatial-actor-transition-crosshost-probe')],
                                      frame(request), timeout=20, private_output=True)
                reply = harness.read_frame(output, plan_pb.ActorTransitionCrosshostReply)
                if not reply.done or reply.driver_error or not reply.HasField('seeded_departure') or \
                        reply.seeded_departure.world_id != world['world_id'] or \
                        reply.seeded_departure.actor_id != self.actor_id(name):
                    raise AssertionError('C++ seed did not confirm actual durable actor context')
                (self.output / f'seed-{host}-{name}.pb').write_bytes(output)
                # Direct read-only durable witness, independently from client inspection.
                read = plan_pb.ActorTransitionDurableReadRequest(protocol_version=1,
                    database_path=str(world['root'] / 'state/world.db'), actor_id=self.actor_id(name),
                    expected_world_id=world['world_id'])
                raw = self.remote(host, ['env', 'PYTHONPATH=' + str(self.python),
                    'python3', str(fixture_path(self.bundle, 'actor_transition_durable_witness.py'))], frame(read))
                saved = harness.read_frame(raw, plan_pb.ActorTransitionDurableReadReply)
                entity = core.Entity.FromString(saved.entity)
                if saved.error or not saved.actor_present or entity.id != self.actor_id(name) or \
                        entity.room != 'saved-room' or entity.pose != pose or saved.world_id != world['world_id']:
                    raise AssertionError('Independent durable context seed witness failed')
        host_support.atomic_json(self.output / 'authorities.json', observations)
        self.plan_path = self.make_plan()
        self.checkpoint('seeded')

    def actor_id(self, name):
        claims = self.actors[name][2]
        return 'identity:' + claims.issuer_id.hex() + ':' + claims.actor_id.hex()

    def actor_config(self, host, name, unavailable=False):
        world = self.worlds[host]
        config = tls_pb.ActorSessionClientTlsConfig(protocol_version=1, address=self.addresses[host],
            port=world['unavailable_port'] if unavailable else world['port'], server_name=host + '.run03.test',
            expected_world_id=world['world_id'], certificate_path=str(self.issuer.service_cert),
            private_key_path=str(self.issuer.service_key), server_ca_path=str(world['certificate']),
            actor_signing_key_path=str(self.actors[name][0]), actor_credential=self.actors[name][1].credential,
            io_timeout_ms=5000)
        path = self.local_root / ('actor-' + host + '-' + name + ('-unavailable' if unavailable else '') + '.textproto')
        return write_private(path, config)

    def make_plan(self):
        plan = plan_pb.ActorTransitionCrosshostPlan(protocol_version=1)
        for source_host, destination_host in (HOSTS, tuple(reversed(HOSTS))):
            source, destination = self.worlds[source_host], self.worlds[destination_host]
            for scenario in (1, 2, 3):
                name = source_host if scenario == 2 else 'shared'
                case = plan.cases.add(protocol_version=1, scenario=scenario,
                    source_tls_config_path=str(self.actor_config(source_host, name)), source_host=source_host,
                    source_world_id=source['world_id'], source_database_path=str(source['root'] / 'state/world.db'),
                    expected_source_room='saved-room', expected_source_pose=source['pose'])
                step = case.steps.add(destination_tls_config_path=str(self.actor_config(destination_host, name, scenario == 3)),
                    destination_world_id=destination['world_id'], destination_host=destination_host,
                    destination_database_path=str(destination['root'] / 'state/world.db'),
                    transition_id=os.urandom(32), departure_sequence=1)
                if scenario == 1:
                    step.expected_destination_room = 'saved-room'
                    step.expected_destination_pose.CopyFrom(destination['pose'])
                    case.steps.add(destination_tls_config_path=str(self.actor_config(source_host, name)),
                        destination_world_id=source['world_id'], destination_host=source_host,
                        destination_database_path=str(source['root'] / 'state/world.db'),
                        transition_id=os.urandom(32), departure_sequence=1)
                else:
                    step.source_after_failure_actions.add(seq=1, input=core.ActorInputRequest(axes=[0] * 6))
                    step.source_after_failure_actions.add(seq=2, stop=core.ActorStopRequest())
        path = self.local_root / 'plan.pb'
        path.write_bytes(plan.SerializeToString())
        path.chmod(0o600)
        host_support.atomic_json(self.output / 'plan-summary.json', dict(
            plan_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            worlds={host: dict(world_id=world['world_id'], participant_id=world['participant_id'],
                              root=str(world['root']), process=world['process'],
                              tls_server_name=host + '.run03.test',
                              tls_server_certificate_sha256=hashlib.sha256(world['certificate'].read_bytes()).hexdigest(),
                              tls_client_ca_sha256=hashlib.sha256(self.issuer.service_cert.read_bytes()).hexdigest())
                    for host, world in self.worlds.items()},
            issuer_id=self.issuer.info.issuer_id.hex(), issuer_public_key=self.issuer.info.public_key.hex(),
            actors={name: self.actor_id(name) for name in self.actors}, cases=len(plan.cases),
            semantic_cases=[({'1': 'round-trip', '2': 'private-denial', '3': 'unavailable-destination'}[str(case.scenario)] + ':' +
                case.source_host + '-to-' + case.steps[0].destination_host) for case in plan.cases]))
        return path

    def execute(self):
        env = os.environ.copy()
        env['PYTHONPATH'] = str(self.python) + ':' + str(self.source / 'tests' / 'core') + ':' + str(self.source)
        env['SPATIAL_ACTOR_TRANSITION_WITNESS'] = str(fixture_path(self.bundle, 'actor_transition_durable_witness.py'))
        env['SPATIAL_ACTOR_TRANSITION_WITNESS_PYTHONPATH'] = str(self.python)
        env['SPATIAL_ACTOR_TRANSITION_EVIDENCE'] = str(self.output)
        command = [sys.executable, str(fixture_path(self.bundle, 'test_actor_transition_crosshost.py')),
                   str(self.artifacts / 'spatial-actor-transition-crosshost-probe'), str(self.plan_path)]
        host_support.atomic_json(self.output / 'harness-command.json', dict(argv=command, timeout=720))
        with (self.output / 'harness.stdout').open('wb') as output, (self.output / 'harness.stderr').open('wb') as error:
            try:
                result = subprocess.run(command, env=env, stdout=output, stderr=error, timeout=720)
            except subprocess.TimeoutExpired:
                (self.output / 'harness.exit').write_text('TIMEOUT\n')
                raise
        (self.output / 'harness.exit').write_text(str(result.returncode) + '\n')
        if result.returncode:
            raise AssertionError('Six-case production TLS harness failed')

    def cleanup(self):
        # Cleanup has an independent budget even when setup or the harness expires.
        self.deadline = time.monotonic() + 180
        errors = []
        for host, root in reversed(self.resources):
            try:
                before = self.helper(host, 'snapshot', root)
                (self.output / (host + '-before-cleanup.tar')).write_bytes(before)
                self.helper(host, 'stop', root)
                final = self.helper(host, 'snapshot', root)
                (self.output / (host + '-final-state.tar')).write_bytes(final)
                self.helper(host, 'remove', root)
                with tarfile.open(fileobj=io.BytesIO(final)) as archive:
                    if 'worldd.exit' in archive.getnames():
                        code = archive.extractfile('worldd.exit').read().decode().strip()
                        if code != '0':
                            errors.append(dict(host=host, root=str(root), removed=True,
                                               error='Owned worldd exit was ' + code))
            except Exception as error:
                errors.append(dict(host=host, root=str(root), error=str(error), recovery=[
                    'env', 'PYTHONPATH=' + str(self.python) + ':' + str(self.source / 'tests' / 'core') + ':' + str(self.source),
                    'PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python',
                    'python3', str(fixture_path(self.bundle, 'actor_transition_crosshost_host.py')), 'stop', self.token, '--root', str(root)]))
        if self.issuer is not None:
            try:
                self.issuer.cleanup_owned()
            except Exception as error:
                errors.append(dict(host='issuer', root=str(self.local_root), error=str(error)))
        if self.local_root is not None and not any(item['host'] == 'issuer' for item in errors):
            try:
                host_support.root_guard(self.local_root, self.token)
                # Issuer keys/configs may be removed only after owned issuer termination and backup.
                for proc, _ in getattr(self.issuer, 'daemons', []):
                    if proc.poll() is None:
                        raise RuntimeError('Issuer remains alive')
                host_support.remove(self.local_root, self.token)
            except Exception as error:
                errors.append(dict(host='issuer', root=str(self.local_root), error=str(error)))
        host_support.atomic_json(self.output / 'cleanup.json', dict(errors=errors, complete=not errors))
        if errors:
            raise RuntimeError('Cleanup incomplete; inspect cleanup.json and retain owned paths')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True, type=Path)
    parser.add_argument('--profile', required=True, choices=('debug', 'asan'))
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--fail-after', choices=['issuer', 'issuer-record-after-spawn', 'seeded'] +
        [host + '-' + step for host in HOSTS for step in ('root', 'world', 'authority', 'started')],
        help='Primary-only cleanup regression: deliberately fail at a completed setup boundary')
    args = parser.parse_args()
    if socket.gethostname().split('.')[0] not in HOSTS:
        raise RuntimeError('Runtime forbidden outside sb16/sb16-2')
    os.umask(0o077)
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    run = Run(args)
    def cancel(*_):
        raise InterruptedError('Fixture cancellation; guarded cleanup follows')
    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    status = 'FAIL'
    try:
        host_support.stop_owner_backlog_regression(run.output)
        run.setup()
        run.execute()
        status = 'PASS'
    finally:
        # A second cancellation cannot interrupt bounded ownership-preserving cleanup.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            run.cleanup()
            if args.fail_after == 'issuer-record-after-spawn':
                attempt = run.output / 'issuer-process-attempt.json'
                exit_path = run.output / 'issuer.exit'
                stderr_path = run.output / 'issuer.stderr'
                cleanup_path = run.output / 'cleanup.json'
                if not attempt.is_file() or not exit_path.is_file() or not stderr_path.is_file():
                    raise AssertionError('Identity-record failure did not preserve issuer process/log evidence')
                if exit_path.read_text().strip() != '0':
                    raise AssertionError('Identity-record failure did not close the owned issuer cleanly')
                if not json.loads(cleanup_path.read_text()).get('complete'):
                    raise AssertionError('Identity-record failure cleanup was incomplete')
                if run.local_root and run.local_root.exists():
                    raise AssertionError('Identity-record failure left its owned private root behind')
                if any(proc.poll() is None for proc, _ in getattr(run.issuer, 'daemons', [])):
                    raise AssertionError('Identity-record failure left the owned issuer process alive')
                (run.output / 'issuer-record-failure-regression.txt').write_text(
                    'PASS: issuer child was reaped after injected process identity record failure; '
                    'process/log/exit evidence preserved; owned root removed after backup.\n')
        except BaseException:
            status = 'FAIL_CLEANUP'
            raise
        finally:
            (run.output / 'runtime-status.txt').write_text(status + '\nBounded semantic fixture only. Native acceptance unproven.\n')


if __name__ == '__main__':
    main()
