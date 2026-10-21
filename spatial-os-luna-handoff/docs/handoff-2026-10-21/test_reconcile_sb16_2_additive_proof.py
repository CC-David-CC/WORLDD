#!/usr/bin/env python3
"""Pure tests. Synthetic rows exercise private parsing only; production CLI rejects them."""
import hashlib, importlib.util, json, pathlib, subprocess, sys, tempfile, unittest, xml.etree.ElementTree as ET

SOURCE = pathlib.Path(__file__).with_name("reconcile_sb16_2_additive_proof.py")
SPEC = importlib.util.spec_from_file_location("reconcile_proof", SOURCE)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)
INPUTS = pathlib.Path("/home/dflanag3/spatial-artifacts/m08-issuer-fixture-580834ceeda1-20261021/packaging-cache-repair-01/sb16-2-reconciliation-actual-inputs-01")
ORIGINAL = pathlib.Path("/home/dflanag3/spatial-artifacts/m08-issuer-fixture-580834ceeda1-20261021/packaging-cache-repair-01/sb16-2-frozen-review-inputs-01/run.json")
LOCK = MOD.ROOT / "independent-extract-01/source/acceptance.lock.json"

def make_raw(root, mode="good"):
    manifest = {}
    rows = []
    for name in MOD.STAGES:
        argv = ["source-shaped-synthetic", name]
        stage_dir = root / "canonical-runs" / name
        stage_dir.mkdir(parents=True)
        payloads = {"command.exit": b"0\n", "wrapper.exit": b"0\n", "terminal-status.txt": b"COMPLETE\n", "argv.nul": b"\0".join(x.encode() for x in argv) + b"\0", "stdout.log": b"fixture stdout\n", "stderr.log": b""}
        if mode == "alter_exit" and name == MOD.STAGES[0]:
            payloads["command.exit"] = b"1\n"
        if mode == "alter_argv" and name == MOD.STAGES[0]:
            payloads["argv.nul"] = b"altered\0"
        row = {"name": name, "argv": argv, "runner": "/tmp/canonical-runs/" + name, "logs": {}}
        for leaf, content in payloads.items():
            p = stage_dir / leaf
            p.write_bytes(content)
            key = "canonical-runs/" + name + "/" + leaf
            manifest[key] = hashlib.sha256(content).hexdigest()
            if leaf in ("stdout.log", "stderr.log"):
                row["logs"][leaf.split(".")[0] + "_sha256"] = manifest[key]
        rows.append(row)

    nested = sorted(MOD.NESTED)
    if mode == "duplicate_nested":
        nested[-1] = nested[0]
    suite = ET.Element("testsuite")
    for name in nested:
        ET.SubElement(suite, "testcase", {"name": name})
    p = root / "core-self-managed.xml"
    ET.ElementTree(suite).write(p)
    manifest["core-self-managed.xml"] = hashlib.sha256(p.read_bytes()).hexdigest()

    lock = json.loads(LOCK.read_text())
    ids = list(lock["test_ids"])
    if mode == "missing_p10_id":
        ids.pop()
    p10 = {"status": "passed", "through": "P10", "selected": len(ids), "executed": len(ids), "total_baseline": 151, "not_selected": 0, "diagnostic_only": False, "tests": [{"test": x, "status": "passed", "phase": "P10"} for x in ids]}
    p = root / "P10.json"
    p.write_text(json.dumps(p10))
    manifest["P10.json"] = hashlib.sha256(p.read_bytes()).hexdigest()

    result_names = set(MOD.STORAGE)
    if mode == "missing_storage":
        result_names.pop()
    review = {"status": "passed", "mounted": False, "scope": str(root / "retired-scope"), "service_stop": {"exit_code": 0}, "unmount": {"exit_code": 0}, "detach": {"exit_code": 0}, "service_after": "ActiveState=inactive", "owner_unit_cleanup": {"one": "ActiveState=inactive", "two": "ActiveState=inactive"}, "results": {x: {"passed": True} for x in result_names}}
    p = root / "bounded-storage-git" / "review.json"
    p.parent.mkdir()
    p.write_text(json.dumps(review))
    manifest["bounded-storage-git/review.json"] = hashlib.sha256(p.read_bytes()).hexdigest()
    return {"supplemental_artifact_manifest": {"sha256": manifest}, "supplemental_stages": rows}

