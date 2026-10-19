# M08 local gate inventory extension

This extension adds M08 required CTest registrations to the existing M06/M07 local gate. It keeps the inherited requirements and Debug plus ASan/UBSan profiles. The gate still runs production-off startup, the harness, full non-dedicated CTest core, P10, bounded-storage/Git, native suites, and the existing cross-host reconciliation workflow.

Run `python3 tools/m08_deployed_gate.py run-local` with the same deployment, attempt, and required suite arguments used by `tools/m07_deployed_gate.py run-local`. It checks both deployed profile inventories before calling the M06 gate. The M06 gate then verifies the deployment receipt, host, source/runtime pins, and required inventories before launching stages.

M08 requires file-engine, file-operation journal, broker client/action, actor cabinet gateway, private evidence, presentation, process-scope recovery, copy staging-mask, and cabinet copy worker/path registrations. M07 `workspace-copy-*` suites remain inherited baseline requirements. They do not substitute for cabinet copy tests. The current lifecycle branch adds `process-scope-recovery-validation` and `workspace-copy-staging-mask`; this extension requires both.

This is only a local gate runner. It does not issue a milestone pass receipt. Native cabinet geometry/privacy evidence and required cross-host evidence still need independent qualification review. Existing generic geometry tests do not establish cabinet-specific visibility or metadata privacy unless their locked witnesses directly exercise that path.

Source review on 2026-10-19 supersedes the earlier three-gap note. The actor gateway tests now cover cabinet placement authority, empty storage, locked presentation, and positive actor-to-broker file operations. The dedicated workspace-cabinet-native-crosshost registration exercises cabinet pixels, privacy, and persistent placement. Positive operation cases use an authorized owner; they do not establish separately delegated peer success for each right. Full frozen-candidate host/profile/direction evidence and independent cross-host reconciliation remain required. Registered coverage is not runtime qualification.

## Helper-protected core CTest partition

The M08 helper-enabled core stage protects the full current local inventory except for the four supervised copy-recovery registrations. The protected JUnit file is `core-protected.xml`; the four self-managed lifecycle tests write `core-self-managed.xml` after the outer helper lifecycle releases its lock. The gate checks both exact name sets, rejects failures and skips, and merges the raw suites into `core.xml` for existing readers. The receipt keeps aggregate `junit_cases` and records raw counts and file paths for both stages. The protected stage contains N-4 local registrations; the self-managed stage contains exactly four. Do not pin a total inventory count. The device-binding regression adds one local registration.

M08 qualification requires the full ordered Debug and ASan-UBSan profiles on both required hosts. The M08 CLI preserves inherited profile-selection and collector-continuation argument forwarding for compatibility with existing local-run workflows. A partial-profile or collector-continuation run does not establish M08 qualification. M07 collector code and behavior remain unchanged.
