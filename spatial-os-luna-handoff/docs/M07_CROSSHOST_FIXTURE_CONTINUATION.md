# M07 pinned cross-host fixture continuation

The production candidate remains frozen at 1e58e65dec132a0a16f004ca4527034dca081f77.
The accepted local Debug and ASan receipts remain unchanged.
They describe that candidate and its deployed binaries.

The first cross-host semantic harness passed all six cases.
Its cleanup failed because the original owner helper treated retained dead tmux windows as live processes.
That original failure report and its evidence remain unchanged.
The native cross-host run and four-cell workspace matrix remain unrun.

This tooling adds an explicit test-only continuation.
A manifest pins one external semantic fixture and one external host helper.
The manifest also pins the frozen candidate.
M06 verifies regular non-symlink files and exact SHA256 values before launch.
It rechecks the manifest and both files immediately before fixture execution.
The fixture reports the selected helper path and hash.
Default helper selection remains the deployed bundle path.
The serializer requires the exact M06 tool hash and manifest pin.
It preserves the reviewed baseline's canonical runner receipts, retired PID proof, role/source pins, global mutex, four serialized workspace cells, and no-duplicate-dispatch rules.

Manifest history:
The original manifest at /home/dflanag3/spatial-artifacts/m07-crosshost-fixture-continuation-20261016/manifests/fixture-continuation.json was invalid JSON (SHA256 b71fc4a97e9e0de14eda9038974b39488d859e3d867f0c078d44ff02052ed40e). Its exact bytes, parse failure, and receipt remain preserved. The corrected v2 manifest remains preserved at /home/dflanag3/spatial-artifacts/m07-crosshost-fixture-continuation-20261016/manifests/fixture-continuation-v2.json (SHA256 ee0f0d314c5a32a55fb5eed538f534d57f3da7de89fb41e6be2d3bb0aa9f38d1); do not overwrite either revision.
The active review02 manifest is /home/dflanag3/spatial-artifacts/m07-crosshost-fixture-continuation-20261016/review02/manifests/fixture-continuation-v3.json, SHA256 a90e3a88eabecaeb44b0d58b4784e72da2329960582137ea905116f2a9b84189. Its schema and bytes were verified with json.loads. It selects the review02 semantic fixture and helper paths printed in fixture-continuation-v3-parse-receipt.txt.
The active M06 tool path is /home/dflanag3/spatial-artifacts/m07-crosshost-fixture-continuation-20261016/review02/tools/m06_deployed_gate.py. The serial executor's M06 argv uses this same path. Review01 source and tool exports remain unchanged.

The executor validates these exact immutable planner dependencies before it connects or dispatches work:
- /home/dflanag3/spatial-artifacts/m07-fixture-repaired-crosshost-coordination-20261016/review-01/candidate/prepare_crosshost_plan.py, SHA256 fba21bd6bc7d658706b2094043eecb6248bba664d76cc9b4257d46abdec682e5.
- /home/dflanag3/spatial-artifacts/m07-fixture-repaired-crosshost-coordination-20261016/review-01/candidate/launch_m06_crosshost.sh, SHA256 1d8a55904453d6c67723867573449ee730aea3166410c93553ac741b8b192b69.

Focused controls selected for primary execution on sb16 and sb16-2 from tools/test_m07_fixture_continuation_static.py:
- test_override_loads_exact_alternate_basename_ignoring_adjacent_decoy and test_override_replaces_a_cached_decoy_module: prove exact pinned module bytes win over basename and cache decoys.
- test_default_helper_import_behavior_is_unchanged and test_default_fixture_path_remains_the_unmodified_bundle_path: preserve no-override helper and fixture behavior.
- test_executor_tool_pin_closure_uses_reviewed_candidate_dependencies, test_executor_tool_pin_closure_rejects_absent_dependency, and test_executor_tool_pin_closure_rejects_mismatched_dependency: validate the exact planner dependency closure and early refusal.
- test_pinned_fixture_pair_selection_succeeds, test_wrong_manifest_pin_candidate_schema_and_file_pin_are_rejected, and test_linked_fixture_inputs_are_rejected: cover manifest selection, pin/candidate/schema failures, and linked-file refusal.
- test_m06_argv_requires_the_exact_pinned_continuation_arguments: validate the review02 M06 executable and exact manifest argv.

The coding worker ran no runtime controls. Primary later executed and accepted them on both test hosts.

Only source inspection and syntax compilation ran on a5500. Primary accepted all eleven continuation controls, seven related gate controls, and one actual pinned-helper CLI check on each test host. The full semantic/native cross-host run and four-cell workspace matrix remain pending.

Primary cleared owner-cleanup review02 for focused test use. Primary reports the helper9 and slow-listener1 controls and retained-root recovery passed on both test hosts. These focused results do not qualify the full cross-host run.
The pinned helper is /home/dflanag3/spatial-artifacts/m07-crosshost-owner-cleanup-20261016/review02/source/actor_transition_crosshost_host.py, SHA256 cea2a97df65729884b0d7fc4bc19fead3e48ddc93660d9bb12ed94c2c7089a8c.
Primary accepted the continuation-specific controls below. The full cross-host handoff remains a separate qualification requirement.
Stage the review02 M06 tool at the exact path above; keep M06_REMOTE_TOOL and the manifest paths aligned.
Create a schema-1 manifest with exactly these fields:

    {
      "schema": 1,
      "candidate": "1e58e65dec132a0a16f004ca4527034dca081f77",
      "semantic_fixture": {"path": "/absolute/external/actor_transition_crosshost_fixture.py", "sha256": "<64 lowercase hex>"},
      "host_helper": {"path": "/absolute/external/actor_transition_crosshost_host.py", "sha256": "<64 lowercase hex>"}
    }

