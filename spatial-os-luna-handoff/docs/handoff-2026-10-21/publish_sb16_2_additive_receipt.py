#!/usr/bin/env python3
"""Publish a truthful additive sb16-2 receipt after strict frozen-gate validation."""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

CANDIDATE = "580834ceeda112da0a1cdcd37ca7b288db1512fc"
HOST = "sb16-2"
ARTIFACT_ROOT = Path("/home/dflanag3/spatial-artifacts/m08-issuer-fixture-580834ceeda1-20261021").resolve()
EVIDENCE_ROOT = ARTIFACT_ROOT / "packaging-cache-repair-01"
DEPLOYMENT = ARTIFACT_ROOT / "deployed-01"
ORIGINAL = DEPLOYMENT / "evidence/m08-580834ceeda1-full-sb16-2-01/run.json"
OUTPUT = ORIGINAL.with_name("run-additive-reconciliation.json")
PROOF = EVIDENCE_ROOT / "sb16-2/combined-evidence-01/reconciliation-proof.json"
PROOF_RUNNER = EVIDENCE_ROOT / "sb16-2/combined-evidence-run-01"
COMBINER = EVIDENCE_ROOT / "sb16-2/control/combined-evidence-verifier-01/reconcile_sb16_2_additive_proof.py"
INPUTS = EVIDENCE_ROOT / "sb16-2-reconciliation-actual-inputs-01"
TAIL = EVIDENCE_ROOT / "sb16-2/san-tail-supplement-06/supplemental-result.json"
BINDING = EVIDENCE_ROOT / "sb16-2/post-san-tail06-binding-01.json"
ORIGINAL_SHA = "48eec27b792bedca8f8ac6c4c4f088ae5173b3e40a0b82083859b49a00e6c54a"
PROOF_SHA = "688f000fef724ab5778c6a829d86761c380008180fdf744872b6832b0682cd12"
COMBINER_SHA = "3f85272af58b989708dd6ecac2443e7e99b1618fb71946389c82939b1ef3d9b0"
TAIL_SHA = "c21041a8bfa39e2ce6718b56267e002bbe24502652c1363e064d1632a5a32076"
BINDING_SHA = "2bdc6e1eff67c551a7f06eeccb1bb9e8ab1c6da28b0de5001194c645d46f06d7"
PROFILES = ("Debug", "ASan-UBSan")


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def read_json(path: Path, label: str):
    require(path.is_file() and not path.is_symlink(), label + " is missing or not a regular file")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(label + " is unreadable") from error


def file_sha(path: Path, expected: str, label: str) -> None:
    require(path.is_file() and not path.is_symlink(), label + " is missing")
    require(sha(path) == expected, label + " SHA-256 mismatch")


def expected_combiner_argv(proof_path: Path) -> list[str]:
    return [
        "flock", "-n", "/run/user/1000/spatial-m06-gate.lock",
        "env", "PYTHONDONTWRITEBYTECODE=1", "python3", "-B", str(COMBINER),
        "--inputs-dir", str(INPUTS), "--original-run", str(ORIGINAL),
        "--tail-result", str(TAIL), "--tail-sha256", TAIL_SHA,
        "--post-tail-binding", str(BINDING), "--post-tail-binding-sha256", BINDING_SHA,
        "--output", str(proof_path),
    ]


