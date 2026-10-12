# Spatial Desktop pause handoff

Paused at David's request on 2026-10-12. Do not resume until requested.

## Position in the plan

M01-M05 are qualified. M06 is incomplete. M07-M16 remain pending.
M06 still needs the geometry failure fixed, combined-source checks, visible shared-world geometry, and its full gate.
The objective remains Spatial-M1. This pause does not reduce its scope.
Use the XACE baseline. Keep CallbackList work separate and deferred.

## Saved source and evidence

The controller branch is `m06/world-transitions`.
Before this handoff, its verified box-git checkpoint was `62a688cb694e94ee357179e2e76e7a5c7842cfb4`.
Origin is `dflanag3@box-git:/srv/data/git/spacial_desktop.git`.
The working tree has one unrelated untracked file. Preserve it:
`spatial-os-luna-handoff/evidence/m06-retained-recovery-2026-10-12/primary-four-case.diff`.

Read `spatial-os-luna-handoff/CURRENT_STATE.md` and `docs/M06_GATE_REMAINING_WIRING.txt` for implementation context.
The latter path is relative to `spatial-os-luna-handoff`.

Relevant checkpoints:

- `992e37d`: retained issuer diagnostic evidence and tested evidence-directory guards.
- `70ffc8f`: ignore generated root `local/` worker directories.
- `62a688c`: only launchers write completion receipts. Live process state overrides premature exit files.
- `9f790f4`: combined geometry/host-session candidate; built, unqualified, not integrated.
- `5cbec99`: issuer failure-category diagnostics; separate branch, unqualified.
- `40547559c84f4d52227cda25bd72e8d1b631d3c2`: reviewed timing diagnostics; separate branch, unqualified.

The timing branch is `m06/geometry-issuer-timing-20261012` on box-git.
Its a5500 worktree is `/home/dflanag3/src/spacial-m06-geometry-issuer-timing-20261012`.
Both builds passed. Source hashes and embedded worldd build IDs were verified.
No timing source has been integrated into the controller milestone branch.

## Test state

All nine selected geometry Debug suites passed on both hosts across corrected attempts.
Both original sanitizer runs passed world-authority and eight of nine geometry cases.
The count-limit case failed after creating 4,097 objects. Later sanitizer suites remain unrun.
Retained logs show immediate issuer EOF while reading a reply header. The underlying cause remains unknown.
Snapshot construction before the count guard is a source-supported timing hypothesis, not a measured cause.

The current sb16 diagnostic attempt is:
`/home/dflanag3/spatial-qualification/m06-issuer-timing-20261012-02`.
This mixes explicitly pinned artifacts for diagnosis. It does not qualify M06.

- Staging passed: 540 baseline, 34 issuer-supplement, and 51 timing-supplement members.
- Target preflight passed: 553 files and six executable dependency closures.
- Manifest SHA256: `f138d4f4c3700d418bde244b4cf05ff67a17762757aca14e04124433b4448bb2`.
- Debug standalone timing regression passed in `debug-test-run03`; command and wrapper exited zero.
- ASan standalone timing regression is unrun.
- The new instrumented geometry count reproduction is unrun.

Preserve the failed staging attempt and the exit-127 launch attempt. Neither executed a production test.
The valid Debug executable is `bundle/bin/world-diagnostic-timing-test-debug`.
The ASan executable is `bundle/bin/world-diagnostic-timing-test-asan`.
There is no generic `bin/world-diagnostic-timing-test` in this staged bundle.

Detailed local runtime handoff:
`spatial-os-luna-handoff/local/m06-geometry-qualification/issuer-timing-runtime-plan/PAUSE-HANDOFF-2026-10-12.md`.
Source review and binding:
`spatial-os-luna-handoff/local/m06-geometry-qualification/issuer-timing-01/`.
Committed earlier evidence:
`spatial-os-luna-handoff/evidence/m06-issuer-diagnostic-2026-10-12/`.

## Separate gate work

Host: middle-child.
Worktree: `/home/dflanag3/src/spacial-m06-semantic-gate-20261012`.
Branch: `m06/semantic-crosshost-gate`.
Base: `992e37d5d48a51081ca26f2d6e3211dcda0fa195`.
Luna medium completed its process. Its changes remain uncommitted and unqualified.
The exact worker PID 167156 and wrapper PID 167110 were absent at pause verification.

Four modified files, all under `spatial-os-luna-handoff`:

- `tools/m06_deployed_gate.py`
- `tools/test_m06_deployed_gate_static.py`
- `tests/core/actor_transition_crosshost_fixture.py`
- `docs/M06_DEPLOYED_GATE.md`

Task and worker evidence are under that checkout's `local/m06-semantic-crosshost-gate/`.
Primary review and behavioral tests remain required. Do not integrate based on worker exit zero.
The worker wrote an exit receipt before it stopped. Verify terminal events and process state independently.
This task only connects existing semantic cross-host checks. Native geometry qualification remains separate.
Leave the unrelated old `spatial-m06-crosshost-run03` tmux session untouched.

## Resume sequence

1. Read root AGENTS.md, current status, and the coordination skill.
2. Verify Git heads, dirty files, exact process identities, and receipts. Do not restart a live job.
3. Revalidate the staged sb16 hashes, dependencies, and clean test environment.
4. Run the ASan preflight and standalone timing regression in new run directories.
5. Require zero command/wrapper exits and no sanitizer diagnostics before the count reproduction.
6. Run exactly the existing count-limit case with its reviewed v2 driver and persistent evidence directory.
7. Keep the 600-second outer bound, original workload, assertions, and issuer deadlines.
8. Inspect construction_ms, validation_ms, since_last_success_ms, operation, and transport category.
9. If measurements support snapshot cost as the cause, review and launch the prepared linear-size task.
10. Otherwise diagnose the measured failure. Do not widen deadlines or guess a fix.
11. Review the independent gate draft, run its behavioral checks on test hosts, then checkpoint coherent work.
12. Qualify the actual geometry fix on both hosts and finish the remaining combined checks.
13. Implement native shared-world geometry presentation, then freeze and run the full M06 gate on both hosts.
14. Continue M07-M16 and the complete Spatial-M1 requirements. Do not count focused checks as a phase gate.

The unlaunched snapshot task draft is:
`spatial-os-luna-handoff/local/m06-geometry-qualification/snapshot-linear-plan/task-draft.txt`.
It preserves exact protobuf size limits, ordering, typed rejection, and existing acceptance assertions.

Use middle-child/a5500 for coding and builds. Use sb16/sb16-2 for required runtime qualification.
Keep workers isolated by branch/worktree and use persistent tmux with launcher-owned receipts.
No current task needs model inference or user credentials.
