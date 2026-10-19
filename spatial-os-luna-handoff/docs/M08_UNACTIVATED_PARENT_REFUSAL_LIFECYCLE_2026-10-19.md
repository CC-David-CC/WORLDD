# M08 parent refusal lifecycle correction

Status: source only, UNQUALIFIED. No runtime checks ran for this change.
Base: e82ceb41db1e4f88edea13a30edc8ac1eaa25986.
Branch: m08/refusal-lifecycle-repair-20261019.
Host: ROG-Ally-X-RC72LA.
Checkout: /home/dflanag3/src/m08-refusal-lifecycle-repair-20261019.
No build or persistent worker was needed. No tmux job started.

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
- After primary review, run test_file_action_active_replaced_parent_refuses_startup in Debug and ASan/UBSan on both test hosts.
- Then run all 12 supervised recovery cases in both profiles on both hosts.
The focused case verifies refusal, local cleanup, retained issuer authorization, and preserved external effects.
The combined cases cover authentic active-generation retirement, quarantine, independent work, and publication recovery.
The full M08 gate remains pending.

Passed: static checks recorded in the handoff.
Failed: the historical baseline and overlay refusal oracle on both hosts.
Blocked: none for source preparation.
Skipped: none.
Unrun: all runtime checks for this correction, all builds, and full qualification.
The next safe task is primary diff review, followed by the focused four-lane runtime selection.
