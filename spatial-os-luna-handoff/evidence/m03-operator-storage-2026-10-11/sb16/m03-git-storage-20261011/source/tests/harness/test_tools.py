import hashlib,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from tools.guard import verify
from tools.gate import collect
from tools.delegate import prepare
ROOT=Path(__file__).resolve().parents[2]

class ToolTests(unittest.TestCase):
    def test_suite_has_unique_ids_and_metadata(self):
        tests=collect();self.assertGreaterEqual(len(tests),140);self.assertEqual(len(tests),len({t.id() for t in tests}))
    def test_baseline_intact(self):self.assertEqual(verify(ROOT,[t.id() for t in collect()]),[])
    def test_changed_file_detected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/"a").write_text("old")
            m={"files":{"a":hashlib.sha256(b"old").hexdigest()},"test_ids":["test_a"]}
            (root/"acceptance.lock.json").write_text(json.dumps(m));(root/"a").write_text("changed")
            self.assertTrue(verify(root,["test_a"]))
    def test_missing_collected_test_detected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/"acceptance.lock.json").write_text(json.dumps({"files":{},"test_ids":["required"]}))
            self.assertTrue(verify(root,[]))
    def test_no_sut_is_blocked_not_passed(self):
        env=os.environ.copy();env.pop("SPATIAL_SUT",None)
        r=subprocess.run([sys.executable,str(ROOT/"tools/gate.py"),"--through","P00"],env=env,capture_output=True,text=True,timeout=10)
        self.assertEqual(r.returncode,2);self.assertIn("no production SUT",r.stderr)
    def test_fixture_cannot_certify_product(self):
        with tempfile.TemporaryDirectory() as d:
            env=os.environ.copy();env["SPATIAL_EVIDENCE_DIR"]=d
            argv=json.dumps([sys.executable,str(ROOT/"tests/fixtures/transport_peer.py"),"--mode","fixture-hello"])
            r=subprocess.run([sys.executable,str(ROOT/"tools/gate.py"),"--through","P00","--sut",argv,"--failfast","--report",str(Path(d)/"report.json")],env=env,capture_output=True,text=True,timeout=15)
            self.assertEqual(r.returncode,1);self.assertEqual(json.loads((Path(d)/"report.json").read_text())["status"],"failed")
    def test_helper_requires_real_configuration(self):
        with self.assertRaises(ValueError):prepare({"endpoint":None,"model":None},{},[])
    def test_helper_does_not_guess_protocol(self):
        with self.assertRaises(ValueError):prepare({"endpoint":"http://localhost:9/v1/responses","model":"local"},{},[])
    def test_helper_context_is_explicit(self):
        url,body=prepare({"endpoint":"http://localhost:9/v1/chat/completions","model":"local"},{"id":"x"},[])
        self.assertEqual(body["model"],"local");self.assertFalse(body["stream"]);self.assertNotIn("tools",body)
    def test_helper_context_path_escape_rejected(self):
        with self.assertRaises(ValueError):prepare({"endpoint":"http://localhost:9/v1/chat/completions","model":"local"},{},["../../etc/passwd"])
    def test_helper_no_protocol_override(self):
        with self.assertRaises(ValueError):prepare({"endpoint":"http://localhost:9/v1/chat/completions","model":"local","request_options":{"messages":[]}},{},[])
