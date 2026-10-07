# Production recovery boundaries

## World exits at commit boundaries

Worldd implements world.before_commit at its real SQLite write-transaction boundary.
The hook exits immediately before COMMIT. It does not return a simulated crash response.
The exit status is 86. The process skips C++ destructors and SQLite shutdown.
The conformance supervisor detects worker death and closes its transport.

Worldd also implements world.after_commit_before_reply immediately after a successful COMMIT.
This hook exits with status 87 before the operation returns or its reply is serialized.
The committed entity and world revisions survive exactly once.
Retrying the old expected revision returns REVISION_CONFLICT.

Both hooks cover world entity writes, actor checkpoints, and workspace-context transactions.
Read transactions do not consume them. Rejected writes roll back without consuming them.
A failed COMMIT returns STORAGE_ERROR. It leaves the post-commit fault armed for the next successful commit.
Pending faults belong to one worldd process. They never enter persistent storage.
Restart uses the same world database and restores its last committed state.

The implementation follows SQLite's [rollback-journal commit boundary](https://www.sqlite.org/atomiccommit.html).
The failed-commit check holds an independent reader, using SQLite's [file-locking behavior](https://www.sqlite.org/lockingv3.html).
It does not replace COMMIT with a simulated error.

## Fixture authority

Start an isolated test daemon with --fixture-faults to enable fault controls.
The conformance host passes this flag explicitly.
The hello response advertises fixture.faults/v1 only when the flag is enabled.
A normal daemon returns FORBIDDEN for fault.arm.
The separate --fixture-clock flag does not enable faults.
Migration commands reject fault enablement.

Fault requests use the trusted management pipe. ActorActionRequest contains no fault operation.
Commit fault requests name world.before_commit or world.after_commit_before_reply.
Both require action exit and no storage error.
Storage fault requests name storage.next_write with action error and storage_error STORAGE_FAULT_ENOSPC.
The storage hook returns SQLITE_FULL from the next positive-length SQLite xWrite call.
See STORAGE_FAULTS.md for its private VFS, lifetime rules, and retry behavior.
The acknowledgment confirms armed=true. A second supported arm returns BUSY.
Invalid or unsupported requests do not replace an armed fault.
The next qualifying boundary consumes the arm. Restart clears any pending arm.

## Recovery and limits

No database-schema migration is required by this change.
Preserve the world database and its rollback journal together after a crash.
Restart the normal world supervisor with the same state directory.
For a standalone management daemon, use build/worldd --state-dir /absolute/world-state.
Omit fixture flags during normal operation.
Do not delete journals or copy only the database while its writer runs.

This hook does not qualify power-loss durability or physical disk exhaustion.
The Git-effect boundary remains unimplemented.
Durable surface attachment and stale-handle validation also remain unimplemented.
No host configuration or operator-session change is required.

## Qualification

Both commit-boundary acceptance cases pass on sb16 and sb16-2.
Eleven recovery checks exercise actual daemon exit and independent SQLite inspection.
Four new checks cover post-commit persistence, failed commits, rejected requests, and checkpoint restoration.
They extend the existing fixture-authority, malformed-request, and process-local fault checks.
Both hosts passed Debug and ASan/UBSan qualification.
Six additional storage checks cover real write failures, retry, isolation, and checkpoint recovery.
Each run passed all 216 core checks and all 51 harness checks.
The full P00-P09 gate executed 148 of 151 acceptance tests.
Its results are 145 passed, one assertion failure, two errors, zero skipped, and three unrun.
All 138 preceding P00-P08 cases still pass.

Seven P09 primitives pass: both commit exits, storage failure, preview rollback, repeated kills, worker identity, and worker death.
The two errors identify unimplemented Git-effect and surface-attachment operations.
The remaining assertion requires STALE_TARGET instead of the current UNIMPLEMENTED response.
P09 remains incomplete. The three P10 cases remain unrun.

An isolated sb16 mutation moved the post-commit exit before SQLite COMMIT.
The locked acceptance case detected the wrong durable label: before instead of after.
The mutation remained in a separate worktree and never entered the production source.

All 108 source hashes match reviewed local files and both hosts.
The acceptance lock, locked assertions, protobuf schemas, independent oracles, and native witness remain unchanged.
Current evidence is in evidence/recovery-storage-2026-10-07.
The post-commit and mutation evidence remains in evidence/recovery-after-commit-2026-10-07.
The earlier before-commit evidence remains in evidence/recovery-before-commit-2026-10-07.
Current evidence includes the predecessor failure at the storage-write fault boundary.
Each host directory records source hashes, build identities, exact commands, logs, and reports.

Run the focused counterexamples with ctest --test-dir build -V -R '^(recovery-boundaries|storage-faults)$'.
The cumulative command uses tools/qualify_core.py --through P07 with the recorded private XLibre server.
Sanitizer runs add --sanitize --build-dir build-sanitize.
Then run tools/gate.py --through P09 through tools/with_test_display.py.
The focused commands.json and each p09-command.json preserve complete argument lists.

Tests use authenticated disposable software displays. They do not qualify native GPU or physical device behavior.
No host configuration or operator session changed.
Local-model review remains waived under David's hardware-failure instruction.
Next: implement recovery.git_side_effect_boundary after the real Git effect and before the ready acknowledgment.
