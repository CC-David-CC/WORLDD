#!/usr/bin/env python3
"""M07 additions to the reviewed artifact-only M06 qualification workflow."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import uuid

HERE = Path(__file__).resolve()
M06_PATH = HERE.with_name("m06_deployed_gate.py")
SPEC = importlib.util.spec_from_file_location("m06_deployed_gate", M06_PATH)
m06 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m06)

M07_REQUIRED = frozenset({
    "copy-tree-worker", "copy-finalize-worker", "copy-worker-production",
    "workspace-copy-journal", "workspace-copy-journal-migration", "workspace-execution-v7-migration",
    "identity-participant-domain-addition", "workspace-presentation-store",
    "workspace-presentation-projection", "workspace-presentation-actor",
    "workspace-presentation-shared-representation", "workspace-git",
    "workspace-copy-revocation-denial",
    "workspace-copy-journal-observer",
    "workspace-copy-engine", "workspace-copy-absolute-deadline", "workspace-copy-actor-revocation",
    "workspace-copy-lost-publication-ack", "workspace-copy-hostile-git",
    "workspace-copy-worker-death-recovery", "workspace-geometry-native-crosshost",
    "workspace-copy-fixture-profiles", "workspace-copy-fault-relay-timing",
    "workspace-copy-fault-evidence-durability",
    "workspace-authorization-fence", "workspace-authorization-fence-scoped",
    "workspace-authorization-fence-registry", "workspace-authorization-fence-credential",
    "workspace-authorization-fence-no-authority", "workspace-authorization-fence-bootstrap",
})
M07_DEDICATED = frozenset(m06.DEDICATED_CROSSHOST | {"workspace-geometry-native-crosshost"})
GEOMETRY_ENV = (
    "SPATIAL_GEOMETRY_EVIDENCE", "SPATIAL_GEOMETRY_OBSERVER_HOST",
    "SPATIAL_GEOMETRY_AUTHORITY_ADDRESS", "SPATIAL_GEOMETRY_REMOTE_SOURCE",
    "SPATIAL_GEOMETRY_REMOTE_BIN", "SPATIAL_GEOMETRY_REMOTE_PYTHON",
    "SPATIAL_GEOMETRY_REMOTE_XSERVER", "SPATIAL_GEOMETRY_REMOTE_EVIDENCE",
)
ARTIFACT_TARGETS = (
    "workspace-copy-engine-fixture", "workspace-copy-fault-fixture",
    "workspace-execution-v7-migration-artifacts", "identity-participant-domain-addition-artifacts",
    "workspace-presentation-actor-fixture", "workspace-presentation-shared-representation-fixture",
    "workspace-geometry-native-crosshost-fixture",
)

def _read(path, label):
    return json.loads(Path(path).read_text())

def audit_inventory(inventory):
    tests = inventory if isinstance(inventory, list) else inventory.get("tests", [])
    rows = {row.get("name"): row for row in tests}
    required = set(m06.REQUIRED) | set(M07_REQUIRED)
    missing = sorted(required - set(rows))
    disabled = sorted(name for name, row in rows.items()
                      if name in required and any(prop.get("name") == "DISABLED"
                         and str(prop.get("value")).upper() in ("1", "ON", "TRUE")
                         for prop in row.get("properties", [])))
    return dict(required=sorted(required), missing=missing, disabled=disabled,
                registrations={name: ("missing" if name not in rows else
                    "disabled" if name in disabled else "registered")
                    for name in sorted(required)})

def _validate_configure_map(path):
    value = _read(path, "configure environment")
    if not isinstance(value, dict) or set(value) != set(GEOMETRY_ENV):
        raise RuntimeError("M07 configure-env map must contain all eight geometry keys")
    if any(not isinstance(value[key], str) or not value[key] for key in GEOMETRY_ENV):
        raise RuntimeError("M07 configure-env values must be nonempty strings")
    return value

def prepare(a):
    if not a.build_paths or not a.configure_env:
        raise RuntimeError("M07 prepare requires explicit build-path and configure-env maps")
    configure_env = _validate_configure_map(a.configure_env)
    if not a.attempt:
        a.attempt = f"m07-{a.candidate[:12]}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}"
    a.artifact_targets = ARTIFACT_TARGETS
    m06.prepare(a)
    out = a.output.resolve() / a.attempt
    record = _read(out / "provenance.json", "M07 build provenance")
    profiles = {}
    for profile in m06.PROFILES:
        inventory = record["profiles"][profile]["inventory"]
        audit = audit_inventory(inventory)
        profiles[profile] = audit
        audit["unrun"] = sorted(M07_REQUIRED)
    result = dict(schema=1, status="ready" if all(not row["missing"] and not row["disabled"] for row in profiles.values())
                  else "blocked", candidate=record["candidate"], profiles=profiles,
                  configure_env_sha256=hashlib.sha256(json.dumps(configure_env, sort_keys=True).encode()).hexdigest(),
                  artifact_targets=list(ARTIFACT_TARGETS), qualification="UNQUALIFIED")
    m06.write_json(out / "m07-inventory-preflight.json", result)
    if result["status"] != "ready":
        raise RuntimeError("M07 inventory is missing or disables required registrations; see m07-inventory-preflight.json")
    return 0

def run_local(a):
    ns = argparse.Namespace(
        deployment=a.deployment, attempt=a.attempt, native_controller=a.native_controller,
        real_world=a.real_world, recovery=a.recovery, host_session=a.host_session, held_input=a.held_input,
        required_tests=sorted(M07_REQUIRED), dedicated_crosshost=sorted(M07_DEDICATED),
    )
    return m06.gate(ns)

def _role_map(path, local_host, direction):
    roles = _read(path, "workspace geometry roles")
    if not isinstance(roles, dict) or set(roles) != set(GEOMETRY_ENV):
        raise RuntimeError("role map must contain exactly the eight geometry variables")
    if any(not isinstance(roles[name], str) or not roles[name] for name in GEOMETRY_ENV):
        raise RuntimeError("all geometry role values must be nonempty")
    source, observer = direction.split("-to-")
    if local_host != source or roles["SPATIAL_GEOMETRY_OBSERVER_HOST"] != observer:
        raise RuntimeError("runner host and observer do not match the requested authority direction")
    for key in ("SPATIAL_GEOMETRY_EVIDENCE", "SPATIAL_GEOMETRY_REMOTE_SOURCE", "SPATIAL_GEOMETRY_REMOTE_BIN",
                "SPATIAL_GEOMETRY_REMOTE_PYTHON", "SPATIAL_GEOMETRY_REMOTE_XSERVER",
                "SPATIAL_GEOMETRY_REMOTE_EVIDENCE"):
        if not Path(roles[key]).is_absolute():
            raise RuntimeError(key + " must be an absolute remote path")
    return roles

def _remote_profile_pin(host, deployment, candidate, profile, provenance_sha256, receipt_sha256, roles):
    row = dict(path=str(deployment), provenance_sha256=provenance_sha256,
               deployment_receipt_sha256=receipt_sha256)
    m06._validate_remote_deployment(host, row, candidate)
    remote_provenance = str(deployment / "provenance.json")
    remote_receipt = str(deployment / "deployment-receipt.json")
    prov_hash = m06._run_on_host(host, ["sha256sum", remote_provenance]).decode().split()[0]
    rec_hash = m06._run_on_host(host, ["sha256sum", remote_receipt]).decode().split()[0]
    if prov_hash != provenance_sha256 or rec_hash != receipt_sha256:
        raise RuntimeError("observer deployment receipt hashes differ from the pinned matrix")
    provenance = json.loads(m06._run_on_host(host, ["cat", remote_provenance]).decode())
    expected_build = str(deployment / "build" / profile)
    expected_runtime = provenance["runtime_manifest"]["hosts"][host][profile]["xace_wrapper"]
    expected = {
        "SPATIAL_GEOMETRY_REMOTE_SOURCE": str(deployment / "source"),
        "SPATIAL_GEOMETRY_REMOTE_BIN": expected_build,
        "SPATIAL_GEOMETRY_REMOTE_PYTHON": str(Path(expected_build) / "python"),
        "SPATIAL_GEOMETRY_REMOTE_XSERVER": expected_runtime["path"],
    }
    for key, value in expected.items():
        if roles[key] != value:
            raise RuntimeError("observer role does not match the pinned deployment: " + key)
    profile_row = provenance["profiles"][profile]
    check_script = (
        "import hashlib,json,pathlib,sys; r=pathlib.Path(sys.argv[1]); p=json.loads((r/'provenance.json').read_text()); "
        "q=p['profiles'][sys.argv[2]]; b=r/'build'/sys.argv[2]; "
        "bad=[n for n,h in q['executables'].items() if hashlib.sha256((b/n).read_bytes()).hexdigest()!=h]; "
        "bad += [n for n,h in q['runtime_files'].items() if n not in q['ctest_metadata'] and hashlib.sha256((b/n).read_bytes()).hexdigest()!=h]; "
        "print(json.dumps({'bad':bad})); sys.exit(bool(bad))")
    checked = json.loads(m06._run_on_host(host, ["python3", "-c", check_script, str(deployment), profile]).decode())
    if checked.get("bad"):
        raise RuntimeError("observer profile binaries/runtime differ from provenance: " + repr(checked["bad"][:8]))
    actual_xserver_hash = m06._run_on_host(host, ["sha256sum", expected_runtime["path"]]).decode().split()[0]
    if actual_xserver_hash != expected_runtime["sha256"]:
        raise RuntimeError("observer X server wrapper differs from its runtime receipt")
    return dict(host=host, path=str(deployment), provenance_sha256=prov_hash,
                deployment_receipt_sha256=rec_hash, profile=profile,
                remote_source=expected["SPATIAL_GEOMETRY_REMOTE_SOURCE"],
                remote_bin=expected_build, remote_python=expected["SPATIAL_GEOMETRY_REMOTE_PYTHON"],
                remote_xserver=expected_runtime["path"], executables=profile_row["executables"])

def _json_lines(payload):
    rows = []
    for line in payload.splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows

def _verify_cleanup_receipts(method_dir, security_dir):
    terminal_path = method_dir / "peer-resource-terminal.txt"
    exact_path = method_dir / "exact-unit-terminal.json"
    local_cleanup_path = security_dir / "authority-native-observer" / "cleanup-receipts.json"
    for path in (terminal_path, exact_path, local_cleanup_path):
        if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
            raise RuntimeError("required workspace cross-host cleanup evidence is missing: " + str(path))
    terminal_records = _json_lines(terminal_path.read_bytes())
    exact_terminal = json.loads(exact_path.read_text())
    observer_cleanup = json.loads(local_cleanup_path.read_text())
    observer_stream = "\n".join(row.get("stdout", "") for row in observer_cleanup if isinstance(row, dict))
    observer_records = _json_lines(observer_stream.encode())
    for rows, unit in ((terminal_records, exact_terminal.get("unit")), (observer_records, None)):
        adapters = [row for row in rows if "scope_removed" in row]
        units = [row for row in rows if "unit_after" in row]
        if len(adapters) != 1 or len(units) != 1:
            raise RuntimeError("workspace cross-host cleanup stream lacks unique adapter and unit receipts")
        adapter, record = adapters[0], units[0]
        if (adapter.get("scope_removed") is not True or adapter.get("unexpected_population") is not False
                or adapter.get("exit_code") != 0 or record.get("cgroup_exists") is not False
                or record.get("query_exit") not in (0, 4)
                or "LoadState=not-found" not in record.get("unit_after", "")
                or "ActiveState=inactive" not in record.get("unit_after", "")):
            raise RuntimeError("workspace cross-host cleanup receipt indicates an unclean scope/unit")
        if unit is not None and record.get("unit") != unit:
            raise RuntimeError("exact unit terminal receipt disagrees with observer cleanup stream")
    if any(row.get("returncode") != 0 for row in observer_cleanup if isinstance(row, dict) and "returncode" in row):
        raise RuntimeError("local native observer process did not exit cleanly")
    if (exact_terminal.get("cgroup_absent") is not True or exact_terminal.get("query_exit") not in (0, 4)
            or "LoadState=not-found" not in exact_terminal.get("state", "")
            or "ActiveState=inactive" not in exact_terminal.get("state", "")):
        raise RuntimeError("exact unit terminal audit is incomplete")
    return dict(adapter_scope_removed=True, unexpected_population=False,
                exact_unit=exact_terminal.get("unit"), cgroup_absent=True,
                observer_cleanup_sha256=m06.sha(local_cleanup_path),
                remote_cleanup_stream_sha256=m06.sha(terminal_path),
                exact_terminal_sha256=m06.sha(exact_path))

def _scan_local_logs(paths):
    records = []
    for root in paths:
        if not root.is_dir() or root.is_symlink():
            raise RuntimeError("workspace cross-host evidence root is missing or unsafe: " + str(root))
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise RuntimeError("workspace cross-host evidence contains a symlink: " + str(path))
            if path.is_file() and path.suffix.lower() in (".log", ".stderr", ".stdout", ".txt"):
                text = path.read_text(errors="replace")
                if m06.SAN_RE.search(text):
                    raise RuntimeError("sanitizer diagnostic in retained local log: " + str(path))
                records.append(dict(path=str(path), sha256=m06.sha(path), bytes=path.stat().st_size))
    service_logs = [row for row in records if Path(row["path"]).name == "geometry-host-session.log"]
    names = {Path(row["path"]).name for row in records}
    required_logs = {"peer-native.stderr", "boundary.stdout", "boundary.stderr", "observer.stderr"}
    if len(service_logs) != 1 or not required_logs.issubset(names):
        raise RuntimeError("local evidence lacks required native/service logs")
    return records

def _scan_remote_evidence(host, path):
    script = (
        "import hashlib,json,pathlib,re,sys; r=pathlib.Path(sys.argv[1]); "
        "rx=re.compile(rb'AddressSanitizer|LeakSanitizer|UndefinedBehaviorSanitizer|runtime error:'); "
        "files=[]; bad=[]; "
        "[(files.append({'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size}), bad.append(str(p)) if rx.search(p.read_bytes()) else None) for p in sorted(r.rglob('*')) if p.is_file()]; "
        "print(json.dumps({'exists':r.is_dir(),'files':files,'bad':bad})); sys.exit(not r.is_dir())")
    result = json.loads(m06._run_on_host(host, ["python3", "-c", script, str(path)], timeout=60).decode())
    if not result.get("exists") or not result.get("files") or result.get("bad"):
        raise RuntimeError("observer evidence is missing or contains sanitizer output")
    service_logs = [row for row in result["files"] if row["path"].endswith("geometry-host-session.log")]
    if len(service_logs) != 1:
        raise RuntimeError("observer evidence lacks one retained geometry host service log")
    return dict(files=result["files"], service_log=service_logs[0])

def run_workspace_crosshost(a):
    host = socket.gethostname().split(".")[0]
    if host not in m06.TEST_HOSTS:
        raise RuntimeError("workspace cross-host stage runs only on sb16 or sb16-2")
    if a.direction not in ("sb16-to-sb16-2", "sb16-2-to-sb16") or a.profile not in m06.PROFILES:
        raise RuntimeError("workspace cross-host run must select a required direction and profile")
    roles = _role_map(a.roles, host, a.direction)
    matrix = _read(a.m06_plan, "M06 deployment plan")
    if matrix.get("candidate") != a.candidate or set(matrix.get("deployments", {})) != set(m06.TEST_HOSTS):
        raise RuntimeError("workspace cross-host requires the exact M06 pinned deployment matrix")
    _source_host, observer = a.direction.split("-to-")
    local_pin = matrix["deployments"][host]
    deployment = a.deployment.resolve()
    if (str(deployment) != local_pin.get("path") or
            m06.sha(deployment / "provenance.json") != local_pin.get("provenance_sha256") or
            m06.sha(deployment / "deployment-receipt.json") != local_pin.get("deployment_receipt_sha256")):
        raise RuntimeError("local deployment differs from the M06 pinned host artifacts")
    m06._validate_remote_deployment(host, local_pin, a.candidate)
    observer_pin = matrix["deployments"][observer]
    observer_roles = _remote_profile_pin(observer, Path(observer_pin["path"]), a.candidate, a.profile,
        observer_pin["provenance_sha256"], observer_pin["deployment_receipt_sha256"], roles)
    provenance_path = deployment / "provenance.json"
    receipt_path = deployment / "deployment-receipt.json"
    provenance = _read(provenance_path, "deployment provenance")
    receipt = _read(receipt_path, "deployment receipt")
    if (provenance.get("candidate") != a.candidate or receipt.get("candidate") != a.candidate
            or receipt.get("host") != host or receipt.get("status") != "verified"):
        raise RuntimeError("workspace cross-host deployment pin is not verified")
    source = deployment / "source"
    build = deployment / "build" / a.profile
    process = subprocess.run(["ctest", "--test-dir", str(build), "--show-only=json-v1"],
        cwd=source, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    if process.returncode:
        raise RuntimeError("cannot read deployed CTest inventory")
    inventory = json.loads(process.stdout)["tests"]
    matches = [row for row in inventory if row.get("name") == "workspace-geometry-native-crosshost"]
    if len(matches) != 1:
        raise RuntimeError("workspace geometry CTest registration is missing or duplicated")
    row = matches[0]
    command = list(row.get("command", []))
    baked = provenance.get("configure_env", {})
    if set(baked) != set(GEOMETRY_ENV) or any(not baked[key] for key in GEOMETRY_ENV):
        raise RuntimeError("provenance lacks the complete nonempty configure-time role placeholders")
    for index, item in enumerate(command):
        for key in GEOMETRY_ENV:
            prefix = key + "="
            if item.startswith(prefix):
                if item[len(prefix):] != baked[key]:
                    raise RuntimeError("deployed CTest command no longer matches its recorded configure-time placeholder: " + key)
                command[index] = prefix + (str(a.output.resolve()) if key == "SPATIAL_GEOMETRY_EVIDENCE" else roles[key])
                break
    for key in GEOMETRY_ENV:
        if not any(item.startswith(key + "=") for item in command):
            raise RuntimeError("workspace geometry command omits its baked role argument: " + key)
    evidence = a.output.resolve()
    if evidence.exists() or deployment in evidence.parents or source in evidence.parents:
        raise RuntimeError("evidence output must be new and outside the deployment")
    evidence.mkdir(parents=True, mode=0o700)
    security_evidence = evidence / "security"
    security_evidence.mkdir(mode=0o700)
    runtime = provenance["runtime_manifest"]["hosts"][host][a.profile]
    env = os.environ.copy()
    for key in ("DISPLAY", "SPATIAL_TEST_DISPLAY", "SPATIAL_SECURITY_TEST_CORE_ONLY", "LD_LIBRARY_PATH", "__EGL_VENDOR_LIBRARY_FILENAMES", "LSAN_OPTIONS"):
        env.pop(key, None)
    env.update(PYTHONPATH=str(build / "python") + ":" + str(source),
        SPATIAL_TEST_XSERVER=runtime["xace_wrapper"]["path"],
        SPATIAL_SECURITY_EVIDENCE=str(security_evidence),
        ASAN_OPTIONS="detect_leaks=1:abort_on_error=1",
        UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1")
    for prop in row.get("properties", []):
        if prop.get("name") == "ENVIRONMENT":
            for pair in prop.get("value", []):
                if "=" in pair:
                    key, value = pair.split("=", 1)
                    if key in ("SPATIAL_TEST_XSERVER", "SPATIAL_SECURITY_EVIDENCE"):
                        continue
                    env[key] = value
    env["SPATIAL_TEST_XSERVER"] = runtime["xace_wrapper"]["path"]
    env["SPATIAL_SECURITY_EVIDENCE"] = str(security_evidence)
    log = evidence / "workspace-geometry-native-crosshost.log"
    result, seconds = m06.run(command, cwd=source, env=env, log=log, timeout=1800)
    output = log.read_text(errors="replace")
    method_dir = evidence / "test_shared_workspace_geometry_changes_both_native_hosts"
    cleanup = _verify_cleanup_receipts(method_dir, method_dir)
    local_logs = _scan_local_logs((evidence, security_evidence))
    remote_logs = _scan_remote_evidence(observer, Path(roles["SPATIAL_GEOMETRY_REMOTE_EVIDENCE"]) /
                                        "test_shared_workspace_geometry_changes_both_native_hosts")
    passed = (result.returncode == 0 and re.search(r"Ran 1 test in .*\n\nOK(?:\n|$)", output) is not None
              and not m06.SAN_RE.search(output))
    summary = dict(schema=1, status="passed" if passed else "failed_or_incomplete",
        qualification="UNQUALIFIED", candidate=a.candidate, host=host, profile=a.profile,
        direction=a.direction, roles=roles, observer_deployment=observer_roles,
        m06_deployment_pins={host: {key: matrix["deployments"][host][key] for key in ("path", "provenance_sha256", "deployment_receipt_sha256")} for host in m06.TEST_HOSTS},
        cleanup=cleanup, local_log_inventory=local_logs, remote_log_inventory=remote_logs,
        deployment_provenance_sha256=m06.sha(provenance_path),
        deployment_receipt_sha256=m06.sha(receipt_path), command=command, exit_code=result.returncode,
        seconds=seconds, test_output_sha256=m06.sha(log), test_log=str(log), phase_complete=False)
    m06.write_json(evidence / "workspace-crosshost-receipt.json", summary)
    return 0 if passed else 1

def reconcile(a):
    candidate = a.candidate
    if not re.fullmatch(r"[0-9a-f]{40}", candidate):
        raise RuntimeError("reconciliation requires one full candidate SHA")
    m06_report = _read(a.m06_reconciliation, "M06 cross-host reconciliation")
    if (m06_report.get("candidate") != candidate or
            m06_report.get("status") != "passed_needs_primary_review" or
            m06_report.get("phase_complete") is not False):
        raise RuntimeError("M06 cross-host handoff is absent, stale, or not passed")
    local_receipts = m06_report.get("local_runs", {})
    if set(local_receipts) != set(m06.TEST_HOSTS):
        raise RuntimeError("M06 reconciliation lacks both host-local receipts")
    for host, row in local_receipts.items():
        if host == "sb16":
            local_report = _read(row.get("path", ""), "sb16 local gate receipt")
            if m06.sha(Path(row["path"])) != row.get("sha256"):
                raise RuntimeError("sb16 local gate receipt hash mismatch")
        else:
            local_report = row.get("remote_receipt", {})
        if (local_report.get("candidate") != candidate
                or not M07_REQUIRED.issubset(set(local_report.get("required_tests", [])))
                or set(local_report.get("dedicated_crosshost", [])) != set(M07_DEDICATED)):
            raise RuntimeError(host + " local receipt does not pin all mandatory M07 suites/exclusions")
        for profile in m06.PROFILES:
            names = set(row.get("inventories", {}).get(profile, {}).get("names", []))
            profile_result = local_report.get("profiles", {}).get(profile, {})
            core = next((item for item in profile_result.get("commands", []) if item.get("name") == "core"), {})
            if (not M07_REQUIRED.issubset(names)
                    or core.get("exit_code") != 0 or core.get("junit_cases") != len(names) - len(M07_DEDICATED)
                    or core.get("all_registered_present") is not True or core.get("failed_or_skipped") is not False):
                raise RuntimeError(host + " " + profile + " local CTest result is incomplete for M07")
    m06_plan_path = Path(a.m06_reconciliation).resolve().parent / "plan.json"
    m06_plan = _read(m06_plan_path, "M06 pinned deployment plan")
    if m06_plan.get("candidate") != candidate or set(m06_plan.get("deployments", {})) != set(m06.TEST_HOSTS):
        raise RuntimeError("M06 pinned deployment plan is absent or stale")
    expected_pins = {host: {key: m06_plan["deployments"][host][key]
                    for key in ("path", "provenance_sha256", "deployment_receipt_sha256")}
                     for host in m06.TEST_HOSTS}
    rows = _read(a.workspace_results, "workspace cross-host result index")
    if not isinstance(rows, list):
        raise RuntimeError("workspace result index must be a JSON array")
    expected = {(host, profile, host + "-to-" + ("sb16-2" if host == "sb16" else "sb16"))
                for host in sorted(m06.TEST_HOSTS) for profile in m06.PROFILES}
    seen = set()
    accepted = []
    for item in rows:
        path = Path(item.get("path", ""))
        receipt = _read(path, "workspace cross-host receipt")
        key = (receipt.get("host"), receipt.get("profile"), receipt.get("direction"))
        if key in seen or key not in expected:
            raise RuntimeError("workspace result index has an unexpected or duplicate matrix row")
        seen.add(key)
        log = Path(receipt.get("test_log", ""))
        expected_pin = receipt.get("m06_deployment_pins", {}).get(receipt.get("host"), {})
        observer_host = receipt.get("direction", "").split("-to-")[-1]
        observer_pin = receipt.get("m06_deployment_pins", {}).get(observer_host, {})
        if (receipt.get("m06_deployment_pins") != expected_pins or
                receipt.get("deployment_provenance_sha256") != expected_pin.get("provenance_sha256") or
                receipt.get("deployment_receipt_sha256") != expected_pin.get("deployment_receipt_sha256") or
                receipt.get("observer_deployment", {}).get("provenance_sha256") != observer_pin.get("provenance_sha256") or
                receipt.get("observer_deployment", {}).get("deployment_receipt_sha256") != observer_pin.get("deployment_receipt_sha256")):
            raise RuntimeError("workspace cross-host roles differ from M06 pinned host artifacts")
        if (receipt.get("candidate") != candidate or receipt.get("status") != "passed"
                or receipt.get("qualification") != "UNQUALIFIED" or receipt.get("phase_complete") is not False
                or receipt.get("exit_code") != 0 or not receipt.get("cleanup", {}).get("adapter_scope_removed")
                or not receipt.get("cleanup", {}).get("cgroup_absent")
                or not receipt.get("local_log_inventory") or not receipt.get("remote_log_inventory", {}).get("files")
                or not log.is_file() or m06.sha(log) != receipt.get("test_output_sha256")):
            raise RuntimeError("workspace cross-host receipt/log is stale or failed")
        text = log.read_text(errors="replace")
        if re.search(m06.SAN_RE, text) or not re.search(r"Ran 1 test in .*\n\nOK(?:\n|$)", text):
            raise RuntimeError("workspace cross-host output lacks one clean passing test")
        accepted.append(dict(matrix=list(key), receipt=str(path), receipt_sha256=m06.sha(path),
                             log_sha256=receipt["test_output_sha256"]))
    if seen != expected:
        missing = sorted(expected - seen)
        raise RuntimeError("workspace cross-host matrix is incomplete: " + repr(missing))
    output = a.output.resolve()
    if output.exists():
        raise RuntimeError("M07 reconciliation output already exists")
    output.mkdir(parents=True, exist_ok=False)
    report = dict(schema=1, status="passed_needs_primary_review", qualification="UNQUALIFIED",
        candidate=candidate, phase_complete=False, m06_reconciliation=str(a.m06_reconciliation.resolve()),
        m06_reconciliation_sha256=m06.sha(a.m06_reconciliation.resolve()),
        workspace_crosshost=accepted,
        review_required=["primary review", "full combined M07 and M1 gate reconciliation"])
    m06.write_json(output / "m07-reconciliation.json", report)
    return 0

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--candidate", required=True); p.add_argument("--output", type=Path, required=True)
    p.add_argument("--attempt"); p.add_argument("--runtime-manifest", type=Path, required=True)
    p.add_argument("--build-paths", type=Path, required=True); p.add_argument("--configure-env", type=Path, required=True)
    p.set_defaults(fn=prepare)
    p = sub.add_parser("run-local")
    p.add_argument("--deployment", type=Path, required=True); p.add_argument("--attempt")
    for name in ("native-controller", "real-world", "recovery", "host-session", "held-input"):
        p.add_argument("--" + name, required=True)
    p.set_defaults(fn=run_local)
    p = sub.add_parser("reconcile")
    p.add_argument("--candidate", required=True); p.add_argument("--m06-reconciliation", type=Path, required=True)
    p.add_argument("--workspace-results", type=Path, required=True); p.add_argument("--output", type=Path, required=True)
    p.set_defaults(fn=reconcile)
    p = sub.add_parser("workspace-crosshost")
    p.add_argument("--deployment", type=Path, required=True); p.add_argument("--candidate", required=True)
    p.add_argument("--profile", required=True); p.add_argument("--direction", required=True)
    p.add_argument("--roles", type=Path, required=True); p.add_argument("--m06-plan", type=Path, required=True); p.add_argument("--output", type=Path, required=True)
    p.set_defaults(fn=run_workspace_crosshost)
    a = parser.parse_args()
    try:
        return a.fn(a)
    except BaseException as error:
        print("BLOCKED/FAILED: " + repr(error), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
