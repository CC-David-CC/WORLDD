# Implementation status

## 2026-10-07: actors and input lifetimes, P00 through P03

The persistent worldd now owns independent actor sessions and live motion.
Typed protobuf operations open, inspect, close, and control actors.
Session tokens bind actions to one actor in one running world.
Navigation grants and monotonic sequences reject unauthorized or replayed actions.
Invalid input does not change accepted axes or refresh their expiry.

The production scheduler uses real monotonic time.
The test host selects a controlled clock at the same scheduler boundary.
A protobuf configuration file controls gains, deadzone, and input timeout.
The management I/O loop continues ticking during incomplete requests and output backpressure.

Actor identity and explicit pose checkpoints persist in SQLite.
A checkpoint commits all changed actors atomically.
Restart restores acknowledged poses and invalidates old tokens and input.
Only the actor runtime can change actor entities.

This completes the P03 management-session milestone.
Public-key enrollment, delegation, network admission, and hostile-process confinement remain pending.
The full project goal remains active.
See docs/MILESTONES.md for all required stages and additional Spatial-M1 qualification.

## Verified results

Both sb16 and sb16-2 passed:

- 39 harness self-tests.
- 13 binary protocol and numerical boundary tests.
- 11 daemon lifecycle and storage tests.
- 10 additional actor runtime counterexamples.
- All 60 cumulative P00/P01/P02/P03 acceptance tests.

The same checks passed with AddressSanitizer and UndefinedBehaviorSanitizer on both hosts.
The remaining 81 legacy acceptance tests are unrun.
Additional Spatial-M1, native-session, and physical-device requirements remain pending.

The initial P03 diagnostic failed at actor.open before implementation.
An isolated mutation removed navigation-grant enforcement.
The required permission test detected that mutation.
The production source retains the permission check.

Two P03 malformed-input encodings now use protobuf.
Both still require rejection by the production daemon.
See docs/ACTORS.md for the exact migration and counterexamples.
The lock retains all 141 legacy test IDs.
David authorized continuation without the local agent after its hardware failure.

## Commands and evidence

Run on sb16 or sb16-2 from this folder:

```sh
python3 tools/qualify_core.py --through P03 --evidence-dir local/new-actor-run
python3 tools/qualify_core.py --through P03 --sanitize --build-dir build-sanitize --evidence-dir local/new-actor-sanitize
```

Use a new evidence directory.
Builds use two jobs and never acquire dependencies.
Retained results are in evidence/actors-2026-10-07.
Reports record production, schema, configuration, test hashes, and binary build identity.
The mutation record identifies the changed source and the failing permission test.

The management channel uses trusted inherited pipes.
No public actor listener exists yet.
Use a private state directory.
Stop its authority before copying the complete directory for backup.
Restart from the same directory to resume durable world state.
No login, desktop, or service configuration changed for P03.

## Previous checkpoints

Commit f0e1ef3 delivered the persistent world daemon and passing P00-P02 gates.
Commit d31fc59 delivered the protobuf P00/P01 core.
Commit 7283fd9 contains the earlier JSON P00 implementation.
The corresponding evidence remains in evidence/worldd-2026-10-07 and evidence/core-2026-10-07.

## Next milestone

P04 adds the actual compositor and matching render/selection behavior.
Use an explicitly dedicated display.
Both hosts have a GTX 1060 with 6 GB VRAM.
Graphics development packages and Xvfb are now installed on both hosts.
Authenticated disposable displays reported Mesa llvmpipe OpenGL 4.5.
The first P04 diagnostic failed at the unimplemented render.capture operation.
See docs/GRAPHICS_PREPARATION.md for package changes, recovery records, and test-display commands.

Workspace Git integration, confinement, cabinets, XLibre security, shared-world networking,
model-driven actors, application restoration, and native hardware acceptance remain pending.
No Spatial-M1 or LOCAL_READY qualification is claimed.
