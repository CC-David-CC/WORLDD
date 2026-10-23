"""Hostile replies to the real worldd shared RenderWorker client."""
import os
from pathlib import Path
import socket
import struct
import subprocess
import time
import unittest
from harness.wire import frame
import core_pb2 as core
import presentation_pb2 as protocol
import test_presentation_server as fixture

class SharedClientFailureTests(unittest.TestCase):
    setUp=fixture.PresentationServerTests.setUp
    start=fixture.PresentationServerTests.start
    connect=fixture.PresentationServerTests.connect
    scene=fixture.PresentationServerTests.scene
    config=fixture.PresentationServerTests.config
    receive=fixture.PresentationServerTests.receive
    send=fixture.PresentationServerTests.send

    def attempt(self,kind):
        self.scene();peer,client=socket.socketpair();peer.settimeout(12)
        native=self.config();pidfd=os.pidfd_open(os.getpid())
        state=self.root/'world';state.mkdir();path=self.root/'world.log';log=path.open('wb')
        proc=None
        try:
            proc=subprocess.Popen([str(fixture.EXECUTABLE.parent/'worldd'),'--state-dir',str(state),
                '--native-security-fd',str(native),'--presentation-fd',str(client.fileno()),
                '--presentation-pidfd',str(pidfd),'--presentation-pid',str(os.getpid()),
                '--presentation-owner-pid',str(os.getpid())],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,
                pass_fds=(native,pidfd,client.fileno()))
        finally:client.close();os.close(native);os.close(pidfd)
        try:
            # recv discards ancillary descriptors. This peer does not use the credential.
            hello=self.receive(peer,protocol.PresentationWorldRequest)
            self.assertTrue(hello.HasField('hello'));self.assertEqual(hello.request_id,1)
            exchange = kind.startswith('exchange_')
            if exchange:
                self.send(peer,core.RenderResult(protocol_version=1,request_id=1,ready=core.RendererReady()))
                request=core.Request(protocol_version=1,request_id=1,
                    surface_find=core.SurfaceFindRequest(probe_id='hostile-exchange'))
                proc.stdin.write(frame(request));proc.stdin.flush()
                forwarded=self.receive(peer,protocol.PresentationWorldRequest)
                self.assertTrue(forwarded.HasField('command'),forwarded)
                self.assertEqual(forwarded.request_id,2)
                kind=kind.removeprefix('exchange_')
            start=time.monotonic()
            if kind=='wrong_id':
                self.send(peer,core.RenderResult(protocol_version=1,request_id=2,ready=core.RendererReady()))
            elif kind=='descriptor':
                fd=os.memfd_create('unexpected-reply-descriptor',os.MFD_CLOEXEC)
                try:self.send(peer,core.RenderResult(protocol_version=1,request_id=1,ready=core.RendererReady()),(fd,))
                finally:os.close(fd)
            elif kind=='version':
                self.send(peer,core.RenderResult(protocol_version=2,request_id=2 if exchange else 1,ready=core.RendererReady()))
            elif kind=='partial':peer.sendall(b'\x00')
            elif kind=='malformed':peer.sendall(struct.pack('!I',1)+b'\xff')
            self.assertEqual(proc.wait(timeout=12),2)
            if kind=='partial':
                self.assertGreaterEqual(time.monotonic()-start,4.9)
                self.assertLess(time.monotonic()-start,8)
            diagnostic=path.read_text(errors='replace')
            expected={'wrong_id':'Missing shared compositor readiness','descriptor':'Invalid shared compositor reply',
                'partial':'Presentation input deadline exceeded','malformed':'Invalid shared compositor reply',
                'version':'Invalid shared compositor reply'}[kind]
            if exchange:expected='Shared compositor is unavailable'
            self.assertIn(expected,diagnostic)
            self.assertEqual(peer.recv(1),b'', 'Rejected reply must close the shared channel')
            for marker in ('AddressSanitizer','LeakSanitizer','runtime error:'):self.assertNotIn(marker,diagnostic)
        finally:
            if proc.poll() is None:proc.kill()
            proc.communicate(timeout=3);peer.close();log.close()
            if os.environ.get('SPATIAL_SECURITY_EVIDENCE'):
                target=Path(os.environ['SPATIAL_SECURITY_EVIDENCE']);target.mkdir(parents=True,exist_ok=True)
                (target/(self._testMethodName+'-worldd.log')).write_bytes(path.read_bytes())

    def test_wrong_reply_identity(self):self.attempt('wrong_id')
    def test_unsolicited_reply_descriptor(self):self.attempt('descriptor')
    def test_partial_reply_deadline(self):self.attempt('partial')
    def test_malformed_reply(self):self.attempt('malformed')
    def test_wrong_protocol_version(self):self.attempt('version')
    def test_malformed_reply_after_readiness_closes_shared_channel(self):self.attempt('exchange_malformed')
    def test_wrong_version_after_readiness_closes_shared_channel(self):self.attempt('exchange_version')

if __name__=='__main__':unittest.main(verbosity=2)
