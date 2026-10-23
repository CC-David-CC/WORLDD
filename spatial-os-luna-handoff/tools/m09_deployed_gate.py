#!/usr/bin/env python3
"""Mandatory M09 profile over M08. Local passes still require cross-host qualification."""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import uuid

HERE = Path(__file__).resolve()
M08_PATH = HERE.with_name("m08_deployed_gate.py")
SPEC = importlib.util.spec_from_file_location("m08_deployed_gate", M08_PATH)
m08 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m08)
m07 = m08.m07
m06 = m08.m06

# Keep M08's cumulative profile and add the suites that execute M09 witnesses.
M09_REQUIRED = frozenset({
    "application-control-lease",
    "application-observation",
    "world-application-control",
    "native-surface_identity",
    "native-world_surface_groups",
    "native-observer-endpoint",
    "world-input-witness-bindings",
    "world-native-input-witness-v2",
    "native-input-lifecycle-v3-artifact",
    "native-input-lifecycle-v3-collector",
    "native-lifecycle-endpoint-v3",
    "world-native-lifecycle-v3",
    "session-world-issuer-setup",
    "workspace-authority",
})
REQUIRED = frozenset(m08.REQUIRED) | M09_REQUIRED
M09_SUITE_BY_MODULE = {
    "tests.core.test_application_control_lease": "application-control-lease",
    "tests.core.test_application_observation": "application-observation",
    "tests.core.test_world_application_control": "world-application-control",
    "tests.native.test_surface_identity": "native-surface_identity",
    "tests.native.test_world_surface_groups": "native-world_surface_groups",
    "tests.native.test_native_observer_endpoint": "native-observer-endpoint",
    "tests.core.test_world_input_witness": "world-input-witness-bindings",
    "tests.core.test_world_native_input_witness": "world-native-input-witness-v2",
    "tests.native.test_input_state_observer_v3": "native-input-lifecycle-v3-artifact",
    "tests.native.test_input_state_observer_v3_collect": "native-input-lifecycle-v3-collector",
    "tests.native.test_native_lifecycle_endpoint_v3": "native-lifecycle-endpoint-v3",
    "tests.core.test_world_native_lifecycle_v3": "world-native-lifecycle-v3",
    "tests.native.test_session_world_issuer_setup": "session-world-issuer-setup",
    "tests.core.test_workspace_authority": "workspace-authority",
}
M09_CASES = {
    "application-group-identity": {
        "suites": ("native-world_surface_groups", "world-application-control"),
        "tests": (
            "tests.native.test_world_surface_groups.WorldSurfaceGroupTests.test_dialog_popup_group_control_and_foreign_hint_use_connection_identity",
            "tests.native.test_world_surface_groups.WorldSurfaceGroupTests.test_same_application_connections_can_form_relationship",
            "tests.core.test_world_application_control.WorldApplicationControlTests.test_popup_uses_same_application_reservation",
        ),
        "status": "SOURCE_PRESENT_NATIVE_RUNTIME_UNRUN",
    },
    "independent-controllers": {
        "suites": ("world-application-control", "application-control-lease"),
        "tests": (
            "tests.core.test_world_application_control.WorldApplicationControlTests.test_independent_applications_allow_simultaneous_actors",
        ),
        "status": "EXISTING_CASE",
    },
    "balanced-transfer": {
        "suites": ("world-application-control", "application-control-lease", "native-observer-endpoint",
                   "world-input-witness-bindings", "world-native-input-witness-v2",
                   "native-input-lifecycle-v3-artifact", "native-input-lifecycle-v3-collector",
                   "native-lifecycle-endpoint-v3", "world-native-lifecycle-v3", "session-world-issuer-setup",
                   "workspace-authority"),
        "tests": (
            "tests.native.test_native_observer_endpoint.NativeObserverEndpointTests.test_actual_server_receives_parent_owned_cloexec_endpoint",
            "tests.native.test_native_observer_endpoint.NativeObserverEndpointTests.test_setup_resolves_real_native_seats_and_distinct_owners",
            "tests.native.test_native_observer_endpoint.NativeObserverEndpointTests.test_live_arm_acknowledges_exact_command_without_changing_native_input",
            "tests.core.test_world_native_input_witness.WorldNativeInputWitnessTests.test_observation_revocation_has_native_transition_and_independent_receipts",
            "tests.core.test_world_native_input_witness.WorldNativeInputWitnessTests.test_control_revocation_has_native_transition_and_independent_receipts",
            "tests.core.test_world_native_input_witness.WorldNativeInputWitnessTests.test_voluntary_unfocus_has_native_transition_and_independent_receipts",
            "tests.core.test_world_native_input_witness.WorldNativeInputWitnessTests.test_stop_has_native_transition_and_independent_receipts",
            "tests.core.test_world_native_input_witness.WorldNativeInputWitnessTests.test_shared_renderer_query_joins_real_actor_seats_and_native_receipts",
            "tests.core.test_world_input_witness.WorldInputWitnessTests.test_real_principals_activation_handles_and_native_devices_join",
            "tests.core.test_world_input_witness.WorldInputWitnessTests.test_unprimed_actor_is_not_given_a_native_seat",
            "tests.core.test_world_input_witness.WorldInputWitnessTests.test_revoked_control_authority_rejects_binding",
            "tests.core.test_world_application_control.WorldApplicationControlTests.test_unfocus_releases_native_input_before_handoff",
            "tests.core.test_world_application_control.WorldApplicationControlTests.test_controller_disconnect_releases_before_handoff",
            "tests.core.test_world_application_control.WorldApplicationControlTests.test_actor_revocation_releases_native_input",
            "tests.core.test_world_application_control.WorldApplicationControlTests.test_observation_revocation_cancels_control",
            "tests.core.test_world_application_control.WorldApplicationControlTests.test_unmapped_capture_releases_reservation",
            "tests.core.test_application_control_lease.ApplicationControlLeaseTests.test_revoked_reservation_blocks_handoff_until_release_acknowledges_cancellation",
            "tests.native.test_world_surface_groups.WorldSurfaceGroupTests.test_destroyed_parent_releases_descendant_input",
            "tests.core.test_world_native_lifecycle_v3.WorldNativeLifecycleV3Tests.test_target_destruction_preserves_survivor_and_orders_native_release",
            "tests.core.test_world_native_lifecycle_v3.WorldNativeLifecycleV3Tests.test_actor_close_retires_devices_and_rejects_old_session",
            "tests.core.test_world_native_lifecycle_v3.WorldNativeLifecycleV3Tests.test_transport_disconnect_retires_devices_and_preserves_application",
            "tests.core.test_world_native_lifecycle_v3.WorldNativeLifecycleV3Tests.test_actor_revocation_retires_devices_and_rejects_old_session",
            "tests.native.test_native_lifecycle_endpoint_v3.NativeLifecycleEndpointV3Tests.test_setup_pins_distinct_same_application_survivor_and_exact_request",
            "tests.native.test_native_lifecycle_endpoint_v3.NativeLifecycleEndpointV3Tests.test_legacy_arm_cannot_replace_lifecycle_arm",
            "tests.native.test_input_state_observer_v3.LifecycleContractTests.test_fake_all_clear_snapshot_cannot_replace_retirement",
            "tests.native.test_input_state_observer_v3.LifecycleContractTests.test_cached_clearance_cannot_replace_fresh_first_peer_sample",
            "tests.native.test_input_state_observer_v3.LifecycleContractTests.test_independent_receipts_keep_each_actual_window",
            "tests.native.test_input_state_observer_v3_collect.LifecycleCollectorTests.test_fragment_arrivals_do_not_reset_the_total_deadline",
            "tests.native.test_input_state_observer_v3_collect.LifecycleCollectorTests.test_complete_records_without_eof_still_expire",
            "tests.native.test_session_world_issuer_setup.SessionWorldIssuerSetupTests.test_healthy_peer_world_registration_after_idle_expiry",
            "tests.native.test_session_world_issuer_setup.SessionWorldIssuerSetupTests.test_plain_peer_world_registration_after_idle_expiry",
            "tests.core.test_workspace_authority.BrokerTransportCompatibilityTests.test_repository_import_reply_can_exceed_interactive_frame_budget",
            "tests.core.test_workspace_authority.BrokerTransportCompatibilityTests.test_repository_import_notifications_do_not_renew_setup_deadline",
            "tests.core.test_workspace_authority.BrokerTransportCompatibilityTests.test_repository_import_partial_reply_keeps_absolute_setup_deadline",
            "tests.core.test_workspace_authority.BrokerTransportCompatibilityTests.test_repository_import_delayed_wrong_request_identity_is_rejected",
            "tests.core.test_workspace_authority.BrokerTransportCompatibilityTests.test_ordinary_broker_reply_retains_five_second_frame_budget",
        ),
        "status": "FOCUSED_COMPONENT_PASS_COMBINED_QUALIFICATION_PENDING",
    },
    "generation-fence": {
        "suites": ("world-application-control", "native-surface_identity"),
        "tests": (
            "tests.core.test_world_application_control.WorldApplicationControlTests.test_resized_aim_rejects_stale_pixels",
            "tests.native.test_surface_identity.SurfaceIdentityTests.test_remap_changes_generation_without_changing_owner",
            "tests.native.test_surface_identity.SurfaceIdentityTests.test_reused_xid_does_not_reuse_window_generation",
        ),
        "status": "SOURCE_PRESENT_BROKER_WAIT_WITNESS_RUNTIME_UNRUN",
    },
    "observation-control-authority-separation": {
        "suites": ("world-application-control", "application-observation", "application-control-lease"),
        "tests": (
            "tests.core.test_world_application_control.WorldApplicationControlTests.test_observation_does_not_grant_control",
            "tests.core.test_world_application_control.WorldApplicationControlTests.test_readonly_actor_cannot_control_writable_application",
            "tests.core.test_world_application_control.WorldApplicationControlTests.test_readonly_actor_can_control_readonly_application",
            "tests.core.test_application_observation.ApplicationObservationTests.test_observe_does_not_enable_input_control",
        ),
        "status": "PARTIAL_EXISTING_POSITIVE_AND_NEGATIVE_CASES",
    },
    "revocation-and-process-survival": {
        "suites": ("world-application-control", "application-control-lease", "application-observation"),
        "tests": (
            "tests.core.test_world_application_control.WorldApplicationControlTests.test_idle_control_revocation_releases_input_and_preserves_execution",
            "tests.core.test_application_control_lease.ApplicationControlLeaseTests.test_workspace_access_revocation_releases_control_without_stopping_execution",
            "tests.core.test_application_observation.ApplicationObservationTests.test_observe_revocation_removes_metadata_but_preserves_process",
        ),
        "status": "PARTIAL_EXISTING_CASES",
    },
    "render-and-queued-observation-revocation": {
        "suites": ("application-observation",),
        "tests": (
            "tests.core.test_application_observation.ApplicationObservationTests.test_post_render_revocation_returns_fenced_denial_without_observation",
            "tests.core.test_application_observation.ApplicationObservationTests.test_revocation_while_observation_reply_is_queued_closes_channel",
            "tests.core.test_application_observation.ApplicationObservationTests.test_revocation_before_post_render_check_returns_no_metadata",
        ),
        "status": "PARTIAL_NO_NATIVE_PIXEL_OUTPUT_WITNESS",
    },

}
M09_HARNESS_TESTS = tuple(
    "tests.m09_harness.test_m09_deployed_gate.M09DeployedGateTests." + name
    for name in (
        "test_case_manifest_has_seven_named_cases_and_no_duplicate_selectors",
        "test_case_source_inventory_fails_closed_for_missing_future_witness",
        "test_case_source_inventory_rejects_duplicate_method_definitions",
        "test_required_inventory_rejects_missing_skip_and_duplicate_registration",
        "test_wrong_candidate_refuses_before_m06_gate_launch",
        "test_valid_preflight_forwards_all_m08_and_m09_requirements",
        "test_separate_m09_harness_inventory_rejects_missing_duplicate_skip_and_wrong_count",
        "test_m09_suite_manifest_is_additive_to_m08",
    )
)