def validate_combined_runner(proof_path: Path, runner: Path = PROOF_RUNNER) -> list[str]:
    require(runner == PROOF_RUNNER, "combined verifier runner path mismatch")
    require(proof_path == PROOF, "combined proof path mismatch")
    argv_path = runner / "argv.nul"
    require(argv_path.is_file() and not argv_path.is_symlink(), "combined verifier argv receipt missing")
    raw_argv = argv_path.read_bytes()
    require(raw_argv.endswith(b"\0"), "combined verifier argv is not NUL terminated")
    argv = [part.decode("utf-8") for part in raw_argv[:-1].split(b"\0")]
    require(argv == expected_combiner_argv(proof_path), "combined verifier argv does not match frozen command")
    for leaf, expected in (("command.exit", b"0\n"), ("wrapper.exit", b"0\n"),
                           ("terminal-status.txt", b"COMPLETE\n"), ("stderr.log", b"")):
        path = runner / leaf
        require(path.is_file() and not path.is_symlink() and path.read_bytes() == expected,
                "combined verifier runner receipt mismatch: " + leaf)
    try:
        summary = json.loads((runner / "stdout.log").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError("combined verifier stdout is not a JSON summary") from error
    require(summary == {"covered_names": 236, "gate_pass": False, "proof_path": str(PROOF),
                        "qualification": "UNQUALIFIED"},
            "combined verifier stdout summary mismatch")
    return argv


def tree_signature(root: Path, validated_exclusions: set[str]) -> dict[str, tuple]:
    require(root.is_dir() and not root.is_symlink(), "storage evidence root is missing or unsafe")
    result = {}
    def walk_error(error):
        raise RuntimeError("cannot inspect storage subtree: " + str(error)) from error
    require(validated_exclusions == {"volume", "volume.img"},
            "M06 did not validate the exact bounded-storage root-owned entries")
    for current, directories, filenames in os.walk(root, topdown=True, followlinks=False, onerror=walk_error):
        here = Path(current)
        for name in list(directories):
            path = here / name
            rel = path.relative_to(root).as_posix()
            if rel == "volume":
                directories.remove(name)
                continue
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                result[rel] = ("symlink-dir", stat.S_IMODE(info.st_mode), info.st_uid, os.readlink(path))
                directories.remove(name)
            elif stat.S_ISDIR(info.st_mode):
                result[rel] = ("dir", stat.S_IMODE(info.st_mode), info.st_uid)
            else:
                raise RuntimeError("storage evidence has a non-directory entry: " + rel)
        for name in filenames:
            path = here / name
            rel = path.relative_to(root).as_posix()
            if rel == "volume.img":
                continue
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                result[rel] = ("symlink-file", stat.S_IMODE(info.st_mode), info.st_uid, os.readlink(path))
            elif stat.S_ISREG(info.st_mode):
                signature = ("file", stat.S_IMODE(info.st_mode), info.st_uid, info.st_size)
                if info.st_uid == 0:
                    require(path.name == "volume.img" and info.st_size == 2147483648
                            and stat.S_IMODE(info.st_mode) == 0o600,
                            "unexpected root-owned storage file: " + rel)
                    result[rel] = signature + ("root-volume",)
                else:
                    result[rel] = signature + (sha(path),)
            else:
                raise RuntimeError("storage evidence has a special file: " + rel)
    return result


def validate_storage_copy(tail: dict, original_profile: Path, tail_profile: Path, m06) -> None:
    expected_manifest = tail.get("supplemental_artifact_manifest", {}).get("sha256")
    require(isinstance(expected_manifest, dict), "tail artifact manifest is missing")
    prefix = "bounded-storage-git/"
    expected_files = {key[len(prefix):]: value for key, value in expected_manifest.items() if key.startswith(prefix)}
    require("review.json" in expected_files, "tail storage manifest lacks review.json")
    copied = original_profile / "bounded-storage-git"
    source = tail_profile / "bounded-storage-git"
    require(source.is_dir() and copied.is_dir(), "verified SAN storage subtree copy is missing")
    for path in (source / "volume.img", copied / "volume.img"):
        require(path.is_file() and not path.is_symlink(),
                "required root-owned storage image is missing")
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size == 2147483648,
                "storage image is not the exact root-owned 2 GiB fixture")
    original = read_json(ORIGINAL, "original failed run")
    names = m06.storage_scenario_result_names(original.get("storage_expected_results"))
    source_exclusions = m06._bounded_storage_artifact_exclusions(tail_profile, names)
    copy_exclusions = m06._bounded_storage_artifact_exclusions(original_profile, names)
    source_paths = {Path(item["path"]).relative_to("bounded-storage-git").as_posix()
                    for item in source_exclusions}
    copy_paths = {Path(item["path"]).relative_to("bounded-storage-git").as_posix()
                  for item in copy_exclusions}
    require(source_paths == copy_paths == {"volume", "volume.img"},
            "M06 bounded-storage exclusions do not match the reviewed root-owned entries")
    source_signature = tree_signature(source, source_paths)
    copy_signature = tree_signature(copied, copy_paths)
    require(source_signature == copy_signature,
            "copied SAN storage subtree differs in bytes, types, owner, or mode")
    def root_hash(path: Path) -> str:
        before = path.lstat()
        result = subprocess.run(["sudo", "-n", "sha256sum", str(path)],
                                text=True, capture_output=True, check=False, timeout=120)
        after = path.lstat()
        require(result.returncode == 0 and not result.stderr
                and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                    == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
                "cannot safely hash root-owned storage image")
        fields = result.stdout.strip().split()
        require(len(fields) == 2 and fields[1] == str(path), "unexpected privileged hash output")
        return fields[0]
    require(root_hash(source / "volume.img") == root_hash(copied / "volume.img"),
            "root-owned storage image bytes differ")
    for rel, digest in expected_files.items():
        path = copied / rel
        require(path.is_file() and not path.is_symlink() and sha(path) == digest,
                "copied SAN storage file differs from pinned supplemental manifest: " + rel)


