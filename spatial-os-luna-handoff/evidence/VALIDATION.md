# Authoring validation — 2026-10-06

## What this archive is

A test-first implementation handoff. No production Spatial OS compositor, world daemon, test-host implementation or modified X server is included. No target machine, GPU, SpaceMouse or higher-capability helper endpoint was accessed.

## Actual checks performed

| Check | Result | Evidence |
|---|---|---|
| Python syntax compilation of harness/tests/tools | Passed | `python -m compileall -q harness tests tools` exited 0 |
| Harness self-tests, final run | **39 passed** | `harness-selftests.log` |
| Harness self-tests, repeated final run | **39 passed again** | `harness-selftests-repeat.log` |
| Native C core-X11 witness build | Passed with `-Wall -Wextra -Werror` | `native-build.log` |
| Native witness smoke on an owned disposable Xvfb | **8 passed** | `native-smoke.log`, `native-probe-report.json` |
| Acceptance discovery | **141 named tests**, all phase/primitive metadata present | `acceptance-inventory.tsv` |
| Primitive tickets | **130 unique tickets**, all referenced tests and read-first files resolved | `tasks/INDEX.json` and final package checks |
| Acceptance baseline integrity | Passed | SHA256 manifest in `acceptance.lock.json`; self-test `test_baseline_intact` |
| Attempt to run P00 without a product | Correctly **BLOCKED**, exit code 2 | `blocked-without-sut.log`, `blocked-without-sut.exit-code` |
| Mock/fixture trying to certify product | Rejected in harness negative test | `test_fixture_cannot_certify_product` |

The eight native smoke tests exercised actual X window creation, geometry, known quadrants, unpredictable RGB paint, independent XGetImage capture, odd-size resize, unmap/remap, transient-parent recording, destruction, and real core-X11 input reception. Some named tests cover multiple observations. They tested the witness, not the future compositor or actor router.

The native binary was built in the authoring environment but is deliberately not distributed as a portable executable. Build `native/xprobe.c` on the target using `tools/build_probe.py`; P06+ requires the XI2 build.

## Authoring defect found and corrected

An initial harness run passed 38 tests and timed out on the ordinary transport round-trip: that self-test allowed only 0.5 seconds including interpreter startup. The normal peer allowance was changed to 5 seconds; the separate deliberate deadline test still uses 0.05 seconds and asserts a deadline error. Error tests were tightened to distinguish wrong IDs, malformed JSON and malformed response shape from incidental timeout. The correlation fixture now replies in reversed batches, not merely in request order. The original failed log is retained as `harness-selftests-initial.log`.

After this correction, all 39 harness tests passed; the final baseline was then checked and the full harness rerun twice successfully. This is not a changed Spatial OS performance threshold or a weakened product assertion.

Acceptance assertions were also hardened before establishing the delivered baseline: a sweep has both endpoints outside the thin wall; transient movement checks the actual resulting transform; input tests reject both pre-parking windows and moving them during interaction. No production result is claimed from reviewing those assertions.

## Explicitly unrun

- **All 141 Spatial OS production acceptance tests** are unexecuted against a real product, because that product is Luna's implementation task.
- The XI2 branch of the C witness was **not built or run** here. `X11/extensions/XInput2.h` was unavailable in the authoring environment. Core-X11 smoke results do not validate XI2 event handling.
- No native GPU/GLX texture import, multi-actor input-server modification, physical SpaceMouse, cold boot, production supervisor, long resource soak or physical display timing was tested.
- No helper call was made. Its endpoint/model/API must be configured and verified on the actual target.
- The chosen XLibre source was not cloned, built, installed or benchmarked. The handoff contains source-selection instructions and a pinning utility, not a fabricated pin for a build that never ran.

## Limits

These tests define a bounded, concrete first operating environment, not every possible OS feature. Their contract and implementation remain subject to genuine defects discovered on target. Passing them will not alone prove malicious-client isolation, unrestricted old-application support, advanced text/IME behavior, all GPU drivers or indefinite resource stability. See TEST_STRATEGY.md and HARDWARE_ACCEPTANCE.md for the deliberately separate requirements.
