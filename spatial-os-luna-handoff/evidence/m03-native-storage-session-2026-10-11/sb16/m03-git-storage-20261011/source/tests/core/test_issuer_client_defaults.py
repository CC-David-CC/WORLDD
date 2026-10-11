"""Check the production client budget separately from the short fault-test fixture."""
import io
import os
from pathlib import Path
import re
import time
import unittest

from google.protobuf import text_format
import issuer_pb2 as pb
from harness.wire import frame, read_message
from test_issuer_client import ClientFixture, ClientProcess, FaultPeer


class ClientDefaultTests(ClientFixture):
    def run_budget(self, response, *, production_default, two_requests=False):
        peer = FaultPeer(self, response=response)
        path = self.config_file(peer.port)
        config = text_format.Parse(path.read_text(), pb.IssuerTlsClientConfig())
        self.assertEqual(config.io_timeout_ms, 700)
        if production_default:
            config.ClearField('io_timeout_ms')
            path.write_text(text_format.MessageToString(config))
        loaded = text_format.Parse(path.read_text(), pb.IssuerTlsClientConfig())
        self.assertEqual(loaded.HasField('io_timeout_ms'), not production_default)
        client = ClientProcess(path, self.root / 'default-budget-client.log')
        self.connections.append(client)
        request = pb.IssuerRequest(protocol_version=1, request_id=81, info=pb.IssuerInfoRequest())
        data = frame(request)
        if two_requests:
            request.request_id = 82
            data += frame(request)
        started = time.monotonic()
        output, _ = client.proc.communicate(data, timeout=8)
        elapsed = time.monotonic() - started
        diagnostic = Path(client.stderr.name).read_text()
        target = os.environ.get('SPATIAL_SECURITY_EVIDENCE')
        if target:
            directory = Path(target)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / (self._testMethodName + '-client.log')).write_bytes(Path(client.stderr.name).read_bytes())
        for marker in ('AddressSanitizer', 'LeakSanitizer', 'runtime error:'):
            self.assertNotIn(marker, diagnostic)
        self.assertEqual(peer.connections, 1)
        self.assertEqual(len(peer.requests), 3, 'Queued requests must not reconnect or replay after timeout')
        return client.proc.returncode, output, diagnostic, elapsed

    @staticmethod
    def delayed_reply(peer, request, reply):
        if not peer.stop.wait(2.2):
            peer.stream.sendall(frame(reply))

    def test_omitted_timeout_accepts_valid_reply_after_two_seconds(self):
        code, output, diagnostic, elapsed = self.run_budget(self.delayed_reply, production_default=True)
        self.assertEqual(code, 0, diagnostic)
        stream = io.BytesIO(output)
        reply = read_message(stream, pb.IssuerReply)
        self.assertEqual(reply.protocol_version, 1)
        self.assertEqual(reply.request_id, 81)
        self.assertEqual(reply.info, self.info)
        self.assertEqual(stream.read(), b'')
        self.assertEqual(diagnostic, '')
        self.assertGreaterEqual(elapsed, 2.2)

    def test_explicit_short_timeout_is_preserved_and_never_replays(self):
        code, output, diagnostic, _ = self.run_budget(self.delayed_reply, production_default=False, two_requests=True)
        self.assertEqual(code, 2)
        self.assertEqual(output, b'')
        self.assertIn('Issuer client deadline exceeded phase=reply_header wait=read progress=0/4', diagnostic)
        elapsed = int(re.search(r'elapsed_ms=(\d+)', diagnostic)[1])
        self.assertGreaterEqual(elapsed, 700)
        self.assertLess(elapsed, 2000)

    def test_omitted_timeout_is_bounded_and_never_replays(self):
        def withheld(peer, request, reply):
            peer.stop.wait(7)
        code, output, diagnostic, _ = self.run_budget(withheld, production_default=True, two_requests=True)
        self.assertEqual(code, 2)
        self.assertEqual(output, b'')
        self.assertIn('Issuer client deadline exceeded phase=reply_header wait=read progress=0/4', diagnostic)
        elapsed = int(re.search(r'elapsed_ms=(\d+)', diagnostic)[1])
        self.assertGreaterEqual(elapsed, 5000)
        self.assertLess(elapsed, 6000)


if __name__ == '__main__':
    unittest.main(verbosity=2)
