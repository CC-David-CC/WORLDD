"""Checked supervisor cleanup against a real, independently observed native server."""
import fcntl
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import sys
import unittest
from unittest.mock import patch
import native_scope_pb2 as scope
import security_pb2 as security
import input_pb2 as native_input

SCOPE = str(Path(sys.argv.pop(1)).resolve())
# The existing security fixture consumes the independent authentication probe argument.
import test_security_identity as native
from harness.wire import frame
from harness.probe import Probe


class NativeScopeTests(native.SecurityIdentityTests):
    def read_reply(self, proc, timeout=5, message_type=scope.NativeScopeReply):
        def exact(size):
            data = b''
            while len(data) < size:
                self.assertTrue(select.select([proc.stdout], [], [], timeout)[0], 'Native scope reply timed out')
                part = os.read(proc.stdout.fileno(), size-len(data))
                self.assertTrue(part, 'Native scope exited before its reply')
                data += part
            return data
        size = struct.unpack('!I', exact(4))[0]
        self.assertLessEqual(size, 1048576)
        reply = message_type.FromString(exact(size))
        self.assertEqual(reply.protocol_version, 1)
        return reply

    def launch_scope(self, role=security.BROKER, sealed=True, server_id=None, expect_ready=True):
        identity = self.supervisor.inspect()
        config = scope.NativeScopeConfig(protocol_version=1, display_number=self.display,
            supervisor_cookie=self.cookie, server_id=server_id if server_id is not None else identity.server_id,
            server_pid=self.server.pid, server_uid=os.getuid(), launch_id=os.urandom(32), role=role)
        fd = os.memfd_create('test-native-scope', os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
        os.write(fd, config.SerializeToString())
        if sealed: fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
        path = self.root/('scope-'+str(len(list(self.root.glob('scope-*.log'))))+'.log')
        log = path.open('wb')
        try: proc = subprocess.Popen([SCOPE,str(fd)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, pass_fds=(fd,))
        finally: os.close(fd)
        def cleanup():
            if proc.poll() is None:
                proc.stdin.close()
                try: proc.wait(timeout=5)
                except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=3)
            if not proc.stdin.closed: proc.stdin.close()
            proc.stdout.close(); log.close()
            text = path.read_text(errors='replace')
            for marker in ('AddressSanitizer','LeakSanitizer','runtime error:'): self.assertNotIn(marker,text)
        self.addCleanup(cleanup)
        reply = self.read_reply(proc)
        if expect_ready:
            self.assertTrue(reply.HasField('ready'), reply)
            self.assertEqual(reply.ready.identity.launch_id, config.launch_id)
            self.assertEqual(reply.ready.identity.server_id, identity.server_id)
            self.assertEqual(reply.ready.identity.credential_id, reply.ready.credential.id)
            self.assertEqual(reply.ready.identity.role, role)
        else:
            self.assertTrue(reply.HasField('error'), reply)
            self.assertEqual(reply.error.code, scope.NativeScopeError.INVALID_ARGUMENT)
            self.assertNotEqual(proc.wait(timeout=5),0)
        return proc, reply

    def stop_scope(self, proc, sequence=1):
        proc.stdin.write(frame(scope.NativeScopeRequest(protocol_version=1, request_id=sequence, stop=scope.NativeScopeQuery())))
        proc.stdin.flush()

    def stopped(self, proc, expected, sequence=1):
        reply = self.read_reply(proc)
        self.assertEqual(reply.request_id, sequence)
        self.assertTrue(reply.HasField('stopped'), reply)
        self.assertEqual(reply.stopped.identity, expected)
        return reply

    def finish(self, proc):
        proc.stdin.close(); self.assertEqual(proc.wait(timeout=5),0)

    def test_broker_stop_closes_descendants_preserves_independent_application(self):
        self.start(); proc, ready = self.launch_scope()
        broker = self.connect(ready.ready.credential.cookie)
        app = broker.issue(); client = self.connect(app.cookie)
        independent = self.supervisor.issue(workspace=b'other', application=b'other')
        survivor = self.connect(independent.cookie)
        self.stop_scope(proc); self.stopped(proc,ready.ready.identity)
        self.assert_closed(broker); self.assert_closed(client)
        with self.assertRaises(native.Denied): self.connect(app.cookie)
        self.assertEqual(survivor.inspect().credential_id,independent.id)
        self.finish(proc)

    def test_compositor_stop_closes_only_owned_connections(self):
        self.start(); proc, ready = self.launch_scope(security.COMPOSITOR)
        compositor = self.connect(ready.ready.credential.cookie)
        another = self.supervisor.issue(security.COMPOSITOR,b'',b'')
        survivor = self.connect(another.cookie)
        self.stop_scope(proc); self.stopped(proc,ready.ready.identity)
        self.assert_closed(compositor)
        self.assertEqual(survivor.inspect().credential_id,another.id)
        self.finish(proc)

    def test_compositor_stop_releases_real_native_key_and_button(self):
        self.start(); proc, ready = self.launch_scope(security.COMPOSITOR)
        def authority_file(name, cookie):
            data = b'\xff\xff'
            for field in (b'', str(self.display).encode(), b'MIT-MAGIC-COOKIE-1', cookie):
                data += struct.pack('!H',len(field))+field
            path=self.root/name; path.write_bytes(data); path.chmod(0o600); return str(path)
        compositor_auth=authority_file('compositor.auth',ready.ready.credential.cookie)
        app=self.supervisor.issue(workspace=b'input',application=b'witness')
        app_auth=authority_file('application.auth',app.cookie)
        log=(self.root/'bridge.log').open('wb')
        bridge=subprocess.Popen([str(Path(SCOPE).with_name('spatial-input-bridge')),'--owner'],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,
            env=dict(os.environ,SPATIAL_TEST_DISPLAY=':'+str(self.display),XAUTHORITY=compositor_auth))
        def close_bridge():
            if bridge.poll() is None: bridge.terminate()
            try: bridge.wait(timeout=4)
            except subprocess.TimeoutExpired: bridge.kill(); bridge.wait(timeout=3)
            bridge.stdin.close(); bridge.stdout.close(); log.close()
            text=(self.root/'bridge.log').read_text(errors='replace')
            for marker in ('AddressSanitizer','LeakSanitizer','runtime error:'): self.assertNotIn(marker,text)
        self.addCleanup(close_bridge)
        self.read_reply(bridge,message_type=native_input.Reply)
        # Ordinary application credentials cannot enumerate XI devices.
        # Observe real application events here and inspect device identity through the supervisor below.
        with patch.dict(os.environ,{'XAUTHORITY':app_auth}): probe=Probe(self.root,xi2=False,display=':'+str(self.display))
        self.addCleanup(probe.close)
        sequence=0
        def call(**operation):
            nonlocal sequence
            sequence+=1
            bridge.stdin.write(frame(native_input.Request(protocol_version=1,request_id=sequence,**operation)));bridge.stdin.flush()
            reply=self.read_reply(bridge,message_type=native_input.Reply)
            self.assertEqual(reply.request_id,sequence);self.assertEqual(reply.code,native_input.OK)
            return reply
        opened=call(open=native_input.Empty());seat=opened.seat
        target=call(inspect=native_input.Inspect(xid=probe.xid)).target
        call(button=native_input.Button(seat=seat,target=target,x=10,y=10,button=1,pressed=True,focus=True))
        call(key=native_input.Key(seat=seat,keycode=38,pressed=True))
        probe.wait(lambda e:e.get('event')=='button' and e.get('pressed'))
        probe.wait(lambda e:e.get('event')=='key' and e.get('pressed'))
        name=b'XInputExtension'
        head,_=self.supervisor.request(98,struct.pack('<HH',len(name),0)+name)
        self.assertTrue(head[8])
        head,_=self.supervisor.request(head[9],struct.pack('<I',probe.xid),minor=45)
        self.assertTrue(head[8]);self.assertGreater(opened.pointer_device,0)
        self.assertEqual(struct.unpack_from('<H',head,10)[0],opened.pointer_device)
        self.stop_scope(proc);self.stopped(proc,ready.ready.identity)
        probe.wait(lambda e:e.get('event')=='button' and not e.get('pressed'))
        probe.wait(lambda e:e.get('event')=='key' and not e.get('pressed'))
        self.assertIsNone(probe.proc.poll());self.finish(proc)

    def test_missing_credential_is_not_accepted_as_a_cleanup_receipt(self):
        self.start(); proc, ready = self.launch_scope()
        reply=self.supervisor.call(revoke=security.Revoke(credential_id=ready.ready.identity.credential_id))
        self.assertEqual(reply.code,security.OK)
        self.stop_scope(proc);reply=self.read_reply(proc)
        self.assertTrue(reply.HasField('error'),reply)
        self.assertEqual(reply.error.code,scope.NativeScopeError.CLEANUP_UNCONFIRMED)
        self.assertNotEqual(proc.wait(timeout=5),0)

    def test_stop_waits_for_disposable_server_resume(self):
        self.start(); proc, ready = self.launch_scope()
        broker = self.connect(ready.ready.credential.cookie)
        os.kill(self.server.pid,signal.SIGSTOP)
        try:
            self.stop_scope(proc)
            self.assertFalse(select.select([proc.stdout],[],[],.15)[0], 'Stopped server cannot acknowledge cleanup')
            self.assertIsNone(proc.poll())
        finally: os.kill(self.server.pid,signal.SIGCONT)
        self.stopped(proc,ready.ready.identity); self.assert_closed(broker); self.finish(proc)

    def test_stopped_server_timeout_cannot_report_cleanup(self):
        self.start(); proc, _ = self.launch_scope()
        os.kill(self.server.pid,signal.SIGSTOP)
        try:
            self.stop_scope(proc); reply=self.read_reply(proc)
            self.assertTrue(reply.HasField('error'),reply)
            self.assertEqual(reply.error.code,scope.NativeScopeError.CLEANUP_UNCONFIRMED)
            self.assertNotEqual(proc.wait(timeout=5),0)
        finally: os.kill(self.server.pid,signal.SIGCONT)

    def test_server_exit_cannot_replace_a_cleanup_reply(self):
        self.start(); proc, _ = self.launch_scope()
        self.server.terminate(); self.server.wait(timeout=4)
        self.stop_scope(proc); reply=self.read_reply(proc)
        self.assertTrue(reply.HasField('error'),reply)
        self.assertEqual(reply.error.code,scope.NativeScopeError.CLEANUP_UNCONFIRMED)
        self.assertNotEqual(proc.wait(timeout=5),0)

    def test_checked_stop_retry_needs_no_new_server_operation(self):
        self.start(); proc, ready = self.launch_scope()
        self.stop_scope(proc); first=self.stopped(proc,ready.ready.identity)
        self.server.terminate(); self.server.wait(timeout=4)
        self.stop_scope(proc,2); second=self.stopped(proc,ready.ready.identity,2)
        self.assertEqual(first.stopped,second.stopped); self.finish(proc)

    def test_unsealed_config_does_not_issue_a_credential(self):
        self.start(); self.launch_scope(sealed=False,expect_ready=False)

    def test_application_role_is_not_an_authority_scope(self):
        self.start(); self.launch_scope(role=security.APPLICATION,expect_ready=False)

    def test_wrong_server_identity_does_not_issue_a_credential(self):
        self.start(); self.launch_scope(server_id=os.urandom(32),expect_ready=False)


def suite():
    return unittest.TestSuite(NativeScopeTests(n) for n in vars(NativeScopeTests) if n.startswith('test_'))


if __name__ == '__main__': unittest.main(defaultTest='suite',verbosity=2)
