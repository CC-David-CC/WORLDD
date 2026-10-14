#!/usr/bin/env python3
"""Run inherited M05 cross-host regressions against one explicitly pinned M06 deployment.

This tool is an ignored operational overlay. It does not alter deployed source or
convert these focused results into a normal local-gate or M06 completion report.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import time

CANDIDATE = "4f241a95d98e28b037b17ad1826f6816c2cc17ad"
PROFILES = ("Debug", "ASan-UBSan")
SUITES = (("world_remote_keys", 5), ("world_topology", 1),
          ("session_crosshost_recovery", 2))
ADDRESSES = {"sb16": "10.0.7.49", "sb16-2": "10.0.7.94"}
SANITIZER = re.compile(
    r"ERROR: (AddressSanitizer|LeakSanitizer)|"
    r"SUMMARY: (AddressSanitizer|UndefinedBehaviorSanitizer)|runtime error:"
)

VERIFY_HOST = r'''
import hashlib, importlib.util, json, pathlib, re, socket, sys
host, deployment, run_path, run_pin, candidate, provenance_pin, receipt_pin = sys.argv[1:]
root = pathlib.Path(deployment).resolve()
def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            h.update(block)
    return h.hexdigest()
actual_host = socket.gethostname().split(".")[0]
if actual_host != host:
    raise RuntimeError("host identity mismatch: " + actual_host)
prov_path, receipt_path = root / "provenance.json", root / "deployment-receipt.json"
if digest(prov_path) != provenance_pin or digest(receipt_path) != receipt_pin:
    raise RuntimeError("deployment receipt/provenance pin mismatch")
provenance, receipt = json.loads(prov_path.read_text()), json.loads(receipt_path.read_text())
if (provenance.get("candidate") != candidate or receipt.get("candidate") != candidate
        or receipt.get("status") != "verified" or receipt.get("host") != host):
    raise RuntimeError("deployment is not the pinned verified candidate for this host")
source = root / "source"
source_map = provenance.get("source_sha256")
if (not isinstance(source_map, dict) or not source_map
        or receipt.get("source_sha256") != source_map):
    raise RuntimeError("complete deployed source hash map/receipt match is missing")
for name, expected in source_map.items():
    rel = pathlib.Path(name)
    path = source / rel
    if rel.is_absolute() or ".." in rel.parts or not path.is_file() or digest(path) != expected:
        raise RuntimeError("deployed source pin mismatch: " + name)
checker_name = "tools/m06_deployed_gate.py"
checker = source / checker_name
if source_map.get(checker_name) != digest(checker):
    raise RuntimeError("deployed gate checker is not source-pinned")
spec = importlib.util.spec_from_file_location("pinned_m06_gate", checker)
gate = importlib.util.module_from_spec(spec); spec.loader.exec_module(gate)
run = pathlib.Path(run_path).resolve()
if root / "evidence" not in run.parents or digest(run) != run_pin:
    raise RuntimeError("local gate run receipt path/hash mismatch")
local = gate._validate_local_run(run, host, candidate)
report = json.loads(run.read_text())
for key in ("source_sha256_rechecked",):
    if report.get(key) is not True:
        raise RuntimeError("local gate post-check failed: " + key)
for key in ("profile_artifacts_rechecked", "production_off_rechecked",
            "metadata_rechecked", "storage_evidence_rechecked", "runtime_rechecked"):
    values = report.get(key)
    if not isinstance(values, dict) or set(values) != set(gate.PROFILES) or not all(values.values()):
        raise RuntimeError("local gate post-check failed: " + key)
for profile in gate.PROFILES:
    row = report["profiles"][profile]
    commands = {x.get("name"): x for x in row.get("commands", [])}
    required = {"inventory", "production-off-startup", "harness", "core", "P10", "bounded-storage-git"}
    if not required.issubset(commands):
        raise RuntimeError(profile + " required local gate stages are absent")
    for label in required:
        item = commands[label]
        if item.get("exit_code") != 0 or item.get("assertions_passed") is not True:
            raise RuntimeError(profile + " local gate stage failed: " + label)
    if commands["harness"].get("expected_51") is not True:
        raise RuntimeError(profile + " harness count is not 51")
    core = commands["core"]
    if core.get("junit_cases") != 164 or core.get("all_registered_present") is not True or core.get("failed_or_skipped") is not False:
        raise RuntimeError(profile + " local CTest is not exactly 164 passing local registrations")
    acceptance = commands["P10"].get("acceptance", {})
    if (acceptance.get("status") != "passed" or acceptance.get("through") != "P10"
            or acceptance.get("selected") != 151 or acceptance.get("executed") != 151
            or acceptance.get("total_baseline") != 151 or acceptance.get("not_selected") != 0
            or bool(acceptance.get("diagnostic_only"))):
        raise RuntimeError(profile + " P10 receipt is incomplete")
    if row.get("sanitizer_findings") not in ([], None):
        raise RuntimeError(profile + " local sanitizer finding exists")
    gate._validate_crossstage_profile_artifacts(root, provenance, host, profile, run, run_pin)
source_digest = hashlib.sha256(json.dumps(source_map, sort_keys=True).encode()).hexdigest()
print(json.dumps({"host": host, "candidate": candidate, "source_digest": source_digest,
    "provenance_sha256": provenance_pin, "deployment_receipt_sha256": receipt_pin,
    "local_run_sha256": run_pin, "profile_status": {p: report["profiles"][p]["status"] for p in gate.PROFILES},
    "artifact_profiles": sorted(local["inventories"]),
    "sanitizer_pattern": gate.SAN_RE.pattern,
    "inner_skip_pattern": gate.INNER_SKIP_RE.pattern}, sort_keys=True))
'''


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def valid_pin(value, label):
    if not re.fullmatch(r"[0-9a-f]{64}", value or ""):
        raise ValueError(label + " must be a full lowercase SHA-256")


def core_module_path(source, suite):
    if suite not in ("world_remote_keys", "world_topology"):
        raise ValueError("unknown core cross-host suite")
    return source / ("tests/core/test_" + suite + ".py")


def peer_pythonpath(deployment, profile):
    # The remote launcher imports harness from the deployed source tree.
    # Keep generated protobuf bindings first, then expose that exact peer source.
    return str(deployment / "build" / profile / "python") + ":" + str(deployment / "source")


def validate_remote_paths(deployment, run_json):
    if not deployment.is_absolute() or not run_json.is_absolute():
        raise ValueError("peer deployment and receipt paths must be absolute; the peer validates them remotely")


def static_self_check():
    source = Path("/candidate/source")
    assert core_module_path(source, "world_remote_keys") == source / "tests/core/test_world_remote_keys.py"
    assert core_module_path(source, "world_topology") == source / "tests/core/test_world_topology.py"
    peer = Path("/peer/deployment")
    assert peer_pythonpath(peer, "Debug") == "/peer/deployment/build/Debug/python:/peer/deployment/source"
    missing_peer_deployment = Path("/peer-only/not-mounted/deployment")
    missing_peer_receipt = missing_peer_deployment / "evidence/attempt/run.json"
    validate_remote_paths(missing_peer_deployment, missing_peer_receipt)
    try:
        validate_remote_paths(Path("relative-deployment"), missing_peer_receipt)
    except ValueError:
        pass
    else:
        raise AssertionError("relative peer path was accepted")
    return {"status": "passed", "checks": [
        "core module path construction for both suites",
        "peer Python path orders generated bindings before deployed source imports",
        "absolute peer paths accepted without local filesystem resolution",
        "relative peer path rejected"]}


def verify_host(host, deployment, run_path, run_pin, provenance_pin, receipt_pin):
    argv = ["/usr/bin/python3", "-c", VERIFY_HOST, host, str(deployment), str(run_path),
            run_pin, CANDIDATE, provenance_pin, receipt_pin]
    if host == socket.gethostname().split(".")[0]:
        result = subprocess.run(argv, text=True, capture_output=True, timeout=180)
    else:
        result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                                 host, shlex.join(argv)], text=True, capture_output=True, timeout=240)
    if result.returncode:
        raise RuntimeError(host + " preflight failed: " + result.stderr[-4000:])
    data = json.loads(result.stdout)
    if data.get("host") != host or data.get("candidate") != CANDIDATE:
        raise RuntimeError(host + " returned a mismatched verification receipt")
    return data


def validate_args(a, host):
    a.deployment = a.deployment.resolve(strict=True)
    a.local_run_json = a.local_run_json.resolve(strict=True)
    validate_remote_paths(a.peer_deployment, a.peer_run_json)
    a.output = a.output.resolve(strict=False)
    if a.candidate != CANDIDATE:
        raise ValueError("only the reviewed 4f241a95 M06 candidate is supported")
    if host not in ADDRESSES:
        raise RuntimeError("runner is allowed only on sb16 or sb16-2")
    peer = "sb16-2" if host == "sb16" else "sb16"
    if a.peer != peer:
        raise ValueError("peer must be the other qualification host")
    for value, name in ((a.local_run_sha256, "local run SHA"),
                        (a.peer_run_sha256, "peer run SHA"),
                        (a.provenance_sha256, "local provenance SHA"),
                        (a.peer_provenance_sha256, "peer provenance SHA"),
                        (a.deployment_receipt_sha256, "local deployment receipt SHA"),
                        (a.peer_deployment_receipt_sha256, "peer deployment receipt SHA")):
        valid_pin(value, name)
    for path, label in ((a.deployment, "deployment"), (a.peer_deployment, "peer deployment"),
                        (a.local_run_json, "local run receipt"), (a.peer_run_json, "peer run receipt"),
                        (a.output, "output")):
        if not path.is_absolute():
            raise ValueError(label + " path must be absolute")
    root = Path("/home/dflanag3/spatial-qualification/m06-inherited-crosshost-4f241-20261013").resolve()
    output = a.output.resolve()
    if root not in output.parents or output.exists():
        raise ValueError("output must be a fresh path beneath " + str(root))
    if a.deployment / "evidence" not in a.local_run_json.parents:
        raise ValueError("local run receipt must be below the local deployment evidence directory")
    # Peer paths are checked after SSH on the peer. Never resolve them on this host.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--local-run-json", type=Path, required=True)
    parser.add_argument("--local-run-sha256", required=True)
    parser.add_argument("--provenance-sha256", required=True)
    parser.add_argument("--deployment-receipt-sha256", required=True)
    parser.add_argument("--peer", required=True, choices=("sb16", "sb16-2"))
    parser.add_argument("--peer-deployment", type=Path, required=True)
    parser.add_argument("--peer-local-run-json", dest="peer_run_json", type=Path, required=True)
    parser.add_argument("--peer-local-run-sha256", dest="peer_run_sha256", required=True)
    parser.add_argument("--peer-provenance-sha256", required=True)
    parser.add_argument("--peer-deployment-receipt-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    a = parser.parse_args()
    host = socket.gethostname().split(".")[0]
    validate_args(a, host)
    peer = a.peer
    local = verify_host(host, a.deployment, a.local_run_json, a.local_run_sha256,
                        a.provenance_sha256, a.deployment_receipt_sha256)
    remote = verify_host(peer, a.peer_deployment, a.peer_run_json, a.peer_run_sha256,
                         a.peer_provenance_sha256, a.peer_deployment_receipt_sha256)
    if local["source_digest"] != remote["source_digest"]:
        raise RuntimeError("hosts do not have identical deployed source closures")
    if local["artifact_profiles"] != remote["artifact_profiles"]:
        raise RuntimeError("host artifact profile closures differ")
    if (local.get("sanitizer_pattern") != remote.get("sanitizer_pattern")
            or local.get("inner_skip_pattern") != remote.get("inner_skip_pattern")):
        raise RuntimeError("source-pinned diagnostic checks differ between hosts")
    sanitizer_re = re.compile(local["sanitizer_pattern"])
    inner_skip_re = re.compile(local["inner_skip_pattern"], re.MULTILINE)
    import fcntl
    lock_path = Path("/run/user") / str(os.getuid()) / "spatial-m06-gate.lock"
    lock = lock_path.open("a")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError("local M06 gate lock is busy") from error
    os.umask(0o077)
    a.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    record = {"schema": 1, "status": "running", "phase_complete": False,
        "candidate": CANDIDATE, "host": host, "peer": peer,
        "peer_address": ADDRESSES[peer], "local_address": ADDRESSES[host],
        "deployment": str(a.deployment), "peer_deployment": str(a.peer_deployment),
        "local_run": local, "peer_run": remote, "results": [],
        "selected_suites": [{"name": n, "expected_methods": c} for n, c in SUITES],
        "profiles": list(PROFILES), "runner_sha256": sha(Path(__file__)),
        "started_utc": time.time()}
    (a.output / "run.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    try:
        for profile in PROFILES:
            build = a.deployment / "build" / profile
            source = a.deployment / "source"
            wrapper = json.loads((a.deployment / "provenance.json").read_text())["runtime_manifest"]["hosts"][host][profile]["xace_wrapper"]["path"]
            for suite, expected in SUITES:
                target = a.output / profile / suite
                target.mkdir(parents=True, exist_ok=False)
                env = os.environ.copy()
                env.update(PYTHONPATH=str(build / "python") + ":" + str(source),
                    SPATIAL_TEST_XSERVER=str(wrapper),
                    SPATIAL_SECURITY_EVIDENCE=str(target / "services"),
                    SPATIAL_TEST_XSERVER_LOG=str(target / "xserver.log"),
                    ASAN_OPTIONS="detect_leaks=1:abort_on_error=1",
                    UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1")
                for key in ("DISPLAY", "SPATIAL_TEST_DISPLAY", "SPATIAL_SECURITY_TEST_CORE_ONLY",
                            "LD_LIBRARY_PATH", "__EGL_VENDOR_LIBRARY_FILENAMES"):
                    env.pop(key, None)
                peer_env = {"SPATIAL_REMOTE_HOST": peer,
                    "SPATIAL_WORLD_ADDRESS": ADDRESSES[host],
                    "SPATIAL_REMOTE_PYTHONPATH": peer_pythonpath(a.peer_deployment, profile),
                    "SPATIAL_REMOTE_WITNESS": str(a.peer_deployment / "source" / "tests/core/world_tls_witness.py"),
                    "SPATIAL_REMOTE_KEY_BINARY": str(a.peer_deployment / "build" / profile / "spatial-key")}
                if suite == "session_crosshost_recovery":
                    command = ["/usr/bin/python3", str(source / "tools/with_resource_delegate.py"), "--native", "--",
                        "/usr/bin/python3", str(source / "tools/with_adapter_resources.py"), "--", "env",
                        *[key + "=" + value for key, value in peer_env.items()],
                        "/usr/bin/python3", str(source / "tests/native/test_session_crosshost_recovery.py"),
                        str(source), str(build / "spatial-authority-launch-probe"),
                        str(build / "spatial-security-auth"), str(build / "identityd"),
                        str(build / "spatial-issuer-client")]
                else:
                    env.update(peer_env)
                    command = ["/usr/bin/python3", str(source / "tools/with_test_display.py"), "--",
                        "/usr/bin/python3", str(core_module_path(source, suite)),
                        str(build / "identityd"), str(build / "spatial-issuer-client"), str(build / "worldd")]
                started = time.monotonic()
                log_path = target / "output.log"
                timeout_cleanup = None
                proc = None
                try:
                    with log_path.open("wb") as log:
                        proc = subprocess.Popen(command, cwd=source, env=env, stdout=log,
                                                stderr=subprocess.STDOUT, start_new_session=True)
                        try:
                            exit_code = proc.wait(timeout=300)
                        except subprocess.TimeoutExpired:
                            import signal
                            group = proc.pid
                            try:
                                os.killpg(group, signal.SIGTERM)
                            except ProcessLookupError:
                                pass
                            try:
                                proc.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                try:
                                    os.killpg(group, signal.SIGKILL)
                                except ProcessLookupError:
                                    pass
                                proc.wait(timeout=10)
                            try:
                                os.killpg(group, 0)
                                group_gone = False
                            except ProcessLookupError:
                                group_gone = True
                            if not group_gone:
                                try:
                                    os.killpg(group, signal.SIGKILL)
                                except ProcessLookupError:
                                    pass
                                try:
                                    os.killpg(group, 0)
                                    group_gone = False
                                except ProcessLookupError:
                                    group_gone = True
                            timeout_cleanup = {
                                "status": "incomplete_remote_cleanup_unproven",
                                "local_process_group_gone": group_gone,
                                "remote_fixture_cleanup_proven": False,
                                "note": "A timeout interrupts unittest teardown; preserve evidence and require explicit recovery review."
                            }
                            row = {"profile": profile, "suite": suite, "expected_methods": expected,
                                "exit_code": proc.returncode, "methods": None, "summary": None,
                                "skipped_match": None, "command": command, "peer_environment": peer_env,
                                "seconds": time.monotonic() - started,
                                "stdout_stderr": str(log_path), "timeout_cleanup": timeout_cleanup}
                            record["results"].append(row)
                            record["status"] = "failed_or_incomplete"
                            record["error"] = "TimeoutError: suite exceeded 300 seconds; remote fixture cleanup is unproven"
                            (a.output / "run.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
                            raise TimeoutError("suite exceeded 300 seconds; remote fixture cleanup is unproven")
                except BaseException:
                    if proc is not None and proc.poll() is None:
                        proc.terminate()
                        try:
                            proc.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait(timeout=10)
                    raise
                output = log_path.read_text(errors="replace")
                summary = re.search(r"Ran (\d+) tests? in [^\n]*\n\n(OK(?: \(skipped=\d+\))?|FAILED(?: \([^\n]*\))?)", output)
                skipped = bool(inner_skip_re.search(output))
                row = {"profile": profile, "suite": suite, "expected_methods": expected,
                    "exit_code": exit_code, "methods": int(summary.group(1)) if summary else None,
                    "summary": summary.group(2) if summary else None, "skipped_match": skipped,
                    "command": command, "peer_environment": peer_env,
                    "seconds": time.monotonic() - started, "stdout_stderr": str(log_path),
                    "timeout_cleanup": timeout_cleanup}
                record["results"].append(row)
                (a.output / "run.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
                if exit_code != 0 or not summary or int(summary.group(1)) != expected or summary.group(2) != "OK" or skipped:
                    raise RuntimeError("suite failed, skipped cases, or method count mismatch: " + profile + "/" + suite)
                for path in target.rglob("*.log"):
                    if sanitizer_re.search(path.read_text(errors="replace")):
                        raise RuntimeError("sanitizer diagnostic in " + str(path))
                print(profile, suite, "passed", expected, flush=True)
        after_local = verify_host(host, a.deployment, a.local_run_json, a.local_run_sha256,
                                  a.provenance_sha256, a.deployment_receipt_sha256)
        after_remote = verify_host(peer, a.peer_deployment, a.peer_run_json, a.peer_run_sha256,
                                   a.peer_provenance_sha256, a.peer_deployment_receipt_sha256)
        if local != after_local or remote != after_remote:
            raise RuntimeError("pre/post deployment, source, artifact, or local-gate pins changed")
        record["postchecks"] = {"local": after_local, "peer": after_remote}
        record["status"] = "focused_crosshost_cases_passed_needs_primary_review"
    except BaseException as error:
        record["status"] = "failed_or_incomplete"
        record["error"] = repr(error)
        raise
    finally:
        record["ended_utc"] = time.time()
        record["artifacts"] = {str(p.relative_to(a.output)): sha(p) for p in a.output.rglob("*")
                               if p.is_file() and p != a.output / "run.json"}
        (a.output / "run.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-check"]:
        print(json.dumps(static_self_check(), sort_keys=True))
        raise SystemExit(0)
    main()
