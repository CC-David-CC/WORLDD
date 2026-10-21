#!/usr/bin/env python3
"""Pure safety tests for the additive M08 local receipt publisher."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).with_name("publish_sb16_2_additive_receipt.py")
spec = importlib.util.spec_from_file_location("receipt_publisher", SOURCE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def valid_proof():
    names = ["host-actor-controller-native", "host-session-production-native",
             "session-broker-retirement", "session-coordinator-retirement",
             "session-stopped-broker-dependency"]
    return {
        "candidate": mod.CANDIDATE, "host": mod.HOST,
        "validation_status": "COMBINED_EVIDENCE_PASSED", "gate_pass": False,
        "phase_complete": False, "qualification": "UNQUALIFIED",
        "proof_kind": "additive_local_evidence_reconciliation",
        "original_failed_run": {"path": str(mod.ORIGINAL), "sha256": mod.ORIGINAL_SHA,
            "status": "failed", "original_san_core_exit": 8, "protected_cases": 232,
            "passed": 227, "failed_names": names, "skipped": 0},
        "profile_coverage": {p: {"core": 236, "harness": 89, "locked_P10": 151,
            "bounded_storage_git": 6} for p in mod.PROFILES},
        "derived_coverage": {"expected_local_names": 236, "original_san_passed_names": 227,
            "unique_covered_names": 236, "named_native_reruns": list(range(5)),
            "self_managed_nested_names": list(range(4))},
    }


class PublisherTests(unittest.TestCase):
    def test_candidate_runner_requires_exact_argv_zero_exits_and_summary(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            argv = mod.expected_combiner_argv(mod.PROOF)
            (root / "argv.nul").write_bytes(b"\0".join(x.encode() for x in argv) + b"\0")
            for leaf, raw in {
                "command.exit": b"0\n", "wrapper.exit": b"0\n",
                "terminal-status.txt": b"COMPLETE\n", "stderr.log": b"",
                "stdout.log": (json.dumps({"covered_names": 236, "gate_pass": False,
                    "proof_path": str(mod.PROOF), "qualification": "UNQUALIFIED"}) + "\n").encode(),
            }.items():
                (root / leaf).write_bytes(raw)
            with mock.patch.object(mod, "PROOF_RUNNER", root):
                self.assertEqual(mod.validate_combined_runner(mod.PROOF, root), argv)

    def test_runner_rejects_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            argv = mod.expected_combiner_argv(mod.PROOF)
            (root / "argv.nul").write_bytes(b"\0".join(x.encode() for x in argv) + b"\0")
            for leaf, raw in {
                "command.exit": b"2\n", "wrapper.exit": b"0\n",
                "terminal-status.txt": b"COMPLETE\n", "stderr.log": b"",
                "stdout.log": (json.dumps({"covered_names": 236, "gate_pass": False,
                    "proof_path": str(mod.PROOF), "qualification": "UNQUALIFIED"}) + "\n").encode(),
            }.items():
                (root / leaf).write_bytes(raw)
            with mock.patch.object(mod, "PROOF_RUNNER", root):
                with self.assertRaisesRegex(RuntimeError, "command.exit"):
                    mod.validate_combined_runner(mod.PROOF, root)

    def test_runner_rejects_wrong_argv_and_false_gate_pass(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "argv.nul").write_bytes(b"wrong\0")
            for leaf, raw in {
                "command.exit": b"0\n", "wrapper.exit": b"0\n",
                "terminal-status.txt": b"COMPLETE\n", "stderr.log": b"",
                "stdout.log": (json.dumps({"covered_names": 236, "gate_pass": True,
                    "proof_path": str(mod.PROOF), "qualification": "UNQUALIFIED"}) + "\n").encode(),
            }.items():
                (root / leaf).write_bytes(raw)
            with mock.patch.object(mod, "PROOF_RUNNER", root):
                with self.assertRaisesRegex(RuntimeError, "argv"):
                    mod.validate_combined_runner(mod.PROOF, root)
                (root / "argv.nul").write_bytes(
                    b"\0".join(x.encode() for x in mod.expected_combiner_argv(mod.PROOF)) + b"\0")
                with self.assertRaisesRegex(RuntimeError, "summary"):
                    mod.validate_combined_runner(mod.PROOF, root)

    def test_storage_tree_signature_detects_content_change(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            left = root / "left"
            right = root / "right"
            left.mkdir()
            right.mkdir()
            (left / "review.json").write_text("{}\n")
            (right / "review.json").write_text("{}\n")
            self.assertEqual(mod.tree_signature(left, {"volume", "volume.img"}), mod.tree_signature(right, {"volume", "volume.img"}))
            (right / "review.json").write_text("changed\n")
            self.assertNotEqual(mod.tree_signature(left, {"volume", "volume.img"}), mod.tree_signature(right, {"volume", "volume.img"}))

    def test_storage_copy_refuses_incomplete_tree_without_required_image(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "tail"
            copied = root / "original"
            for base in (source, copied):
                storage = base / "bounded-storage-git"
                storage.mkdir(parents=True)
                (storage / "review.json").write_text("{}\n")
            tail = {"supplemental_artifact_manifest": {"sha256": {
                "bounded-storage-git/review.json": hashlib.sha256(b"{}\n").hexdigest(),
            }}}
            with self.assertRaisesRegex(RuntimeError, "image"):
                mod.validate_storage_copy(tail, copied, source, None)

    def test_storage_copy_refuses_missing_review_pin(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "tail"
            copied = root / "copy"
            (source / "bounded-storage-git").mkdir(parents=True)
            (copied / "bounded-storage-git").mkdir(parents=True)
            with self.assertRaisesRegex(RuntimeError, "review.json"):
                mod.validate_storage_copy({"supplemental_artifact_manifest": {"sha256": {}}}, copied, source, None)

    def test_proof_preserves_failed_original(self):
        proof = valid_proof()
        original = {"status": "failed", "phase_complete": False}
        mod.validate_profile_proof(proof, original)
        proof["original_failed_run"]["original_san_core_exit"] = 0
        with self.assertRaisesRegex(RuntimeError, "original failure"):
            mod.validate_profile_proof(proof, original)

    def test_proof_rejects_wrong_profile_count(self):
        proof = valid_proof()
        proof["profile_coverage"]["ASan-UBSan"]["core"] = 235
        with self.assertRaisesRegex(RuntimeError, "profile coverage"):
            mod.validate_profile_proof(proof, {"status": "failed", "phase_complete": False})

    def test_proof_rejects_candidate_mutation(self):
        proof = valid_proof()
        proof["candidate"] = "0" * 40
        with self.assertRaisesRegex(RuntimeError, "candidate or host"):
            mod.validate_profile_proof(proof, {"status": "failed", "phase_complete": False})

    def test_receipt_marks_union_and_preserves_original_exit(self):
        original = {"profiles": {
            p: {"status": "failed" if p == "ASan-UBSan" else "passed",
                "commands": [{"name": "core", "exit_code": 8 if p == "ASan-UBSan" else 0,
                              "command": ["original-core-command"]}]}
            for p in mod.PROFILES
        }}
        proof = valid_proof()
        proof["tail"] = {"supplemental_stages": [
            {"name": n, "passed": True, "argv": ["recorded", n]}
            for n in ("core-supervised-copy-recovery", "P10", "bounded-storage-git")
        ]}
        manifests = {"ASan-UBSan": {"artifact_sha256": {}, "artifact_exclusions": [],
                                    "artifact_links": []}}
        receipt = mod.make_receipt(original, proof, "a" * 64, ["flock", "reconcile"],
                                   manifests, proof["tail"])
        self.assertEqual(receipt["status"], "local_passed_crosshost_pending")
        self.assertEqual(receipt["original_failed_run"]["san_core_exit_code"], 8)
        self.assertFalse(receipt["additive_coverage"]["aggregate_is_ctest_invocation"])
        san = next(c for c in receipt["profiles"]["ASan-UBSan"]["commands"] if c["name"] == "core")
        debug = next(c for c in receipt["profiles"]["Debug"]["commands"] if c["name"] == "core")
        self.assertEqual(san["exit_code"], 0)
        self.assertEqual(san["original_ctest_exit_code"], 8)
        self.assertFalse(san["is_single_ctest_execution"])
        self.assertEqual(debug["exit_code"], 0)
        self.assertEqual(debug["command"], ["original-core-command"])
        self.assertEqual(receipt["profiles"]["ASan-UBSan"]["commands"][1]["name"], "core-supervised-copy-recovery")

    def test_debug_command_and_manifest_remain_untouched(self):
        debug = {"status": "passed", "commands": [{"name": "core", "exit_code": 0,
                 "command": ["original-core-command"]}], "artifact_sha256": {"old": "pin"},
                 "artifact_links": [], "artifact_exclusions": []}
        san = {"status": "running", "commands": [{"name": "core", "exit_code": 8}]}
        original = {"status": "failed", "profiles": {"Debug": debug, "ASan-UBSan": san}}
        proof = valid_proof()
        proof["tail"] = {"supplemental_stages": [
            {"name": n, "passed": True, "argv": ["recorded", n]}
            for n in ("core-supervised-copy-recovery", "P10", "bounded-storage-git")
        ]}
        manifests = {"ASan-UBSan": {"artifact_sha256": {"new": "pin"},
                                    "artifact_exclusions": [], "artifact_links": []}}
        receipt = mod.make_receipt(original, proof, "a" * 64, ["verify"], manifests, proof["tail"])
        self.assertEqual(receipt["profiles"]["Debug"], debug)
        self.assertEqual(original["profiles"]["Debug"], debug)
        self.assertEqual(receipt["profiles"]["ASan-UBSan"]["artifact_sha256"], {"new": "pin"})

    def test_missing_tail_stage_refuses(self):
        proof = valid_proof()
        original = {"profiles": {
            p: {"commands": [{"name": "core", "exit_code": 8}]} for p in mod.PROFILES
        }}
        tail = {"supplemental_stages": [{"name": "P10", "passed": True, "argv": []}]}
        with self.assertRaisesRegex(RuntimeError, "tail stage"):
            mod.make_receipt(original, proof, "a" * 64, ["reconcile"],
                             {"ASan-UBSan": {}}, tail)

    def test_wrong_original_sha_refuses(self):
        with mock.patch.object(mod, "file_sha",
                               side_effect=RuntimeError("original failed report SHA-256 mismatch")):
            with self.assertRaisesRegex(RuntimeError, "original failed report"):
                mod.create_receipt()

    def test_output_must_be_fixed_sibling(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(RuntimeError, "fixed sibling"):
                mod.create_receipt(Path(td) / "run.json")

    def test_consumer_refusal_stops_crossstage_validation(self):
        from types import SimpleNamespace
        m06 = SimpleNamespace(
            _validate_local_run=mock.Mock(side_effect=RuntimeError("consumer refused")),
            _validate_crossstage_profile_artifacts=mock.Mock())
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td) / "candidate.json"
            temp.write_text("{}\n")
            with self.assertRaisesRegex(RuntimeError, "consumer refused"):
                mod.validate_with_frozen_m06(m06, {}, temp)
        m06._validate_crossstage_profile_artifacts.assert_not_called()

    def test_consumer_validates_both_profile_closures(self):
        from types import SimpleNamespace
        m06 = SimpleNamespace(
            _validate_local_run=mock.Mock(return_value={"inventories": {"Debug": {}, "ASan-UBSan": {}}}),
            _validate_crossstage_profile_artifacts=mock.Mock())
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td) / "candidate.json"
            temp.write_text("{}\n")
            mod.validate_with_frozen_m06(m06, {"pin": "frozen"}, temp)
        self.assertEqual([c.args[3] for c in m06._validate_crossstage_profile_artifacts.call_args_list],
                         ["Debug", "ASan-UBSan"])

    def test_output_publish_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            candidate = Path(td) / "candidate"
            output = Path(td) / "receipt"
            candidate.write_text("candidate")
            output.write_text("existing")
            with self.assertRaises(FileExistsError):
                candidate.hardlink_to(output)
            self.assertEqual(output.read_text(), "existing")


    def test_create_receipt_composes_and_publishes_only_after_checks(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            original_path = root / "original" / "run.json"
            original_path.parent.mkdir()
            original = {"status": "failed", "phase_complete": False, "blockers": ["old failure"],
                "profiles": {p: {"status": "failed" if p == "ASan-UBSan" else "passed",
                    "commands": [{"name": "core", "exit_code": 8 if p == "ASan-UBSan" else 0,
                                  "command": ["old", p]}], "artifact_sha256": {"old": p},
                    "artifact_exclusions": [], "artifact_links": []} for p in mod.PROFILES}}
            original["profiles"]["ASan-UBSan"]["commands"][0]["exit_code"] = 8
            original_path.write_text(json.dumps(original))
            output = original_path.with_name("new-run.json")
            proof_path = root / "proof.json"
            proof = valid_proof()
            proof["original_failed_run"].update(path=str(original_path), sha256=mod.sha(original_path))
            proof_path.write_text(json.dumps(proof))
            tail_path = root / "tail.json"
            tail = {"supplemental_stages": [{"name": n, "passed": True, "argv": [n]}
                for n in ("core-supervised-copy-recovery", "P10", "bounded-storage-git")]}
            tail_path.write_text(json.dumps(tail))
            binding = root / "binding.json"; binding.write_text("{}")
            combiner = root / "combiner.py"
            combiner.write_text("import json\ndef build_proof(*args):\n with args[0].with_name('proof.json').open() as stream:\n  return json.load(stream)\n")
            deployment = root / "deployment"; deployment.mkdir()
            (deployment / "provenance.json").write_text("{}")
            (deployment / "deployment-receipt.json").write_text("{}")
            input_dir = root / "inputs"; input_dir.mkdir()
            fake_m06 = SimpleNamespace(
                _validate_local_run=mock.Mock(return_value={"inventories": {p: {} for p in mod.PROFILES}}),
                _validate_crossstage_profile_artifacts=mock.Mock(),
            )
            patches = mock.patch.multiple(mod, ORIGINAL=original_path, OUTPUT=output,
                PROOF=proof_path, COMBINER=combiner, TAIL=tail_path, BINDING=binding,
                DEPLOYMENT=deployment, INPUTS=input_dir, PROOF_RUNNER=root / "runner",
                ORIGINAL_SHA=mod.sha(original_path), PROOF_SHA=mod.sha(proof_path),
                COMBINER_SHA=mod.sha(combiner), TAIL_SHA=mod.sha(tail_path),
                BINDING_SHA=mod.sha(binding))
            with patches, mock.patch.object(mod, "validate_combined_runner", return_value=["flock", "verify"]), \
                 mock.patch.object(mod, "validate_storage_copy") as storage_copy, \
                 mock.patch.object(mod, "profile_manifest", return_value={"artifact_sha256": {},
                     "artifact_exclusions": [], "artifact_links": []}), \
                 mock.patch.object(mod, "import_deployed_validators", return_value=(fake_m06, None, {})), \
                 mock.patch.object(mod, "verify_source_closure"), \
                 mock.patch.object(mod, "validate_storage_evidence"):
                result = mod.create_receipt(output)
                self.assertEqual(result["status"], "local_passed_crosshost_pending")
                created = json.loads(output.read_text())
                self.assertEqual(created["status"], "local_passed_crosshost_pending")
                self.assertEqual(created["profiles"]["Debug"]["commands"][0]["command"], ["old", "Debug"])
                self.assertEqual(created["original_failed_run"]["san_core_exit_code"], 8)
                self.assertEqual(created["preserved_original_failure_state"]["blockers"], ["old failure"])
                self.assertEqual(storage_copy.call_count, 1)
                self.assertEqual(fake_m06._validate_local_run.call_count, 2)
                self.assertFalse(created["gate_pass"])
                output.unlink()
                fake_m06._validate_local_run.reset_mock()
                fake_m06._validate_local_run.side_effect = RuntimeError("consumer refused")
                with self.assertRaisesRegex(RuntimeError, "consumer refused"):
                    mod.create_receipt(output)
                self.assertFalse(output.exists())
                self.assertEqual(list(root.glob(".additive-receipt-*.tmp")), [])
                fake_m06._validate_local_run.side_effect = None
                fake_m06._validate_local_run.return_value = {"inventories": {p: {} for p in mod.PROFILES}}
                storage_copy.side_effect = RuntimeError("copied storage evidence missing")
                with self.assertRaisesRegex(RuntimeError, "copied storage evidence missing"):
                    mod.create_receipt(output)
                self.assertFalse(output.exists())
                storage_copy.side_effect = None
                with mock.patch.object(mod, "recheck_pins", side_effect=RuntimeError("final pin changed")):
                    with self.assertRaisesRegex(RuntimeError, "final pin changed"):
                        mod.create_receipt(output)
                self.assertFalse(output.exists())
                self.assertEqual(list(root.glob(".additive-receipt-*.tmp")), [])

    def test_create_receipt_refuses_copy_or_final_pin_change_without_publication(self):
        # The full-path fixture above proves publication. These fail-closed branches
        # run before a candidate can become the fixed sibling receipt.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); original = root / "run.json"; original.write_text("{}")
            output = original.with_name("new.json")
            with mock.patch.object(mod, "OUTPUT", output), mock.patch.object(mod, "ORIGINAL", original), \
                 mock.patch.object(mod, "file_sha", side_effect=RuntimeError("pin changed")):
                with self.assertRaisesRegex(RuntimeError, "pin changed"):
                    mod.create_receipt(output)
            self.assertFalse(output.exists())

    def test_main_calls_publisher_for_requested_output(self):
        with mock.patch.object(mod, "create_receipt", return_value={"status": "pending"}) as publish:
            self.assertEqual(mod.main(["--output", str(mod.OUTPUT)]), 0)
            publish.assert_called_once_with(mod.OUTPUT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
