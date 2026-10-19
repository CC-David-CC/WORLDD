# M08 parent refusal lifecycle correction

Status: focused four-lane PASS. M08 remains UNQUALIFIED.
The corrected case passed in Debug and ASan/UBSan on both required hosts.
Base: e82ceb41db1e4f88edea13a30edc8ac1eaa25986.
Branch: m08/refusal-lifecycle-repair-20261019.
Host: ROG-Ally-X-RC72LA.
Checkout: /home/dflanag3/src/m08-refusal-lifecycle-repair-20261019.
No build was needed. Persistent tmux sequences ran on both test hosts.
Each sequence required Debug success before starting ASan/UBSan.

The new active-parent refusal case reached its intended production rejection.
Both hosts rejected startup with "Active copy parent identity cannot be verified".
The test then required a failed-generation fence and issuer retirement.
The candidate had exited before completing the activation relay.
The same oracle failed with the baseline and supervisor39 overlay.
This case requires no supervisor39 production change.

AUTHORITY_LAUNCH_COORDINATOR.txt distinguishes START_ATTEMPTED from issuer activation.
Its OWNED_SCOPES_STOPPED state proves checked local process and native cleanup only.
SUPERVISOR_CRASH_FENCING.txt forbids inferring activation from previous_generation.
It requires accurate retention of an interrupted startup's unconsumed authorization.
A strict fence requires the exact consumed authorization in issuer.cpp.
The old generation still requires genuine fencing and retirement before foreign replacement cgroups are created.

Both hosts retained candidate revision 8 at OWNED_SCOPES_STOPPED.
The initial revision was 4 at START_ATTEMPTED.
Process and native stopped identities match the initial identities.
The candidate retains authorization generation zero, no fence, and no retirement file.
The supervisor exits 2 and reports the unresolved fence challenge separately.
The old generation retains its genuine fence and retirement.
Historical issuer database rows were not preserved in those runs.
The corrected case reads and preserves those authoritative rows before fixture teardown.

Only this negative case uses the new local-cleanup assertion helper.
It requires actual storage-owner EOF cleanup and supervisor failure exit 2.
It checks the terminal error against the durable launch record and both exact local receipts.
It requires the exact retained authorization at generation zero in issuer storage.
Activation, fence, retirement, obligation, and prior authorization rows must remain unchanged.
It retains the before/after issuer facts as external JSON evidence.
It calls no retirement operation and fabricates no fence or release receipt.
The shared active-failure cleanup and retirement helpers remain strict.

The parent rejection, foreign root/worker identities, prepared output inode and payload remain checked.
The complete RUNNING journal row must remain unchanged and no destination may appear.
No production source, schema, locked test, or normative contract changes.
The selected inventory remains exactly 12 methods with all original method names.
This corrects the lifecycle claim in M08_LEGACY_RECOVERY_REMAINING_FIXTURE_REPAIR_2026-10-19.md.

Preserved failures on sb16 and sb16-2 live below:
/home/dflanag3/spatial-artifacts/m08-legacy-recovery-remaining-repair-20261019.
Baseline evidence: cases-four-debug/native/test_file_action_active_replaced_parent_refuses_startup.
Overlay evidence: cases-supervisor39-refusal-debug/native/test_file_action_active_replaced_parent_refuses_startup.
Each candidate has lostack-broker-1/{initial.pb,cleanup.pb,journal-snapshot.db,supervisor.log,retention.json}.
Each original generation has lostack-broker-0/retirement.pb.

Selected checks, recorded before execution:

- Static AST parsing, unchanged 12-case inventory, unchanged test method bodies, and git diff whitespace checks on ROG.
- Repository data guard on ROG before handoff.
- After primary review, test_file_action_active_replaced_parent_refuses_startup ran in Debug and ASan/UBSan on both test hosts.
- The combined 12-case selection remains pending in both profiles on both hosts.
The focused case verifies refusal, local cleanup, retained issuer authorization, and preserved external effects.
The combined cases cover authentic active-generation retirement, quarantine, independent work, and publication recovery.
The full M08 gate remains pending.

Focused results: four passed, zero failed, zero skipped, and zero unrun.
The historical baseline and overlay refusal-oracle failures remain preserved on both hosts.
The larger 11-case runs hit storage exhaustion. Retries wait for the separate image-cleanup repair.
All 12 combined cases and the full M08 gate remain pending.
The next safe task is the image-cleanup repair, followed by the reviewed larger selection.

| Host | Debug | ASan/UBSan |
|---|---:|---:|
| sb16 | 1 PASS, 13.288 seconds | 1 PASS, 14.614 seconds |
| sb16-2 | 1 PASS, 14.623 seconds | 1 PASS, 15.896 seconds |

Each lane reported exactly one passing test.
Inner and outer canonical receipts record command.exit=0, wrapper.exit=0, and COMPLETE.
Each driver-result.json confirms the temporary copy helpers returned to their absent baseline.
Preserved case and runner logs contain no ASan, LeakSanitizer, or UBSan findings.
Independent evidence inspection verified exact local-stop identities and the retained generation-zero issuer authorization.
It also verified unchanged issuer rows and the original generation's genuine fenced retirement.
Both source images remained unchanged.
Recorded authority identities and driver/test processes were no longer live after either sequence.

The tested fixture source is f46030657cd75bce86ea3bb6b47bcdeb81d43eb2.
Test SHA-256: 965176e2fd8b0de268717ae4887f4c252477ef15dc20f10a95ead3f3e8b7a888.
Driver SHA-256: 222b403fb40cd79bb55f56bcb79d562066249ceff13f2ca5ea58ccee8164a136.
Production uses the packaged 812bcb6 binaries with supervisor 39cbc68a5ab1f713e603a916561c89ba0761247a.
The full component manifests pin source, runtime, generated bindings, graphics closure, helpers, and the canonical runner.
Relocated CTest metadata remains separately labeled and unconsumed by the direct Python driver.

Current evidence on each host is under:
/home/dflanag3/spatial-artifacts/m08-refusal-lifecycle-repair-20261019.
The focused-four-lane-plan.json and launch-provenance.json record exact commands and pins.
Each profile has launcher-runs/PROFILE, runs-focused-PROFILE, and cases-focused-PROFILE.
Candidate evidence includes cleanup.pb, unactivated-issuer-state.json, supervisor.log, and retention.json.
The focused-evidence-inspection.json files summarize checked receipts, identities, and sanitizer scans.
Summary SHA-256 on sb16: ec04e4d8d1f82f3063dc82c0a65c0ab61e789fb8b224bbbe5460c0c0b831c107.
Summary SHA-256 on sb16-2: f9c824823f866845cb8f3ad3c44e5e42406b65e4e17d673b4a6bd83f85e6836b.
All images and evidence remain preserved pending the separate cleanup repair.
