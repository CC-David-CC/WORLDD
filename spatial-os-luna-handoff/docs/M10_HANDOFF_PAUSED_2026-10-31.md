# M10 handoff, 2026-10-31

Status: **PAUSED by David. M10 remains UNQUALIFIED.**

## Goal

Deliver one frozen M10 candidate where a human and two scripted agents can
navigate worlds, use real files, and control real applications through the same
authorized actor-action system. Prove it on both `sb16` and `sb16-2`.

## Current source

- Checkout: `/home/dflanag3/src/m10-presentation-dispatch-second-pass-20261031`
- Branch: `m10/presentation-dispatch-second-pass-20261031`
- Repair commit: `5566612dc5ca9f6f25c55cc80ef68dec2805cb32`
- The repair commit is pushed to the matching `origin` branch. It is an
  unqualified checkpoint, not a frozen candidate.
- Base candidate: `d49c422b1335bdfeeb7d8b49060e8f1adc3e4f75`.

The repair reports three distinct milestones: input accepted into the bounded
queue, input handled by actor checks, and output presentation settled. It also
fences stale status snapshots and prevents a late presentation from publishing
after cancellation. The runtime input expiry remains 250 ms. David approved a
350 ms observation allowance for timing checks.

## Test evidence

The prior frozen candidate completed its Debug matrix and failed:

- `sb16`: 301/305 passed; tests 260, 261, 262, and 269 failed.
- `sb16-2`: 299/305 passed; tests 260 through 264 and 269 failed.
- Combined: 600/610 Debug host-test runs passed.
- Neither host started ASan/UBSan. Test 264 failed in setup, before measuring
  tap throughput.

The failures implicated capture receipts waiting for presentation, transient
controller-busy replies during ordinary work, and input status waiting behind
an issuer exchange. The preserved full-run evidence is under
`/home/dflanag3/spatial-artifacts/m10-presentation-dispatch-liveness-20261030`.

On `middle-child`, the repair's focused CTest selection passed 4/4 in Debug and
4/4 in ASan/UBSan. Python syntax checks and `git diff --check` also passed.
These checks do not prove native behavior on either qualification host.

The following repair-native suites are still **UNRUN** on both hosts:

- `host-continuous-capture-native`
- `scripted-actor-native`
- `three-actor-files-native`
- `three-actor-applications-native`
- `three-actor-raw-input-native`
- `host-visible-controls-native`

No replacement candidate has been built, frozen, or fully gated. The replacement
candidate must pass the full Debug and ASan/UBSan matrices on both hosts, plus
the required cross-host checks. Preserve all failures; do not weaken acceptance
assertions to obtain a pass.

## Current machine and artifact state

At the last check, no M06/M10 gate process was active. Available disk space was
38 GB on `middle-child`, 52 GB on `sb16`, and 62 GB on `sb16-2`.

Candidate inputs already exist under
`/home/dflanag3/spatial-artifacts/m10-presentation-dispatch-second-pass-20261031`:

- `runtime-manifest-5566612.json`
- `build-paths-5566612.json`
- ASan/UBSan build directory `build-asan-ubsan`

The reusable Debug build directory is
`/home/dflanag3/spatial-artifacts/m10-presentation-dispatch-second-pass-20261030/build-debug`.
These are build inputs, not a packaged or qualified candidate. Check capacity
before packaging. The old failed deployment consumes about 14 GB per test host;
preserve its unresolved logs and provenance before removing that deployment to
keep at least 50 GB free on `sb16`.

## Resume sequence

1. Verify both hosts are idle. Preserve the exact old failure evidence and
   source pins outside the test hosts. Remove the old deployed payload only
   after verifying the preserved evidence and no live process uses it.
2. Build a package for source commit
   `5566612dc5ca9f6f25c55cc80ef68dec2805cb32`. Use the existing manifest and
   build-path files. Verify package contents, source hashes, and both profiles.
3. Deploy the same package to both hosts. Run the six named native suites on
   each host. Record per-suite results and independent witness evidence.
4. Fix production defects found by those checks. Review the combined source,
   run `repository_data_guard.py`, and push a coherent unqualified checkpoint.
5. Freeze one replacement candidate. Run the full Debug and ASan/UBSan gates on
   both hosts against that exact candidate. Complete the cross-host checks.
6. Update `CURRENT_STATE.md` and this handoff with final evidence. M10 is
   qualified only when all required checks pass on both hosts.

M10 remains unqualified until that evidence exists.
