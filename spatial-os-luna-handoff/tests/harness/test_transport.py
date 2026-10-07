from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys,tempfile,unittest
from harness.rpc import Client,RpcError,TransportError

class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def peer(self,mode="echo"):
        path=Path(__file__).resolve().parents[1]/"fixtures/transport_peer.py"
        c=Client([sys.executable,str(path),"--mode",mode],self.root/"state",self.root/"evidence",timeout=5.0)
        self.addCleanup(c.stop);return c
    def test_roundtrip(self):self.assertEqual(self.peer().call("echo",{"a":[1,2,"学校"]}),{"a":[1,2,"学校"]})
    def test_concurrent_response_correlation(self):
        c=self.peer("reverse-eight")
        with ThreadPoolExecutor(max_workers=8) as p:
            results=list(p.map(lambda i:c.call("echo",{"i":i}),range(32)))
        self.assertEqual(results,[{"i":i} for i in range(32)])
    def test_typed_error(self):
        with self.assertRaises(RpcError) as ctx:self.peer("error").call("x")
        self.assertEqual(ctx.exception.code,"TEST_ERROR");self.assertEqual(ctx.exception.details,{"x":1})
    def test_timeout_is_failure(self):
        with self.assertRaisesRegex(TransportError,"Deadline exceeded"):self.peer("timeout").call("x",timeout=.05)
    def test_eof_is_failure(self):
        with self.assertRaisesRegex(TransportError,"closed stdout"):self.peer("eof").call("x")
    def test_malformed_json_is_failure(self):
        with self.assertRaisesRegex(TransportError,"Expecting value"):self.peer("garbage").call("x")
    def test_wrong_id_is_failure(self):
        with self.assertRaisesRegex(TransportError,"Unsolicited, duplicate, or late"):self.peer("wrong-id").call("x")
    def test_result_and_error_is_failure(self):
        with self.assertRaisesRegex(TransportError,"exactly one"):self.peer("both").call("x")
    def test_nonfinite_reply_is_failure(self):
        with self.assertRaisesRegex(TransportError,"NaN"):self.peer("nonfinite").call("x")
    def test_nonfinite_request_never_sent(self):
        c=self.peer()
        with self.assertRaises(ValueError):c.call("x",{"n":float("nan")})
        self.assertEqual(c.call("x",{"still":"alive"}),{"still":"alive"})
    def test_killed_process_is_failure(self):
        c=self.peer();c.proc.kill()
        with self.assertRaises(TransportError):c.call("x")
    def test_invalid_command_rejected(self):
        with self.assertRaises(ValueError):Client([],self.root/"x",self.root/"y")
