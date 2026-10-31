# M10 presentation and actor liveness repair

Historical checkpoint note: this document records the earlier receipt semantics. The 2026-10-31 repair note supersedes them: processed_capture_sequence means checked actor handling, and presented_capture_sequence means the native output barrier settled. Follow the newer note for current behavior.

Status: unqualified repair in progress.

## Source and ownership

- Frozen failing candidate: `bc902621c25e1f9f6d4b0b8f1d91f29a314de5ca`.
- Repair branch: `m10/presentation-dispatch-liveness-20261030`.
- Worktree: `/home/dflanag3/src/m10-presentation-dispatch-liveness-20261030`.
- Base commit: `bc902621c25e1f9f6d4b0b8f1d91f29a314de5ca`.
- Owned production files: `host_actor_worker.{hpp,cpp}`, `host_actor_controller.{hpp,cpp}`, and `session_coordinator.{hpp,cpp}`.
- Owned regression file: `src/testing/host_actor_controller_probe.cpp`.
- The frozen deployment and active sb16-2 gate remain untouched.

## Failure and invariant

The sb16-2 ASan/UBSan gate failed `three-actor-files-native` at CTest case 262. A queued key release and capture event waited behind local geometry presentation. The worker watchdog interrupted the actor at 407 ms during `geometry_begin_frame`; the host then reported `Local presentation completion lost its actor context`.

Invariant: an authenticated actor must keep refreshing movement while rendering or another actor's authority exchange is pending. Presentation completion must not block actor input dispatch. The processed-capture receipt must still wait for native output to settle. Keep the qualification assertions unchanged.

The repair moves steady-state controls and geometry presentation to a dedicated presentation thread. The actor worker continues to process capture and movement. It records dispatch separately from the processed-output receipt. The controller drops its lock around local presentation calls and rechecks actor binding, capture generation, owner, and identity before publishing. New input does not cancel an in-flight frame. Its output receipt still covers only input dispatched before that presentation cycle. The SessionCoordinator API lock serializes access to its retained state.

## Contract clarification

The first focused Debug run showed that `worker_presentation_receipt_cannot_publish_after_actor_interruption` encoded the old coupling: it expected a blocked renderer to trigger the actor watchdog and close the actor channel. That expectation conflicts with the M10 requirement that rendering cannot stop authenticated movement refresh.

The contract now distinguishes actor dispatch from native output completion. Actor input remains live while presentation is pending. The processed-capture receipt still waits for output. A real actor-channel interruption during that wait must prevent a late receipt. The probe now closes the actor endpoint independently during the held presentation and checks both properties. The locked `three-actor-files-native` qualification test remains unchanged.

## Selected checks, recorded before execution

1. `host-actor-worker` in Debug and ASan/UBSan. Checks actor-worker queueing, capture dispatch, watchdogs, movement freshness, and the new blocked-presentation liveness regression.
2. `host-actor-shared-control` in Debug and ASan/UBSan. Checks shared control state and actor action behavior after moving presentation work off the actor worker.
3. `host-actor-controller` in Debug and ASan/UBSan. Checks actor-controller transitions, presentation receipts, stale publication fencing, and cleanup.
4. `three-actor-files-native` on sb16 and sb16-2 in Debug and ASan/UBSan after the focused probes pass. Reproduces the frozen-gate failure with the production host and X session.
5. After focused checks pass, build and run the native scenario on both hosts. Then freeze a new candidate and run the full required gate on both hosts. The prior gate is evidence for its old candidate only.
6. ThreadSanitizer build and the same three focused controller, worker, and shared-control probes on middle-child. Checks races introduced by the dedicated presentation thread and serialized coordinator API. This diagnostic is outside the required M10 profile gate.

## Evidence and next steps

- The prior frozen candidate gate finished on both hosts. sb16 passed its local matrix. sb16-2 completed 305 tests and failed test 262, `three-actor-files-native`; its wrapper exited 1.
- No qualification process remains active. The old candidate cannot qualify while that failure remains.
- Initial focused Debug run: `host-actor-controller` and `host-actor-shared-control` passed. `host-actor-worker` failed because the presentation-interruption probe expected renderer delay itself to interrupt the actor. The revised probe passed on the focused Debug and ASan/UBSan reruns.
- The first Debug and ASan/UBSan build attempts both caught a misspelled retirement-generation field. The source spelling is corrected. The incremental builds then completed successfully.
- After the final lock-order fix, `host-actor-worker`, `host-actor-shared-control`, and `host-actor-controller` passed 3/3 in Debug and 3/3 in ASan/UBSan on middle-child.
- The clean ASan/UBSan worker test also passed three consecutive runs. Earlier diagnostic runs produced two 60-second CTest timeouts in the worker test. Recheck the timeout anomaly during the broader gate.
- The focused shared-files native regression passed on sb16 in 21.527 seconds and sb16-2 in 27.465 seconds using the repaired Debug executable.
- The complete seven-test `test_three_actor_files.py` class passed on both hosts in Debug: sb16 in 56.840 seconds and sb16-2 in 70.399 seconds.
- The same seven-test class passed on both hosts in ASan/UBSan: sb16 in 73.915 seconds and sb16-2 in 97.225 seconds. Every corrected run exited 0 and ended with `OK`.
- The first ASan/UBSan class attempt omitted `spatialwm` beside the staged `spatial-host-session`. Each test stopped at the fixture's executable check before launching the system under test. Preserve that setup failure at `ASan-full-class-01`; the corrected run at `ASan-full-class-02` includes the matching compositor binary.
- ThreadSanitizer build `tsan-presentation-thread-02` passed `host-actor-controller`, `host-actor-worker`, and `host-actor-shared-control` 3/3 in 10.11 seconds with no race report. The first TSan configure attempt omitted `-DBUILD_TESTING=ON` and stopped during CMake generation; its setup log remains at `tsan-presentation-thread-01`.
- The earlier focused Debug and ASan/UBSan controller probes remain 3/3 each. The required 305-test qualification gate has not run against this repair.
- Review all source and document changes. Run `repository_data_guard.py`, commit the repair, build and freeze a new candidate, then run the full required Debug, sanitizer, and native gates on both hosts.
- M10 remains unqualified until the frozen candidate passes every required gate.
