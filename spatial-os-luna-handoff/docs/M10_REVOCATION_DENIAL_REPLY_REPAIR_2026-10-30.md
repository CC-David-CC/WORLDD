# M10 revocation denial reply repair

Status: unqualified repair in progress.

## Source and ownership

- Frozen failing candidate: `a1031f677466efd8c1fefdb0faf9391295003797`.
- Repair branch: `m10/revocation-denial-reply-20261030`.
- Worktree: `/home/dflanag3/src/m10-revocation-denial-reply-20261030/spatial-os-luna-handoff`.
- Owned production file: `spatial-os-luna-handoff/src/world/identity_sessions.cpp`.
- The frozen deployment and its running gates remain untouched.

## Failure and invariant

On sb16, ASan/UBSan test 284 reached native release after issuer revocation, but the actor stream closed before returning the typed `UNAUTHORIZED` reply. The compositor and independent X witness showed no prohibited key reached the application. The failure is in worldd's reply lifecycle.

Dispatch sanitizes a request that overlaps identity revocation. It queues `UNAUTHORIZED`, but only copy-status denials receive the marker that lets the later disconnect preserve a safe denial frame. The disconnect path therefore drops the ordinary actor denial and closes the stream.

Invariant: after identity revocation, deliver the sanitized `UNAUTHORIZED` reply for the cancelled request when possible. Never preserve a success frame or an unvalidated reply. Native input remains fenced before issuer commit.

The repair marks only the final `UNAUTHORIZED` identity-revocation reply as preservable. The marker is set after the frame-size fallback, so a fallback error cannot be mistaken for that denial. The existing copy-status behavior remains intact.

## Selected checks, recorded before execution

1. `queued-copy-status-replacement` in Debug and ASan/UBSan. This checks replacement, preservation, malformed-frame refusal, and close behavior for unmarked replies.
2. `session-guardian-precommit` in Debug and ASan/UBSan on sb16 and sb16-2. This reproduces the queued actor action during revocation and requires native release, no late key delivery, and a typed denial reply.
3. `session-guardian-precommit-first-pointer` in Debug and ASan/UBSan on both hosts. This protects the adjacent first-pointer cancellation boundary.
4. `session-application` in Debug and ASan/UBSan on both hosts. This checks the connected actor action and application control paths.
5. After focused checks pass, freeze a new candidate and run its full required gate on both hosts. The already-running full gates remain evidence for the old candidate only.

At repair start, the old-candidate full gate was still running on both test hosts. sb16 had recorded the test 284 failure; sb16-2 had not yet reached that test. Neither job should be interrupted or overwritten.

## Evidence and local checks

The retained sb16 evidence is under `spatial-artifacts/m10-m10-scale-liveness-and-control-flush-20261029/deployments/a1031f6-02/evidence/full-a1031f6-06-sb16/ASan-UBSan/`:

- `core.log` records `EOFError: Actor stream closed` for test 284.
- `ctest/spatial_security_evidence/test_supervisor_guardian_fences_input_before_issuer_commits_revocation-coordinator.log` records request 8 queued with `revocation_denial=0`, then `disconnect_drop_output`.
- The independent probe log records key and button releases. It records no F6 event.
- The old sb16 full gate completed all 305 tests with one failed CTest, test 284. Its command and wrapper exits are both 1.
- sb16-2's old-candidate gate remains running. It passed test 195 (`world-application-control`, 557.45 seconds) and test 196; no failure was recorded through test 196 at the latest check.

On middle-child, `worldd` and `queued-copy-status-replacement-test` built successfully in both profiles. The selected CTest passed in each profile:

- Debug: `test-debug-unit-01`, 1/1 passed.
- ASan/UBSan: `test-asan-unit-01`, 1/1 passed.

Both builds and their receipts are under `spatial-artifacts/m10-revocation-denial-reply-20261030/`. Native runtime checks and all full-candidate qualification remain pending.
