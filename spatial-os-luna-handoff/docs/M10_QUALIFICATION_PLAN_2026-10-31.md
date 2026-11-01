# M10 qualification plan, 2026-10-31

Status: **qualification in progress; M10 remains unqualified.** The goal
remains delivering one frozen candidate where a human and two scripted agents
navigate worlds, use real files, and control real applications through the
same authorized actor actions on both `sb16` and `sb16-2`.

## Frozen candidate and live run status

- Source commit: `7cc4b7e3870f2c3582054b3ffcb62e98c1c9a795`.
- Archive: `m06-artifacts.tar.gz`, SHA-256
  `24183b3fcdfab7f92a4347949cef885da52ea20adf43ba079fccaa6c01887c86`.
- Archive manifest SHA-256:
  `b14365b442788c4220b99035917dea5f4b8ffa095bf5c9602a5a4b74265be4d2`.
- Both hosts run the same verified deployment at
  `deployments/7cc4b7e-prep-01/deployed-01`.
- Full local gates are active in tmux sessions `m10-qual-7cc-sb16` and
  `m10-qual-7cc-sb16-2`.
- Each host registered 305 CTest cases per profile. At the latest check,
  Debug had completed tests 1 through 30 successfully on each host. Test 31,
  `workspace-file-action-broker`, was still running on both. No test failure
  had been reported. This is partial progress, not a pass.
- The Debug launcher removes the stale global
  `SPATIAL_TEST_NATIVE_MEMORY_MAX`; per-profile storage fixtures are distinct
  and verified.
- Removing verified, unused local build staging recovered 14,759,346,176
  bytes. The candidate archive, manifest, source pins, and active deployments
  remain preserved.

The frozen candidate is immutable. Any runtime or package change requires a
successor candidate and reruns of affected evidence. This documentation update
does not alter the frozen archive or the source commit under test.

## What prior evidence says

### Latest complete candidate

Candidate `d49c422b1335bdfeeb7d8b49060e8f1adc3e4f75` completed its Debug
matrix. `sb16` passed 301/305 tests; `sb16-2` passed 299/305. Neither host
started ASan/UBSan. The combined Debug result was 600/610 host-test runs.

The failing tests were:

| Test | sb16 | sb16-2 | Recorded failure |
|---|---|---|---|
| 260 `host-continuous-capture-native` | Failed | Failed | Capture/control settling failed; sb16-2 also received `Host actor operation already in progress`. |
| 261 `scripted-actor-native` | Failed | Failed | Human capture refresh exceeded 250 ms during pending authority work; each host also saw a controller-busy response in a related case. |
| 262 `three-actor-files-native` | Failed | Failed | Both failures returned `Host actor operation already in progress` during an authorized world/file workflow. |
| 263 `three-actor-applications-native` | Passed | Failed | sb16-2 received `Host actor operation already in progress` while stopping the human actor. |
| 264 `three-actor-raw-input-native` | Passed | Failed | sb16-2 received `Host actor operation already in progress` before measuring the required 100 taps per second. |
| 269 `host-visible-controls-native` | Failed | Failed | Capture/control state did not settle while the menu remained available without world observation authority. |

This pattern points to our host actor, capture receipt, and controller paths.
The preserved traces contain no XLibre or Xvfb error. The current evidence does
not assign these failures to XLibre, Xorg, Mesa, or hardware.

The repair at `5566612dc5ca9f6f25c55cc80ef68dec2805cb32` separates accepted,
processed, and presented capture progress. It also fences stale controller
snapshots and late presentation publication. Its focused CTest set passed 4/4
in Debug and 4/4 in ASan/UBSan on `middle-child`. The six native tests above
have not run against this repair on either qualification host.

### Earlier attempts and lessons

- On 2026-10-28, several failures were fixture or harness defects: bytes passed
  to JSON, stale Btrfs cgroup state, a relay activation ordering race, and a
  helper that expected an action reply instead of a denial. They count as
  corrected only after a fresh candidate reruns the affected test.
- A copy readiness window expired before the test submitted its request. The
  fixture now allows slower writers to become ready; the 300-second copy
  operation deadline and completion assertions remain unchanged.
- The presentation-thread repair passed the full seven-method real-file class
  on both hosts in Debug and ASan/UBSan. It also passed focused controller,
  worker, shared-control, and ThreadSanitizer checks. That evidence predates
  the newer receipt revision and does not cover the latest full matrix.
- A production guardian test once received EOF instead of a typed denial after
  revocation. Its independent application witness saw no prohibited input.
  David allowed that reply-close failure as a narrowly scoped exception only
  if every other required case passes on both hosts in Debug and ASan/UBSan.
  Preserve and report it as a failure plus exception. Never count it as PASS.
  Any late input, missed release, incomplete fence, or second failure falls
  outside the exception.
- The 100-tap-per-second requirement applies to this candidate. The old sb16-2
  raw-input setup failure did not measure it.
