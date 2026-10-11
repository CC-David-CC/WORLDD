"""Real broker launches, namespace confinement, and independent native identities."""
import ctypes
import fcntl
import json
import os
from pathlib import Path
import socket
import select
import struct
import subprocess
import time
import unittest
from unittest.mock import patch
import broker_pb2 as broker
import core_pb2 as core
import security_pb2 as security
import test_security_identity as native
from harness.git_fixture import GitRepo
from harness.wire import frame, make_request, result_value, read_message
from harness.resource_broker import ResourceBroker


BROKER = native.PROBE.parent/'workspaced'
POLICY = ['workspace.select', 'files.read', 'files.write', 'process.launch']


class Broker:
    def __init__(self, fixture, config, sealed=True):
        self.fixture = fixture
        self.sequence = 0
        self.stream, child = socket.socketpair()
        self.stream.settimeout(7)
        fd = os.memfd_create('native-launch-test', os.MFD_ALLOW_SEALING | os.MFD_CLOEXEC)
        os.write(fd, config)
        if sealed:
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
        self.state = fixture.root/'execution'
        self.log_path = fixture.root/'broker.log'
        self.log = self.log_path.open('wb')
        self.resources = None; self.owner_log = None
        try:
            command = [str(BROKER), '--state-dir', str(self.state), '--native-security-fd', str(fd)]
            configuration = os.environ.get('SPATIAL_TEST_BROKER_CONFIG')
            if configuration:
                self.owner_log = (fixture.root/'broker-owner.log').open('wb')
                self.resources = ResourceBroker(BROKER, command, child, self.log, self.owner_log,
                    configuration, extra_fds={'--native-security-fd':fd})
                self.proc = self.resources.process
            else:
                self.proc = subprocess.Popen(command, stdin=child, stdout=child, stderr=self.log, pass_fds=(fd,))
        finally:
            child.close()
            os.close(fd)
        fixture.addCleanup(self.close)

    def close(self):
        self.stream.close()
        if self.resources: self.resources.close()
        elif self.proc.poll() is None:
            try: self.proc.wait(timeout=4)
            except subprocess.TimeoutExpired: self.proc.kill(); self.proc.wait(timeout=3)
        self.log.close()
        if self.owner_log: self.owner_log.close()
        text = self.log_path.read_text(errors='replace')
        if os.environ.get('SPATIAL_SECURITY_EVIDENCE'):
            target = Path(os.environ['SPATIAL_SECURITY_EVIDENCE'])
            target.mkdir(parents=True, exist_ok=True)
            (target/(self.fixture._testMethodName+'-broker.log')).write_text(text)
        for marker in ('AddressSanitizer', 'LeakSanitizer', 'runtime error:'):
            self.fixture.assertNotIn(marker, text)

    def request(self, **operation):
        self.sequence += 1
        self.stream.sendall(frame(broker.Request(protocol_version=1, request_id=self.sequence, **operation)))
        def exact(size):
            data = b''
            while len(data) < size:
                try: part = self.stream.recv(size-len(data))
                except OSError as error: raise AssertionError('Broker transport failed: '+self.log_path.read_text()) from error
                if not part: raise AssertionError('Broker closed: '+self.log_path.read_text())
                data += part
            return data
        size = struct.unpack('!I', exact(4))[0]
        if size > 1048576: raise AssertionError('Excessive broker reply')
        reply = broker.Reply.FromString(exact(size))
        if reply.protocol_version != 1 or reply.request_id != self.sequence: raise AssertionError('Wrong broker reply identity')
        return reply

    def call(self, method, **args):
        request = make_request(method, args)
        request.request_id = self.sequence+1
        reply = self.request(management=request)
        if reply.HasField('error') or reply.management.HasField('error'): raise AssertionError(reply)
        return result_value(reply.management)


