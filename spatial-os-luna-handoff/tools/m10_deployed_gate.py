#!/usr/bin/env python3
"""Mandatory additive M10 qualification profile over the complete M09 gate."""
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
ROOT = HERE.parents[1]
SPEC = importlib.util.spec_from_file_location("m09_deployed_gate", HERE.with_name("m09_deployed_gate.py"))
m09 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m09)
m08 = m09.m08
m06 = m09.m06

# M06 runs the complete local CTest inventory. This additive list makes every
# M10 suite mandatory in both packaged profiles instead of merely present.
M10_REQUIRED = frozenset({
    "actor-control-adapters",
    "actor-control-client",
    "actor-control-layer",
    "actor-control-level-motion",
    "actor-control-menu",
    "actor-control-world",
    "actor-device-stream",
    "actor-local-view-authority",
    "actor-motion-refresh-client",
    "actor-relative-look",
    "actor-session-client",
    "actor-stop-lifetime",
    "actor-stop-liveness",
    "actor-stop-owner",
    "actor-stop-progress",
    "app-initializer-lifecycle",
    "application-slots",
    "capture-presentation-native",
    "control-administrative-transport",
    "host-actor-shared-control",
    "host-actor-worker",
    "host-capture-start-failure-native",
    "host-continuous-capture-native",
    "local-frame-client",
    "local-frame-lifetime",
    "local-frame-owner",
    "local-frame-presentation",
    "local-presentation-continuation",
    "local-presentation-retirement",
    "motion-frame-selection",
    "native-actor-binding",
    "native-actor-input",
    "native-application-dependencies",
    "native-application-input",
    "native-application-launch",
    "native-fb-line-arithmetic",
    "native-input-observer-artifact",
    "native-input-observer-collector",
    "presentation-owner",
    "render-input-progress",
    "render-observation-client",
    "render-runtime",
    "scripted-actor-native",
    "session-application",
    "three-actor-applications-native",
    "three-actor-editor-terminal-native",
    "three-actor-files-native",
    "three-actor-raw-input-native",
    "world-authority",
    "world-actor-scale",
    "world-issuer-progress",
    "world-participant-native",
    "world-remote-lifecycle",
    "workspace-authority",
    "workspace-processes",
})
REQUIRED = frozenset(m09.REQUIRED) | M10_REQUIRED