def validate_profile_proof(proof: dict, original: dict) -> None:
    require(proof.get("candidate") == CANDIDATE and proof.get("host") == HOST,
            "combined proof candidate or host mismatch")
    require(proof.get("validation_status") == "COMBINED_EVIDENCE_PASSED"
            and proof.get("gate_pass") is False and proof.get("phase_complete") is False
            and proof.get("qualification") == "UNQUALIFIED",
            "combined proof must remain unqualified")
    require(proof.get("original_failed_run") == {
        "path": str(ORIGINAL), "sha256": ORIGINAL_SHA, "status": "failed",
        "original_san_core_exit": 8, "protected_cases": 232, "passed": 227,
        "failed_names": [
            "host-actor-controller-native", "host-session-production-native",
            "session-broker-retirement", "session-coordinator-retirement",
            "session-stopped-broker-dependency",
        ], "skipped": 0,
    }, "proof does not preserve the exact original failure")
    expected_profile = {"core": 236, "harness": 89, "locked_P10": 151, "bounded_storage_git": 6}
    require(proof.get("profile_coverage") == {profile: expected_profile for profile in PROFILES},
            "combined profile coverage counts differ")
    coverage = proof.get("derived_coverage", {})
    require(coverage.get("expected_local_names") == 236
            and coverage.get("original_san_passed_names") == 227
            and coverage.get("unique_covered_names") == 236
            and len(coverage.get("named_native_reruns", [])) == 5
            and len(coverage.get("self_managed_nested_names", [])) == 4,
            "combined coverage is incomplete")
    require(original.get("status") == "failed" and original.get("phase_complete") is False,
            "original failed run was changed or is not a failed attempt")


def make_receipt(original: dict, proof: dict, proof_sha256: str,
                 runner_argv: list[str], profile_manifests: dict, tail_receipt: dict) -> dict:
    receipt = copy.deepcopy(original)
    original_state = {key: copy.deepcopy(original.get(key)) for key in (
        "status", "error", "execution_error", "blockers", "postrun_invariant_failures",
        "source_sha256_rechecked", "profile_artifacts_rechecked",
        "production_off_rechecked", "runtime_rechecked", "metadata_rechecked",
        "storage_evidence_rechecked",
    )}
    receipt["preserved_original_failure_state"] = original_state
    receipt["blockers"] = []
    receipt.update(
        report_kind="additive_local_reconciliation",
        status="local_passed_crosshost_pending", phase_complete=False,
        qualification="UNQUALIFIED", gate_pass=False, additive_receipt_schema=1,
        original_failed_run={
            "path": str(ORIGINAL), "sha256": ORIGINAL_SHA, "status": "failed",
            "san_core_exit_code": 8, "san_core_passed": 227, "san_core_failed": 5,
            "failed_registrations": proof["original_failed_run"]["failed_names"],
        },
        additive_coverage={
            "proof_path": str(PROOF), "proof_sha256": proof_sha256,
            "proof_kind": proof["proof_kind"], "qualification": "UNQUALIFIED",
            "validation_status": proof["validation_status"],
            "aggregate_command_argv": runner_argv, "aggregate_command_exit_code": 0,
            "aggregate_is_ctest_invocation": False, "is_single_ctest_execution": False,
            "core_registration_coverage": 236,
            "original_san_passes": 227, "named_native_reruns": 5,
            "self_managed_recovery_cases": 4,
        },
        preserved_original_commands={profile: original["profiles"][profile]["commands"]
                                     for profile in PROFILES},
    )
    for profile in PROFILES:
        row = receipt["profiles"][profile]
        old_core = next((item for item in row.get("commands", []) if item.get("name") == "core"), None)
        require(old_core is not None, profile + " original core command is missing")
        core_row = {
            "name": "core", "command": runner_argv, "exit_code": 0,
            "all_registered_present": True, "failed_or_skipped": False, "junit_cases": 236,
            "assertions_passed": True, "evidence_kind": "additive_union_not_single_ctest",
            "proof_path": str(PROOF), "proof_sha256": proof_sha256,
            "original_ctest_exit_code": old_core.get("exit_code"),
            "is_single_ctest_execution": False,
        }
        if profile == "ASan-UBSan":
            row["commands"] = [core_row if item.get("name") == "core" else dict(item)
                               for item in row.get("commands", [])]
            for stage_name in ("core-supervised-copy-recovery", "P10", "bounded-storage-git"):
                tail_row = next((r for r in tail_receipt["supplemental_stages"]
                                 if r.get("name") == stage_name), None)
                require(tail_row is not None and tail_row.get("passed") is True,
                        "tail stage is not a recorded pass: " + stage_name)
                row["commands"] = [item for item in row["commands"] if item.get("name") != stage_name]
                row["commands"].append({
                    "name": stage_name, "command": tail_row["argv"], "exit_code": 0,
                    "assertions_passed": True, "evidence_kind": "additive_supplemental_stage",
                    "source_receipt": str(TAIL), "source_receipt_sha256": TAIL_SHA,
                })
            row["status"] = "passed_needs_crosshost_and_review"
            row.update(profile_manifests[profile])
    receipt["postrun_invariant_failures"] = []
    receipt["execution_error"] = None
    receipt["error"] = None
    return receipt