def expected_m09_harness_tests():
    expected = tuple(sorted(M09_HARNESS_TESTS))
    if (len(expected) != 8 or len(set(expected)) != 8
            or any(not name.startswith("tests.m09_harness.") for name in expected)):
        raise RuntimeError("M09 harness inventory must contain eight unique dedicated test IDs")
    return expected


def validate_m09_harness_output(output, exit_code):
    expected = expected_m09_harness_tests()
    pattern = re.compile(r"(?m)^(\S+) \(([^)]+)\) \.\.\. ([^\r\n]+)$")
    rows = [(match.group(2), match.group(1), match.group(3).strip())
            for match in pattern.finditer(output)]
    reported = [row[0] for row in rows]
    counts = {name: reported.count(name) for name in set(reported)}
    duplicates = sorted(name for name, count in counts.items() if count > 1)
    skipped = sorted(name for name, _short, status in rows if status.startswith("skipped"))
    summary = re.search(r"Ran (\d+) tests in .*\n\nOK(?:\n|$)", output)
    valid = (exit_code == 0 and summary is not None and int(summary.group(1)) == 8
             and len(rows) == 8 and not duplicates and not skipped
             and all(status == "ok" and short == name.rsplit(".", 1)[-1]
                     for name, short, status in rows)
             and set(reported) == set(expected))
    result = dict(valid=valid, expected=expected, reported=reported,
                  missing=sorted(set(expected) - set(reported)),
                  unexpected=sorted(set(reported) - set(expected)),
                  duplicates=duplicates, skipped=skipped, exit_code=exit_code)
    if not valid:
        raise RuntimeError("M09 dedicated harness inventory failed: " + json.dumps(result, sort_keys=True))
    return result


