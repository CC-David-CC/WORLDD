"""A revoked native input owner fails locally while an independent owner remains usable."""
import fcntl
import os
from pathlib import Path
import select
import struct
import subprocess
import sys
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
        other = self.supervisor.issue(security.COMPOSITOR, b'', b'')
        descriptors = []
        for index, credential in enumerate((self.credential, other)):
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
            self.assertIsNone(proc.poll())
        proc.stdin.close(); self.assertEqual(proc.wait(timeout=5), 0)

    def test_explicit_seat_close_preserves_channel(self):
        proc = self.launch(); self.read(proc, core.RenderResult)
        opened = self.call(proc, 1, open=ip.Empty())
        self.assertEqual(self.call(proc, 2, close=ip.Seat(id=opened.seat)).code, ip.OK)
        self.assertEqual(self.call(proc, 3, status=ip.Seat(id=opened.seat)).code, ip.NO_SEAT)
        self.assertEqual(self.call(proc, 4, open=ip.Empty()).code, ip.OK)
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

    def test_broker_without_root_ownership_is_denied_by_server(self):
        self.start()
        broker = self.connect(self.supervisor.issue(security.BROKER, b'', b'').cookie)
        reply = self.independent_input(broker, 1, open=ip.Empty())
        if reply is not None: self.assertEqual(reply.code, ip.FORBIDDEN)


if __name__ == '__main__': unittest.main(verbosity=2)