M10_SUITE_BY_MODULE = {
    "tests.core.test_actor_stop_lifetime": "actor-stop-lifetime",
    "tests.core.test_actor_stop_liveness": "actor-stop-liveness",
    "tests.core.test_actor_stop_owner": "actor-stop-owner",
    "tests.core.test_actor_stop_progress": "actor-stop-progress",
    "tests.core.test_render_input_progress": "render-input-progress",
    "tests.core.test_world_authority": "world-authority",
    "tests.core.test_world_actor_scale": "world-actor-scale",
    "tests.core.test_world_issuer_progress": "world-issuer-progress",
    "tests.core.test_world_participant_native": "world-participant-native",
    "tests.core.test_world_remote_lifecycle": "world-remote-lifecycle",
    "tests.core.test_workspace_authority": "workspace-authority",
    "tests.native.test_session_application": "session-application",
    "tests.native.test_three_actor_applications": "three-actor-applications-native",
    "tests.native.test_three_actor_editor_terminal": "three-actor-editor-terminal-native",
    "tests.native.test_three_actor_files": "three-actor-files-native",
    "tests.native.test_three_actor_raw_input": "three-actor-raw-input-native",
}
M10_CASES = {
    "movement-and-revocation": (
        "tests.core.test_world_issuer_progress.WorldIssuerProgressTests.test_nested_close_wait_keeps_other_actor_motion_and_revocation_live",
        "tests.core.test_world_actor_scale.WorldActorScaleTests.test_255_actors_refresh_during_other_actor_authentication",
        "tests.core.test_world_actor_scale.WorldActorScaleTests.test_256_actors_refresh_movement_within_expiry",
        "tests.core.test_world_actor_scale.WorldActorScaleTests.test_256_distinct_actors_authenticate_and_remain_live",
        "tests.core.test_world_remote_lifecycle.RemoteLifecycleTests.test_256_tls_slots_reject_excess_and_recover",
        "tests.core.test_world_issuer_progress.WorldIssuerProgressTests.test_four_authentications_wait_without_blocking_authenticated_actor",
        "tests.core.test_world_issuer_progress.WorldIssuerProgressTests.test_default_checked_movement_stays_live_and_revocable_during_auxiliary_info",
        "tests.core.test_world_issuer_progress.WorldIssuerProgressTests.test_default_disabled_retained_refresh_has_no_motion_effect",
        "tests.core.test_world_issuer_progress.WorldIssuerProgressTests.test_authenticated_motion_refresh_completes_while_info_reply_is_pending",
        "tests.core.test_world_issuer_progress.WorldIssuerProgressTests.test_pending_primary_info_does_not_gate_actor_inspection_or_movement",
        "tests.core.test_world_issuer_progress.WorldIssuerProgressTests.test_pending_auxiliary_info_does_not_gate_actor_inspection_or_movement",
        "tests.core.test_world_issuer_progress.WorldIssuerProgressTests.test_motion_refresh_progresses_behind_focused_key_during_other_actor_authentication",
        "tests.core.test_world_issuer_progress.WorldIssuerProgressTests.test_direct_world_without_guardian_defers_key_tap_during_authentication",
        "tests.core.test_world_issuer_progress.WorldIssuerProgressTests.test_other_actor_authentication_does_not_gate_motion_refresh",
        "tests.native.test_session_application.SessionApplicationTests.test_supervised_key_tap_progresses_during_other_actor_authentication",
        "tests.core.test_world_issuer_progress.WorldIssuerProgressTests.test_actor_revocation_releases_native_input_during_other_actor_authentication",
        "tests.core.test_world_issuer_progress.WorldIssuerProgressTests.test_motion_refresh_progresses_while_native_renderer_is_slow",
        "tests.core.test_workspace_authority.WorkspaceAuthorityTests.test_authenticated_motion_refresh_runs_during_delayed_broker_access_reply",
        "tests.core.test_world_authority.WorldAuthorityTests.test_navigation_revocation_stops_motion_during_other_actor_authentication",
    ),
    "stop-and-release": (
        "tests.core.test_world_participant_native.WorldParticipantNativeTests.test_invalid_prepared_seat_is_closed_without_input",
        "tests.core.test_world_participant_native.WorldParticipantNativeTests.test_independent_compositor_credential_fences_prepared_seat",
        "tests.core.test_world_participant_native.WorldParticipantNativeTests.test_first_pointer_prepares_empty_seat_before_delivery",
        "tests.core.test_world_participant_native.WorldParticipantNativeTests.test_revocation_fences_prepared_seat_before_first_pointer",
        "tests.core.test_world_participant_native.WorldParticipantNativeTests.test_revocation_during_seat_preparation_discards_first_pointer",
        "tests.native.test_session_application.SessionApplicationTests.test_supervisor_guardian_fences_first_pointer_before_issuer_commits_revocation",
        "tests.core.test_world_issuer_progress.WorldIssuerProgressTests.test_queued_key_has_no_effect_after_issuer_revocation_ack",
        "tests.core.test_world_issuer_progress.WorldIssuerProgressTests.test_revoked_actor_cannot_finish_delayed_credential_check",
        "tests.core.test_world_issuer_progress.WorldIssuerProgressTests.test_closed_actor_channel_discards_delayed_credential_check",
        "tests.core.test_world_issuer_progress.WorldIssuerProgressTests.test_revocation_discards_queued_key_during_other_actor_authentication",
        "tests.core.test_world_issuer_progress.WorldIssuerProgressTests.test_revocation_discards_unsent_cooperative_inspection",
        "tests.core.test_world_issuer_progress.WorldIssuerProgressTests.test_shutdown_cancels_pending_auxiliary_info",
        "tests.core.test_world_issuer_progress.WorldIssuerProgressTests.test_auxiliary_info_connection_loss_releases_native_input",
        "tests.core.test_actor_stop_liveness.ActorStopLivenessTests.test_stop_completes_during_another_actor_authentication",
        "tests.core.test_actor_stop_liveness.ActorStopLivenessTests.test_controlled_stop_completes_during_another_actor_authentication",
        "tests.core.test_actor_stop_liveness.ActorStopLivenessTests.test_cancellation_gate_rejects_invalid_context_and_sequence_before_effects",
        "tests.core.test_actor_stop_progress.ActorStopProgressTests.test_native_stop_releases_input_during_issuer_wait",
        "tests.core.test_actor_stop_progress.ActorStopProgressTests.test_native_controlled_stop_releases_input_during_issuer_wait",
        "tests.core.test_actor_stop_progress.ActorStopProgressTests.test_stop_retires_queued_capture_motion_and_world_exits_cleanly",
        "tests.core.test_world_issuer_progress.WorldIssuerProgressTests.test_actor_revocation_releases_input_without_waiting_for_other_actor_authentication",
        "tests.core.test_world_issuer_progress.WorldIssuerProgressTests.test_world_issuer_connection_loss_releases_input_and_keeps_workspace_application",
        "tests.core.test_world_issuer_progress.WorldIssuerProgressTests.test_global_issuer_loss_releases_key_and_clears_pointer_button_state",
        "tests.native.test_session_application.SessionApplicationTests.test_supervisor_guardian_fences_input_before_issuer_commits_revocation",
        "tests.core.test_actor_stop_lifetime.ActorStopLifetimeTests.test_pending_native_stop_preserves_other_actor_movement_and_needs_checked_release",
        "tests.core.test_actor_stop_lifetime.ActorStopLifetimeTests.test_disconnect_retains_close_barrier_and_refuses_early_actor_rebind",
        "tests.core.test_actor_stop_lifetime.ActorStopLifetimeTests.test_revocation_during_pending_cancel_upgrades_close_without_stale_success",
        "tests.core.test_actor_stop_owner.ActorStopOwnerTests.test_checked_close_gates_new_actor_incarnation",
        "tests.core.test_actor_stop_owner.ActorStopOwnerTests.test_abandoned_stop_retains_cleanup_until_checked_close",
        "tests.core.test_actor_stop_owner.ActorStopOwnerTests.test_typed_close_denial_retains_barrier_without_retry",
        "tests.core.test_actor_stop_owner.ActorStopOwnerTests.test_lost_close_transport_retains_native_owner_without_replay",
        "tests.core.test_actor_stop_owner.ActorStopOwnerTests.test_typed_cancel_denial_can_upgrade_to_one_distinct_close",
        "tests.core.test_render_input_progress.RenderInputProgressTests.test_delayed_reply_keeps_polls_bounded_and_exact_owner",
    ),
    "human-and-two-agent-workflows": (
        "tests.native.test_three_actor_files.ThreeActorFileTests.test_human_and_two_local_agents_create_edit_rename_and_read_real_shared_files",
        "tests.native.test_three_actor_files.ThreeActorFileTests.test_read_only_local_agent_cannot_create_a_file_or_launch_a_process",
        "tests.native.test_three_actor_applications.ThreeActorApplicationTests.test_exact_placement_retry_returns_receipt_after_native_application_exits",
        "tests.native.test_three_actor_applications.ThreeActorApplicationTests.test_human_and_two_agents_place_real_windows_and_use_distinct_native_devices",
        "tests.native.test_three_actor_applications.ThreeActorApplicationTests.test_agent_key_tap_batches_preserve_every_edge_at_high_rate",
        "tests.native.test_three_actor_raw_input.ThreeActorRawInputTests.test_raw_device_and_application_witnesses_preserve_high_rate_taps",
        "tests.native.test_three_actor_applications.ThreeActorApplicationTests.test_atomic_key_tap_survives_delayed_client_completion_without_repeat",
        "tests.native.test_three_actor_applications.ThreeActorApplicationTests.test_explicit_key_hold_survives_long_turn_and_stop_releases_it",
        "tests.native.test_three_actor_editor_terminal.ThreeActorEditorTerminalTests.test_human_and_two_agents_type_and_save_real_editor_files",
        "tests.native.test_three_actor_editor_terminal.ThreeActorEditorTerminalTests.test_human_and_two_agents_type_commands_in_real_terminals",
    ),
}
M10_SELECTORS = tuple(selector for rows in M10_CASES.values() for selector in rows)
M10_HARNESS_TESTS = tuple(
    "tests.m10_harness.test_m10_deployed_gate.M10DeployedGateTests." + name
    for name in (
        "test_required_profile_is_additive_to_m09",
        "test_every_required_suite_name_is_unique_and_m10_scoped",
        "test_named_case_suite_map_covers_every_module",
        "test_named_sources_resolve_exactly_once",
        "test_deployment_inventory_preserves_m09_and_m10_case_sources",
        "test_named_method_log_requires_every_case_once",
        "test_named_method_log_rejects_skipped_case",
        "test_inventory_rejects_missing_m10_suite",
        "test_inventory_rejects_skippable_m10_suite",
    )
)


