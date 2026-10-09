"""Owned transport faults must expose their distinct diagnostic causes."""
import os
from pathlib import Path
import re
import signal
import sys
import time
import unittest
SOURCE=Path(sys.argv.pop(1)).resolve()
sys.path.insert(0,str(SOURCE/'tests/core'))
import test_world_bootstrap as fixture
import world_session_pb2 as session
import issuer_pb2 as issuer
import core_pb2 as core


class TransportDiagnosticControls(fixture.BootstrapWorldFixture):
    def logs(self,pattern):
        return '\n'.join(p.read_text(errors='replace') for p in self.root.glob(pattern))

    def await_log(self,pattern,needle):
        deadline=time.monotonic()+3
        while needle not in self.logs(pattern):
            if time.monotonic()>deadline:self.fail('Missing diagnostic marker: '+needle+'\n'+self.logs(pattern))
            time.sleep(.01)
        return self.logs(pattern)

    def unavailable(self,world):
        reply=world.peers[1].call(begin=session.WorldAuthenticationBegin(credential=self.record.credential))
        self.assertTrue(reply.HasField('error'),reply)
        self.assertEqual(reply.error.code,core.UNAVAILABLE)
        self.assertEqual(reply.error.message,'World identity service is disabled')

    def test_paused_owned_issuer_exposes_client_deadline(self):
        world=self.scoped_world(session.REGISTERED_ACTORS)
        daemon=self.daemons[0][0]
        self.assertIsNone(daemon.poll())
        os.kill(daemon.pid,signal.SIGSTOP)
        try:
            text=self.await_log('world-*.log','reason=deadline')
            self.await_log('world-*.log','participant diagnostic: Issuer client deadline exceeded')
        finally:
            os.kill(daemon.pid,signal.SIGCONT)
        tag=issuer.IssuerRequest.DESCRIPTOR.fields_by_name['revocations'].number
        self.assertIn('operation='+str(tag)+' ',text)
        self.assertIn('stage=read-header',text)
        self.await_log('world-*.log','identity service disabled')
        self.unavailable(world)

    def test_stopped_owned_issuer_exposes_tls_close(self):
        world=self.scoped_world(session.REGISTERED_ACTORS)
        self.stop_daemons()
        text=self.await_log('world-*.log','reason=tls-fatal')
        self.await_log('world-*.log','participant diagnostic: Issuer TLS connection failed')
        self.assertRegex(text,r'ssl_error=[1-9][0-9]*')
        self.await_log('world-*.log','identity service disabled')
        self.unavailable(world)

    def test_incomplete_frame_exposes_server_deadline(self):
        self.owner_peer.stream.sendall(b'\x00')
        text=self.await_log('tls-*.log','issuer-server diagnostic: close phase=1')
        rows=[row for row in text.splitlines() if 'close phase=1' in row and 'offset=1 ' in row]
        self.assertTrue(rows,text)
        late=re.search(r' late_ms=(-?\d+)',rows[-1])
        self.assertIsNotNone(late)
        self.assertGreaterEqual(int(late.group(1)),0)
        self.closed(self.owner_peer)


if __name__=='__main__':unittest.main(verbosity=2)
