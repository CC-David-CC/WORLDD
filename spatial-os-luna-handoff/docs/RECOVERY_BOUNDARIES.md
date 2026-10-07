# Production recovery boundaries

## World exit before commit

Worldd implements world.before_commit at its real SQLite write-transaction boundary.
The hook exits immediately before COMMIT. It does not return a simulated crash response.
The exit status is 86. The process skips C++ destructors and SQLite shutdown.
The conformance supervisor detects worker death and closes its transport.

The hook covers world entity writes, actor checkpoints, and workspace-context transactions.
Read transactions do not consume it. Rejected writes roll back without consuming it.
Pending faults belong to one worldd process. They never enter persistent storage.
Restart uses the same world database and restores its last committed state.

## Fixture authority

Start an isolated test daemon with --fixture-faults to enable fault controls.
The conformance host passes this flag explicitly.
The hello response advertises fixture.faults/v1 only when the flag is enabled.
A normal daemon returns FORBIDDEN for fault.arm.
The separate --fixture-clock flag does not enable faults.
Migration commands reject fault enablement.

Fault requests use the trusted management pipe. ActorActionRequest contains no fault operation.
The request must name world.before_commit with action exit and no storage error.
The acknowledgment confirms armed=true. A second supported arm returns BUSY.
Invalid or unsupported requests do not replace an armed fault.
The next qualifying transaction exits the process. Restart clears the arm.

## Recovery and limits

No database-schema migration is required by this change.
Preserve the world database and its rollback journal together after a crash.
Restart the normal world supervisor with the same state directory.
For a standalone management daemon, use build/worldd --state-dir /absolute/world-state.
Omit fixture flags during normal operation.
Do not delete journals or copy only the database while its writer runs.

This hook does not qualify power-loss durability or physical disk exhaustion.
The post-commit, Git-effect, and SQLite write-error boundaries remain unimplemented.
Durable surface attachment and stale-handle validation also remain unimplemented.
No host configuration or operator-session change is required.

## Qualification

The focused acceptance case passed on sb16 and sb16-2.
Seven added checks exercise actual daemon exit and independent SQLite inspection.
They cover create, update, delete, checkpoint, fixture authority, malformed requests, and process-local fault state.
Both hosts passed Debug and ASan/UBSan qualification for this primitive.
Each run passed all 206 core checks and all 51 harness checks.
The full P00-P09 gate executed 148 of 151 acceptance tests.
Its results are 143 passed, one assertion failure, four errors, zero skipped, and three unrun.
All 138 preceding P00-P08 cases still pass.

Five P09 primitives pass: before-commit exit, preview rollback, repeated kills, worker identity, and worker death.
The four errors identify unimplemented post-commit, storage-write, Git-effect, and surface-attachment operations.
The remaining assertion requires STALE_TARGET instead of the current UNIMPLEMENTED response.
P09 remains incomplete. The three P10 cases remain unrun.

All 104 source hashes match reviewed local files and both hosts.
The acceptance lock, locked assertions, protobuf schemas, independent oracles, and native witness remain unchanged.
Evidence is in evidence/recovery-before-commit-2026-10-07.
The predecessor failure is retained in evidence/recovery-transport-2026-10-07.
Each host directory records source hashes, build identities, exact commands, logs, and reports.

Run the focused counterexamples with ctest --test-dir build -V -R '^recovery-boundaries$'.
The cumulative command uses tools/qualify_core.py --through P07 with the recorded private XLibre server.
Sanitizer runs add --sanitize --build-dir build-sanitize.
Then run tools/gate.py --through P09 through tools/with_test_display.py.
The focused commands.json and each p09-command.json preserve complete argument lists.

Tests use authenticated disposable software displays. They do not qualify native GPU or physical device behavior.
No host configuration or operator session changed.
Local-model review remains waived under David's hardware-failure instruction.
Next: implement recovery.after_commit_before_reply at the successful commit boundary.
