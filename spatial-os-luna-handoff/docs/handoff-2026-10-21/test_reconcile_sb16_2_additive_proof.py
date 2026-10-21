#!/usr/bin/env python3
"""Whole-main pure tests with a temporary synthetic evidence tree."""
import contextlib, hashlib, importlib.util, json, pathlib, shutil, tempfile, unittest, unittest.mock
import xml.etree.ElementTree as ET

SOURCE = pathlib.Path(__file__).with_name("reconcile_sb16_2_additive_proof.py")
SPEC = importlib.util.spec_from_file_location("reconcile_proof", SOURCE)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)
INPUTS = pathlib.Path("/home/dflanag3/spatial-artifacts/m08-issuer-fixture-580834ceeda1-20261021/packaging-cache-repair-01/sb16-2-reconciliation-actual-inputs-01")
ORIGINAL = pathlib.Path("/home/dflanag3/spatial-artifacts/m08-issuer-fixture-580834ceeda1-20261021/packaging-cache-repair-01/sb16-2-frozen-review-inputs-01/run.json")
LOCK = MOD.ROOT / "independent-extract-01/source/acceptance.lock.json"

def digest(data):
    return hashlib.sha256(data).hexdigest()

def put_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    return digest(path.read_bytes())

class Bundle:
    def __init__(self, root, mutation=None):
        self.root, self.mutation = root, mutation
        self.inputs = root / "inputs"
        self.inputs.mkdir()
        for name in ("pins.json", "native-replay.json", "cabinet-replay.json", "san-inventory.json", "debug-inventory.json", "san-core-protected.xml", "provenance.json", "post-native-integrity.json"):
            shutil.copyfile(INPUTS / name, self.inputs / name)
        self.original = root / "original/run.json"
        self.original.parent.mkdir()
        shutil.copyfile(ORIGINAL, self.original)
        self.lock = root / "deployed-01/source/acceptance.lock.json"
        self.lock.parent.mkdir(parents=True)
        shutil.copyfile(LOCK, self.lock)
        gate_source = root / "deployed-01/source/tools/m06_deployed_gate.py"
        gate_source.parent.mkdir(parents=True)
        shutil.copyfile(MOD.ROOT / "independent-extract-01/source/tools/m06_deployed_gate.py", gate_source)
        evidence = root / "packaging-cache-repair-01/sb16-2"
        self.tail_path = evidence / "san-tail-supplement-06/supplemental-result.json"
        self.audit_path = evidence / "frozen-runtime-post-san-tail06-01.json"
        self.audit_runner = evidence / "frozen-runtime-post-san-tail06-run-01"
        self.binding_path = evidence / "post-san-tail06-binding-01.json"
        self.output = root / "reconciliation-proof.json"
        self.raw_root = self.tail_path.parent / "ASan-UBSan"
        self.manifest = {}
        self.stage_rows = []
        self._tail_outputs()
        self._audit()
        self.tail = self._tail()
        self.tail_sha = put_json(self.tail_path, self.tail)
        self.binding = {
            "schema": 1, "classification": "PRIMARY_POST_TAIL_ARTIFACT_BINDING_UNQUALIFIED",
            "candidate": MOD.CANDIDATE, "host": MOD.HOST,
            "tail": {"path": str(self.tail_path), "sha256": self.tail_sha},
            "audit": {"path": str(self.audit_path), "sha256": digest(self.audit_path.read_bytes())},
            "audit_runner": {"path": str(self.audit_runner), "argv": self.audit_argv,
                             "command_exit": 0, "wrapper_exit": 0, "terminal_status": "COMPLETE"},
            "qualification": "UNQUALIFIED", "gate_pass": False, "phase_complete": False
        }
        if mutation == "audit_hash":
            self.binding["audit"]["sha256"] = "0" * 64
        put_json(self.binding_path, self.binding)
        self._mutate()

    @property
    def audit_argv(self):
        return ["env", "PYTHONDONTWRITEBYTECODE=1", "python3", "-B",
                str(self.root / "packaging-cache-repair-01/sb16-2/control/audit-frozen-runtime-01.py"),
                "--output", str(self.audit_path)]

    def _runner(self, directory, argv, manifest=None):
        directory.mkdir(parents=True, exist_ok=True)
        payloads = {"command.exit": b"0\n", "wrapper.exit": b"0\n", "terminal-status.txt": b"COMPLETE\n",
                    "argv.nul": b"\0".join(x.encode() for x in argv) + b"\0",
                    "stdout.log": b"fixture stdout\n", "stderr.log": b""}
        for leaf, content in payloads.items():
            path = directory / leaf
            path.write_bytes(content)
            if manifest is not None:
                manifest[str(path.relative_to(self.raw_root))] = digest(content)

    def _tail_outputs(self):
        with unittest.mock.patch.object(MOD, "ROOT", self.root):
            _, recipes = MOD.stage_recipes(self.raw_root)
        for name in MOD.STAGES:
            path = self.raw_root / "canonical-runs" / name
            argv = recipes[name]["argv"]
            self._runner(path, argv, self.manifest)
            self.stage_rows.append({
                "name": name, **recipes[name], "runner": str(path), "passed": True,
                "validation": (
                    {"all_registered_present": True, "failed_or_skipped": False, "junit_cases": 4}
                    if name == "core-supervised-copy-recovery" else
                    {"rows_valid": True, "case_count": 151, "acceptance": {"status": "passed", "through": "P10", "selected": 151, "executed": 151, "total_baseline": 151, "not_selected": 0, "diagnostic_only": False}}
                    if name == "P10" else
                    {"status": "passed", "cleanup_valid": True, "mounted": False, "result_names": sorted(MOD.STORAGE)}),
                "runner_receipt": {"launcher_exit": 0, "command.exit": "0", "wrapper.exit": "0",
                                   "terminal-status.txt": "COMPLETE", "argv.nul": argv},
                "logs": {"stdout_sha256": self.manifest[f"canonical-runs/{name}/stdout.log"],
                         "stderr_sha256": self.manifest[f"canonical-runs/{name}/stderr.log"],
                         "sanitizer_findings": []},
                "output_log_scan": {"sanitizer_findings": []}})
        nested = sorted(MOD.NESTED)
        if self.mutation == "missing_nested":
            nested.pop()
        suite = ET.Element("testsuite")
        for name in nested:
            ET.SubElement(suite, "testcase", {"name": name})
        p = self.raw_root / "core-self-managed.xml"
        p.parent.mkdir(parents=True, exist_ok=True)
        ET.ElementTree(suite).write(p)
        self.manifest["core-self-managed.xml"] = digest(p.read_bytes())

        ids = list(json.loads(LOCK.read_text())["test_ids"])
        if self.mutation == "missing_p10":
            ids.pop()
        p = self.raw_root / "P10.json"
        self.manifest["P10.json"] = put_json(p, {
            "status": "passed", "through": "P10", "selected": len(ids), "executed": len(ids),
            "total_baseline": 151, "not_selected": 0, "diagnostic_only": False,
            "tests": [{"test": x, "status": "passed", "phase": "P10"} for x in ids]})
        names = set(MOD.STORAGE)
        if self.mutation == "missing_storage":
            names.remove(sorted(names)[0])
        p = self.raw_root / "bounded-storage-git/review.json"
        self.manifest["bounded-storage-git/review.json"] = put_json(p, {
            "status": "passed", "mounted": False, "scope": str(self.raw_root / "retired-scope"),
            "service_stop": {"exit_code": 0}, "unmount": {"exit_code": 0}, "detach": {"exit_code": 0},
            "service_after": "ActiveState=inactive",
            "owner_unit_cleanup": {"one": "ActiveState=inactive", "two": "ActiveState=inactive"},
            "results": {x: {"passed": True} for x in names}})

    def _tail(self):
        return {
            "classification": "UNQUALIFIED_SAN_SUPPLEMENTAL_STAGE_EVIDENCE",
            "candidate": MOD.CANDIDATE, "host": MOD.HOST, "profile": "ASan-UBSan",
            "profile_pass": False, "phase_complete": False, "qualification_status": "UNQUALIFIED",
            "original_failure_preserved": True,
            "original_failed_run": {"path": str(self.original), "sha256": MOD.ORIGINAL_SHA},
            "native_replay_prerequisite": {"path": MOD.TARGET_PATHS["native-replay.json"], "sha256": MOD.NATIVE_SHA},
            "supplemental_stages": self.stage_rows,
            "supplemental_artifact_manifest": {"sha256": self.manifest},
            "source_closures_rechecked_after": True,
            "pins_before": {"provenance_sha256": MOD.PROVENANCE_SHA, "source_gate_sha256": MOD.TAIL_M06_SHA,
                            "m08_gate_sha256": MOD.TAIL_M08_SHA, "runner_sha256": MOD.RUNNER_SHA,
                            "deployment_receipt_sha256": MOD.DEPLOYMENT_SHA}}

    def _audit(self):
        profiles = {}
        for name, count, metadata in (
                ("Debug", 314, "2e203e50db6e8a8e54c91596b2f4307b84f42d0cde744ece20c31cc41f8c9634"),
                ("ASan-UBSan", 304, "1b3d0472d5b5c17002e6c4bd066e9a591eeafd68c910beefdedeb7df324b0f54")):
            profiles[name] = {"status": "artifact_hashes_passed", "files_checked": count,
                              "qualification": "NOT_A_TEST_PASS",
                              "mutable_relocated_ctest_metadata_snapshot": {"CTestTestfile.cmake": metadata}}
        put_json(self.audit_path, {"status": "FOCUSED_PREPOST_ARTIFACT_AUDIT_PASSED",
                                   "candidate": MOD.CANDIDATE, "host": MOD.HOST, "source_count": 1222,
                                   "original_failed_run_sha256": MOD.ORIGINAL_SHA, "profiles": profiles})
        self._runner(self.audit_runner, self.audit_argv)

    def _mutate(self):
        if self.mutation in ("wrong_deployment", "wrong_recipe", "sanitizer", "nested_sanitizer", "nested_hash"):
            if self.mutation == "wrong_deployment":
                self.tail["pins_before"]["deployment_receipt_sha256"] = "a" * 64
            elif self.mutation == "wrong_recipe":
                row = self.stage_rows[0]
                row["argv"] = ["ctest", "--version"]
                row["runner_receipt"]["argv.nul"] = row["argv"]
                p = pathlib.Path(row["runner"]) / "argv.nul"
                p.write_bytes(b"ctest\0--version\0")
                self.manifest[str(p.relative_to(self.raw_root))] = digest(p.read_bytes())
            else:
                p = (pathlib.Path(self.stage_rows[0]["runner"]) / "stderr.log") if self.mutation == "sanitizer" else self.raw_root / "native/child/stderr.log"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("ERROR: AddressSanitizer: heap-use-after-free\n" if self.mutation != "nested_hash" else "changed")
                self.manifest[str(p.relative_to(self.raw_root))] = digest(p.read_bytes()) if self.mutation != "nested_hash" else "0" * 64
                if self.mutation == "sanitizer":
                    self.stage_rows[0]["logs"]["stderr_sha256"] = digest(p.read_bytes())
            self.tail_sha = put_json(self.tail_path, self.tail)
            self.binding["tail"]["sha256"] = self.tail_sha
            put_json(self.binding_path, self.binding)
            return
        if self.mutation in ("wrong_raw_exit", "argv_mismatch"):
            leaf, data = ("command.exit", b"1\n") if self.mutation == "wrong_raw_exit" else ("argv.nul", b"changed\0")
            p = self.raw_root / "canonical-runs" / MOD.STAGES[0] / leaf
            p.write_bytes(data)
            self.manifest[str(p.relative_to(self.raw_root))] = digest(data)
            self.tail = self._tail()
            self.tail_sha = put_json(self.tail_path, self.tail)
            self.binding["tail"]["sha256"] = self.tail_sha
            put_json(self.binding_path, self.binding)
        elif self.mutation in ("missing_nested", "missing_p10", "missing_storage"):
            self.tail = self._tail()
            self.tail_sha = put_json(self.tail_path, self.tail)
            self.binding["tail"]["sha256"] = self.tail_sha
            put_json(self.binding_path, self.binding)
        elif self.mutation == "changed_original":
            with self.original.open("ab") as f:
                f.write(b" ")