def run_m09_harness(source, deployment, candidate):
    host = socket.gethostname().split(".")[0]
    if host not in m06.TEST_HOSTS:
        raise RuntimeError("M09 dedicated harness may run only on sb16 or sb16-2")
    source = Path(source).resolve(strict=True)
    deployment = Path(deployment).resolve(strict=True)
    root = deployment / "evidence" / "m09-harness"
    if root.is_symlink() or root.resolve().is_relative_to(source):
        raise RuntimeError("M09 harness evidence must remain outside deployed source")
    root.mkdir(parents=True, exist_ok=True)
    attempt = candidate[:12] + "-" + uuid.uuid4().hex[:12]
    output = root / attempt
    output.mkdir(parents=False, exist_ok=False)
    command = [sys.executable, "-B", "-m", "unittest",
               "tests.m09_harness.test_m09_deployed_gate", "-v"]
    environment = os.environ.copy()
    pythonpath = [str(source)]
    if environment.get("PYTHONPATH"):
        pythonpath.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(pythonpath)
    try:
        result = subprocess.run(command, cwd=source, env=environment, text=True,
                                capture_output=True, timeout=180)
        text = result.stdout + result.stderr
        (output / "m09-harness.log").write_text(text, encoding="utf-8", newline="\n")
        inventory = validate_m09_harness_output(text, result.returncode)
        status = "passed_unqualified"
        exit_code = result.returncode
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        text = str(error)
        log = output / "m09-harness.log"
        if not log.exists():
            log.write_text(text + "\n", encoding="utf-8", newline="\n")
        inventory = dict(valid=False, error=repr(error))
        status = "failed_or_incomplete"
        exit_code = getattr(locals().get("result"), "returncode", None)
    log_path = output / "m09-harness.log"
    receipt = dict(schema=1, status=status, qualification="UNQUALIFIED",
                   candidate=candidate, host=host, command=command,
                   source=str(source), log=str(log_path),
                   log_sha256=hashlib.sha256(log_path.read_bytes()).hexdigest(),
                   inventory=inventory, exit_code=exit_code, phase_complete=False)
    receipt_path = output / "m09-harness.json"
    receipt["receipt_path"] = str(receipt_path)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8", newline="\n")
    if status != "passed_unqualified":
        raise RuntimeError("M09 dedicated harness failed; inspect " + str(receipt_path))
    return receipt


