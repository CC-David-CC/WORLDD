# Git completion crash boundary

Workspaced implements git.after_worktree_before_ready at its actual external-effect boundary.
The broker first commits the creation intent to execution.db.
It then creates the real branch and linked worktree through Git.
It verifies the result and flushes the worktree, Git metadata, completion receipt, and containing directories.
An armed fault exits with status 88 before the registry can become ready.
The exit skips C++ destructors and SQLite shutdown.

The world authority detects the dead broker before sending an operation reply.
The conformance supervisor closes transport after the actual worker failure.
No simulated crash response or replacement Git state is involved.

## Fault authority

Start workspaced with --fixture-faults to enable its Git fault control.
Worldd independently requires --fixture-faults before it forwards the request.
The conformance supervisor enables both explicitly.
Normal daemons return FORBIDDEN. Migration commands reject fault enablement.
The request requires action exit and no storage_error.
Only the inherited trusted management channel can carry this request.
Actor actions cannot enable faults.

The broker independently validates the protobuf request.
The world reserves its fault arm only after the broker acknowledges armed=true.
This reservation prevents a second world, storage, or Git fault from replacing the pending arm in that runtime.
Reads, rejected creation requests, matching completed retries, and unrelated world edits leave the Git arm pending.
The next new successful worktree effect reaches the exit boundary.
Armed state remains process-local and is never restored from storage.

## Recovery and uncertainty

Restart the existing supervisor with the same world and execution directories.
For a standalone broker, use build/workspaced --state-dir /absolute/execution-state.
Omit fixture flags during normal operation.
Keep execution.db, Git backing repositories, worktrees, and completion receipts together.
Keep SQLite journals with their databases.
No schema migration or host configuration change is required.

Startup reconciles a pending operation only when its private receipt and real Git state match the recorded intent.
A matching completed effect becomes ready without another checkout.
Matching retries return the same worktree path and preserve uncommitted files.
Changed operation payloads return OPERATION_CONFLICT.

Missing or corrupt receipts and changed Git effects remain uncertain.
Recovery retains those files and refuses to repeat creation automatically.
Inspect the intent, receipt, Git registration, branch, and worktree before explicit repair.
Do not erase retained data to obtain a clean acknowledgment.
Git and SQLite remain separate effects. This hook does not qualify physical power-loss durability.

## Verification

The locked acceptance case observes the real branch before restarting the runtime.
Additional checks inspect pending registry rows and receipts before recovery.
They cover actual status 88, independent fixture authority, retry, isolation, and lost pending arms.
They also cover uncertain receipts, changed Git state, and uncommitted files.

Run focused checks with ctest --test-dir build -V -R '^(recovery-boundaries|workspace-git)$'.
The acceptance case is test_git_effect_completed_before_db_ack_is_reconciled_once in tests/acceptance/test_09_recovery.py.
Both hosts passed Debug and ASan/UBSan qualification with 222 core checks and 51 harness checks.
The P00-P09 gate reports 146 passes, one assertion failure, one error, and zero skips.
Three P10 cases remain unrun. P09 and full Spatial-M1 remain incomplete.
The remaining P09 requirements are durable surface attachment and stale-handle validation.

All 108 source hashes match both hosts and the reviewed local files.
The acceptance lock, schemas, locked assertions, oracles, and native witness remain unchanged.
Evidence is in evidence/recovery-git-2026-10-07.
Each host directory includes the failing baseline, focused checks, complete commands, logs, and source hashes.
The full commands remain in debug/run.json, sanitize/run.json, and their p09-command.json files.
Tests use private software displays. Native GPU and physical-device qualification remain pending.
No host configuration or operator session changed.
Local-model review remains waived under David's hardware-failure instruction.