def _flatten_selectors():
    selectors = list(M10_SELECTORS)
    if len(M10_CASES) != 3 or len(selectors) != len(set(selectors)):
        raise RuntimeError("M10 named case manifest is missing or duplicated")
    modules = {selector.rsplit(".", 2)[0] for selector in selectors}
    if modules != set(M10_SUITE_BY_MODULE):
        raise RuntimeError("M10 named case module-to-suite map differs")
    if not M10_REQUIRED or REQUIRED != m09.REQUIRED | M10_REQUIRED:
        raise RuntimeError("M10 required profile is not additive to M09")
    return selectors


def validate_case_source(source):
    source = Path(source).resolve(strict=True)
    resolved = {}
    for selector in _flatten_selectors():
        parts = selector.split(".")
        if len(parts) != 5 or parts[0] != "tests":
            raise RuntimeError("malformed M10 selector: " + selector)
        path = source.joinpath(*parts[:2], parts[2] + ".py")
        if not path.is_file() or path.is_symlink():
            raise RuntimeError("M10 case source is missing or unsafe: " + str(path))
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeError) as error:
            raise RuntimeError("cannot parse M10 case source " + str(path)) from error
        classes = [node for node in ast.walk(tree)
                   if isinstance(node, ast.ClassDef) and node.name == parts[3]]
        matches = [child for cls in classes for child in cls.body
                   if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and child.name == parts[4]]
        if len(matches) != 1:
            raise RuntimeError("M10 required case selector must resolve exactly once: " + selector)
        resolved[selector] = dict(path=str(path.relative_to(source)), method=parts[4],
                                  suite=M10_SUITE_BY_MODULE[".".join(parts[:3])])
    return resolved