def _case_selectors():
    selectors = [selector for row in M09_CASES.values() for selector in row["tests"]]
    suites = {suite for row in M09_CASES.values() for suite in row["suites"]}
    if (len(M09_CASES) != 7 or len(selectors) != len(set(selectors))
            or not suites.issubset(M09_REQUIRED) or suites != set(M09_REQUIRED)):
        raise RuntimeError("M09 focused case manifest must contain seven unique cases covering its exact suites")
    if any(not row.get("status") for row in M09_CASES.values()):
        raise RuntimeError("M09 focused case manifest must label each case's evidence status")
    modules = {selector.rsplit(".", 2)[0] for selector in selectors}
    if modules != set(M09_SUITE_BY_MODULE) or set(M09_SUITE_BY_MODULE.values()) != set(M09_REQUIRED):
        raise RuntimeError("M09 named cases must map to the exact required runtime suites")
    return selectors


def validate_case_source(source):
    """Require each exact M09 unittest method in the frozen candidate source."""
    source = Path(source).resolve(strict=True)
    seen = {}
    for selector in _case_selectors():
        parts = selector.split(".")
        if len(parts) != 5 or parts[0] != "tests":
            raise RuntimeError("malformed M09 unittest selector: " + selector)
        path = source.joinpath(*parts[:2], parts[2] + ".py")
        if not path.is_file() or path.is_symlink():
            raise RuntimeError("M09 case source is missing or unsafe: " + str(path))
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeError) as error:
            raise RuntimeError("cannot parse M09 case source " + str(path) + ": " + str(error)) from error
        class_name, method_name = parts[3], parts[4]
        matches = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                matches.extend(child for child in node.body
                               if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                               and child.name == method_name)
        if len(matches) != 1:
            raise RuntimeError("M09 required case selector must resolve exactly once: " + selector)
        seen[selector] = dict(path=str(path.relative_to(source)), method=method_name)
    return seen


