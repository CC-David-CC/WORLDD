#!/usr/bin/env python3
"""Validate additive sb16-2 evidence without publishing a gate receipt."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, pathlib, re, sys, xml.etree.ElementTree as ET

CANDIDATE = "580834ceeda112da0a1cdcd37ca7b288db1512fc"
HOST = "sb16-2"
ROOT = pathlib.Path("/home/dflanag3/spatial-artifacts/m08-issuer-fixture-580834ceeda1-20261021").resolve()
EVIDENCE_ROOT = ROOT / "packaging-cache-repair-01"
POST_TAIL_BINDING = EVIDENCE_ROOT / "sb16-2/post-san-tail06-binding-01.json"
POST_TAIL_AUDIT = EVIDENCE_ROOT / "sb16-2/frozen-runtime-post-san-tail06-01.json"
POST_TAIL_AUDIT_RUNNER = EVIDENCE_ROOT / "sb16-2/frozen-runtime-post-san-tail06-run-01"
POST_TAIL = EVIDENCE_ROOT / "sb16-2/san-tail-supplement-06/supplemental-result.json"
ORIGINAL_SHA = "48eec27b792bedca8f8ac6c4c4f088ae5173b3e40a0b82083859b49a00e6c54a"
PROVENANCE_SHA = "14cf36a0937bd9aa495f73f091693ef207c98e30a82b6dfdba567dee7615f1ab"
NATIVE_SHA = "e7ace1dc6238e064116e9205f67577c1d6d51e84c417093a0e8604de2c115032"
CABINET_SHA = "9edd2be97fa7a9b5c16c0fc6636cae4a3089accc1f9fc513ede23de14e718eda"
SAN_INV_SHA = "ab3dd3d6404e480889ade8fa0de60873a0f7e53d6f810f340bd4024a16ab3565"
DEBUG_INV_SHA = "74819792ca4c0cdbff9cf5f8452f0968beaca6b112d0f46df9d762831cedc8d7"
JUNIT_SHA = "40157305721c9e28828cdd65fe704f68af8b263f602e6f19fb02cc6cacf27e14"
INTEGRITY_SHA = "854c48a65bf1aafeedbd1e61ac5a2e3b84d393bb9c9a6f9bdce0081f6be417e7"
TAIL_M06_SHA = "8feb2c142d0ffd37a35011be412d44066fb292d3e883462021c292c3882ee21d"
TAIL_M08_SHA = "500f340e263a8784f7a616b5086daf26fd5dc104027b2dcd23b2aa80db75c2f1"
RUNNER_SHA = "7053d5ffe05e7050b035d6b715b794dd742b68ee29b9198a703979391323f537"
TAIL_SHA = "c21041a8bfa39e2ce6718b56267e002bbe24502652c1363e064d1632a5a32076"
BINDING_SHA = "2bdc6e1eff67c551a7f06eeccb1bb9e8ab1c6da28b0de5001194c645d46f06d7"
DEPLOYMENT_SHA = "5a2fbae3ea6595c05dcf8ffb2d766ad24b7d4d7210bbf33b0fd8d16022a0ed50"
POST_TAIL_AUDIT_SHA = INTEGRITY_SHA
SAN_RE = re.compile(r"AddressSanitizer:|LeakSanitizer:|UndefinedBehaviorSanitizer:|runtime error:")
FAILURES = {"host-actor-controller-native", "host-session-production-native", "session-coordinator-retirement", "session-broker-retirement", "session-stopped-broker-dependency"}
NATIVE_COUNTS = {"identity-tls": 25, "host-actor-controller-native": 1, "host-session-production-native": 26, "session-coordinator-retirement": 8, "session-broker-retirement": 9, "session-stopped-broker-dependency": 9}
NESTED = {"workspace-copy-supervised-sigkill-recovery", "workspace-copy-supervised-prepared-worker-proof-recovery", "workspace-copy-supervised-published-prepared-recovery", "workspace-copy-supervised-published-sync-failure-recovery"}
CABINET = ("test_cabinet_view_move_and_use_authority_are_separate", "test_cabinet_placement_requires_effective_file_authority", "test_registered_worktree_scope_controls_cabinet_placement_and_file_use")
STAGES = ("core-supervised-copy-recovery", "P10", "bounded-storage-git")
STORAGE = {"large_import_retirement", "application_and_actor_quota", "read_only", "sibling_survival", "restart", "production_application_copy_stage_mask"}
ORIGINAL_TARGETS = {
    "/home/dflanag3/spatial-artifacts/m08-issuer-fixture-580834ceeda1-20261021/deployed-01/evidence/m08-580834ceeda1-full-sb16-2-01/run.json",
    "/home/dflanag3/spatial-artifacts/m08-issuer-fixture-580834ceeda1-20261021/packaging-cache-repair-01/sb16-2-frozen-review-inputs-01/run.json"
}
TARGET_PATHS = {
    "native-replay.json": "/home/dflanag3/spatial-artifacts/m08-issuer-fixture-580834ceeda1-20261021/packaging-cache-repair-01/sb16-2/issuer-replay-03/run-20261021T191536Z-f704ee1a/result.json",
    "cabinet-replay.json": "/home/dflanag3/spatial-artifacts/m08-issuer-fixture-580834ceeda1-20261021/packaging-cache-repair-01/sb16-2/cabinet-controller-04/run-20261021T214626Z-d1f8d4b7/result.json",
    "san-inventory.json": "/home/dflanag3/spatial-artifacts/m08-issuer-fixture-580834ceeda1-20261021/deployed-01/evidence/m08-580834ceeda1-full-sb16-2-01/ASan-UBSan/inventory.json",
    "debug-inventory.json": "/home/dflanag3/spatial-artifacts/m08-issuer-fixture-580834ceeda1-20261021/deployed-01/evidence/m08-580834ceeda1-full-sb16-2-01/Debug/inventory.json",
    "san-core-protected.xml": "/home/dflanag3/spatial-artifacts/m08-issuer-fixture-580834ceeda1-20261021/deployed-01/evidence/m08-580834ceeda1-full-sb16-2-01/ASan-UBSan/core-protected.xml",
    "provenance.json": "/home/dflanag3/spatial-artifacts/m08-issuer-fixture-580834ceeda1-20261021/deployed-01/provenance.json",
    "post-native-integrity.json": "/home/dflanag3/spatial-artifacts/m08-issuer-fixture-580834ceeda1-20261021/packaging-cache-repair-01/sb16-2/frozen-runtime-post-replay-01.json"
}

def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1048576), b""):
            h.update(block)
    return h.hexdigest()

def need(ok, message):
    if not ok:
        raise ValueError(message)

def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))

def pinned(path, expected, label):
    p = pathlib.Path(path).resolve(strict=True)
    need(ROOT in p.parents, label + " is outside the candidate artifact root")
    need(sha(p) == expected, label + " SHA-256 mismatch")
    return p

def stage_recipes(raw_root):
    source = ROOT / "deployed-01/source"
    path = source / "tools/m06_deployed_gate.py"
    need(sha(path) == TAIL_M06_SHA, "frozen stage recipe source mismatch")
    spec = importlib.util.spec_from_file_location("_combined_frozen_m06", path)
    gate = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = gate
    spec.loader.exec_module(gate)
    rows = gate.profile_stage_commands(source, ROOT / "deployed-01/build/ASan-UBSan",
            ROOT / "deployed-01/production-off/ASan-UBSan", raw_root,
            sorted(gate.DEDICATED_CROSSHOST), copy_worker_helpers=True, profile="ASan-UBSan")
    selected = {}
    for name, argv, timeout in rows:
        if name not in STAGES:
            continue
        frozen = list(argv)
        if name == "bounded-storage-git":
            need(argv[:3] == ["sudo", "-n", "python3"], "unexpected storage recipe")
            argv = ["sudo", "-n", "env", "PYTHONDONTWRITEBYTECODE=1", "python3", "-B", *argv[3:]]
        selected[name] = dict(argv=argv, frozen_recipe_argv=frozen, timeout_seconds=timeout, cwd=str(source))
    need(set(selected) == set(STAGES), "frozen recipe inventory mismatch")
    return gate, selected

def validate_raw_stage_files(raw_root, tail, acceptance_lock):
    manifest = tail.get("supplemental_artifact_manifest", {}).get("sha256")
    need(isinstance(manifest, dict) and manifest, "tail raw artifact manifest missing")
    gate, recipes = stage_recipes(raw_root)
    logs_checked = 0
    for key, expected in manifest.items():
        relpath = pathlib.Path(key)
        need(not relpath.is_absolute() and ".." not in relpath.parts, "unsafe raw artifact path")
        path = raw_root / relpath
        need(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(raw_root), "missing or redirected raw artifact: " + key)
        need(sha(path) == expected, "raw artifact hash mismatch: " + key)
        if path.suffix == ".log":
            need(not SAN_RE.search(path.read_text(errors="replace")), "raw sanitizer finding: " + key)
            logs_checked += 1
    for name in STAGES:
        row = next((r for r in tail["supplemental_stages"] if r.get("name") == name), {})
        for key, value in recipes[name].items():
            need(row.get(key) == value, "raw stage differs from frozen recipe: " + name + ":" + key)
        runner = pathlib.Path(row.get("runner", ""))
        need(runner.parts[-2:] == ("canonical-runs", name), "tail runner path mismatch: " + name)
        rel = pathlib.PurePosixPath("canonical-runs") / name
        for leaf in ("command.exit", "wrapper.exit", "terminal-status.txt", "argv.nul", "stdout.log", "stderr.log"):
            key = str(rel / leaf)
            need(key in manifest, "tail manifest misses raw runner file: " + key)
            p = raw_root / key
            need(p.is_file() and not p.is_symlink() and sha(p) == manifest[key], "raw runner file mismatch: " + key)
        raw_dir = raw_root / str(rel)
        need((raw_dir / "command.exit").read_text().strip() == "0" and (raw_dir / "wrapper.exit").read_text().strip() == "0" and (raw_dir / "terminal-status.txt").read_text().strip() == "COMPLETE", "raw canonical exits mismatch: " + name)
        argv = [x.decode() for x in (raw_dir / "argv.nul").read_bytes().split(bytes([0]))[:-1]]
        need(argv == row.get("argv"), "raw canonical argv mismatch: " + name)
        text = (raw_dir / "stdout.log").read_text(errors="replace") + "\n" + (raw_dir / "stderr.log").read_text(errors="replace")
        try:
            gate.reject_inner_skips(text)
            gate.reject_inner_failures(text)
        except RuntimeError as exc:
            raise ValueError("raw stage log failure: " + str(exc)) from exc
        logs = row.get("logs", {})
        need(sha(raw_dir / "stdout.log") == logs.get("stdout_sha256") and sha(raw_dir / "stderr.log") == logs.get("stderr_sha256"), "raw canonical log pin mismatch: " + name)

    required = ("core-self-managed.xml", "P10.json", "bounded-storage-git/review.json")
    raw = {}
    for key in required:
        need(key in manifest, "tail manifest misses raw stage output: " + key)
        p = raw_root / key
        need(p.is_file() and not p.is_symlink() and sha(p) == manifest[key], "raw stage output mismatch: " + key)
        raw[key] = p

    cases = list(ET.parse(raw["core-self-managed.xml"]).getroot().iter("testcase"))
    names = [c.get("name") for c in cases]
    need(len(names) == 4 and len(set(names)) == 4 and set(names) == NESTED and all(c.find(tag) is None for c in cases for tag in ("failure", "error", "skipped")), "raw nested recovery JUnit mismatch")

    lock = read(acceptance_lock)
    expected_ids = lock.get("test_ids", [])
    p10 = read(raw["P10.json"])
    tests = p10.get("tests", [])
    ids = [row.get("test") for row in tests]
    need(p10.get("status") == "passed" and p10.get("through") == "P10" and p10.get("selected") == p10.get("executed") == p10.get("total_baseline") == 151 and p10.get("not_selected") == 0 and p10.get("diagnostic_only") is False and len(ids) == 151 and len(set(ids)) == 151 and set(ids) == set(expected_ids) and all(row.get("status") == "passed" and row.get("phase", "") <= "P10" for row in tests), "raw locked P10 inventory mismatch")

    storage = read(raw["bounded-storage-git/review.json"])
    results = storage.get("results", {})
    owners = storage.get("owner_unit_cleanup", {})
    scope = storage.get("scope")
    need(set(results) == STORAGE and all(isinstance(v, dict) and v.get("passed") is True for v in results.values()), "raw storage result inventory mismatch")
    need(storage.get("status") == "passed" and storage.get("mounted") is False and not storage.get("scope") is None and not pathlib.Path(scope).exists(), "raw storage cleanup status mismatch")
    need(all(storage.get(k, {}).get("exit_code") == 0 for k in ("service_stop", "unmount", "detach")) and "ActiveState=inactive" in storage.get("service_after", "") and len(owners) == 2 and all("ActiveState=inactive" in v for v in owners.values()), "raw storage cleanup mismatch")
    return {"manifest_files_checked": len(manifest), "logs_scanned": logs_checked, "sanitizer_findings": [], "frozen_recipes_match": True, "runner_files_checked": len(STAGES) * 6, "nested_cases": sorted(names), "p10_ids": len(ids), "storage_results": sorted(results)}

def validate_tail(path, expected_sha, native_target, acceptance_lock):
    need(len(expected_sha) == 64 and all(c in "0123456789abcdef" for c in expected_sha), "tail SHA-256 is malformed")
    need(expected_sha == TAIL_SHA, "tail is not the reviewed successful attempt")
    p = pinned(path, expected_sha, "SAN tail result")
    need(p == POST_TAIL, "tail result path mismatch")
    need(p.name == "supplemental-result.json", "tail result filename mismatch")
    d = read(p)
    need("test_fixture_status" not in d, "synthetic tail fixtures are forbidden in the production CLI")
    need(d.get("classification") == "UNQUALIFIED_SAN_SUPPLEMENTAL_STAGE_EVIDENCE", "tail classification mismatch")
    need((d.get("candidate"), d.get("host"), d.get("profile")) == (CANDIDATE, HOST, "ASan-UBSan"), "tail identity mismatch")
    need(d.get("profile_pass") is False and d.get("phase_complete") is False and d.get("qualification_status") == "UNQUALIFIED", "tail must remain unqualified")
    need(d.get("original_failure_preserved") is True, "original failure not preserved")
    original = d.get("original_failed_run", {})
    need(original.get("sha256") == ORIGINAL_SHA and pathlib.Path(original.get("path", "")).name == "run.json", "tail original pin mismatch")
    prereq = d.get("native_replay_prerequisite", {})
    need(prereq.get("sha256") == NATIVE_SHA and prereq.get("path") == native_target, "tail native prerequisite mismatch")
    rows = d.get("supplemental_stages")
    need(isinstance(rows, list) and [r.get("name") for r in rows] == list(STAGES), "tail stage inventory mismatch")
    runners = [pathlib.Path(r.get("runner", "")).resolve(strict=True) for r in rows]
    raw_root = runners[0].parent.parent
    need(raw_root == POST_TAIL.parent / "ASan-UBSan" and all(r == raw_root / "canonical-runs" / name for r, name in zip(runners, STAGES)), "tail raw output root differs from reviewed sanitizer attempt")
    for row, name in zip(rows, STAGES):
        rec = row.get("runner_receipt", {})
        need(row.get("passed") is True and rec.get("launcher_exit") == 0 and rec.get("command.exit") == "0" and rec.get("wrapper.exit") == "0" and rec.get("terminal-status.txt") == "COMPLETE", "tail runner failed: " + name)
        need(rec.get("argv.nul") == row.get("argv"), "tail argv mismatch: " + name)
        v = row.get("validation", {})
        if name == "core-supervised-copy-recovery":
            need(v.get("all_registered_present") is True and v.get("failed_or_skipped") is False and v.get("junit_cases") == 4, "nested recovery JUnit mismatch")
        elif name == "P10":
            expected = {"status": "passed", "through": "P10", "selected": 151, "executed": 151, "total_baseline": 151, "not_selected": 0, "diagnostic_only": False}
            need(v.get("rows_valid") is True and v.get("case_count") == 151 and v.get("acceptance") == expected, "P10 mismatch")
        else:
            need(v.get("status") == "passed" and v.get("cleanup_valid") is True and v.get("mounted") is False and set(v.get("result_names", [])) == STORAGE, "bounded-storage evidence mismatch")
        need(row.get("logs", {}).get("sanitizer_findings") == [] and row.get("output_log_scan", {}).get("sanitizer_findings") == [], "tail sanitizer finding: " + name)
    need(d.get("source_closures_rechecked_after") is True, "tail closure audit missing")
    pins = d.get("pins_before", {})
    need(pins.get("provenance_sha256") == PROVENANCE_SHA and pins.get("source_gate_sha256") == TAIL_M06_SHA and pins.get("m08_gate_sha256") == TAIL_M08_SHA and pins.get("runner_sha256") == RUNNER_SHA, "tail frozen source/runner pins mismatch")
    need(pins.get("deployment_receipt_sha256") == DEPLOYMENT_SHA, "tail deployment receipt pin mismatch")
    raw = validate_raw_stage_files(raw_root, d, acceptance_lock)
    return {"path": str(p), "sha256": expected_sha, "stages": list(STAGES), "raw_evidence": raw, "qualification": "UNQUALIFIED"}

def build_proof(inputs_dir, original_path, tail_path, tail_sha, binding_path, binding_sha):
    base = pathlib.Path(inputs_dir).resolve(strict=True)
    pins = read(base / "pins.json")
    def inp(name, digest):
        row = pins.get(name)
        need(isinstance(row, dict) and row.get("sha256") == digest and row.get("target_path") == TARGET_PATHS[name], "input pin/path mismatch: " + name)
        return pinned(base / name, digest, name)

    original_path = pathlib.Path(original_path).resolve(strict=True)
    need(str(original_path) in ORIGINAL_TARGETS and sha(original_path) == ORIGINAL_SHA, "original failed report path/hash mismatch")
    old = read(original_path)
    need(old.get("candidate") == CANDIDATE and old.get("host") == HOST and old.get("status") == "failed" and old.get("phase_complete") is False, "original failed report identity/status mismatch")
    san = old.get("profiles", {}).get("ASan-UBSan", {})
    san_commands = san.get("commands", [])
    need(san.get("status") in ("running", "failed") and [r.get("name") for r in san_commands] == ["inventory", "production-off-startup", "harness", "core"] and [r.get("exit_code") for r in san_commands] == [0, 0, 0, 8], "original ASan stage history mismatch")
    need(old.get("execution_error", {}).get("type") == "RuntimeError" and old.get("execution_error", {}).get("message") == "inner test reports a failed or errored case", "original SAN failure diagnostic mismatch")
    san_harness = san_commands[2]
    need(san_harness.get("reported_harness_count") == 89 and san_harness.get("harness_inventory_valid") is True and san_harness.get("assertions_passed") is True, "original SAN harness mismatch")
    dbg = old["profiles"]["Debug"]
    dc = {r["name"]: r for r in dbg["commands"]}
    debug_names = ["inventory", "production-off-startup", "harness", "core", "core-supervised-copy-recovery", "P10", "bounded-storage-git"]
    need(dbg.get("status") == "passed_needs_crosshost_and_review" and [r.get("name") for r in dbg["commands"]] == debug_names and all(r.get("exit_code") == 0 and r.get("assertions_passed") is True for r in dbg["commands"]), "original Debug stage history mismatch")
    need(dc["harness"].get("reported_harness_count") == 89 and dc["harness"].get("harness_inventory_valid") is True, "original Debug harness mismatch")
    need(dc["core"].get("junit_cases") == 236 and dc["core"].get("all_registered_present") is True and dc["core"].get("failed_or_skipped") is False and dc["core-supervised-copy-recovery"].get("junit_cases") == 4, "original Debug core mismatch")
    debug_p10 = dc["P10"].get("acceptance", {})
    need(debug_p10.get("status") == "passed" and debug_p10.get("through") == "P10" and debug_p10.get("selected") == debug_p10.get("executed") == debug_p10.get("total_baseline") == 151 and debug_p10.get("not_selected") == 0 and debug_p10.get("diagnostic_only") is False and dc["P10"].get("acceptance_rows_valid") is True, "original Debug P10 mismatch")

    san_inv = inp("san-inventory.json", SAN_INV_SHA)
    debug_inv = inp("debug-inventory.json", DEBUG_INV_SHA)
    si, di = read(san_inv), read(debug_inv)
    need(si.get("count") == 239 and di.get("count") == 239 and si.get("names") == di.get("names") and si.get("local_names") == di.get("local_names"), "profile inventories differ")
    all_local = set(si["local_names"])
    protected = all_local - NESTED
    need(len(all_local) == 236 and len(protected) == 232 and NESTED <= all_local, "inventory partition mismatch")
    junit = inp("san-core-protected.xml", JUNIT_SHA)
    cases = list(ET.parse(junit).getroot().iter("testcase"))
    got = [c.get("name") for c in cases]
    failed = {c.get("name") for c in cases if c.find("failure") is not None}
    errors = {c.get("name") for c in cases if c.find("error") is not None}
    skipped = {c.get("name") for c in cases if c.find("skipped") is not None}
    need(len(got) == 232 and len(set(got)) == 232 and set(got) == protected and failed == FAILURES and not errors and not skipped, "original SAN JUnit is not exact 232/227/5/0")

    provenance = inp("provenance.json", PROVENANCE_SHA)
    need(read(provenance).get("candidate") == CANDIDATE, "provenance candidate mismatch")
    native_path = inp("native-replay.json", NATIVE_SHA)
    native = read(native_path)
    names = list(NATIVE_COUNTS)
    need(native.get("classification") == "CANDIDATE_580_FOCUSED_UNQUALIFIED_REPLAY" and native.get("gate_pass") is False and native.get("all_rows_passed") is True and native.get("post_hashes_match_expected") is True and native.get("planned_rows") == names, "native replay summary mismatch")
    nr = native.get("results", [])
    need([r.get("name") for r in nr] == names, "native row inventory mismatch")
    for r in nr:
        n = r["name"]
        count = r.get("identity_tls_unittest", {}).get("method_count") if n == "identity-tls" else r.get("method_count")
        need(r.get("row_passed") is True and r.get("exit_code") == 0 and r.get("command_exit") == "0" and r.get("wrapper_exit") == "0" and r.get("argv_receipt_matches") is True and count == NATIVE_COUNTS[n] and r.get("sanitizer_findings") == [], "native row mismatch: " + n)

    cabinet_path = inp("cabinet-replay.json", CABINET_SHA)
    cabinet = read(cabinet_path)
    need(cabinet.get("classification") == "CANDIDATE_580_CABINET_FOCUSED_UNQUALIFIED" and cabinet.get("gate_pass") is False and cabinet.get("all_rows_passed") is True and cabinet.get("post_pins_match") is True and (cabinet.get("host"), cabinet.get("candidate")) == (HOST, CANDIDATE), "cabinet replay summary mismatch")
    rows = cabinet.get("rows", [])
    expected_rows = [(profile, method) for profile in ("Debug", "ASan-UBSan") for method in CABINET]
    need([(r.get("profile"), r.get("method")) for r in rows] == expected_rows, "cabinet row inventory mismatch")
    for r in rows:
        need(r.get("row_passed") is True and r.get("runner_exit") == 0 and r.get("command_exit") == "0" and r.get("wrapper_exit") == "0" and r.get("argv_receipt_matches") is True and r.get("method_count") == r.get("expected_method_count") == 1 and r.get("method_name_seen") is True and not r.get("skipped") and not r.get("unittest_failure_or_error") and r.get("sanitizer_findings") == [], "cabinet method mismatch: " + r.get("method", "?"))

    audit = read(inp("post-native-integrity.json", INTEGRITY_SHA))
    need(audit.get("status") == "FOCUSED_PREPOST_ARTIFACT_AUDIT_PASSED" and audit.get("candidate") == CANDIDATE and audit.get("host") == HOST and audit.get("original_failed_run_sha256") == ORIGINAL_SHA and audit.get("source_count") == 1222, "source/runtime audit mismatch")
    for profile, count in (("Debug", 314), ("ASan-UBSan", 304)):
        row = audit.get("profiles", {}).get(profile, {})
        metadata_sha = "2e203e50db6e8a8e54c91596b2f4307b84f42d0cde744ece20c31cc41f8c9634" if profile == "Debug" else "1b3d0472d5b5c17002e6c4bd066e9a591eeafd68c910beefdedeb7df324b0f54"
        metadata = row.get("mutable_relocated_ctest_metadata_snapshot", {})
        need(row.get("status") == "artifact_hashes_passed" and row.get("files_checked") == count and row.get("qualification") == "NOT_A_TEST_PASS" and metadata == {"CTestTestfile.cmake": metadata_sha}, "artifact audit mismatch: " + profile)

    acceptance_lock = ROOT / "deployed-01/source/acceptance.lock.json"
    expected_lock_sha = read(provenance).get("source_sha256", {}).get("acceptance.lock.json")
    need(isinstance(expected_lock_sha, str) and sha(acceptance_lock) == expected_lock_sha, "locked acceptance inventory source mismatch")
    tail = validate_tail(tail_path, tail_sha, pins["native-replay.json"]["target_path"], acceptance_lock)
    need(binding_sha == BINDING_SHA, "binding is not the reviewed post-tail result")
    binding = pinned(binding_path, binding_sha, "post-tail binding")
    need(binding == POST_TAIL_BINDING, "post-tail binding path mismatch")
    bind = read(binding)
    need(bind.get("schema") == 1 and bind.get("classification") == "PRIMARY_POST_TAIL_ARTIFACT_BINDING_UNQUALIFIED", "post-tail binding kind mismatch")
    need((bind.get("candidate"), bind.get("host"), bind.get("qualification"), bind.get("gate_pass"), bind.get("phase_complete")) == (CANDIDATE, HOST, "UNQUALIFIED", False, False), "post-tail binding identity/status mismatch")
    tail_bind = bind.get("tail", {})
    need(pathlib.Path(tail_path).resolve() == POST_TAIL and tail_bind == {"path": str(POST_TAIL), "sha256": tail_sha}, "post-tail binding tail mismatch")
    audit_bind = bind.get("audit", {})
    need(audit_bind.get("path") == str(POST_TAIL_AUDIT) and pathlib.Path(audit_bind.get("path", "")).resolve() == POST_TAIL_AUDIT, "post-tail audit path mismatch")
    need(audit_bind.get("sha256") == POST_TAIL_AUDIT_SHA, "post-tail audit differs from reviewed hash")
    audit_path = pinned(audit_bind["path"], audit_bind.get("sha256", ""), "post-tail audit")
    audit = read(audit_path)
    need(audit.get("status") == "FOCUSED_PREPOST_ARTIFACT_AUDIT_PASSED" and audit.get("candidate") == CANDIDATE and audit.get("host") == HOST and audit.get("source_count") == 1222, "post-tail artifact audit mismatch")
    for profile, count in (("Debug", 314), ("ASan-UBSan", 304)):
        need(audit.get("profiles", {}).get(profile, {}).get("status") == "artifact_hashes_passed" and audit["profiles"][profile].get("files_checked") == count and audit["profiles"][profile].get("qualification") == "NOT_A_TEST_PASS", "post-tail artifact profile mismatch: " + profile)
    expected_argv = ["env", "PYTHONDONTWRITEBYTECODE=1", "python3", "-B", str(EVIDENCE_ROOT / "sb16-2/control/audit-frozen-runtime-01.py"), "--output", str(POST_TAIL_AUDIT)]
    runner = bind.get("audit_runner", {})
    need(runner.get("path") == str(POST_TAIL_AUDIT_RUNNER) and pathlib.Path(runner.get("path", "")).resolve() == POST_TAIL_AUDIT_RUNNER, "post-tail audit runner path mismatch")
    need(runner.get("argv") == expected_argv and runner.get("command_exit") == 0 and runner.get("wrapper_exit") == 0 and runner.get("terminal_status") == "COMPLETE", "post-tail audit runner binding mismatch")
    for leaf, expected in (("command.exit", "0"), ("wrapper.exit", "0"), ("terminal-status.txt", "COMPLETE")):
        need((POST_TAIL_AUDIT_RUNNER / leaf).read_text(encoding="utf-8").strip() == expected, "raw post-tail audit runner receipt mismatch: " + leaf)
    raw_argv = [x.decode() for x in (POST_TAIL_AUDIT_RUNNER / "argv.nul").read_bytes().split(bytes([0]))[:-1]]
    need(raw_argv == expected_argv, "raw post-tail audit argv mismatch")
    passed = set(got) - failed
    need(len(passed | FAILURES | NESTED) == 236 and passed.isdisjoint(FAILURES | NESTED) and FAILURES.isdisjoint(NESTED), "derived coverage is not exact and disjoint")
    return {
        "schema": 1, "proof_kind": "additive_local_evidence_reconciliation", "qualification": "UNQUALIFIED",
        "gate_pass": False, "phase_complete": False, "candidate": CANDIDATE, "host": HOST,
        "original_failed_run": {"path": str(original_path), "sha256": ORIGINAL_SHA, "status": "failed", "original_san_core_exit": 8, "protected_cases": 232, "passed": 227, "failed_names": sorted(FAILURES), "skipped": 0},
        "derived_coverage": {"expected_local_names": 236, "original_san_passed_names": 227, "named_native_reruns": sorted(FAILURES), "self_managed_nested_names": sorted(NESTED), "unique_covered_names": 236},
        "evidence_sha256": {"san_inventory": sha(san_inv), "debug_inventory": sha(debug_inv), "san_core_junit": sha(junit), "provenance": sha(provenance), "native_replay": sha(native_path), "cabinet_replay": sha(cabinet_path), "post_native_integrity": sha(base / "post-native-integrity.json"), "post_tail_binding": sha(binding), "post_tail_integrity": sha(audit_path)},
        "validation_status": "COMBINED_EVIDENCE_PASSED",
        "profile_coverage": {"Debug": {"core": 236, "harness": 89, "locked_P10": 151, "bounded_storage_git": 6},
                             "ASan-UBSan": {"core": 236, "harness": 89, "locked_P10": 151, "bounded_storage_git": 6}},
        "cabinet_supplements": {"Debug": 3, "ASan-UBSan": 3},
        "tail": tail,
        "limitations": ["The preserved original ASan-UBSan CTest failed with exit 8.", "This proof combines separate original, named-rerun, and supplemental evidence.", "This is not a local gate receipt and does not qualify M08."]
    }

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inputs-dir", required=True, type=pathlib.Path)
    ap.add_argument("--original-run", required=True, type=pathlib.Path)
    ap.add_argument("--tail-result", required=True, type=pathlib.Path)
    ap.add_argument("--tail-sha256", required=True)
    ap.add_argument("--post-tail-binding", required=True, type=pathlib.Path)
    ap.add_argument("--post-tail-binding-sha256", required=True)
    ap.add_argument("--output", required=True, type=pathlib.Path)
    a = ap.parse_args(argv)
    out = a.output.resolve()
    need(ROOT in out.parents and not out.exists(), "output must be a new file under candidate artifact root")
    need(out.parent.is_dir(), "output parent must already exist")
    proof = build_proof(a.inputs_dir, a.original_run, a.tail_result, a.tail_sha256, a.post_tail_binding, a.post_tail_binding_sha256)
    tmp = out.with_name(out.name + ".tmp")
    need(not tmp.exists(), "proof temporary output already exists")
    tmp.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(out)
    print(json.dumps({"proof_path": str(out), "qualification": "UNQUALIFIED", "gate_pass": False, "covered_names": 236}, sort_keys=True))
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("REFUSED/INCOMPLETE: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