def require_m10_inventory(inventory):
    rows = inventory if isinstance(inventory, list) else inventory.get("tests", [])
    try:
        names = m06.audit_inventory(rows, REQUIRED)
    except (KeyError, TypeError, RuntimeError) as error:
        raise RuntimeError("M10 qualification inventory refused: " + str(error)) from error
    by_name = {row.get("name"): row for row in rows}
    skippable = sorted(name for name in REQUIRED if any(
        prop.get("name") in ("SKIP_RETURN_CODE", "SKIP_REGULAR_EXPRESSION")
        for prop in by_name[name].get("properties", [])))
    if skippable:
        raise RuntimeError("M10 required CTest registrations can skip: " + ", ".join(skippable))
    return names


def require_deployment_inventory(deployment, candidate):
    checked = m09.require_deployment_inventory(deployment, candidate)
    m08.require_deployment_inventory(deployment)
    for profile in m06.PROFILES:
        require_m10_inventory(checked["provenance"]["profiles"][profile]["inventory"])
    m10_case_sources = validate_case_source(checked["source"])
    return dict(checked, m10_case_sources=m10_case_sources)


def validate_m10_method_log(output, inventory):
    require_m10_inventory(inventory)
    expected = {}
    for selector in _flatten_selectors():
        parts = selector.split(".")
        suite = M10_SUITE_BY_MODULE[".".join(parts[:3])]
        expected[suite, parts[-2] + "." + parts[-1]] = selector
    observed = {selector: [] for selector in expected.values()}
    numbers = {}
    started = set()
    pending = {}

    def record(suite, identity, status, line):
        observed[expected[suite, identity]].append(dict(suite=suite, runtime_id=identity,
            status=status, terminal_line=line))

    for line_number, line in enumerate(output.splitlines(), 1):
        start = re.fullmatch(r"\s*Start\s+(\d+): (\S+)", line)
        if start is not None and start.group(2) in REQUIRED:
            number, suite = int(start.group(1)), start.group(2)
            if number in numbers or suite in started:
                raise RuntimeError("M10 named runtime suite has duplicate CTest start headers")
            numbers[number] = suite
            started.add(suite)
            continue
        prefix = re.fullmatch(r"(\d+): (.*)", line)
        if prefix is None:
            continue
        suite = numbers.get(int(prefix.group(1)))
        if suite not in REQUIRED:
            continue
        body = prefix.group(2)
        case = re.fullmatch(r"(test_\w+) \(([^)]+)\) \.\.\. (.*)", body)
        if case is not None:
            if suite in pending:
                raise RuntimeError("M10 named case has no terminal result: " + str(pending[suite]))
            method, identity, status = case.groups()
            parts = identity.split(".")
            cls = parts[-2] if len(parts) > 1 and parts[-1] == method else parts[-1]
            key = cls + "." + method
            if (suite, key) not in expected:
                continue
            if status == "ok" or status in ("FAIL", "ERROR") or status.startswith("skipped"):
                record(suite, key, status, line_number)
            else:
                pending[suite] = key
        elif suite in pending and (body in ("ok", "FAIL", "ERROR") or body.startswith("skipped")):
            record(suite, pending.pop(suite), body, line_number)
    invalid = {selector: hits for selector, hits in observed.items()
               if len(hits) != 1 or hits[0]["status"] != "ok"}
    if pending or invalid:
        raise RuntimeError("M10 named runtime coverage is incomplete or invalid: " +
                           json.dumps(dict(pending=pending, invalid=invalid), sort_keys=True))
    return {selector: hits[0] for selector, hits in observed.items()}


