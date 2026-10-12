from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys, tempfile, unittest
from harness.rpc import Client, RpcError, TransportError

class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
    def peer(self, mode='echo'):
        path = Path(__file__).resolve().parents[1]/'fixtures/transport_peer.py'
        c = Client([sys.executable, str(path), '--mode', mode],
                   self.root/'state', self.root/'evidence', timeout=5.0)
        self.addCleanup(c.stop)
        return c
    def test_roundtrip(self):
        self.assertEqual(self.peer().call('system.hello')['build_id'], 'fixture-学校')
    def test_concurrent_response_correlation(self):
        c = self.peer('reverse-eight')
        def call(i):
            return c.call('geometry.transform_point',
                          {'pose': {'p': [0,0,0], 'q': [0,0,0,1]}, 'point': [i,0,0]})
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(call, range(32)))
        self.assertEqual(results, [{'point': [i,0,0]} for i in range(32)])
    def test_typed_error(self):
        with self.assertRaises(RpcError) as ctx:
            self.peer('error').call('system.hello')
        self.assertEqual(ctx.exception.code, 'INVALID_ARGUMENT')
        self.assertIsNone(ctx.exception.details)
    def test_timeout_is_failure(self):
        with self.assertRaisesRegex(TransportError, 'Deadline exceeded'):
            self.peer('timeout').call('system.hello', timeout=.05)
    def test_eof_is_failure(self):
        with self.assertRaisesRegex(TransportError, 'closed stdout'):
            self.peer('eof').call('system.hello')
    def test_malformed_protobuf_is_failure(self):
        with self.assertRaises(TransportError):
            self.peer('garbage').call('system.hello')
    def test_wrong_id_is_failure(self):
        with self.assertRaisesRegex(TransportError, 'Unsolicited, duplicate, or late'):
            self.peer('wrong-id').call('system.hello')
    def test_missing_outcome_is_failure(self):
        with self.assertRaisesRegex(TransportError, 'exactly one'):
            self.peer('no-outcome').call('system.hello')
    def test_nonfinite_reply_is_failure(self):
        with self.assertRaisesRegex(TransportError, 'Non-finite'):
            self.peer('nonfinite').call('system.hello')
    def test_nonfinite_request_never_sent(self):
        c = self.peer()
        with self.assertRaises(ValueError):
            c.call('motion.integrate', {'seconds': float('nan')})
        self.assertEqual(c.call('system.hello')['build_id'], 'fixture-学校')
    def test_killed_process_is_failure(self):
        c = self.peer()
        c.proc.kill()
        with self.assertRaises(TransportError):
            c.call('system.hello')
    def test_invalid_command_rejected(self):
        with self.assertRaises(ValueError):
            Client([], self.root/'x', self.root/'y')