def import_deployed_validators():
    source = DEPLOYMENT / "source"
    provenance = read_json(DEPLOYMENT / "provenance.json", "deployment provenance")
    require(provenance.get("candidate") == CANDIDATE, "deployment candidate mismatch")
    for rel in ("tools/m06_deployed_gate.py", "tools/m07_deployed_gate.py"):
        expected = provenance.get("source_sha256", {}).get(rel)
        require(isinstance(expected, str) and len(expected) == 64, "missing source pin: " + rel)
        file_sha(source / rel, expected, "deployed " + rel)
    sys.path.insert(0, str(source))
    m06 = importlib.import_module("tools.m06_deployed_gate")
    m07 = importlib.import_module("tools.m07_deployed_gate")
    return m06, m07, provenance


def verify_source_closure(provenance: dict) -> None:
    for relative, expected in provenance.get("source_sha256", {}).items():
        rel = Path(relative)
        require(not rel.is_absolute() and ".." not in rel.parts,
                "unsafe pinned source path: " + relative)
        file_sha(DEPLOYMENT / "source" / rel, expected, "source closure " + relative)


def recheck_pins(provenance: dict, provenance_sha256: str, deployment_receipt_sha256: str) -> None:
    file_sha(ORIGINAL, ORIGINAL_SHA, "original failed report")
    file_sha(PROOF, PROOF_SHA, "combined evidence proof")
    file_sha(COMBINER, COMBINER_SHA, "combined evidence verifier source")
    file_sha(TAIL, TAIL_SHA, "SAN tail result")
    file_sha(BINDING, BINDING_SHA, "post-tail binding")
    validate_combined_runner(PROOF)
    file_sha(DEPLOYMENT / "provenance.json", provenance_sha256, "deployment provenance")
    file_sha(DEPLOYMENT / "deployment-receipt.json", deployment_receipt_sha256,
             "deployment receipt")
    verify_source_closure(provenance)


def profile_manifest(profile: str) -> dict:
    m06, _, _ = import_deployed_validators()
    target = ORIGINAL.parent / profile
    original = read_json(ORIGINAL, "original failed run")
    expected = m06.storage_scenario_result_names(original.get("storage_expected_results"))
    if profile == "ASan-UBSan":
        require((target / "bounded-storage-git/review.json").is_file(),
                "copied SAN bounded-storage review is missing")
    manifest = m06.artifact_evidence_manifest(target, expected)
    return {"artifact_sha256": manifest["sha256"], "artifact_exclusions": manifest["exclusions"],
            "artifact_links": manifest["artifact_links"]}


def validate_storage_evidence(m06) -> None:
    source = DEPLOYMENT / "source"
    for profile in PROFILES:
        metadata = read_json(ORIGINAL.parent / profile / "metadata-relocation.json",
                            profile + " metadata relocation")
        augmentation = metadata.get("storage_local_evidence_augmentation", {})
        require(augmentation.get("tests") == list(m06.STORAGE_LOCAL_EVIDENCE_TESTS),
                profile + " storage evidence registration mapping changed")
        profile_path = Path(augmentation.get("profile", {}).get("path", ""))
        m06.verify_storage_evidence_path(source, profile_path)
        destinations = augmentation.get("destinations", {})
        declared = augmentation.get("profile", {}).get("registrations", {})
        require(set(destinations) == set(m06.STORAGE_LOCAL_EVIDENCE_TESTS)
                and set(declared) == set(destinations),
                profile + " storage evidence paths are incomplete")
        for name, path in destinations.items():
            require(path == declared[name].get("path"), profile + " storage evidence mapping differs")
            m06.verify_storage_evidence_path(source, Path(path))