def validate_m10_harness_output(output, exit_code):
    expected = tuple(sorted(M10_HARNESS_TESTS))
    if len(expected) != 9 or len(set(expected)) != 9:
        raise RuntimeError("M10 harness inventory must contain nine unique test IDs")
    pattern = re.compile(r"(?m)^(\S+) \(([^)]+)\) \.\.\. ([^\r\n]+)$")
    rows = [(match.group(2), match.group(1), match.group(3).strip())
            for match in pattern.finditer(output)]
    reported = [row[0] for row in rows]
    duplicates = sorted(name for name in set(reported) if reported.count(name) > 1)
    skipped = sorted(name for name, _short, status in rows if status.startswith("skipped"))
    summary = re.search(r"Ran (\d+) tests in .*\n\nOK(?:\n|$)", output)
    valid = (exit_code == 0 and summary is not None and int(summary.group(1)) == len(expected)
             and len(rows) == len(expected) and not duplicates and not skipped
             and all(status == "ok" and short == name.rsplit(".", 1)[-1]
                     for name, short, status in rows)
             and set(reported) == set(expected))
    result = dict(valid=valid, expected=expected, reported=reported,
                  missing=sorted(set(expected) - set(reported)), duplicates=duplicates,
                  skipped=skipped, exit_code=exit_code)
    if not valid:
        raise RuntimeError("M10 dedicated harness inventory failed: " + json.dumps(result, sort_keys=True))
    return result