class RawEvidenceTests(unittest.TestCase):
    def exercise(self, mode):
        with tempfile.TemporaryDirectory(prefix="m08-raw-proof-", dir=MOD.ROOT) as td:
            root = pathlib.Path(td)
            tail = make_raw(root, mode)
            if mode == "missing_file":
                (root / "P10.json").unlink()
            return MOD.validate_raw_stage_files(root, tail, LOCK)

    def test_exact_raw_bundle_is_parsed_by_private_validator(self):
        with tempfile.TemporaryDirectory(prefix="m08-raw-proof-", dir=MOD.ROOT) as td:
            summary = self.exercise("good")
            self.assertEqual(summary["nested_cases"], sorted(MOD.NESTED))
            self.assertEqual(summary["p10_ids"], 151)
            self.assertEqual(set(summary["storage_results"]), MOD.STORAGE)

    def test_actual_canonical_nonzero_rejects(self):
        with self.assertRaises(ValueError):
            self.exercise("alter_exit")

    def test_actual_argv_mismatch_rejects(self):
        with self.assertRaises(ValueError):
            self.exercise("alter_argv")

    def test_duplicate_nested_registration_rejects(self):
        with self.assertRaises(ValueError):
            self.exercise("duplicate_nested")

    def test_missing_locked_P10_id_rejects(self):
        with self.assertRaises(ValueError):
            self.exercise("missing_p10_id")

    def test_missing_storage_result_rejects(self):
        with self.assertRaises(ValueError):
            self.exercise("missing_storage")

class ProductionCliTests(unittest.TestCase):
    def invoke(self, changed_input=False):
        with tempfile.TemporaryDirectory(prefix="m08-reconcile-cli-", dir=MOD.ROOT) as td:
            root = pathlib.Path(td)
            input_dir = root / "inputs"
            input_dir.mkdir()
            names = ("pins.json", "native-replay.json", "cabinet-replay.json", "san-inventory.json", "debug-inventory.json", "san-core-protected.xml", "provenance.json", "post-native-integrity.json")
            for name in names:
                (input_dir / name).write_bytes((INPUTS / name).read_bytes())
            if changed_input:
                with (input_dir / "native-replay.json").open("ab") as f:
                    f.write(b" ")
            pins = json.loads((input_dir / "pins.json").read_text())
            tail = root / "supplemental-result.json"
            value = {"test_fixture_status": "SYNTHETIC", "classification": "UNQUALIFIED_SAN_SUPPLEMENTAL_STAGE_EVIDENCE", "host": MOD.HOST, "candidate": MOD.CANDIDATE, "profile": "ASan-UBSan"}
            tail.write_text(json.dumps(value))
            raw = root / "raw"
            raw.mkdir()
            output = root / "reconciliation-proof.json"
            argv = [sys.executable, "-B", str(SOURCE), "--inputs-dir", str(input_dir), "--original-run", str(ORIGINAL), "--tail-result", str(tail), "--tail-sha256", hashlib.sha256(tail.read_bytes()).hexdigest(), "--post-tail-binding", str(root / "binding.json"), "--post-tail-binding-sha256", "0" * 64, "--output", str(output)]
            result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
            return result.returncode, output.exists(), result.stderr

    def test_outer_cli_rejects_synthetic_tail_without_output(self):
        rc, exists, err = self.invoke()
        self.assertEqual(rc, 2)
        self.assertFalse(exists)
        self.assertIn("REFUSED", err)

    def test_changed_mispinned_native_report_rejects_without_output(self):
        rc, exists, _ = self.invoke(changed_input=True)
        self.assertEqual(rc, 2)
        self.assertFalse(exists)

if __name__ == "__main__":
    unittest.main(verbosity=2)