class WholeMainTests(unittest.TestCase):
    def run_bundle(self, mutation=None, expect_failure=False):
        with tempfile.TemporaryDirectory(prefix="m08-whole-proof-") as td:
            root = pathlib.Path(td).resolve()
            b = Bundle(root, mutation)
            patches = {
                "ROOT": root, "EVIDENCE_ROOT": root / "packaging-cache-repair-01",
                "POST_TAIL_BINDING": b.binding_path, "POST_TAIL_AUDIT": b.audit_path,
                "POST_TAIL_AUDIT_RUNNER": b.audit_runner, "POST_TAIL": b.tail_path,
                "ORIGINAL_TARGETS": {str(b.original)},
                "TAIL_SHA": b.tail_sha, "BINDING_SHA": digest(b.binding_path.read_bytes()),
                "POST_TAIL_AUDIT_SHA": digest(b.audit_path.read_bytes())}
            args = ["--inputs-dir", str(b.inputs), "--original-run", str(b.original),
                    "--tail-result", str(b.tail_path), "--tail-sha256", b.tail_sha,
                    "--post-tail-binding", str(b.binding_path),
                    "--post-tail-binding-sha256", digest(b.binding_path.read_bytes()),
                    "--output", str(b.output)]
            with contextlib.ExitStack() as stack:
                for key, value in patches.items():
                    stack.enter_context(unittest.mock.patch.object(MOD, key, value))
                if expect_failure:
                    with self.assertRaises(ValueError):
                        MOD.main(args)
                    self.assertFalse(b.output.exists())
                else:
                    self.assertEqual(MOD.main(args), 0)
                    report = json.loads(b.output.read_text())
                    self.assertEqual(report["qualification"], "UNQUALIFIED")
                    self.assertFalse(report["gate_pass"])
                    self.assertFalse(report["phase_complete"])
                    self.assertEqual(report["original_failed_run"]["original_san_core_exit"], 8)
                    self.assertEqual(report["derived_coverage"]["unique_covered_names"], 236)

    def test_whole_main_success_writes_only_unqualified_proof(self):
        self.run_bundle()

    def test_whole_main_rejects_mutated_raw_evidence_without_output(self):
        for mutation in ("missing_nested", "missing_p10", "missing_storage", "wrong_raw_exit", "argv_mismatch", "wrong_deployment", "wrong_recipe", "sanitizer", "nested_sanitizer", "nested_hash"):
            with self.subTest(mutation=mutation):
                self.run_bundle(mutation, expect_failure=True)

    def test_whole_main_rejects_wrong_post_tail_audit_hash(self):
        self.run_bundle("audit_hash", expect_failure=True)

    def test_whole_main_rejects_changed_original_report(self):
        self.run_bundle("changed_original", expect_failure=True)

if __name__ == "__main__":
    unittest.main(verbosity=2)