def require_m09_inventory(inventory):
    rows = inventory if isinstance(inventory, list) else inventory.get("tests", [])
    try:
        names = m06.audit_inventory(rows, REQUIRED)
    except (KeyError, TypeError, RuntimeError) as error:
        raise RuntimeError("M09 qualification inventory refused: " + str(error)) from error
    by_name = {row.get("name"): row for row in rows}
    skippable = sorted(name for name in REQUIRED if any(
        prop.get("name") in ("SKIP_RETURN_CODE", "SKIP_REGULAR_EXPRESSION")
        for prop in by_name[name].get("properties", [])))
    if skippable:
        raise RuntimeError("M09 qualification inventory refused: required CTest registrations can skip: "
                           + ", ".join(skippable))
    if not M09_REQUIRED.issubset(set(names)):
        raise RuntimeError("M09 qualification inventory lacks a focused application-control suite")
    return names


def validate_m09_method_log(output, inventory):
    """Join named unittest PASS records to their actual CTest registration numbers."""
    require_m09_inventory(inventory)
    # Filtered CTest batches renumber tests. Use their own start headers,
    # while requiring each suite to belong to the checked candidate inventory.
    numbers = {}
    started = set()
    expected = {}
    for selector in _case_selectors():
        module, cls, method = selector.rsplit(".", 2)
        suite = M09_SUITE_BY_MODULE[module]
        expected[suite, cls + "." + method] = selector
    observed = {selector: [] for selector in expected.values()}
    pending = {}

    def record(suite, identity, status, line):
        observed[expected[suite, identity]].append(dict(suite=suite, runtime_id=identity,
            status=status, terminal_line=line))

    for line_number, line in enumerate(output.splitlines(), 1):
        start = re.fullmatch(r"\s*Start\s+(\d+): (\S+)", line)
        if start is not None and start.group(2) in M09_REQUIRED:
            number, suite = int(start.group(1)), start.group(2)
            if number in numbers or suite in started:
                raise RuntimeError("M09 named runtime suite has duplicate CTest start headers")
            numbers[number] = suite
            started.add(suite)
            continue
        prefix = re.fullmatch(r"(\d+): (.*)", line)
        if prefix is None:
            continue
        suite = numbers.get(int(prefix.group(1)))
        if suite not in M09_REQUIRED:
            continue
        body = prefix.group(2)
        case = re.fullmatch(r"(test_\w+) \(([^)]+)\) \.\.\. (.*)", body)
        if case is not None:
            if suite in pending:
                raise RuntimeError("M09 named runtime case has no terminal result: " + str(pending[suite]))
            method, identity, status = case.groups()
            parts = identity.split(".")
            cls = parts[-2] if len(parts) > 1 and parts[-1] == method else parts[-1]
            key = cls + "." + method
            if (suite, key) not in expected:
                continue
            if status == "ok" or status in ("FAIL", "ERROR") or status.startswith("skipped"):
                record(suite, key, status, line_number)
            else:
                # Healthy fixture diagnostics can split unittest's start and result.
                pending[suite] = key
        elif suite in pending and (body in ("ok", "FAIL", "ERROR") or body.startswith("skipped")):
            record(suite, pending.pop(suite), body, line_number)
    invalid = {name: hits for name, hits in observed.items()
               if len(hits) != 1 or hits[0]["status"] != "ok"}
    if pending or invalid:
        raise RuntimeError("M09 named runtime coverage is incomplete or invalid: " +
                           json.dumps(dict(pending=pending, invalid=invalid), sort_keys=True))
    return {name: hits[0] for name, hits in observed.items()}


