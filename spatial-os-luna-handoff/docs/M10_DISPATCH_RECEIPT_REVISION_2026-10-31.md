# M10 capture progress and liveness repair

Status: unqualified repair in progress. No replacement candidate is frozen.

## Qualification evidence

Frozen candidate: `d49c422b1335bdfeeb7d8b49060e8f1adc3e4f75`.

The full Debug phase completed on both required hosts and failed. Sb16 passed
301/305 tests. Sb16-2 passed 299/305. Neither host started ASan/UBSan. The
combined Debug result is 600/610 host-test runs passing.

Sb16 failed tests 260, 261, 262, and 269. Sb16-2 failed tests 260 through 264
and 269. The failures have three related causes:

- `processed_capture_sequence` advanced only after output presentation. Capture
  tests then timed out with the compositor stopped or world observation denied.
- Inspection and geometry reads returned transient `Host actor operation
  already in progress` errors during ordinary controller work. Several native
  setup cases failed at this boundary.
- Human input status waited behind a credential exchange. The test timed out
  before it could prove movement stayed live during that exchange.

The sb16-2 wrapper briefly waited in uninterruptible file I/O after Debug
finished. Its root filesystem had 62 GB free. The wrapper later exited with the
Debug failure. No XLibre or Xvfb error appears in these traces. The frozen
candidate evidence remains under
`/home/dflanag3/spatial-artifacts/m10-presentation-dispatch-liveness-20261030`.

## Repair source and contract

Worktree:
`/home/dflanag3/src/m10-presentation-dispatch-second-pass-20261031/spatial-os-luna-handoff`

Branch: `m10/presentation-dispatch-second-pass-20261031`, based on the frozen
candidate. All repair changes are uncommitted. The candidate package and its
evidence were not changed.

The host reports three separate progress receipts:

- `accepted_capture_sequence`: source and capture checks passed, movement
  intent was updated, and the event entered the bounded actor queue. This does
  not authorize a discrete action or prove its execution.
- `processed_capture_sequence`: the event reached checked actor handling. It
  does not wait for compositor output.
- `presented_capture_sequence`: the output barrier settled for the capture
  sequence seen by that presentation cycle. A late frame cannot publish after
  cancellation or binding retirement.

Queue overflow still cancels that capture binding and clears movement intent.
The absolute SpaceMouse axes coalescing path now updates the accepted receipt
when it replaces an older queued sample. Relative mouse coalescing preserves
its accumulated counts and reports the newest accepted sequence.

Controller status snapshots carry a mutex-protected revision. Atomic snapshot
publication refuses an older revision or actor generation. Cached status also
must match the current capture binding. During a slow actor exchange, status
reads can use that checked snapshot while capture progress remains live.
Inspection and geometry reads wait through ordinary controller contention.
They reject immediately while a world transition is active. Consequential
actions retain their immediate conflict response.

The native scripted-actor test records actual input-send times. It checks that
both agents continue refreshing through the held issuer exchange and observes
the human actor's real world pose independently. A 350 ms observation budget
matches David's approved roughly one-third-second tolerance; the fixture's
configured input timeout remains 250 ms.

## Checks completed on middle-child

Debug build:

```sh
cmake --build /home/dflanag3/spatial-artifacts/m10-presentation-dispatch-second-pass-20261030/build-debug --target spatial-host-actor-controller-probe spatial-actor-session-client-probe -j2
ctest --test-dir /home/dflanag3/spatial-artifacts/m10-presentation-dispatch-second-pass-20261030/build-debug -R '^(actor-session-client|host-actor-controller|host-actor-worker|host-actor-shared-control)$' --parallel 1 --output-on-failure
```

Result: 4/4 passed.

ASan/UBSan build:

```sh
cmake -S /home/dflanag3/src/m10-presentation-dispatch-second-pass-20261031/spatial-os-luna-handoff -B /home/dflanag3/spatial-artifacts/m10-presentation-dispatch-second-pass-20261031/build-asan-ubsan -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON -DSPATIAL_INPUT_TESTS=ON -DSPATIAL_SANITIZERS=ON
cmake --build /home/dflanag3/spatial-artifacts/m10-presentation-dispatch-second-pass-20261031/build-asan-ubsan --target spatial-host-actor-controller-probe spatial-actor-session-client-probe -j2
ctest --test-dir /home/dflanag3/spatial-artifacts/m10-presentation-dispatch-second-pass-20261031/build-asan-ubsan -R '^(actor-session-client|host-actor-controller|host-actor-worker|host-actor-shared-control)$' --parallel 1 --output-on-failure
```

Result: 4/4 passed. The first sanitizer CTest invocation had not built
`spatial-actor-session-client-probe`; after building it, the complete selection
passed. `python3 -m py_compile` passed for all four changed native Python test
modules. `git diff --check` passes after the transition-status regression, approved 350 ms timing
adjustment, and documentation update.

The Debug and ASan/UBSan focused suites cover controller transition fencing,
capture queue receipts, absolute-axis coalescing, blocked presentation, late
output after interruption, cached status during slow actor calls, and one-way
movement refresh.

## Selected next checks and purpose

The focused CTest selection covers four boundaries before native deployment.
All four passed in Debug and ASan/UBSan on middle-child:

- actor-session-client: verifies the session client request and receipt behavior still passes.
- host-actor-controller: checks transition fencing and versioned status snapshots.
- host-actor-worker: checks accepted, processed, and presented receipt separation, queue coalescing, and cancellation fencing.
- host-actor-shared-control: checks shared-control behavior after the receipt changes.

The next native checks exercise production capture, real actor progress during held issuer work, shared files, real applications, high-rate taps, and visible human controls. Run host-continuous-capture-native, scripted-actor-native, three-actor-files-native, three-actor-applications-native, three-actor-raw-input-native, and host-visible-controls-native on both sb16 and sb16-2. The native input timeout remains 250 ms; the approved observation allowance for these timing checks is 350 ms.

## Remaining work

1. Build the repaired production artifacts and run host-continuous-capture-native,
   scripted-actor-native, three-actor-files-native, three-actor-applications-native,
   three-actor-raw-input-native, and host-visible-controls-native on both sb16 and
   sb16-2. Preserve each independent witness and result.
2. Resolve any remaining production failure. Do not change acceptance assertions
   to obtain a pass.
3. Review the complete diff, run repository_data_guard.py, and commit the coherent
   repair.
4. Build and freeze one replacement candidate.
5. Run the full required Debug and ASan/UBSan gate on both hosts against that same
   frozen candidate. Run required cross-host checks and retain source pins,
   manifests, receipts, and concise result summaries.

Focused local checks do not qualify M10. M10 remains unqualified until the new
frozen candidate passes every required gate.
