#!/usr/bin/env python3
"""Additive M09 application-group control profile over the frozen M08 gate."""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve()
M08_PATH = HERE.with_name("m08_deployed_gate.py")
SPEC = importlib.util.spec_from_file_location("m08_deployed_gate", M08_PATH)
m08 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m08)
m07 = m08.m07
m06 = m08.m06

# Keep M08's cumulative profile and add the suites that execute M09 witnesses.
# The native group witness is absent now, so this draft intentionally blocks.
M09_REQUIRED = frozenset({
    "application-control-lease",
    "application-observation",
    "world-application-control",
    "native-surface_identity",
    "native-world_surface_groups",
})
REQUIRED = frozenset(m08.REQUIRED) | M09_REQUIRED
M09_CASES = {
    "application-group-identity": {
        "suites": ("native-world_surface_groups", "world-application-control"),
        "tests": (
            "tests.native.test_world_surface_groups.WorldSurfaceGroupTests.test_dialog_popup_group_control_and_foreign_hint_use_connection_identity",
            "tests.native.test_world_surface_groups.WorldSurfaceGroupTests.test_same_application_connections_can_form_relationship",
            "tests.core.test_world_application_control.WorldApplicationControlTests.test_popup_uses_same_application_reservation",
        ),
        "status": "BLOCKED_PENDING_NATIVE_DIALOG_POPUP_WITNESS",
    },
    "independent-controllers": {
        "suites": ("world-application-control", "application-control-lease"),
        "tests": (
            "tests.core.test_world_application_control.WorldApplicationControlTests.test_independent_applications_allow_simultaneous_actors",
        ),
        "status": "EXISTING_CASE",
    },
    "balanced-transfer": {
        "suites": ("world-application-control", "application-control-lease"),
        "tests": (
            "tests.core.test_world_application_control.WorldApplicationControlTests.test_unfocus_releases_native_input_before_handoff",
            "tests.core.test_world_application_control.WorldApplicationControlTests.test_controller_disconnect_releases_before_handoff",
            "tests.core.test_world_application_control.WorldApplicationControlTests.test_actor_revocation_releases_native_input",
            "tests.core.test_world_application_control.WorldApplicationControlTests.test_observation_revocation_cancels_control",
            "tests.core.test_world_application_control.WorldApplicationControlTests.test_unmapped_capture_releases_reservation",
            "tests.core.test_application_control_lease.ApplicationControlLeaseTests.test_revoked_reservation_blocks_handoff_until_release_acknowledges_cancellation",
            "tests.native.test_world_surface_groups.WorldSurfaceGroupTests.test_destroyed_parent_releases_descendant_input",
        ),
        "status": "PARTIAL_EXISTING_CASES",
    },
    "generation-fence": {
        "suites": ("world-application-control", "native-surface_identity"),
        "tests": (
            "tests.core.test_world_application_control.WorldApplicationControlTests.test_resized_aim_rejects_stale_pixels",
            "tests.native.test_surface_identity.SurfaceIdentityTests.test_remap_changes_generation_without_changing_owner",
            "tests.native.test_surface_identity.SurfaceIdentityTests.test_reused_xid_does_not_reuse_window_generation",
        ),
        "status": "PARTIAL_NO_BROKER_WAIT_REPLACEMENT_WITNESS",
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
    "tests.harness.test_m09_deployed_gate.M09DeployedGateTests." + name
    for name in (
        "test_case_manifest_has_seven_named_cases_and_no_duplicate_selectors",
        "test_case_source_inventory_fails_closed_for_missing_future_witness",
        "test_case_source_inventory_rejects_duplicate_method_definitions",
        "test_required_inventory_rejects_missing_skip_and_duplicate_registration",
        "test_wrong_candidate_refuses_before_m06_gate_launch",
        "test_valid_preflight_forwards_all_m08_and_m09_requirements",
        "test_harness_expectations_append_without_changing_m08_baseline",
        "test_m09_suite_manifest_is_additive_to_m08",
    )
)


def expected_harness_tests():
    """Preserve M08's exact 89 IDs and append this module's exact IDs."""
    inherited = tuple(m08.load_harness_test_inventory())
    result = inherited + M09_HARNESS_TESTS
    if len(inherited) != 89 or len(set(inherited)) != 89:
        raise RuntimeError("M09 refuses an altered M08 89-test harness baseline")
    if len(result) != 97 or len(set(result)) != 97:
        raise RuntimeError("M09 additive harness inventory must contain 89 inherited and 8 unique M09 IDs")
    return tuple(sorted(result))


def _case_selectors():
    selectors = [selector for row in M09_CASES.values() for selector in row["tests"]]
    suites = {suite for row in M09_CASES.values() for suite in row["suites"]}
    if (len(M09_CASES) != 7 or len(selectors) != len(set(selectors))
            or not suites.issubset(M09_REQUIRED) or suites != set(M09_REQUIRED)):
        raise RuntimeError("M09 focused case manifest must contain seven unique cases covering its exact suites")
    if any(not row.get("status") for row in M09_CASES.values()):
        raise RuntimeError("M09 focused case manifest must label each case's evidence status")
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
    require_deployment_inventory(a.deployment, a.candidate)
    m08.require_deployment_inventory(a.deployment)
    selected_profiles = getattr(a, "selected_profiles", None)
    storage_lane_by_profile = m08.require_storage_lane_configuration(
        profiles=m06.PROFILES if selected_profiles is None else tuple(selected_profiles))
    forwarded = argparse.Namespace(
        deployment=a.deployment, attempt=a.attempt,
        native_controller=a.native_controller, real_world=a.real_world,
        recovery=a.recovery, host_session=a.host_session, held_input=a.held_input,
        selected_profiles=selected_profiles,
        storage_lane_by_profile=storage_lane_by_profile,
        copy_worker_helpers=True,
        collector_continuation=getattr(a, "collector_continuation", None),
        collector_continuation_sha256=getattr(a, "collector_continuation_sha256", None),
        required_tests=sorted(REQUIRED),
        dedicated_crosshost=sorted(m08.M08_DEDICATED_CROSSHOST),
        expected_harness_tests=expected_harness_tests(),
        storage_expected_results=sorted(m08.M08_STORAGE_EXPECTED_RESULTS),
    )
    return m06.gate(forwarded)


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