def require_m09_runtime_evidence(deployment, attempt, candidate):
    """Write separate M09 method evidence after the cumulative local gate succeeds."""
    deployment = Path(deployment).resolve(strict=True)
    out = deployment / "evidence" / attempt
    receipt_path = out / "m09-named-runtime.json"
    if receipt_path.exists():
        raise RuntimeError("M09 named runtime receipt already exists: " + str(receipt_path))
    receipt = dict(candidate=candidate, qualification="UNQUALIFIED", phase_complete=False,
                   requirement="Exact named M09 methods pass in their required CTest suites", profiles={})
    try:
        provenance = json.loads((deployment / "provenance.json").read_text())
        report = json.loads((out / "run.json").read_text())
        if provenance.get("candidate") != candidate or report.get("candidate") != candidate:
            raise RuntimeError("candidate does not match M09 runtime evidence")
        if report.get("host") != socket.gethostname().split(".")[0]:
            raise RuntimeError("host does not match M09 runtime evidence")
        if (report.get("status") != "local_passed_crosshost_pending"
                or report.get("source_sha256_rechecked") is not True):
            raise RuntimeError("cumulative local gate has not passed its final source checks")
        profiles = report.get("selected_profiles")
        if (not isinstance(profiles, list) or not profiles or len(profiles) != len(set(profiles))
                or not set(profiles).issubset(m06.PROFILES)):
            raise RuntimeError("invalid M09 runtime profile selection")
        case_sources = validate_case_source(deployment / "source")
        for profile in profiles:
            if report["profiles"][profile].get("status") != "passed_needs_crosshost_and_review":
                raise RuntimeError("cumulative runtime profile did not pass: " + profile)
            log = out / profile / "core.log"
            raw = log.read_bytes()
            methods = validate_m09_method_log(raw.decode("utf-8", errors="replace"),
                                              provenance["profiles"][profile]["inventory"])
            receipt["profiles"][profile] = dict(status="passed_needs_crosshost_and_review",
                log=str(log), log_sha256=hashlib.sha256(raw).hexdigest(), methods=methods,
                named_method_count=len(methods))
        receipt.update(status="local_named_methods_passed_crosshost_pending", host=report["host"],
                       source_method_count=len(case_sources))
    except (OSError, KeyError, ValueError, RuntimeError) as error:
        receipt.update(status="failed_or_incomplete", error=repr(error))
        if out.is_dir():
            m06.write_json(receipt_path, receipt)
        raise RuntimeError("M09 named runtime evidence failed: " + str(error)) from error
    m06.write_json(receipt_path, receipt)
    return receipt