def run_m10_harness(source, deployment, candidate):
    host = socket.gethostname().split(".")[0]
    if host not in m06.TEST_HOSTS:
        raise RuntimeError("M10 dedicated harness may run only on sb16 or sb16-2")
    source = Path(source).resolve(strict=True)
    deployment = Path(deployment).resolve(strict=True)
    root = deployment / "evidence" / "m10-harness"
    if root.is_symlink() or root.resolve().is_relative_to(source):
        raise RuntimeError("M10 harness evidence must remain outside deployed source")
    output = root / (candidate[:12] + "-" + uuid.uuid4().hex[:12])
    output.mkdir(parents=True, exist_ok=False)
    command = [sys.executable, "-B", "-m", "unittest", "tests.m10_harness.test_m10_deployed_gate", "-v"]
    environment = os.environ.copy()
    pythonpath = [str(source)]
    if environment.get("PYTHONPATH"):
        pythonpath.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(pythonpath)
    try:
        result = subprocess.run(command, cwd=source, env=environment, text=True,
                                capture_output=True, timeout=180)
        text = result.stdout + result.stderr
        (output / "m10-harness.log").write_text(text, encoding="utf-8", newline="\n")
        inventory = validate_m10_harness_output(text, result.returncode)
        status = "passed_unqualified"
        exit_code = result.returncode
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        log = output / "m10-harness.log"
        if not log.exists():
            log.write_text(repr(error) + "\n", encoding="utf-8", newline="\n")
        inventory = dict(valid=False, error=repr(error))
        status = "failed_or_incomplete"
        exit_code = getattr(locals().get("result"), "returncode", None)
    log_path = output / "m10-harness.log"
    receipt = dict(schema=1, status=status, qualification="UNQUALIFIED", candidate=candidate,
        host=host, command=command, source=str(source), log=str(log_path),
        log_sha256=hashlib.sha256(log_path.read_bytes()).hexdigest(), inventory=inventory,
        exit_code=exit_code, phase_complete=False)
    receipt_path = output / "m10-harness.json"
    receipt["receipt_path"] = str(receipt_path)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8", newline="\n")
    if status != "passed_unqualified":
        raise RuntimeError("M10 dedicated harness failed; inspect " + str(receipt_path))
    return receipt