- The signed-shift XLibre issue and the Mesa EGL cleanup issue are separate
  dependency fixes. Neither explains the latest candidate's traces. Reopen an
  upstream issue only if a new trace reaches that boundary and independently
  reproduces the fault there.

## Run sequence

Use the same source pin, archive bytes, manifest, and deployment on both hosts.
Keep the 250 ms input freshness limit. The approved 350 ms allowance is only an
observation window for the timing assertions.

1. **Preflight and preserve.** Verify `sb16` and `sb16-2` are idle and each has
   at least 50 GB free. Copy the failed candidate's run receipts and required
   logs off the hosts. Verify hashes and provenance before removing its roughly
   14 GB deployment from each host. Keep the unresolved evidence.

2. **Review source and build.** Review the complete change from base candidate
   `d49c422...` through repair commit `5566612...`. Confirm locked tests and
   acceptance assertions remain intact. Reuse the recorded Debug and
   ASan/UBSan build paths only after checking their CMake source and flags.
   Rebuild both profiles, including the production-off artifacts and all
   package helpers. Run the repository data guard and protobuf reproducibility
   proof. If source changes, commit and push them before packaging.

3. **Package and freeze.** Produce one immutable package from the reviewed
   commit. Record source commit, dependency receipts, generated source proof,
   profile inventories, artifact hashes, and archive hash. Verify extraction
   and all runtime dependencies. Deploy identical archive bytes to both test
   hosts. From this point, any code or package change creates a new candidate
   and invalidates affected results.

4. **Full Debug on both hosts.** Run the complete required M09 plus M10 local
   gate concurrently on `sb16` and `sb16-2`, using the frozen package. Require every
   registered CTest, harness check, locked acceptance test, production-off
   startup check, and resource-boundary check. Record the terminal counts and
   inspect every failure. The M10 native cases already run inside this matrix.
   Stop progression on a failure; diagnose it and create a successor candidate.

5. **Full ASan/UBSan on both hosts.** Once Debug passes on both, run the same
   full matrix under ASan/UBSan on `sb16` and `sb16-2`. Verify the selected
   sanitizer executables and libraries match the frozen package. Treat startup,
   teardown, timeout, or sanitizer output as failures until explained.

6. **Dedicated native retest on both hosts.** After both full profiles pass,
   run these six repair-sensitive suites on each host and retain independent
   witness logs: `host-continuous-capture-native`, `scripted-actor-native`,
   `three-actor-files-native`, `three-actor-applications-native`,
   `three-actor-raw-input-native`, and `host-visible-controls-native`. Run them
   against the same frozen deployment. Confirm the raw-input witness and the
   application independently observe at least 100 ordered taps per second.
   Then run the required cross-host semantic and native geometry checks.

7. **Reconcile and decide.** Compare every expected method and case against
   each host's profile inventory. Report PASS, FAIL, EXCEPTION, BLOCKED, and
   UNRUN separately. Accept only the documented first-pointer reply-close
   exception, under its exact conditions. M10 qualifies only if all other
   required checks pass on both hosts, the exception has no late native input,
   and all artifacts point to the same frozen candidate.

## Failure ownership procedure

For every failure, retain the candidate hash, host, profile, CTest ID, exact
command, terminal receipt, full log, and independent witness output. Capture
the first failing assertion and relevant production trace before retrying.

Classify the cause only after tracing the request across the boundaries:

| Evidence | Ownership |
|---|---|
| Production actor, issuer, controller, world, broker, or receipt code violates its contract | Spatial Desktop runtime defect. Fix it in our source and add or preserve a regression. |
| The runtime meets the contract, but a fixture, launcher, stale test state, or expected reply is wrong | Test or packaging defect. Correct the harness without weakening the runtime assertion; rerun the affected suite on a new candidate. |
| The failure is reproduced at an external library, X server, display server, or driver boundary with the Spatial Desktop layer removed or independently witnessed | Upstream dependency issue. Preserve a minimal reproducer and hashes, identify the owning project, then decide whether to patch locally or file an upstream report. |
| The trace does not isolate a boundary | Unresolved. Do not label it hardware or upstream; gather a discriminating trace. |

Never infer an XLibre or Xvfb defect from a failed native test alone. Conversely,
do not dismiss an external failure as ours without checking its boundary. The
last full candidate produced runtime/controller symptoms, not an upstream
failure report.

## Candidate disposition

Candidate `7cc4b7e3870f2c3582054b3ffcb62e98c1c9a795` includes repair
`5566612dc5ca9f6f25c55cc80ef68dec2805cb32` and is frozen at the archive hash
listed above. The earlier complete candidate failed. The current candidate's
Debug gate is active on both hosts. Sanitizer, dedicated native retests,
cross-host checks, and final reconciliation remain pending. M10 remains
unqualified until those required results pass on both hosts.
