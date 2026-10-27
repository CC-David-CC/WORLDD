"""A revoked native input owner fails locally while an independent owner remains usable."""
import fcntl
import os
from pathlib import Path
import select
import struct
import subprocess
import sys
import time
import unittest
import compositor_native_pb2 as config
import core_pb2 as core
import input_pb2 as ip
import security_pb2 as security
from harness.wire import frame

PROBE = str(Path(sys.argv.pop(1)).resolve())
import test_security_identity as native


class ScopedInputTests(unittest.TestCase):
    setUp = native.SecurityIdentityTests.setUp
    start = native.SecurityIdentityTests.start
    connect = native.SecurityIdentityTests.connect

    def independent_input(self, client, sequence, **operation):
        name = b'SPATIAL-INPUT'
        header, _ = client.request(98, struct.pack('<HH', len(name), 0)+name)
        if not header[8]:
            # Hiding the extension is insufficient proof. Attempt its real opcode anyway.
            header, _ = self.supervisor.request(98, struct.pack('<HH', len(name), 0)+name)
            self.assertTrue(header[8])
        request = ip.Request(protocol_version=1, request_id=sequence, **operation).SerializeToString()
        client.send(header[9], struct.pack('<I', len(request))+request)
        for _ in range(128):
            header = client.exact(32)
            if header[0] == 34: continue
            self.assertEqual(struct.unpack_from('<H', header, 2)[0], client.sequence % 65536)
            if header[0] == 0:
                self.assertEqual(header[1], 10)  # BadAccess is an explicit server denial.
                return None
            self.assertEqual(header[0], 1)
            size = struct.unpack_from('<I', header, 4)[0]*4
            self.assertLessEqual(size, 4096)
            data = client.exact(size)
            count = struct.unpack_from('<I', header, 8)[0]
            self.assertLessEqual(count, size)
            reply = ip.Reply.FromString(data[:count])
            self.assertEqual(reply.request_id, sequence)
            return reply
        self.fail('Excessive native events')

    def read(self, proc, message):
        def exact(size):
            data = b''
            while len(data) < size:
                self.assertTrue(select.select([proc.stdout], [], [], 5)[0], 'Input reply timed out')
                part = os.read(proc.stdout.fileno(), size-len(data))
                self.assertTrue(part, 'Input owner exited')
                data += part
            return data
        size = struct.unpack('!I', exact(4))[0]
        self.assertLessEqual(size, 1048576)
        return message.FromString(exact(size))

    def launch(self, *, sealed=True, bad_pid=False, role=security.COMPOSITOR):
        self.start()
        self.credential = self.supervisor.issue(role, b'', b'')
        self.other_credential = self.supervisor.issue(security.COMPOSITOR, b'', b'')
        descriptors = []
        for index, credential in enumerate((self.credential, self.other_credential)):
            value = config.NativeCompositorConfig(protocol_version=1, display_number=self.display,
                compositor_cookie=credential.cookie, server_id=self.supervisor.inspect().server_id,
                server_pid=self.server.pid + (1 if bad_pid and index == 0 else 0), server_uid=os.getuid())
            fd = os.memfd_create('scoped-input', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
            descriptors.append(fd)
            os.write(fd, value.SerializeToString())
            if sealed or index:
                fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
        path = self.root/'input.log'
        log = path.open('wb')
        try:
            proc = subprocess.Popen([PROBE, *map(str, descriptors)], pass_fds=descriptors,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log)
        finally:
            for fd in descriptors: os.close(fd)
        def cleanup():
            if not proc.stdin.closed: proc.stdin.close()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=3); self.fail('Input probe hung')
            proc.stdout.close(); log.close()
            text = path.read_text(errors='replace')
            for marker in ('AddressSanitizer', 'LeakSanitizer', 'runtime error:'): self.assertNotIn(marker, text)
            if os.environ.get('SPATIAL_SECURITY_EVIDENCE'):
                target = Path(os.environ['SPATIAL_SECURITY_EVIDENCE']); target.mkdir(parents=True, exist_ok=True)
                (target/(self._testMethodName+'-input.log')).write_text(text)
        self.addCleanup(cleanup)
        return proc

    def call(self, proc, sequence, **operation):
        proc.stdin.write(frame(ip.Request(protocol_version=1, request_id=sequence, **operation)))
        proc.stdin.flush()
        envelope = self.read(proc, core.RenderResult)
        self.assertEqual(envelope.request_id, sequence)
        if envelope.HasField('error'): return envelope.error
        self.assertTrue(envelope.HasField('ready'))
        reply = self.read(proc, ip.Reply)
        self.assertEqual(reply.request_id, sequence)
        return reply

    def test_revocation_fails_closed_without_exiting_or_harming_other_owner(self):
        proc = self.launch()
        self.assertTrue(self.read(proc, core.RenderResult).HasField('ready'))
        opened = self.call(proc, 1, open=ip.Empty())
        self.assertEqual(opened.code, ip.OK); self.assertGreater(opened.seat, 0)
        self.assertEqual(self.call(proc, 2, status=ip.Seat(id=opened.seat)).code, ip.OK)
        revoked = self.supervisor.call(revoke=security.Revoke(credential_id=self.credential.id))
        self.assertEqual(revoked.code, security.OK); self.assertTrue(revoked.revoked)
        for sequence in (3, 4):
            reply = self.call(proc, sequence, status=ip.Seat(id=opened.seat))
            self.assertIsInstance(reply, core.Error)
            self.assertEqual(reply.code, core.UNAVAILABLE)
            self.assertIn('[channel closed]',reply.message)
            self.assertIsNone(proc.poll())
        proc.stdin.close(); self.assertEqual(proc.wait(timeout=5), 0)

    def test_explicit_seat_close_preserves_channel(self):
        proc = self.launch(); self.read(proc, core.RenderResult)
        opened = self.call(proc, 1, open=ip.Empty())
        self.assertEqual(self.call(proc, 2, close=ip.Seat(id=opened.seat)).code, ip.OK)
        self.assertEqual(self.call(proc, 3, status=ip.Seat(id=opened.seat)).code, ip.NO_SEAT)
        self.assertEqual(self.call(proc, 4, open=ip.Empty()).code, ip.OK)
        proc.stdin.close(); self.assertEqual(proc.wait(timeout=5), 0)

    def test_revoked_peer_is_detected_before_next_native_request(self):
        proc = self.launch(); self.read(proc, core.RenderResult)
        def health(sequence):
            proc.stdin.write(frame(ip.Request(protocol_version=1, request_id=sequence)))
            proc.stdin.flush()
            return self.read(proc, core.RenderResult)
        self.assertTrue(health(1).HasField('ready'))
        revoked = self.supervisor.call(revoke=security.Revoke(credential_id=self.credential.id))
        self.assertEqual(revoked.code, security.OK); self.assertTrue(revoked.revoked)
        result=health(2)
        self.assertTrue(result.HasField('error'), result)
        self.assertEqual(result.error.code, core.UNAVAILABLE)
        self.assertIsNone(proc.poll())
        proc.stdin.close(); self.assertEqual(proc.wait(timeout=5), 0)

    def test_unsealed_configuration_is_rejected(self):
        proc = self.launch(sealed=False); self.assertEqual(proc.wait(timeout=5), 2)

    def test_wrong_server_process_is_rejected(self):
        proc = self.launch(bad_pid=True); self.assertEqual(proc.wait(timeout=5), 2)

    def test_broker_credential_cannot_be_used_for_input(self):
        proc = self.launch(role=security.BROKER); self.assertEqual(proc.wait(timeout=5), 2)

    def test_independent_connection_cannot_control_another_connections_seat(self):
        proc = self.launch(); self.read(proc, core.RenderResult)
        opened = self.call(proc, 1, open=ip.Empty())
        self.assertEqual(opened.code, ip.OK)
        other = self.connect(self.credential.cookie)
        reply = self.independent_input(other, 1, close=ip.Seat(id=opened.seat))
        self.assertIsNotNone(reply); self.assertEqual(reply.code, ip.NO_SEAT)
        self.assertEqual(self.call(proc, 2, status=ip.Seat(id=opened.seat)).code, ip.OK)

    def test_seat_revocation_requires_the_owning_compositor_credential(self):
        proc = self.launch(); self.read(proc, core.RenderResult)
        opened = self.call(proc, 1, open=ip.Empty())
        self.assertEqual(opened.code, ip.OK)
        unrelated = self.connect(self.other_credential.cookie)
        denied = self.independent_input(unrelated, 1, revoke=ip.Seat(id=opened.seat))
        self.assertIsNotNone(denied); self.assertEqual(denied.code, ip.FORBIDDEN)
        active = self.call(proc, 2, key=ip.Key(seat=opened.seat,keycode=50,pressed=True))
        self.assertEqual(active.code, ip.NO_FOCUS,
            'unrelated compositor credential changed the owner seat state')
        same_grant = self.connect(self.credential.cookie)
        revoked = self.independent_input(same_grant, 1, revoke=ip.Seat(id=opened.seat))
        self.assertIsNotNone(revoked); self.assertEqual(revoked.code, ip.OK)
        cleared = self.call(proc, 3, status=ip.Seat(id=opened.seat))
        self.assertEqual(cleared.code, ip.OK)
        self.assertFalse(cleared.held_buttons or any(cleared.held_keys))
        blocked = self.call(proc, 4, key=ip.Key(seat=opened.seat,keycode=50,pressed=True))
        self.assertEqual(blocked.code,ip.FORBIDDEN,
            'same-credential revocation did not permanently fence the seat')

    def test_broker_without_root_ownership_is_denied_by_server(self):
        self.start()
        broker = self.connect(self.supervisor.issue(security.BROKER, b'', b'').cookie)
        reply = self.independent_input(broker, 1, open=ip.Empty())
        if reply is not None: self.assertEqual(reply.code, ip.FORBIDDEN)

    def test_guardian_binding_requires_exact_credential_and_independent_connection(self):
        owner=self.launch();self.read(owner,core.RenderResult)
        seat=self.call(owner,1,open=ip.Empty()).seat
        self.assertEqual(self.call(owner,2,guard=ip.Seat(id=seat)).code,ip.FORBIDDEN)
        unrelated=self.connect(self.other_credential.cookie)
        self.assertEqual(self.independent_input(unrelated,1,guard=ip.Seat(id=seat)).code,ip.FORBIDDEN)
        guard=self.connect(self.credential.cookie)
        self.assertEqual(self.independent_input(guard,1,guard=ip.Seat(id=seat)).code,ip.OK)
        self.assertEqual(self.independent_input(guard,2,guard=ip.Seat(id=seat)).code,ip.OK)
        competitor=self.connect(self.credential.cookie)
        self.assertEqual(self.independent_input(competitor,1,guard=ip.Seat(id=seat)).code,ip.FORBIDDEN)
        competitor.close()
        self.assertEqual(self.call(owner,3,key=ip.Key(seat=seat,keycode=38,pressed=True)).code,ip.NO_FOCUS)
        guard.close();sequence=3;deadline=time.monotonic()+2
        while True:
            sequence+=1;reply=self.call(owner,sequence,key=ip.Key(seat=seat,keycode=38,pressed=True))
            if reply.code==ip.FORBIDDEN or time.monotonic()>=deadline:break
            self.assertEqual(reply.code,ip.NO_FOCUS);time.sleep(.01)
        self.assertEqual(reply.code,ip.FORBIDDEN)
        replacement=self.connect(self.credential.cookie)
        self.assertEqual(self.independent_input(replacement,1,guard=ip.Seat(id=seat)).code,ip.FORBIDDEN)
        owner.stdin.close();self.assertEqual(owner.wait(timeout=5),0)

    def test_owner_can_close_guarded_seat_before_guardian_disconnects(self):
        owner=self.launch();self.read(owner,core.RenderResult)
        seat=self.call(owner,1,open=ip.Empty()).seat;guard=self.connect(self.credential.cookie)
        self.assertEqual(self.independent_input(guard,1,guard=ip.Seat(id=seat)).code,ip.OK)
        self.assertEqual(self.call(owner,2,close=ip.Seat(id=seat)).code,ip.OK)
        guard.close()
        another=self.call(owner,3,open=ip.Empty());self.assertEqual(another.code,ip.OK)
        self.assertNotEqual(another.seat,seat)
        self.assertEqual(self.call(owner,4,key=ip.Key(seat=another.seat,keycode=38,pressed=True)).code,ip.NO_FOCUS)
        owner.stdin.close();self.assertEqual(owner.wait(timeout=5),0)

    def test_guardian_disconnect_releases_real_application_input(self):
        from test_resource_isolation import Client
        owner=self.launch();self.read(owner,core.RenderResult)
        seat=self.call(owner,1,open=ip.Empty()).seat;guard=self.connect(self.credential.cookie)
        self.assertEqual(self.independent_input(guard,1,guard=ip.Seat(id=seat)).code,ip.OK)
        survivor=self.connect(self.other_credential.cookie)
        survivor_seat=self.independent_input(survivor,1,open=ip.Empty()).seat
        app=Client(self.display,self.supervisor.issue(security.APPLICATION,b'guard-test',b'editor').cookie)
        self.clients.append(app);window=app.create_window()
        self.assertIsNone(app.operation(8,app.pack('I',window)))
        self.assertIsNone(app.operation(2,app.pack('III',window,1<<11,15)))
        target=self.call(owner,2,inspect=ip.Inspect(xid=window)).target
        self.assertEqual(self.call(owner,3,button=ip.Button(seat=seat,target=target,x=8,y=8,
            button=1,pressed=True,focus=True)).code,ip.OK)
        self.assertEqual(self.call(owner,4,key=ip.Key(seat=seat,keycode=50,pressed=True)).code,ip.OK)
        def events(expected):
            result=[];deadline=time.monotonic()+2
            while len(result)<len(expected):
                left=deadline-time.monotonic();self.assertGreater(left,0)
                self.assertTrue(select.select([app.stream],[],[],left)[0],'Independent application saw no input')
                row=app.exact(32)
                self.assertIn(row[0],(2,3,4,5,34),row)
                if row[0]!=34:result.append((row[0],row[1]))
            self.assertEqual(result,expected)
        events([(4,1),(2,50)])
        guard.close();events([(5,1),(3,50)])
        state=self.call(owner,5,status=ip.Seat(id=seat))
        self.assertEqual(state.code,ip.OK);self.assertFalse(state.held_buttons or any(state.held_keys))
        self.assertEqual(self.call(owner,6,key=ip.Key(seat=seat,keycode=38,pressed=True)).code,ip.FORBIDDEN)
        self.assertEqual(self.independent_input(survivor,2,key=ip.Key(seat=survivor_seat,keycode=38,pressed=True)).code,ip.NO_FOCUS)
        owner.stdin.close();self.assertEqual(owner.wait(timeout=5),0)


if __name__ == '__main__': unittest.main(verbosity=2)
