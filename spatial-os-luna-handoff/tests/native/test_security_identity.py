"""Independent native X clients exercise immutable connection authentication."""
import fcntl
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import security_pb2 as pb
from display_ready import read_display_notice

PROBE = Path(sys.argv.pop(1)).resolve()
AUTH = b'MIT-MAGIC-COOKIE-1'


def padded(data): return data+b'\0'*((-len(data))%4)


class Denied(Exception): pass


class NativeClient:
    """Speak X11 and the documented protobuf extension without production helpers."""
    def __init__(self, display, cookie, endian='<', name=AUTH):
        self.endian = endian; self.sequence = 0
        self.stream = socket.socket(socket.AF_UNIX); self.stream.settimeout(3)
        try:
            self.stream.connect('/tmp/.X11-unix/X'+str(display))
            self.stream.sendall(struct.pack(endian+'BBHHHHH',ord('l' if endian=='<' else 'B'),0,11,0,len(name),len(cookie),0)
                                +padded(name)+padded(cookie))
            prefix = self.exact(8); size = struct.unpack(endian+'H',prefix[6:8])[0]*4
            if size > 1048576: raise AssertionError('Excessive native setup')
            payload = self.exact(size)
            if prefix[0] != 1: raise Denied(payload[:prefix[1]].decode(errors='replace'))
            self.setup = payload
            name = b'SPATIAL-SECURITY'
            header, _ = self.request(98,struct.pack(endian+'HH',len(name),0)+padded(name))
            if not header[8]: raise AssertionError('SPATIAL-SECURITY is missing')
            self.opcode = header[9]
        except BaseException:
            self.close(); raise

    def exact(self, size):
        result = b''
        while len(result) < size:
            data = self.stream.recv(size-len(result))
            if not data: raise EOFError('Native connection closed')
            result += data
        return result

    def close(self): self.stream.close()

    def send(self, opcode, payload=b'', minor=0):
        self.sequence += 1
        data = padded(payload)
        self.stream.sendall(struct.pack(self.endian+'BBH',opcode,minor,1+len(data)//4)+data)

    def request(self, opcode, payload=b'', minor=0):
        self.send(opcode,payload,minor); header = self.exact(32)
        if header[0] != 1: raise AssertionError('Native request returned error/event: '+str(header[:4]))
        if struct.unpack(self.endian+'H',header[2:4])[0] != self.sequence%65536:
            raise AssertionError('Native reply sequence mismatch')
        size = struct.unpack(self.endian+'I',header[4:8])[0]*4
        if size > 1048576: raise AssertionError('Excessive native reply')
        return header,self.exact(size)

    def exchange(self, message=None, payload=None):
        data = payload if payload is not None else message.SerializeToString()
        header,data = self.request(self.opcode,struct.pack(self.endian+'I',len(data))+data)
        size = struct.unpack(self.endian+'I',header[8:12])[0]
        if size > 4096 or len(data) != (size+3)//4*4: raise AssertionError('Invalid extension reply framing')
        reply = pb.Reply.FromString(data[:size])
        if reply.protocol_version != 1: raise AssertionError(reply)
        if message is not None and reply.request_id != message.request_id: raise AssertionError(reply)
        return reply

    def call(self, **operation):
        self.operation_sequence = getattr(self,'operation_sequence',0)+1
        return self.exchange(pb.Request(protocol_version=1,request_id=self.operation_sequence,**operation))

    def inspect(self):
        reply = self.call(inspect=pb.Inspect())
        if reply.code != pb.OK or not reply.HasField('identity'): raise AssertionError(reply)
        return reply.identity

    def issue(self, role=pb.APPLICATION, workspace=b'workspace-one', application=b'application-one'):
        reply = self.call(issue=pb.Issue(role=role,workspace=workspace,application=application))
        if reply.code != pb.OK or not reply.HasField('credential'): raise AssertionError(reply)
        return reply.credential


class SecurityIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.cookie = os.urandom(32); self.clients = []
        self.addCleanup(lambda:[client.close() for client in reversed(self.clients)])

    def start(self, bootstrap=None, sealed=True, expect_ready=True):
        binary = Path(os.environ['SPATIAL_TEST_XSERVER'])
        self.assertTrue(binary.is_absolute() and binary.name=='Xvfb' and os.access(binary,os.X_OK))
        bootstrap = bootstrap if bootstrap is not None else pb.Bootstrap(protocol_version=1,supervisor_cookie=self.cookie).SerializeToString()
        config = os.memfd_create('spatial-test-bootstrap',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
        os.write(config,bootstrap)
        if sealed: fcntl.fcntl(config,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
        incoming,outgoing = os.pipe()
        self.log_path = self.root/f'server-{len(list(self.root.glob("server-*")))}.log'
        log_path = self.log_path
        log = log_path.open('wb')
        argv = [str(binary),'-displayfd',str(outgoing),'-screen','0','128x128x24','-nolisten','tcp','-noreset',
                '-ac','+byteswappedclients','-spatial-security-fd',str(config)]
        # Authentication uses core X11. Keep graphics-driver qualification separate.
        # Default and cumulative runs retain GLX. The focused sanitizer profile
        # records this setting and keeps every identity assertion enabled.
        if os.environ.get('SPATIAL_SECURITY_TEST_CORE_ONLY') == '1': argv += ['-extension','GLX']
        env = {**os.environ,'LIBGL_ALWAYS_SOFTWARE':'1'}
        try: proc = subprocess.Popen(argv,stdout=log,stderr=log,pass_fds=(config,outgoing),env=env)
        finally: os.close(config); os.close(outgoing)
        def close():
            timed_out = False
            if proc.poll() is None: proc.terminate()
            try: proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait(timeout=3); timed_out = True
            log.close()
            diagnostics = log_path.read_text(errors='replace')
            if os.environ.get('SPATIAL_SECURITY_EVIDENCE'):
                evidence = Path(os.environ['SPATIAL_SECURITY_EVIDENCE']); evidence.mkdir(parents=True,exist_ok=True)
                (evidence/(self._testMethodName+'-'+log_path.name)).write_bytes(log_path.read_bytes())
            for marker in ('AddressSanitizer','LeakSanitizer','runtime error:'):
                self.assertNotIn(marker,diagnostics,diagnostics)
            self.assertFalse(timed_out,'Native server did not shut down: '+diagnostics)
            if expect_ready: self.assertIn(proc.returncode,(0,-15),diagnostics)
        self.addCleanup(close)
        try:
            data = read_display_notice(incoming)
        finally: os.close(incoming)
        if not expect_ready:
            self.assertFalse(data); self.assertNotEqual(proc.wait(timeout=3),0); return
        self.assertTrue(data.endswith(b'\n') and data[:-1].isdigit(),self.log_path.read_text(errors='replace'))
        self.display = int(data); self.server = proc
        self.supervisor = self.connect(self.cookie)
        return self.supervisor

    def connect(self, cookie, **options):
        client = NativeClient(self.display,cookie,**options); self.clients.append(client); return client

    def assert_closed(self, client):
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            if select.select([client.stream],[],[],.05)[0]:
                self.assertEqual(client.stream.recv(1024),b''); return
        self.fail('Revoked native connection remained open')

    def test_bootstrap_binds_supervisor_before_native_requests(self):
        client = self.start(); identity = client.inspect()
        self.assertEqual(identity.role,pb.SUPERVISOR)
        self.assertFalse(identity.workspace or identity.application)
        self.assertEqual(len(identity.credential_id),32); self.assertEqual(len(identity.server_id),32)
        self.assertGreater(identity.connection_id,0)

    def test_bad_cookies_cannot_use_disabled_host_access_control(self):
        self.start()
        for cookie in (b'',b'x'*31,b'x'*32,b'x'*33):
            with self.subTest(length=len(cookie)), self.assertRaises(Denied): self.connect(cookie)
        with self.assertRaises(Denied): self.connect(self.cookie,name=b'')
        self.assertEqual(self.supervisor.inspect().role,pb.SUPERVISOR)

    def test_application_labels_are_immutable_and_connections_are_distinct(self):
        root = self.start(); first = root.issue(); second = root.issue(workspace=b'workspace-two',application=b'application-two')
        a = self.connect(first.cookie); b = self.connect(second.cookie); again = self.connect(first.cookie)
        for client,credential in ((a,first),(b,second),(again,first)):
            identity = client.inspect(); self.assertEqual(identity.role,pb.APPLICATION)
            self.assertEqual((identity.workspace,identity.application,identity.credential_id),
                             (credential.workspace,credential.application,credential.id))
            self.assertEqual(client.call(issue=pb.Issue(role=pb.SUPERVISOR)).code,pb.FORBIDDEN)
            self.assertEqual(client.inspect(),identity)
        self.assertNotEqual(a.inspect().connection_id,again.inspect().connection_id)
        self.assertEqual(a.inspect().server_id,b.inspect().server_id)

    def test_broker_can_issue_only_application_credentials(self):
        root = self.start(); grant = root.issue(pb.BROKER,b'',b''); broker = self.connect(grant.cookie)
        for role in (pb.SUPERVISOR,pb.BROKER,pb.COMPOSITOR):
            self.assertEqual(broker.call(issue=pb.Issue(role=role)).code,pb.FORBIDDEN)
        credential = broker.issue(); self.assertEqual(self.connect(credential.cookie).inspect().role,pb.APPLICATION)

    def test_compositor_cannot_issue_credentials(self):
        root = self.start(); grant = root.issue(pb.COMPOSITOR,b'',b''); compositor = self.connect(grant.cookie)
        self.assertEqual(compositor.call(issue=pb.Issue(role=pb.APPLICATION,workspace=b'one',application=b'one')).code,pb.FORBIDDEN)

    def test_revocation_closes_existing_connections_and_blocks_reuse(self):
        root = self.start(); grant = root.issue(); a = self.connect(grant.cookie); b = self.connect(grant.cookie)
        reply = root.call(revoke=pb.Revoke(credential_id=grant.id))
        self.assertEqual(reply.code,pb.OK); self.assertTrue(reply.revoked)
        self.assert_closed(a); self.assert_closed(b)
        with self.assertRaises(Denied): self.connect(grant.cookie)
        self.assertEqual(root.inspect().role,pb.SUPERVISOR)

    def test_revoking_broker_cascades_without_harming_independent_credentials(self):
        root = self.start(); parent = root.issue(pb.BROKER,b'',b''); broker = self.connect(parent.cookie)
        child = broker.issue(); app = self.connect(child.cookie)
        independent = root.issue(workspace=b'other',application=b'other'); survivor = self.connect(independent.cookie)
        self.assertEqual(root.call(revoke=pb.Revoke(credential_id=parent.id)).code,pb.OK)
        self.assert_closed(broker); self.assert_closed(app)
        with self.assertRaises(Denied): self.connect(child.cookie)
        self.assertEqual(survivor.inspect().workspace,b'other')

    def test_issuing_connection_loss_revokes_its_credentials(self):
        root = self.start(); parent = root.issue(pb.BROKER,b'',b''); broker = self.connect(parent.cookie)
        child = broker.issue(); app = self.connect(child.cookie); broker.close()
        self.assert_closed(app)
        with self.assertRaises(Denied): self.connect(child.cookie)
        self.assertEqual(root.inspect().role,pb.SUPERVISOR)

    def test_credential_ownership_prevents_other_broker_revocation(self):
        root = self.start(); a = self.connect(root.issue(pb.BROKER,b'',b'').cookie)
        b = self.connect(root.issue(pb.BROKER,b'',b'').cookie); grant = a.issue(); app = self.connect(grant.cookie)
        self.assertEqual(b.call(revoke=pb.Revoke(credential_id=grant.id)).code,pb.FORBIDDEN)
        self.assertEqual(app.inspect().credential_id,grant.id)

    def test_invalid_labels_and_roles_are_rejected_without_changing_identity(self):
        root = self.start(); before = root.inspect()
        for issue in (pb.Issue(role=pb.APPLICATION),pb.Issue(role=pb.APPLICATION,workspace=b'x'*257,application=b'a'),
                      pb.Issue(role=pb.APPLICATION,workspace=b'a\0b',application=b'a'),
                      pb.Issue(role=pb.BROKER,workspace=b'not-empty'),pb.Issue(role=999)):
            self.assertEqual(root.call(issue=issue).code,pb.INVALID_ARGUMENT)
        self.assertEqual(root.call(issue=pb.Issue(role=pb.SUPERVISOR)).code,pb.FORBIDDEN)
        self.assertEqual(root.inspect(),before)

    def test_unknown_fields_and_replayed_request_ids_are_rejected(self):
        root = self.start(); request = pb.Request(protocol_version=1,request_id=50,inspect=pb.Inspect())
        self.assertEqual(root.exchange(request).code,pb.OK)
        self.assertEqual(root.exchange(request).code,pb.STALE_SEQUENCE)
        request.request_id = 51
        self.assertEqual(root.exchange(payload=request.SerializeToString()+b'\x98\x06\x01').code,pb.INVALID_ARGUMENT)
        self.assertEqual(root.exchange(request).code,pb.OK)

    def test_swapped_native_client_has_the_same_identity_checks(self):
        root = self.start(); grant = root.issue(); client = self.connect(grant.cookie,endian='>')
        self.assertEqual(client.inspect().workspace,grant.workspace)
        self.assertEqual(client.call(issue=pb.Issue(role=pb.BROKER)).code,pb.FORBIDDEN)

    def test_ordinary_xlib_application_uses_private_xauthority(self):
        root = self.start(); credential = root.issue(); path = self.root/'app.Xauthority'
        values = (b'',str(self.display).encode(),AUTH,credential.cookie)
        path.write_bytes(struct.pack('!H',65535)+b''.join(struct.pack('!H',len(value))+value for value in values)); path.chmod(0o600)
        env = {**os.environ,'DISPLAY':':'+str(self.display),'XAUTHORITY':str(path)}
        result = subprocess.run([str(PROBE)],env=env,capture_output=True,timeout=4)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(root.call(revoke=pb.Revoke(credential_id=credential.id)).code,pb.OK)
        self.assertEqual(subprocess.run([str(PROBE)],env=env,capture_output=True,timeout=4).returncode,3)

    def test_bootstrap_requires_sealed_valid_protobuf(self):
        self.start(sealed=False,expect_ready=False)
        self.start(bootstrap=pb.Bootstrap(protocol_version=2,supervisor_cookie=self.cookie).SerializeToString(),expect_ready=False)
        self.start(bootstrap=pb.Bootstrap(protocol_version=1,supervisor_cookie=b'short').SerializeToString(),expect_ready=False)

    def test_bootstrap_rejects_unknown_fields_empty_and_excessive_payloads(self):
        valid = pb.Bootstrap(protocol_version=1,supervisor_cookie=self.cookie).SerializeToString()
        for data in (b'',b'\xff',b'x'*4097,valid+b'\x98\x06\x01'):
            with self.subTest(size=len(data)): self.start(bootstrap=data,expect_ready=False)

    def test_registry_is_bounded_and_revoked_slots_are_reusable(self):
        root = self.start(); issued = [root.issue() for _ in range(255)]
        self.assertEqual(len({c.cookie for c in issued}),255)
        self.assertEqual(len({c.id for c in issued}),255)
        self.assertEqual(root.call(issue=pb.Issue(role=pb.APPLICATION,workspace=b'w',application=b'a')).code,pb.RESOURCE_EXHAUSTED)
        for old in issued:
            self.assertEqual(root.call(revoke=pb.Revoke(credential_id=old.id)).code,pb.OK)
            new = root.issue(); self.assertNotEqual(new.id,old.id); self.assertNotEqual(new.cookie,old.cookie)
        for old in (issued[0],issued[-1]):
            with self.assertRaises(Denied): self.connect(old.cookie)

    def test_supervisor_connections_have_separate_issuance_lifetimes(self):
        first = self.start(); second = self.connect(self.cookie)
        a = self.connect(first.issue().cookie); independent = second.issue()
        b = self.connect(independent.cookie); first.close(); self.assert_closed(a)
        self.assertEqual(b.inspect().credential_id,independent.id)
        self.assertEqual(second.inspect().role,pb.SUPERVISOR)

    def test_application_cannot_revoke_any_credentials(self):
        root = self.start(); grant = root.issue(); app = self.connect(grant.cookie)
        for credential_id in (grant.id,root.inspect().credential_id,b'',b'x'*32):
            self.assertEqual(app.call(revoke=pb.Revoke(credential_id=credential_id)).code,pb.FORBIDDEN)
        self.assertEqual(app.inspect().credential_id,grant.id)

    def test_supervisor_cannot_revoke_bootstrap_or_revoke_an_unknown_credential(self):
        root = self.start(); identity = root.inspect()
        self.assertEqual(root.call(revoke=pb.Revoke(credential_id=identity.credential_id)).code,pb.FORBIDDEN)
        self.assertEqual(root.call(revoke=pb.Revoke(credential_id=b'x'*32)).code,pb.NOT_FOUND)
        self.assertEqual(root.call(revoke=pb.Revoke(credential_id=b'x')).code,pb.INVALID_ARGUMENT)
        self.assertEqual(root.inspect(),identity)

    def test_valid_maximum_opaque_labels_round_trip(self):
        root = self.start(); grant = root.issue(workspace=b'\xff'*256,application=b'\xfe'*256)
        app = self.connect(grant.cookie); identity = app.inspect()
        self.assertEqual((identity.workspace,identity.application),(grant.workspace,grant.application))

    def test_new_server_generation_rejects_old_application_cookie(self):
        root = self.start(); grant = root.issue(); before = root.inspect()
        second = self.start(); after = second.inspect()
        self.assertNotEqual(before.server_id,after.server_id)
        self.assertNotEqual(before.credential_id,after.credential_id)
        with self.assertRaises(Denied): self.connect(grant.cookie)
        self.assertEqual(second.inspect().role,pb.SUPERVISOR)

    def test_malformed_and_unsupported_requests_leave_connection_usable(self):
        root = self.start()
        for data in (b'',b'\xff',pb.Request(protocol_version=1,request_id=1).SerializeToString()):
            self.assertEqual(root.exchange(payload=data).code,pb.INVALID_ARGUMENT)
        request = pb.Request(protocol_version=2,request_id=1,inspect=pb.Inspect())
        self.assertEqual(root.exchange(request).code,pb.UNSUPPORTED_VERSION)
        request.protocol_version = 1
        self.assertEqual(root.exchange(request).code,pb.OK)
        nested = pb.Request(protocol_version=1,request_id=2,issue=pb.Issue(role=pb.APPLICATION,workspace=b'w',application=b'a'))
        nested.issue.ParseFromString(nested.issue.SerializeToString()+b'\x98\x06\x01')
        self.assertEqual(root.exchange(nested).code,pb.INVALID_ARGUMENT)
        self.assertEqual(root.exchange(pb.Request(protocol_version=1,request_id=2,inspect=pb.Inspect())).code,pb.OK)


if __name__ == '__main__': unittest.main(verbosity=2)