def require_deployment_inventory(deployment, candidate):
    deployment = Path(deployment).resolve(strict=True)
    if not re.fullmatch(r"[0-9a-f]{40}", str(candidate)):
        raise RuntimeError("M09 requires one explicit full candidate SHA")
    provenance = json.loads((deployment / "provenance.json").read_text(encoding="utf-8"))
    receipt = json.loads((deployment / "deployment-receipt.json").read_text(encoding="utf-8"))
    if (provenance.get("candidate") != candidate or receipt.get("candidate") != candidate
            or receipt.get("status") != "verified"):
        raise RuntimeError("M09 deployment provenance/receipt does not match the requested candidate")
    profiles = provenance.get("profiles")
    if not isinstance(profiles, dict) or set(profiles) != set(m06.PROFILES):
        raise RuntimeError("M09 requires both Debug and ASan-UBSan deployed inventories")
    for profile in m06.PROFILES:
        row = profiles[profile]
        if not isinstance(row, dict) or not isinstance(row.get("inventory"), list):
            raise RuntimeError("M09 missing CTest inventory for " + profile)
        require_m09_inventory(row["inventory"])
    source = deployment / "source"
    case_sources = validate_case_source(source)
    return dict(provenance=provenance, receipt=receipt, source=source, case_sources=case_sources)


def run_local(a):
    # Candidate and M09-specific gates are checked before M06 launches work.
    checked = require_deployment_inventory(a.deployment, a.candidate)
    m08.require_deployment_inventory(a.deployment)
    m09_harness_receipt = run_m09_harness(checked["source"], a.deployment, a.candidate)
    print("M09 dedicated harness receipt: " + m09_harness_receipt["receipt_path"])
    selected_profiles = getattr(a, "selected_profiles", None)
    storage_lane_by_profile = m08.require_storage_lane_configuration(
        profiles=m06.PROFILES if selected_profiles is None else tuple(selected_profiles))
    forwarded = argparse.Namespace(
        deployment=a.deployment, attempt=a.attempt or ("m09-" + a.candidate[:12] + "-" + uuid.uuid4().hex[:12]),
        native_controller=a.native_controller, real_world=a.real_world,
        recovery=a.recovery, host_session=a.host_session, held_input=a.held_input,
        selected_profiles=selected_profiles,
        storage_lane_by_profile=storage_lane_by_profile,
        copy_worker_helpers=True,
        collector_continuation=getattr(a, "collector_continuation", None),
        collector_continuation_sha256=getattr(a, "collector_continuation_sha256", None),
        required_tests=sorted(REQUIRED),
        dedicated_crosshost=sorted(m08.M08_DEDICATED_CROSSHOST),
        expected_harness_tests=m08.load_harness_test_inventory(),
        storage_expected_results=sorted(m08.M08_STORAGE_EXPECTED_RESULTS),
    )
    result = m06.gate(forwarded)
    if result == 0:
        require_m09_runtime_evidence(a.deployment, forwarded.attempt, a.candidate)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    gate = sub.add_parser("run-local")
    gate.add_argument("--deployment", type=Path, required=True)
    gate.add_argument("--candidate", required=True)
    gate.add_argument("--attempt")
    for name in ("native-controller", "real-world", "recovery", "host-session", "held-input"):
        gate.add_argument("--" + name, required=True)
    gate.add_argument("--selected-profile", dest="selected_profiles", action="append", choices=m06.PROFILES)
    gate.add_argument("--collector-continuation", type=Path)
    gate.add_argument("--collector-continuation-sha256")
    gate.set_defaults(fn=run_local)
    args = parser.parse_args()
    try:
        return args.fn(args)
    except BaseException as error:
        print("BLOCKED/FAILED: " + repr(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