Run the M06 handoff on sb16 with both explicit manifest arguments:

    python3 -B /home/dflanag3/spatial-artifacts/m07-crosshost-fixture-continuation-20261016/review02/tools/m06_deployed_gate.py crosshost-handoff --plan PLAN --fixture-continuation-manifest /home/dflanag3/spatial-artifacts/m07-crosshost-fixture-continuation-20261016/review02/manifests/fixture-continuation-v3.json --fixture-continuation-manifest-sha256 a90e3a88eabecaeb44b0d58b4784e72da2329960582137ea905116f2a9b84189

Run the serialized workspace continuation on G164 through the canonical tmux runner.
Pass --m06-tool-sha256 and both manifest arguments using the active review02 v3 path and hash above.
Use the four role files and exact accepted local receipts from the pinned M07 coordination plan.
Do not modify deployments, product sources, prior receipts, or the failed first-run evidence.

Current work is FOCUSED-PASS. Full cross-host qualification remains pending.
Only static source inspection and syntax compilation are permitted in this task.
No runtime tests, semantic harness, native job, workspace cell, or deployment has run here.
The repaired helper source review passed for focused test deployment.
Its focused runtime controls and this tooling review passed. Full cross-host qualification remains pending.

Primary focused verification, 2026-10-16:
All eleven continuation controls and seven related M06 gate controls passed on each host.
The actual parent CLI also loaded the explicitly pinned helper successfully on each host.
Runtime panes 3650985 and 3619470 retired after 0/0/COMPLETE.
Primary verified exact argv, source pins, seven runner receipts, test assertions, and process retirement.
Each host retains its audit at /home/dflanag3/spatial-artifacts/m07-crosshost-fixture-continuation-20261016/primary-focused-controls-02/PRIMARY_FOCUSED_AUDIT_01.json.
Audit SHA256: sb16 0e1e61a23188292af0d34c281f90bf25354a500e294f6a26a4223a8f0bc33f29.
Audit SHA256: sb16-2 6edf2341716e3039fc756e3a3534d98c7448f64d54733b81728a6ea994bd090a.

The first focused attempt passed all eleven new controls but stopped during related-suite setup.
That deployed suite imports a sibling m07_deployed_gate.py. The external package omitted it.
Primary added the unchanged frozen-candidate companion, SHA256 1faf5f776ef53126ae3cc675393038757f5d02d09c5809aef64a67dc8f43a501.
The corrected combined selection then passed. The original setup failure remains preserved.
This packaging addition changed no production source or four reviewed Python source pins.
M07 remains UNQUALIFIED until its complete remaining cross-host gate and final audits pass.


## M07 workspace collector continuation, review03

M07 remains UNQUALIFIED. Its production candidate remains 1e58e65dec132a0a16f004ca4527034dca081f77.
The first workspace test passed. Its outer collector then required boundary logs that this fixture never produces.
The failed attempt remains preserved.

The separate collector validates the actual peer and native-observer files.
It requires resource launch, terminal, readiness, cleanup, service, and process-identity evidence.
Process-identity witness contents remain opaque. The test asserts process identity through Protobuf exchanges.
The collector scans each retained log once. It rejects unsafe files, missing evidence, and sanitizer diagnostics.
Existing cleanup and test-result checks remain mandatory.
It hashes M06 source bytes once. It compiles those same bytes without using cached bytecode.

The executor checks the selected collector path and hash on both hosts before dispatch.
It also verifies the approved M06 dependency on both hosts.
Workflow identity, commands, receipts, and reconciliation bind the selected tool.
All four workspace cells remain serialized. Existing receipts prevent duplicate dispatch.
Use the exact workspace-<attempt> evidence scope for each direction and build profile.
Use collector-workspace-fixed-03 and its prepared roles for the next matrix.
The accepted M06 prerequisite remains unchanged. Its twelve semantic and thirty-six native tests remain accepted.

Primary validation passed 28 focused controls on each host.
The corrected collector also accepted the actual retained evidence on sb16.
The historical collector rejected that same evidence for the recorded filename mismatch.
The first primary batch stopped during related-test setup because its launcher omitted the frozen protobuf binding path.
That failed batch remains preserved. The corrected launcher passed with 0/0/COMPLETE and retired pane processes.

Each host retains primary-workspace-controls-04/PRIMARY_FOCUSED_AUDIT_01.json under the external artifact root.
sb16 audit SHA256: 3a968ac425066c41dc50185d5dcd8d7336aeec72730ac83aac80999a48deaf00.
sb16-2 audit SHA256: 5ae70a9c4bc8e5a199ceaaaeba67b1683b55bf8ac8a3f794b4be6778a8e66b79.

Source lineage:
- Deployed M07 tool: 1faf5f776ef53126ae3cc675393038757f5d02d09c5809aef64a67dc8f43a501.
- Maintained typed-retirement amendment: 15c6eb522bcbc5406b93c8d3d472c6c85a0c34f170ac6de4553fc70bf993580d.
- Approved M06 dependency: f765b2bec2d0faeb6bec43acddf806124daf2b8f7acc7ac4437040f575f69735.
- New collector: c4dccc708c9cb6c31159e6e26ad40bfc164396cb6be62ce0b4cd61a2253bcb67.
- New executor: 563ccbabb833f34e5a897fef39348245bd92bde97d6e02e195d803a595dfc873.
- New focused controls: 46ffe032d31997daa92bf96a9bce74e825593f9596f628db5ec619ef88b2c7c6.

An accidental unittest command ran on a5500 during source preparation. It failed during module loading.
The returned output was truncated. The exact exit record and complete traceback were not captured.
No production service or test host was contacted by that command.

The four-cell workspace matrix, final reconciliation, and both final artifact audits remain pending.
Focused controls do not qualify M07.
