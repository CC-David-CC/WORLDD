# Testing strategy: evidence, not acknowledgments

## The three separate results

**Harness result:** Does this archive's RPC reader, oracle, gate and witness work? `tests/harness` and `tests/native` answer that question. They cannot certify the Spatial OS.

**Production acceptance result:** Do the actual world daemon, compositor, input path and Git/process integration satisfy P00–P10? `tests/acceptance` answer that question through a thin conformance host linked to or calling production code. There is no implementation in this archive; these tests begin unrun.

**Machine acceptance result:** Does the actual selected X server, GPU, display, SpaceMouse and native login session work on the authorized machine? Follow `HARDWARE_ACCEPTANCE.md`. A software-rendered or synthetic-input pass cannot substitute for this result.

Keep all three statuses in every milestone report. Do not add their test counts into a single misleading “OS tests passed” number.

## Independent witnesses

| Requirement | Evidence outside the component making the claim |
|---|---|
| Geometry | Known answers, an independent Python oracle, signed axes, seeded cases and time-partition invariance |
| Rendering | Read rendered P6 pixels, expected projections, depth ordering and actor-specific views |
| Native drawing | A separately compiled X client paints unpredictable colors; the compositor must display those pixels |
| Compositor output | That C client reads the compositor's actual X drawable with XGetImage |
| Input routing | The C client records the actual X/XI2 event it receives, including local coordinates and device IDs |
| Git | An independent Git process reads worktrees, refs, files and commits |
| Processes | A real child writes its cwd/argv; the test waits for the actual child to finish |
| Persistence | Kill the actual test runtime, reopen the same storage, inspect the state |
| Cross-system recovery | Observe a real Git side effect before restarting the database-owning runtime |

The renderer's own resource counters are only diagnostics. A token permission test proves the actor API's authorization rule, not isolation against arbitrary same-user processes. The C witness is independent of the product source but not a hostile-proof security instrument.

## Driver loop

The root AGENTS.md defines the test schedule. Use focused checks during implementation. Run full qualification at the phase gate.

Read the current ticket and its assertions. Record the selected test IDs and their purpose before execution.
Select checks for the changed behavior, its callers, and affected authority boundaries.
Reproduce defects and preserve failure evidence. Implement the production change, then run its regression and related checks.
Run related integration scenarios when components connect.
Include focused Debug and runtime sanitizer checks for process, descriptor, memory, input, and lifetime changes.
Broaden testing when shared infrastructure changes or failures reveal wider effects.
Do not rerun the complete cumulative matrix after each small change.

Use the host required by each focused check. Test both hosts when behavior depends on host configuration or communication.
Independent checks may run on both hosts in parallel. Prevent conflicts over native sessions, fixture resources, and build capacity.

Review each increment and keep small Git commits. Record source and build pins, commands, results, and remaining gaps.
Preserve unexplained failures even when later repetitions pass. Report unrun checks separately.
Use git bisect when a reproducible regression has known good and bad commits.

At the phase gate, freeze the candidate and run all required qualification on both hosts.
Include Debug, sanitizer, harness, acceptance, and required native, cross-host, recovery, physical-input, and performance checks.
Run the cumulative acceptance gate through the phase. Earlier requirements remain mandatory.
After a source change, reassess affected evidence before declaring the phase complete.
This schedule changes test frequency. It does not change acceptance requirements or locked tests.

Do not mark a phase complete from `--case`. Earlier phases remain mandatory after later server changes. A failed prerequisite is BLOCKED, not skipped or passed. The runner rejects missing SUT/display configuration, altered baseline files, missing baseline test IDs, empty selections, skips and expected failures.

The baseline hash is tamper-evident, not administrator-proof. A machine owner can change it. The purpose is to make accidental or expedient test weakening visible in code review. A genuine contract contradiction must have a minimal reproducer, an explanation of the failed invariant and a reviewed versioned correction. Never regenerate the lock merely because a product change is inconvenient.

## Time and asynchronous work

The deterministic fixture supplies a monotonic clock to the **same production actor update loop**. `clock.advance` must process timeouts and motion, including the exact expiry point. Runtime wall-clock mode is checked on hardware. Do not put the movement implementation in a special test-only path.

Use events/fences for rendering, X input and lifecycle processing. Bounded polling is appropriate for an independently scheduled client. A fixed sleep is not proof of completion. `process.wait` waits for the real child; file existence alone is not completion. Every blocking operation has a deadline.

## Required mutation review before a phase is closed

After a passing gate, Luna must show that its important assertions fail when the corresponding production behavior is deliberately broken in a disposable worktree. Retain a short mutation report naming patch, failing test and restored clean commit. Do not change the acceptance oracle. These checks are implementation-time obligations; this archive cannot run them before a product exists.

Representative mutations:

- P01: swap quaternion order or invert one axis; replace body-twist integration with translation-at-start-of-step.
- P02: ignore expected revision; reply success before durable commit.
- P03: retain velocity after TTL or share a single actor's pose with another.
- P04–P05: reverse texture Y, omit damage processing, retain the old pixmap after resize, or draw the wrong room.
- P06: route both actors through the same master device, forward through an occluder, or retain Shift after cancellation.
- P07: discard the remaining displacement at a doorway or commit a canceled grab.
- P08: read a cached branch table instead of Git; run in the wrong cwd; execute argv through a shell.
- P09: omit intent reconciliation or leave a live handle valid after replacement.
- P10: use a button that changes rooms instead of actual portal crossing; permit Git context and room context to diverge.

Mutation tests are intentionally about behavior, not exact source structure. An equivalent clean implementation must be accepted.

## What is not yet certified by this baseline

The 141 tests cover a bounded initial operating environment, not all conceivable OS primitives. Future features need their own contracts and tests: clipboard/selection ownership, arbitrary text/IME composition, accessibility, full application session restoration, authenticated remote actor transport, audio, multi-monitor scanout, general network filesystems, adversarial tenant isolation, advanced collision shapes and unrestricted toolkit behavior. None is silently assumed to work.

Native input tests use the deliberately narrow X/XI2 witness protocol. Existing arbitrary application compatibility was explicitly removed from scope. However, real X server input state, routing and selected-device behavior must still be correct for the supported primitives. A fake event sink, a scripted screenshot generator, or an adapter that imports the Python oracle cannot pass honestly.

## Evidence layout

`evidence/last-target-run.json` records exact selected/executed tests and outcomes. Each acceptance case retains a unique state directory, RPC trace and native witness logs under the configured evidence root. Keep failures. Do not publish authorization tokens from these traces outside the authorized machine. For distribution, redact secrets without changing test results and retain the unredacted local copy under appropriate permissions.

Run memory/address/undefined-behavior checks in the production build and a real resource soak before the hardware gate. Software behavior and timing thresholds must be reported separately. Do not invent a frame-rate or helper-throughput result.