# This program runs INSIDE the production confinement boundary. It uses installed
# Xlib for ordinary application output. The server identity is read independently.
APP = r'''
import ctypes,json,os,pathlib,struct,socket,time
inherited=[]
for fd in range(3,256):
 try:os.fstat(fd);inherited.append(fd)
 except OSError:pass
auth=pathlib.Path(os.environ['XAUTHORITY']).read_bytes()
pos=2
fields=[]
for _ in range(4):
 n=struct.unpack_from('!H',auth,pos)[0];pos+=2;fields.append(auth[pos:pos+n]);pos+=n
assert fields[2]==b'MIT-MAGIC-COOKIE-1' and len(fields[3])==32
lib=ctypes.CDLL('libX11.so.6')
lib.XOpenDisplay.argtypes=[ctypes.c_char_p];lib.XOpenDisplay.restype=ctypes.c_void_p
lib.XDefaultRootWindow.argtypes=[ctypes.c_void_p];lib.XDefaultRootWindow.restype=ctypes.c_ulong
lib.XCreateSimpleWindow.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.c_int,ctypes.c_int,ctypes.c_uint,ctypes.c_uint,ctypes.c_uint,ctypes.c_ulong,ctypes.c_ulong];lib.XCreateSimpleWindow.restype=ctypes.c_ulong
lib.XMapWindow.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
lib.XSync.argtypes=[ctypes.c_void_p,ctypes.c_int]
lib.XCloseDisplay.argtypes=[ctypes.c_void_p]
d=lib.XOpenDisplay(None);assert d
w=lib.XCreateSimpleWindow(d,lib.XDefaultRootWindow(d),0,0,16,16,0,0,0)
lib.XMapWindow(d,w);lib.XSync(d,0)
record={'cookie':fields[3].hex(),'window':w,'display':os.environ['DISPLAY'],'auth':os.environ['XAUTHORITY'],'inherited_fds':inherited,
        'sockets':os.listdir('/tmp/.X11-unix'),'net':os.readlink('/proc/self/ns/net'),
        'status':pathlib.Path('/proc/self/status').read_text()}
try:pathlib.Path(os.environ['XAUTHORITY']).write_bytes(b'bad')
except OSError:record['auth_readonly']=True
else:record['auth_readonly']=False
pathlib.Path('/state/data/record.json').write_text(json.dumps(record))
while not pathlib.Path('/state/data/release').exists():time.sleep(.005)
lib.XCloseDisplay(d)
'''