def require_m10_runtime_evidence(deployment, attempt, candidate):
    deployment = Path(deployment).resolve(strict=True)
    out = deployment / "evidence" / attempt
    receipt_path = out / "m10-named-runtime.json"
    if receipt_path.exists():
        raise RuntimeError("M10 named runtime receipt already exists: " + str(receipt_path))
    receipt = dict(schema=1, status="failed_or_incomplete", qualification="UNQUALIFIED",
                   phase_complete=False, candidate=candidate,
                   requirement="M10 suites and named real actor workflows pass in both profiles", profiles={})
    try:
        provenance = json.loads((deployment / "provenance.json").read_text())
        report = json.loads((out / "run.json").read_text())
        if provenance.get("candidate") != candidate or report.get("candidate") != candidate:
            raise RuntimeError("candidate does not match M10 runtime evidence")
        if report.get("host") != socket.gethostname().split(".")[0]:
            raise RuntimeError("host does not match M10 runtime evidence")
        if report.get("status") != "local_passed_crosshost_pending" or report.get("source_sha256_rechecked") is not True:
            raise RuntimeError("cumulative M10 local gate has not passed its final source checks")
        if not M10_REQUIRED.issubset(set(report.get("required_tests", []))):
            raise RuntimeError("run report omitted required M10 suites")
        profiles = report.get("selected_profiles")
        if profiles != list(m06.PROFILES):
            raise RuntimeError("M10 requires the full ordered Debug and ASan-UBSan profile batch")
        for profile in profiles:
            row = report["profiles"][profile]
            if row.get("status") != "passed_needs_crosshost_and_review":
                raise RuntimeError("M10 cumulative profile did not pass: " + profile)
            core = next(item for item in row["commands"] if item["name"] == "core")
            if (core.get("exit_code") != 0 or not core.get("assertions_passed")
                    or not core.get("all_registered_present") or core.get("failed_or_skipped")):
                raise RuntimeError("complete local CTest result is incomplete: " + profile)
            log = out / profile / "core.log"
            raw = log.read_bytes()
            methods = validate_m10_method_log(raw.decode("utf-8", errors="replace"),
                                              provenance["profiles"][profile]["inventory"])
            receipt["profiles"][profile] = dict(status="passed_needs_crosshost_and_review",
                log=str(log), log_sha256=hashlib.sha256(raw).hexdigest(), methods=methods,
                named_method_count=len(methods), complete_ctest_cases=core.get("junit_cases"))
        receipt.update(status="local_m10_methods_passed_crosshost_pending",
                       host=report["host"], required_suites=sorted(M10_REQUIRED),
                       named_method_count=len(M10_SELECTORS))
    except (OSError, KeyError, ValueError, RuntimeError, StopIteration) as error:
        receipt["error"] = repr(error)
        if out.is_dir():
            m06.write_json(receipt_path, receipt)
        raise RuntimeError("M10 named runtime evidence failed: " + str(error)) from error
    m06.write_json(receipt_path, receipt)
    return receipt


def run_local(a):
    checked = require_deployment_inventory(a.deployment, a.candidate)
    print("M10 named case sources checked: " + str(len(checked["m10_case_sources"])))
    m09_harness = m09.run_m09_harness(checked["source"], a.deployment, a.candidate)
    m10_harness = run_m10_harness(checked["source"], a.deployment, a.candidate)
    print("M09 harness: " + m09_harness["receipt_path"])
    print("M10 harness: " + m10_harness["receipt_path"])
    selected_profiles = getattr(a, "selected_profiles", None)
    storage_lane_by_profile = m08.require_storage_lane_configuration(
        profiles=m06.PROFILES if selected_profiles is None else tuple(selected_profiles))
    forwarded = argparse.Namespace(
        deployment=a.deployment, attempt=a.attempt or ("m10-" + a.candidate[:12] + "-" + uuid.uuid4().hex[:12]),
        native_controller=a.native_controller, real_world=a.real_world, recovery=a.recovery,
        host_session=a.host_session, held_input=a.held_input, selected_profiles=selected_profiles,
        storage_lane_by_profile=storage_lane_by_profile, copy_worker_helpers=True,
        collector_continuation=getattr(a, "collector_continuation", None),
        collector_continuation_sha256=getattr(a, "collector_continuation_sha256", None),
        required_tests=sorted(REQUIRED), dedicated_crosshost=sorted(m08.M08_DEDICATED_CROSSHOST),
        expected_harness_tests=m08.load_harness_test_inventory(),
        storage_expected_results=sorted(m08.M08_STORAGE_EXPECTED_RESULTS))
    result = m06.gate(forwarded)
    if result == 0:
        m09.require_m09_runtime_evidence(a.deployment, forwarded.attempt, a.candidate)
        require_m10_runtime_evidence(a.deployment, forwarded.attempt, a.candidate)
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