def apply_recheck_summary(receipt: dict) -> None:
    receipt["source_sha256_rechecked"] = True
    for field in ("profile_artifacts_rechecked", "production_off_rechecked", "runtime_rechecked",
                  "metadata_rechecked", "storage_evidence_rechecked"):
        receipt[field] = {profile: True for profile in PROFILES}


def validate_with_frozen_m06(m06, provenance: dict, temp: Path) -> dict:
    report_sha = sha(temp)
    report = m06._validate_local_run(temp, HOST, CANDIDATE)
    for profile in PROFILES:
        m06._validate_crossstage_profile_artifacts(
            DEPLOYMENT, provenance, HOST, profile, temp, report_sha)
    return report


def create_receipt(output: Path = OUTPUT) -> dict:
    require(output == OUTPUT, "receipt output path is not the fixed sibling path")
    require(output.parent == ORIGINAL.parent and not output.exists() and not output.is_symlink(),
            "new sibling receipt already exists or is outside the original attempt")
    for path, digest, label in (
        (ORIGINAL, ORIGINAL_SHA, "original failed report"), (PROOF, PROOF_SHA, "combined evidence proof"),
        (COMBINER, COMBINER_SHA, "combined evidence verifier source"),
        (TAIL, TAIL_SHA, "SAN tail result"), (BINDING, BINDING_SHA, "post-tail binding"),
    ):
        file_sha(path, digest, label)
    argv = validate_combined_runner(PROOF)
    original = read_json(ORIGINAL, "original failed run")
    proof = read_json(PROOF, "combined evidence proof")
    spec = importlib.util.spec_from_file_location("pinned_combined_proof_verifier", COMBINER)
    require(spec is not None and spec.loader is not None, "cannot load pinned verifier")
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    rebuilt = verifier.build_proof(INPUTS, ORIGINAL, TAIL, TAIL_SHA, BINDING, BINDING_SHA)
    require(rebuilt == proof, "saved proof differs from fresh read-only verification")
    validate_profile_proof(proof, original)
    tail = read_json(TAIL, "SAN tail result")
    manifests = {"ASan-UBSan": profile_manifest("ASan-UBSan")}
    m06, _m07, provenance = import_deployed_validators()
    provenance_sha256 = sha(DEPLOYMENT / "provenance.json")
    deployment_receipt_sha256 = sha(DEPLOYMENT / "deployment-receipt.json")
    verify_source_closure(provenance)
    validate_storage_evidence(m06)
    validate_storage_copy(tail, ORIGINAL.parent / "ASan-UBSan", TAIL.parent / "ASan-UBSan", m06)
    receipt = make_receipt(original, proof, PROOF_SHA, argv, manifests, tail)
    with tempfile.NamedTemporaryFile("xb", dir=ORIGINAL.parent, prefix=".additive-receipt-",
                                     suffix=".tmp", delete=False) as stream:
        temp = Path(stream.name)
        stream.write((json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode())
        stream.flush()
        os.fsync(stream.fileno())
    try:
        report = validate_with_frozen_m06(m06, provenance, temp)
        apply_recheck_summary(receipt)
        # Rewrite and fsync the fully annotated candidate before the final consumer audit.
        with temp.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        report = validate_with_frozen_m06(m06, provenance, temp)
        recheck_pins(provenance, provenance_sha256, deployment_receipt_sha256)
        require(not OUTPUT.exists() and not OUTPUT.is_symlink(),
                "receipt output appeared during validation")
        os.link(temp, OUTPUT)
        return {"path": str(OUTPUT), "sha256": sha(OUTPUT),
                "status": "local_passed_crosshost_pending", "qualification": "UNQUALIFIED",
                "phase_complete": False, "gate_pass": False,
                "consumer_validated_inventories": report["inventories"]}
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    print(json.dumps(create_receipt(args.output), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print("REFUSED/INCOMPLETE: " + str(error), file=sys.stderr)
        raise SystemExit(2)