class NativeLaunchTests(unittest.TestCase):
    setUp = native.SecurityIdentityTests.setUp
    start = native.SecurityIdentityTests.start
    connect = native.SecurityIdentityTests.connect
    assert_closed = native.SecurityIdentityTests.assert_closed

    def configuration(self, **changes):
        root = self.start()
        self.grant = root.issue(security.BROKER, b'', b'')
        config = broker.NativeLaunchConfig(protocol_version=1, display_number=self.display,
            broker_cookie=self.grant.cookie, server_id=root.inspect().server_id,
            server_pid=self.server.pid, server_uid=os.getuid())
        for key, value in changes.items(): setattr(config, key, value)
        return config

    def launch_fixture(self):
        config = self.configuration()
        self.broker = Broker(self, config.SerializeToString())
        self.broker.call('system.hello')
        self.repo = GitRepo(self.root)
        self.broker.call('git.repo.import', id='repo', path=str(self.repo.path))
        tree = self.broker.call('git.worktree.create', repo='repo', id='tree', branch='app', start='main', operation_id='tree')
        self.project = Path(tree['path'])
        self.broker.call('workspace.create', id='workspace-one', worktree='tree', policy=POLICY)
        self.broker.call('workspace.member.set', id='workspace-one', actor='human', rights=POLICY)

    def launch(self, operation='one', code=APP, profile='development'):
        reply = self.broker.request(launch=broker.LaunchRequest(workspace='workspace-one', actor='human',
            command=core.ProcessLaunchRequest(operation_id=operation, profile=profile, argv=['/usr/bin/python3','-c',code])))
        self.assertTrue(reply.HasField('launched'), reply)
        return reply.launched

    def record(self, process):
        path = self.broker.state/'applications'/process.process_id/'data/record.json'
        deadline = time.monotonic()+5
        while not path.exists() and time.monotonic() < deadline: time.sleep(.01)
        self.assertTrue(path.exists(), 'Confined native application did not report readiness')
        return json.loads(path.read_text())

    def release(self, process):
        (self.broker.state/'applications'/process.process_id/'data/release').touch()
        result = self.broker.call('process.wait', id=process.process_id, timeout_seconds=5)
        self.assertEqual(result, {'exit_code':0})

    def test_confined_application_has_real_native_identity(self):
        self.launch_fixture(); process = self.launch(); record = self.record(process)
        client = self.connect(bytes.fromhex(record['cookie']))
        identity = client.inspect()
        self.assertEqual(identity.role, security.APPLICATION)
        self.assertEqual(identity.workspace, b'workspace-one')
        self.assertEqual(identity.application, process.process_id.encode())
        self.assertEqual(record['sockets'], ['X'+str(self.display)])
        self.assertNotEqual(record['net'], os.readlink('/proc/self/ns/net'))
        self.assertTrue(record['auth_readonly'])
        self.assertEqual(record['inherited_fds'], [])
        self.assertIn('NoNewPrivs:\t1', record['status'])
        self.release(process)
        self.assert_closed(client)
        with self.assertRaises(native.Denied): self.connect(bytes.fromhex(record['cookie']))

    def test_two_applications_receive_distinct_credentials_and_identities(self):
        self.launch_fixture(); a = self.launch('one'); b = self.launch('two')
        ra = self.record(a); rb = self.record(b)
        self.assertNotEqual(ra['cookie'], rb['cookie'])
        ia = self.connect(bytes.fromhex(ra['cookie'])).inspect(); ib = self.connect(bytes.fromhex(rb['cookie'])).inspect()
        self.assertNotEqual(ia.application, ib.application)
        self.release(a)
        self.assertEqual(self.connect(bytes.fromhex(rb['cookie'])).inspect().application, b.process_id.encode())
        self.release(b)

    def test_read_only_profile_retains_project_confinement(self):
        self.launch_fixture()
        code = "\ntry:open('/project/escape','w')\nexcept OSError:pass\nelse:raise SystemExit(31)\n"+APP
        process = self.launch(code=code, profile='read-only'); self.record(process)
        self.assertFalse((self.project/'escape').exists()); self.release(process)

    def test_broker_loss_revokes_native_access(self):
        self.launch_fixture(); process = self.launch(); record = self.record(process)
        self.broker.proc.kill(); self.broker.proc.wait(timeout=3)
        self.wait_native_denied(bytes.fromhex(record['cookie']), 'Native access survived broker death')

    def wait_native_denied(self, cookie, message):
        deadline = time.monotonic()+3
        while True:
            try: peer = native.NativeClient(self.display, cookie)
            except native.Denied: break
            except (ConnectionError, EOFError): pass # Closure is not a successful denial witness.
            else: peer.close()
            self.assertLess(time.monotonic(), deadline, message)
            time.sleep(.01)

    def test_execution_revocation_revokes_native_access(self):
        self.launch_fixture(); process = self.launch(); record = self.record(process)
        peer = self.connect(bytes.fromhex(record['cookie']))
        self.broker.call('workspace.member.set', id='workspace-one', actor='human', rights=[])
        self.assert_closed(peer)
        with self.assertRaises(native.Denied): self.connect(bytes.fromhex(record['cookie']))

    def test_exit_revokes_credentials_without_a_wait_request(self):
        self.launch_fixture(); process = self.launch(); record = self.record(process)
        (self.broker.state/'applications'/process.process_id/'data/release').touch()
        self.wait_native_denied(bytes.fromhex(record['cookie']), 'Exited application retained its native credential')
        self.assertEqual(self.broker.call('process.wait', id=process.process_id, timeout_seconds=0), {'exit_code':0})

    def test_revocation_during_extension_discovery_requires_fresh_denial(self):
        self.launch_fixture(); process = self.launch(); record = self.record(process)
        original = native.NativeClient.request; interrupted = []
        def request(client, opcode, *args, **kwargs):
            if opcode == 98 and not interrupted:
                (self.broker.state/'applications'/process.process_id/'data/release').touch()
                self.assertTrue(select.select([client.stream], [], [], 3)[0])
                self.assertEqual(client.stream.recv(1), b'')
                interrupted.append(True)
            return original(client, opcode, *args, **kwargs)
        with patch.object(native.NativeClient, 'request', request):
            self.wait_native_denied(bytes.fromhex(record['cookie']), 'Fresh connection never received protocol denial')
        self.assertEqual(interrupted, [True])
        self.assertEqual(self.broker.call('process.wait', id=process.process_id, timeout_seconds=0), {'exit_code':0})

    def test_denial_wait_does_not_accept_a_live_credential(self):
        self.launch_fixture(); process = self.launch(); record = self.record(process)
        with self.assertRaisesRegex(AssertionError, 'Live credential never became denied'):
            self.wait_native_denied(bytes.fromhex(record['cookie']), 'Live credential never became denied')
        self.assertEqual(self.connect(bytes.fromhex(record['cookie'])).inspect().application, process.process_id.encode())
        self.release(process)

    def test_native_broker_revocation_stops_configured_execution(self):
        self.launch_fixture(); process = self.launch(); record = self.record(process)
        self.assertEqual(self.supervisor.call(revoke=security.Revoke(credential_id=self.grant.id)).code, security.OK)
        self.assertNotEqual(self.broker.proc.wait(timeout=4), 0)
        with self.assertRaises(native.Denied): self.connect(bytes.fromhex(record['cookie']))

    def test_failed_exec_releases_the_only_free_credential_slot(self):
        self.launch_fixture()
        for _ in range(253): self.supervisor.issue()
        for operation in ['bad-one', 'bad-two']:
            reply = self.broker.request(launch=broker.LaunchRequest(workspace='workspace-one', actor='human',
                command=core.ProcessLaunchRequest(operation_id=operation, profile='development', argv=['/does-not-exist'])))
            self.assertTrue(reply.HasField('error'))
            self.assertNotEqual(reply.error.code, core.RESOURCE_EXHAUSTED)
        process = self.launch(); self.record(process); self.release(process)

    def test_unknown_configuration_fields_are_rejected(self):
        config = self.configuration()
        process = Broker(self, config.SerializeToString()+b'\x98\x06\x01')
        self.assertNotEqual(process.proc.wait(timeout=4), 0)

    def test_wrong_peer_uid_is_rejected(self):
        config = self.configuration(server_uid=os.getuid()+1)
        process = Broker(self, config.SerializeToString())
        self.assertNotEqual(process.proc.wait(timeout=4), 0)

    def test_application_cannot_issue_native_credentials(self):
        self.launch_fixture(); process = self.launch(); record = self.record(process)
        client = self.connect(bytes.fromhex(record['cookie']))
        self.assertEqual(client.call(issue=security.Issue(role=security.APPLICATION, workspace=b'x', application=b'y')).code, security.FORBIDDEN)
        self.release(process)

    def test_launch_retry_keeps_identity_and_does_not_reissue_after_exit(self):
        self.launch_fixture(); process = self.launch(); record = self.record(process)
        self.assertEqual(self.launch(), process)
        self.assertEqual(self.record(process)['cookie'], record['cookie'])
        self.release(process)
        self.assertEqual(self.launch(), process)
        with self.assertRaises(native.Denied): self.connect(bytes.fromhex(record['cookie']))

    def test_denied_launch_never_creates_application_state(self):
        self.launch_fixture()
        self.broker.call('workspace.member.set', id='workspace-one', actor='human', rights=[])
        before = sorted((self.broker.state/'applications').iterdir())
        result = self.broker.request(launch=broker.LaunchRequest(workspace='workspace-one', actor='human',
            command=core.ProcessLaunchRequest(operation_id='denied', profile='development', argv=['/usr/bin/true'])))
        self.assertEqual(result.error.code, core.FORBIDDEN)
        self.assertEqual(sorted((self.broker.state/'applications').iterdir()), before)

    def checkpoint_denial(self, checkpoint):
        config = self.configuration()
        fd = os.memfd_create('checkpoint-test', os.MFD_ALLOW_SEALING | os.MFD_CLOEXEC)
        os.write(fd, config.SerializeToString())
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
        probe = Path(os.environ.get('SPATIAL_NATIVE_CHECKPOINT_PROBE', str(BROKER.parent/'native-launch-checkpoint-probe')))
        try:
            proc = subprocess.Popen([str(probe), str(fd), str(checkpoint)], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, pass_fds=(fd,))
        finally: os.close(fd)
        try:
            self.assertTrue(select.select([proc.stdout], [], [], 5)[0], 'Checkpoint probe stalled')
            try:
                reply = read_message(proc.stdout, security.Reply)
            except EOFError:
                proc.wait(timeout=4)
                self.fail('Checkpoint probe failed: '+proc.stderr.read().decode(errors='replace'))
            self.assertEqual(reply.protocol_version, 1)
            peer = self.connect(reply.credential.cookie)
            self.assertEqual(peer.inspect().application, b'keeper')
            proc.stdin.write(frame(core.Request(protocol_version=1, request_id=2, hello=core.HelloRequest())))
            proc.stdin.flush()
            self.assertEqual(proc.wait(timeout=4), 0)
            self.assert_closed(peer)
        finally:
            if proc.poll() is None: proc.kill(); proc.wait(timeout=3)
            diagnostic = proc.stderr.read().decode(errors='replace')
            proc.stdin.close(); proc.stdout.close(); proc.stderr.close()
            for marker in ('AddressSanitizer', 'LeakSanitizer', 'runtime error:'): self.assertNotIn(marker, diagnostic)

    def test_pre_issue_authority_denial_preserves_other_credentials(self):
        self.checkpoint_denial(1)

    def test_post_issue_authority_denial_preserves_other_credentials(self):
        self.checkpoint_denial(2)

    def test_unsealed_configuration_is_rejected(self):
        config = self.configuration(); process = Broker(self, config.SerializeToString(), sealed=False)
        self.assertNotEqual(process.proc.wait(timeout=4), 0)

    def test_wrong_server_generation_is_rejected(self):
        config = self.configuration(server_id=b'x'*32); process = Broker(self, config.SerializeToString())
        self.assertNotEqual(process.proc.wait(timeout=4), 0)

    def test_wrong_peer_process_is_rejected(self):
        config = self.configuration(server_pid=os.getpid()); process = Broker(self, config.SerializeToString())
        self.assertNotEqual(process.proc.wait(timeout=4), 0)

    def test_supervisor_credential_is_not_accepted_as_broker(self):
        config = self.configuration(); config.broker_cookie = self.cookie
        process = Broker(self, config.SerializeToString())
        self.assertNotEqual(process.proc.wait(timeout=4), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
