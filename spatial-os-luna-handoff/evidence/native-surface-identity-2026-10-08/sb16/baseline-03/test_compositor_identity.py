"""Real spatialwm snapshots preserve trusted native application attribution."""
import os
from pathlib import Path
import socket
import struct
import subprocess
import time
import unittest
import core_pb2 as core
import security_pb2 as sec
import test_surface_identity as witness
from harness.wire import frame


class CompositorIdentityTests(unittest.TestCase):
    setUp = witness.SurfaceIdentityTests.setUp
    start = witness.SurfaceIdentityTests.start
    connect = witness.SurfaceIdentityTests.connect
    forge = witness.SurfaceIdentityTests.forge

    def scene(self):
        self.supervisor = self.start()
        self.owner_grant = self.supervisor.issue(workspace=b'one', application=b'editor')
        self.owner = self.connect(self.owner_grant.cookie)
        self.window = self.owner.create_window()
        self.assertIsNone(self.owner.operation(8, self.owner.pack('I', self.window)))

    def renderer(self):
        (self.root/'renderer').mkdir()
        cookie = self.supervisor.issue(sec.COMPOSITOR, b'', b'').cookie
        data = b'\xff\xff'
        for part in (b'', str(self.display).encode(), b'MIT-MAGIC-COOKIE-1', cookie): data += struct.pack('!H', len(part))+part
        auth = self.root/'renderer.Xauthority'; auth.write_bytes(data); auth.chmod(0o600)
        stream, child = socket.socketpair(); stream.settimeout(10)
        self.stream = stream; self.sequence = 0
        log_path = self.root/'renderer.log'; log = log_path.open('wb')
        executable = witness.binding.resources.native.PROBE.parent/'spatialwm'
        proc = subprocess.Popen([str(executable), '--state-dir', str(self.root/'renderer'), '--display', ':'+str(self.display)],
                                stdin=child, stdout=child, stderr=log,
                                env=dict(os.environ, XAUTHORITY=str(auth), LIBGL_ALWAYS_SOFTWARE='1'))
        child.close()
        def close():
            stream.close()
            try: proc.wait(timeout=4)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=3)
            log.close(); diagnostic = log_path.read_text(errors='replace')
            if os.environ.get('SPATIAL_SECURITY_EVIDENCE'):
                target = Path(os.environ['SPATIAL_SECURITY_EVIDENCE']); target.mkdir(parents=True, exist_ok=True)
                (target/(self._testMethodName+'-renderer.log')).write_text(diagnostic)
            for marker in ('AddressSanitizer', 'LeakSanitizer', 'runtime error:'): self.assertNotIn(marker, diagnostic)
            self.assertEqual(proc.returncode, 0, diagnostic)
        self.addCleanup(close)
        self.assertTrue(self.receive().HasField('ready'), 'Real compositor did not start')

    def receive(self):
        def exact(size):
            data = b''
            while len(data) < size:
                part = self.stream.recv(size-len(data))
                if not part: raise AssertionError('Production compositor closed its channel')
                data += part
            return data
        size = struct.unpack('!I', exact(4))[0]
        self.assertLessEqual(size, 1048576)
        reply = core.RenderResult.FromString(exact(size)); self.assertEqual(reply.protocol_version, 1)
        return reply

    def snapshot(self):
        self.sequence += 1
        self.stream.sendall(frame(core.RenderCommand(protocol_version=1, request_id=self.sequence, sync=core.RendererSyncRequest())))
        reply = self.receive(); self.assertEqual(reply.request_id, self.sequence)
        self.assertTrue(reply.HasField('snapshot'), reply)
        return {s.xid:s for s in reply.snapshot.surfaces}

    def surface(self, xid=None, mapped=True):
        xid = xid or self.window; deadline = time.monotonic()+3
        while True:
            value = self.snapshot().get(xid)
            if value is not None and value.mapped == mapped: return value
            self.assertLess(time.monotonic(), deadline, 'Missing native surface state')
            time.sleep(.01)

    def test_snapshot_contains_authenticated_application_identity(self):
        self.scene(); self.renderer(); surface = self.surface()
        self.assertTrue(surface.HasField('application_identity'))
        identity = surface.application_identity
        self.assertEqual((identity.workspace, identity.application), (b'one', b'editor'))
        self.assertEqual(len(identity.server_id), 32); self.assertGreater(identity.connection_id, 0)
        self.assertGreater(surface.server_generation, 0)

    def test_properties_do_not_choose_snapshot_identity(self):
        self.scene(); self.forge(self.owner, self.window); self.renderer()
        identity = self.surface().application_identity
        self.assertEqual((identity.workspace, identity.application), (b'one', b'editor'))

    def test_separate_workspaces_remain_distinct_in_one_snapshot(self):
        self.scene()
        peer = self.connect(self.supervisor.issue(workspace=b'two', application=b'editor').cookie)
        window = peer.create_window(); self.assertIsNone(peer.operation(8, peer.pack('I', window)))
        self.renderer(); a = self.surface().application_identity; b = self.surface(window).application_identity
        self.assertEqual(a.application, b.application); self.assertEqual((a.workspace, b.workspace), (b'one', b'two'))

    def test_unmap_clears_attribution_and_remap_restores_it(self):
        self.scene(); self.renderer(); before = self.surface()
        self.assertIsNone(self.owner.operation(10, self.owner.pack('I', self.window)))
        self.assertFalse(self.surface(mapped=False).HasField('application_identity'))
        self.assertIsNone(self.owner.operation(8, self.owner.pack('I', self.window)))
        after = self.surface()
        self.assertEqual(before.application_identity, after.application_identity)
        self.assertNotEqual(before.server_generation, after.server_generation)

    def test_non_application_window_is_not_a_workspace_surface(self):
        self.scene(); window = self.supervisor.create_window()
        self.assertIsNone(self.supervisor.operation(8, self.supervisor.pack('I', window)))
        self.renderer(); self.surface()
        self.assertNotIn(window, self.snapshot())

    def test_revoked_application_disappears_from_snapshot(self):
        self.scene(); self.renderer(); self.surface()
        self.assertEqual(self.supervisor.call(revoke=sec.Revoke(credential_id=self.owner_grant.id)).code, sec.OK)
        deadline = time.monotonic()+3
        while self.window in self.snapshot():
            self.assertLess(time.monotonic(), deadline, 'Revoked application remains visible'); time.sleep(.01)

    def test_same_group_new_connection_has_distinct_attribution(self):
        self.scene(); peer = self.connect(self.owner_grant.cookie); window = peer.create_window()
        self.assertIsNone(peer.operation(8, peer.pack('I', window)))
        self.renderer(); a = self.surface().application_identity; b = self.surface(window).application_identity
        self.assertEqual((a.workspace, a.application), (b.workspace, b.application))
        self.assertNotEqual(a.connection_id, b.connection_id)


if __name__ == '__main__': unittest.main(verbosity=2)
