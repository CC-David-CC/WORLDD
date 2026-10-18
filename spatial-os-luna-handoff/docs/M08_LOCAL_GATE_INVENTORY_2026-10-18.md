# M08 local gate inventory extension

This extension adds M08 required CTest registrations to the existing M06/M07 local gate. It keeps the inherited requirements and Debug plus ASan/UBSan profiles. The gate still runs production-off startup, the harness, full non-dedicated CTest core, P10, bounded-storage/Git, native suites, and the existing cross-host reconciliation workflow.

Run `python3 tools/m08_deployed_gate.py run-local` with the same deployment, attempt, and required suite arguments used by `tools/m07_deployed_gate.py run-local`. It checks both deployed profile inventories before calling the M06 gate. The M06 gate then verifies the deployment receipt, host, source/runtime pins, and required inventories before launching stages.

M08 requires file-engine, file-operation journal, broker client/action, actor cabinet gateway, private evidence, presentation, process-scope recovery, copy staging-mask, and cabinet copy worker/path registrations. M07 `workspace-copy-*` suites remain inherited baseline requirements. They do not substitute for cabinet copy tests. The current lifecycle branch adds `process-scope-recovery-validation` and `workspace-copy-staging-mask`; this extension requires both.

This is only a local gate runner. It does not issue a milestone pass receipt. Native cabinet geometry/privacy evidence and required cross-host evidence still need independent qualification review. Existing generic geometry tests do not establish cabinet-specific visibility or metadata privacy unless their locked witnesses directly exercise that path.

The current source still needs primary review for three production coverage gaps: (1) cabinet absence without file authority, empty representation for granted empty storage, and locked metadata privacy are not all proved end to end; (2) the live actor gateway test group covers browse/read/create but does not yet prove every operation through actor-to-broker delivery; (3) no distinct cabinet-specific native geometry/pixel witness is registered. The file engine's direct tests do not close the actor-path gap.
