#!/usr/bin/env python3
"""M08 local inventory extension for the reviewed M06/M07 deployed gate."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
import sys

HERE = Path(__file__).resolve()
M07_PATH = HERE.with_name("m07_deployed_gate.py")
SPEC = importlib.util.spec_from_file_location("m07_deployed_gate", M07_PATH)
m07 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m07)
m06 = m07.m06

# Cabinet file-operation coverage. M07 workspace-copy tests remain inherited
# baseline tests; they do not stand in for these cabinet action registrations.
M08_REQUIRED = frozenset({
    "process-scope-recovery-validation",  # lifecycle-branch CMake addition
    "workspace-file-operation-journal",
    "workspace-file-effect-observer-exception",
    "broker-file-action-client",
    "workspace-file-action-broker",
    "workspace-file-action-storage-owner-btrfs",
    "storage-owner-fixture-tooling",
    "workspace-file-engine",
    "workspace-copy-staging-mask",  # lifecycle-branch CMake addition
    "copy-tree-worker",
    "copy-tree-worker-nested-mount",
    "cabinet-copy-path",
    "copy-finalize-worker",
    "copy-worker-production",
    "copy-worker-stream",
    "workspace-copy-worker-helpers-tooling",
    "copy-progress-timing-tooling",
    "broker-copy-preprune-cleanup",
    "broker-copy-preprune-deadline-multirow",
    "actor-cabinet-gateway-instrumentation",
    "workspace-actor-cabinet-gateway",
    "workspace-private-test-evidence",
    "presentation-metadata",
    "workspace-presentation-store",
    "workspace-presentation-projection",
    "workspace-presentation-actor",
    "workspace-presentation-shared-representation",
    "process-scope-named-regression",
    "workspace-cabinet-native-crosshost",
})
M08_DEDICATED_CROSSHOST = frozenset(m07.M07_DEDICATED | {"workspace-cabinet-native-crosshost"})
INHERITED_REQUIRED = frozenset(m06.REQUIRED) | frozenset(m07.M07_REQUIRED)
REQUIRED = INHERITED_REQUIRED | M08_REQUIRED
M08_HARNESS_MANIFEST = HERE.with_name("m08_harness_test_inventory.json")
M08_BASELINE_SHA256 = "e4c14f05414e9cbd39f881fbb5d6fe45ffd5301f8f5c0a693bd36ec1808853f2"
M08_HARNESS_GROUP_COUNTS = {
    "test_oracles": 16, "test_tools": 11, "test_recovery_wire": 8,
    "test_transport": 12, "test_workspace_wire": 4,
    "test_m08_deployed_gate": 32, "test_private_evidence_scope": 5,
    "test_resource_broker_environment": 1,
}
M08_STORAGE_EXPECTED_RESULTS = m06.M06_STORAGE_SCENARIO_RESULTS | m06.M08_STORAGE_SCENARIO_ADDITIONS


def load_harness_test_inventory():
    """Load the maintained exact M06 baseline plus additive M08 unittest IDs."""
    record = json.loads(M08_HARNESS_MANIFEST.read_text())
    baseline = record.get("baseline_tests")
    additions = record.get("m08_addition_tests")
    if (record.get("schema") != 1 or not isinstance(baseline, list) or not isinstance(additions, list)
            or record.get("baseline_count") != 51 or len(baseline) != 51
            or record.get("m08_addition_count") != 38 or len(additions) != 38
            or record.get("expected_count") != 89):
        raise RuntimeError("M08 maintained harness inventory count contract is invalid")
    baseline_digest = hashlib.sha256((chr(10).join(baseline) + chr(10)).encode()).hexdigest()
    if record.get("baseline_sha256") != M08_BASELINE_SHA256 or baseline_digest != M08_BASELINE_SHA256:
        raise RuntimeError("M08 inherited harness inventory differs from the frozen 51-test baseline")
    all_ids = baseline + additions
    if (any(not isinstance(name, str) or not name.startswith("tests.harness.") for name in all_ids)
            or len(set(all_ids)) != 89):
        raise RuntimeError("M08 maintained harness test IDs are malformed or duplicated")
    group_counts = {}
    for name in additions:
        parts = name.split(".")
        module = parts[2] if len(parts) >= 5 else ""
        group_counts[module] = group_counts.get(module, 0) + 1
    expected_groups = {"test_m08_deployed_gate": 32, "test_private_evidence_scope": 5,
                       "test_resource_broker_environment": 1}
    if group_counts != expected_groups:
        raise RuntimeError("M08 additive harness inventory does not match its reviewed source groups")
    baseline_groups = {}
    for name in baseline:
        parts = name.split(".")
        module = parts[2] if len(parts) >= 5 else ""
        baseline_groups[module] = baseline_groups.get(module, 0) + 1
    if baseline_groups != {key: value for key, value in M08_HARNESS_GROUP_COUNTS.items()
                           if key in {"test_oracles", "test_tools", "test_recovery_wire", "test_transport", "test_workspace_wire"}}:
        raise RuntimeError("M08 inherited harness inventory does not match the frozen 51-test baseline")
    return tuple(sorted(all_ids))


def require_inventory(inventory):
    """Refuse an inventory missing any inherited or M08 registration."""
    try:
        result = m06.audit_inventory(inventory, REQUIRED)
    except RuntimeError as error:
        raise RuntimeError("M08 qualification inventory refused: " + str(error)) from error
    rows = inventory if isinstance(inventory, list) else inventory.get("tests", [])
    required_rows = {row.get("name"): row for row in rows}
    skippable = []
    for name in sorted(REQUIRED):
        for prop in required_rows[name].get("properties", []):
            if prop.get("name") in ("SKIP_RETURN_CODE", "SKIP_REGULAR_EXPRESSION"):
                skippable.append(name)
                break
    if skippable:
        raise RuntimeError("M08 qualification inventory refused: required CTest registrations can skip: " + ", ".join(skippable))
    return result


STORAGE_COPY_QUOTA_BYTES = "100663296"
STORAGE_PROFILE_KEYS = {
    "Debug": "DEBUG",
    "ASan-UBSan": "ASAN_UBSAN",
}


def storage_profile_key(profile, variable):
    return variable + "_" + STORAGE_PROFILE_KEYS[profile]


def validate_storage_volume(volume_path, mountinfo=None):
    volume_path = Path(volume_path).resolve(strict=True)
    mountinfo = Path("/proc/self/mountinfo").read_text() if mountinfo is None else mountinfo
    found = None
    for line in mountinfo.splitlines():
        fields = line.split()
        if "-" not in fields:
            continue
        separator = fields.index("-")
        if separator < 6 or len(fields) < separator + 4:
            continue
        decode = lambda value: re.sub(r"\\([0-7]{3})",
            lambda match: chr(int(match.group(1), 8)), value)
        root, target = decode(fields[3]), Path(decode(fields[4]))
        fs_type = fields[separator + 1]
        options = set(fields[5].split(",")) | set(fields[separator + 3].split(","))
        if target == volume_path:
            found = (root, fs_type, options)
            break
    if found is None or found[0] != "/" or found[1] != "btrfs" or "rw" not in found[2] or "ro" in found[2]:
        raise RuntimeError("M08 StorageOwner volume must be a writable top-level Btrfs mount")
    if any(option.startswith("compress") and option != "compress=no" for option in found[2]):
        raise RuntimeError("M08 StorageOwner Btrfs volume must have compression disabled")
    if volume_path.stat().st_ino != 256:
        raise RuntimeError("M08 StorageOwner volume root must be the top-level Btrfs inode 256")
    try:
        result = subprocess.run(["btrfs", "inspect-internal", "rootid", str(volume_path)],
                                check=True, capture_output=True, text=True, timeout=10)
        devices = subprocess.run(["btrfs", "filesystem", "show", "--raw", str(volume_path)],
                                 check=True, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("cannot verify StorageOwner Btrfs volume: " + repr(error)) from error
    if re.findall(r"\d+", result.stdout) != ["5"]:
        raise RuntimeError("M08 StorageOwner volume must be Btrfs top-level treeid 5")
    if not re.search(r"(?m)^\s*Total devices\s+1(?:\s|$)", devices.stdout):
        raise RuntimeError("M08 StorageOwner Btrfs volume must use one device")


def require_storage_lane_configuration(environ=None, profiles=None):
    """Require fresh externally provisioned owner fixtures for every selected build profile."""
    env = os.environ if environ is None else environ
    selected = tuple(m06.PROFILES if profiles is None else profiles)
    if not selected or any(profile not in STORAGE_PROFILE_KEYS for profile in selected):
        raise RuntimeError("M08 StorageOwner preflight received an invalid profile selection")
    by_profile = {}
    for profile in selected:
        volume_key = storage_profile_key(profile, "SPATIAL_TEST_STORAGE_VOLUME")
        launch_key = storage_profile_key(profile, "SPATIAL_TEST_STORAGE_LAUNCH_CONFIG")
        quota_key = storage_profile_key(profile, "SPATIAL_TEST_STORAGE_COPY_QUOTA_BYTES")
        volume = env.get(volume_key, "")
        launch_config = env.get(launch_key, "")
        quota = env.get(quota_key, "")
        missing = [name for name, value in ((volume_key, volume), (launch_key, launch_config), (quota_key, quota))
                   if not value]
        if missing:
            raise RuntimeError("M08 StorageOwner Btrfs lane is not provisioned for " + profile + "; missing: " + ", ".join(missing))
        volume_path = Path(volume)
        config_path = Path(launch_config)
        if not volume_path.is_absolute() or not volume_path.is_dir():
            raise RuntimeError(profile + " StorageOwner Btrfs volume must be an existing absolute mount path")
        if volume_path.resolve(strict=True) != volume_path:
            raise RuntimeError(profile + " StorageOwner Btrfs volume path must be canonical")
        validate_storage_volume(volume_path)
        if not config_path.is_absolute() or not config_path.is_file() or not os.access(config_path, os.R_OK):
            raise RuntimeError(profile + " StorageOwner launch configuration must be a readable absolute file")
        if quota != STORAGE_COPY_QUOTA_BYTES:
            raise RuntimeError(profile + " StorageOwner copy quota must be exactly " + STORAGE_COPY_QUOTA_BYTES)
        by_profile[profile] = {
            "SPATIAL_TEST_STORAGE_VOLUME": str(volume_path.resolve()),
            "SPATIAL_TEST_STORAGE_LAUNCH_CONFIG": str(config_path.resolve()),
            "SPATIAL_TEST_STORAGE_COPY_QUOTA_BYTES": quota,
        }
    if len({row["SPATIAL_TEST_STORAGE_VOLUME"] for row in by_profile.values()}) != len(by_profile):
        raise RuntimeError("M08 requires a fresh distinct StorageOwner Btrfs volume for each selected profile")
    if len({row["SPATIAL_TEST_STORAGE_LAUNCH_CONFIG"] for row in by_profile.values()}) != len(by_profile):
        raise RuntimeError("M08 requires a matching distinct StorageOwner launch configuration for each selected profile")
    return by_profile


def require_deployment_inventory(deployment):
    deployment = Path(deployment).resolve()
    provenance_path = deployment / "provenance.json"
    provenance = json.loads(provenance_path.read_text())
    profiles = provenance.get("profiles")
    if not isinstance(profiles, dict) or set(profiles) != set(m06.PROFILES):
        raise RuntimeError("M08 qualification inventory refused: provenance must contain Debug and ASan-UBSan")
    for profile in m06.PROFILES:
        row = profiles[profile]
        if not isinstance(row, dict) or not isinstance(row.get("inventory"), list):
            raise RuntimeError("M08 qualification inventory refused: missing inventory for " + profile)
        require_inventory(row["inventory"])


def run_local(a):
    # Check the deployed package inventory before m06.gate can launch a stage.
    # m06.gate then verifies host, deployment receipt, source/runtime pins, and
    # repeats required-inventory validation against the deployed CTest metadata.
    require_deployment_inventory(a.deployment)
    selected_profiles = getattr(a, "selected_profiles", None)
    storage_lane_by_profile = require_storage_lane_configuration(
        profiles=m06.PROFILES if selected_profiles is None else tuple(selected_profiles))
    ns = argparse.Namespace(
        deployment=a.deployment, attempt=a.attempt,
        native_controller=a.native_controller, real_world=a.real_world,
        recovery=a.recovery, host_session=a.host_session, held_input=a.held_input,
        selected_profiles=selected_profiles,
        storage_lane_by_profile=storage_lane_by_profile,
        copy_worker_helpers=True,
        collector_continuation=getattr(a, "collector_continuation", None),
        collector_continuation_sha256=getattr(a, "collector_continuation_sha256", None),
        required_tests=sorted(m07.M07_REQUIRED | M08_REQUIRED),
        dedicated_crosshost=sorted(M08_DEDICATED_CROSSHOST),
        expected_harness_tests=load_harness_test_inventory(),
        storage_expected_results=sorted(M08_STORAGE_EXPECTED_RESULTS),
    )
    return m06.gate(ns)


def run_workspace_cabinet_crosshost(a):
    a.test_name = "workspace-cabinet-native-crosshost"
    return m07.run_workspace_crosshost(a)


def require_crosshost_result_indexes(geometry_results, cabinet_results):
    indexes = {
        "workspace-geometry-native-crosshost": geometry_results,
        "workspace-cabinet-native-crosshost": cabinet_results,
    }
    for name, path in indexes.items():
        result_path = Path(path)
        if not result_path.is_file() or result_path.is_symlink():
            raise RuntimeError("M08 requires a separate result index for " + name)
        rows = json.loads(result_path.read_text())
        if not isinstance(rows, list) or not rows:
            raise RuntimeError("M08 result index must be a nonempty JSON array for " + name)
    return indexes


def reconcile_workspace_cabinet_crosshost(a):
    candidate = a.candidate
    if not re.fullmatch(r"[0-9a-f]{40}", candidate):
        raise RuntimeError("M08 reconciliation requires one full candidate SHA")
    a.required_tests = REQUIRED
    a.dedicated_tests = M08_DEDICATED_CROSSHOST
    indexes = require_crosshost_result_indexes(a.workspace_geometry_results, a.cabinet_crosshost_results)
    matrices = tuple(indexes.items())
    reconciled = {}
    # Run the shared receipt validator independently for each dedicated matrix.
    # Separate result indexes preserve the evidence identity of the two native cases.
    with tempfile.TemporaryDirectory(prefix="m08-crosshost-reconcile-") as temporary:
        for test_name, result_index in matrices:
            stage = Path(temporary) / test_name
            args = argparse.Namespace(
                candidate=candidate, m06_reconciliation=a.m06_reconciliation,
                workspace_results=result_index, output=stage, test_name=test_name,
                required_tests=REQUIRED, dedicated_tests=M08_DEDICATED_CROSSHOST)
            status = m07.reconcile(args)
            if status != 0:
                raise RuntimeError("M08 matrix validation failed for " + test_name)
            report = json.loads((stage / "m07-reconciliation.json").read_text())
            reconciled[test_name] = report["workspace_crosshost"]
    output = a.output.resolve()
    if output.exists():
        raise RuntimeError("M08 reconciliation output already exists")
    output.mkdir(parents=True, exist_ok=False)
    report = dict(schema=1, status="passed_needs_primary_review", qualification="UNQUALIFIED",
        candidate=candidate, phase_complete=False,
        m06_reconciliation=str(a.m06_reconciliation.resolve()),
        m06_reconciliation_sha256=m06.sha(a.m06_reconciliation.resolve()),
        workspace_geometry_crosshost=dict(
            result_index=str(indexes["workspace-geometry-native-crosshost"].resolve()),
            result_index_sha256=m06.sha(indexes["workspace-geometry-native-crosshost"]),
            receipts=reconciled["workspace-geometry-native-crosshost"]),
        workspace_cabinet_crosshost=dict(
            result_index=str(indexes["workspace-cabinet-native-crosshost"].resolve()),
            result_index_sha256=m06.sha(indexes["workspace-cabinet-native-crosshost"]),
            receipts=reconciled["workspace-cabinet-native-crosshost"]),
        review_required=["primary review", "full combined M08 and M1 gate reconciliation"])
    m06.write_json(output / "m08-reconciliation.json", report)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    gate = sub.add_parser("run-local")
    gate.add_argument("--deployment", type=Path, required=True)
    gate.add_argument("--attempt")
    for name in ("native-controller", "real-world", "recovery", "host-session", "held-input"):
        gate.add_argument("--" + name, required=True)
    gate.add_argument("--selected-profile", dest="selected_profiles", action="append", choices=m06.PROFILES)
    gate.add_argument("--collector-continuation", type=Path)
    gate.add_argument("--collector-continuation-sha256")
    gate.set_defaults(fn=run_local)
    cross = sub.add_parser("workspace-cabinet-crosshost")
    cross.add_argument("--deployment", type=Path, required=True)
    cross.add_argument("--candidate", required=True)
    cross.add_argument("--profile", choices=m06.PROFILES, required=True)
    cross.add_argument("--direction", choices=("sb16-to-sb16-2", "sb16-2-to-sb16"), required=True)
    cross.add_argument("--roles", type=Path, required=True)
    cross.add_argument("--m06-plan", type=Path, required=True)
    cross.add_argument("--output", type=Path, required=True)
    cross.set_defaults(fn=run_workspace_cabinet_crosshost)
    reconcile = sub.add_parser("reconcile-cabinet-crosshost")
    reconcile.add_argument("--candidate", required=True)
    reconcile.add_argument("--m06-reconciliation", type=Path, required=True)
    reconcile.add_argument("--workspace-geometry-results", type=Path, required=True)
    reconcile.add_argument("--cabinet-crosshost-results", type=Path, required=True)
    reconcile.add_argument("--output", type=Path, required=True)
    reconcile.set_defaults(fn=reconcile_workspace_cabinet_crosshost)
    args = parser.parse_args()
    try:
        return args.fn(args)
    except BaseException as error:
        print("BLOCKED/FAILED: " + repr(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
