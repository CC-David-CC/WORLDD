# Implementation status

## 2026-10-07: P07 observation increment

Actors can now request typed protobuf observations from the production worldd action boundary.
The compositor identifies visible representations with the same shader and depth testing used for rendering.
Replies include the sampled camera pose, viewport, world revision, spatial metadata, generations, and visible sample directions.
Observation uses a separate framebuffer and preserves another actor's presented view.

Both hosts passed Debug and ASan/UBSan runtime qualification:

- All 98 cumulative P00-P06 acceptance tests.
- The focused P07 observation acceptance test.
- All 79 core counterexamples, including nine new observation checks.
- All 39 harness self-tests.

An independent native client received input at the observed target's expected pixel.
A deliberate depth-test mutation failed the new occlusion test in an isolated worktree.
All 77 final source hashes matched the reviewed files and both execution hosts.
Only protobuf schema and codec hashes changed in the explicit 0.9.0 acceptance migration.
All locked acceptance assertions, oracles, and native witness sources remain unchanged.

Legacy coverage is 99 passed, zero current failures, and 42 unrun.
P07 remains incomplete. Swept collision, portals, and object manipulation are next.
Observation currently includes rendered panels and mapped native surfaces.
Other spatial representation types remain pending.
The fixture does not yet enforce workspace observation policies.
Full authority, confinement, hardware qualification, and the additional Spatial-M1 milestones remain mandatory.

Evidence is in evidence/observation-2026-10-07.
See docs/OBSERVATION.md for the raster visibility contract, transport migration, limitations, and test coverage.
No operator-session or system configuration changed in this increment.
The full project goal remains active.

## 2026-10-07: P06 captured input complete

Captured native motion now follows the actor's aim and camera pose.
It stays on the original application surface beyond the surface edges.
Additional buttons and releases preserve the last captured pointer position.
The scheduler sends pose-driven motion without nested compositor requests.
A real-time test verifies motion without query polling and expiry at the configured deadline.

Both hosts passed:

- All 98 cumulative P00 through P06 acceptance tests.
- All 70 core counterexamples and 39 harness checks in Debug and sanitizer builds.
- All 13 native input component checks against their private XLibre builds.
- The upstream server unit and modesetting symbol checks.

Independent clients witnessed signed coordinates, transformed capture planes, balanced release, and separate actor devices.
A checked XCB witness verified explicit-grab denial without capture transfer.
An isolated production mutation disabled retained capture coordinates.
The new drag test detected its incorrect release position.
The main runtime retained the correct implementation.

P06 is complete for its automated software qualification.
Legacy coverage remains 98 passed, zero current failures, and 43 unrun.
P07 rooms, portals, collision, observation, and manipulation is next.
Evidence is in evidence/captured-motion-2026-10-07.
See docs/CAPTURED_MOTION.md for protocol migration, coordinate limits, diagnostics, and exact commands.

These results use authenticated disposable Xvfb displays and software rendering.
Sanitizers cover the runtime and compositor, not the XLibre server.
Physical controls, native GPU performance, full workspace security, and the additional Spatial-M1 milestones remain pending.
Interim transient grouping does not replace broker-owned application identity.
The full project goal remains active.


## 2026-10-07: production actor input integration

worldd now routes typed pointer, keyboard, scroll, and cancellation actions through spatialwm to XLibre.
Actor sessions use distinct native input device pairs.
worldd checks grants, sequences, saved aim, current geometry, and surface generations.
Control transfer and cancellation release held native input.
Parent and transient surfaces share an interim control lease.

Both sb16 and sb16-2 passed the Debug and AddressSanitizer/UndefinedBehaviorSanitizer runs:

- All 98 cumulative P00 through P06 acceptance tests.
- All 65 core counterexamples, including nine new actor-input checks.
- All 39 harness self-tests.

Forty actor reconnects reclaimed their native devices and rejected revoked session tokens.
Independent native clients witnessed modifier release, focus transfer, cancellation, and actor isolation.
A new scroll-during-drag counterexample initially failed.
The corrected handler rejects a conflicting scroll without changing either application's input.

Retained evidence is in evidence/actor-input-2026-10-07.
The reports identify source hashes, commands, server binary hashes, and build identities.
See docs/ACTOR_INPUT.md for the explicit protobuf migration and limits.
All locked acceptance assertions and native witness sources remain unchanged.

Legacy coverage is 98 passed, zero current failures, and 43 unrun.
These tests use authenticated disposable Xvfb displays and software rendering.
The sanitizer runs cover the runtime and compositor, not the XLibre server binary.

P06 remains incomplete despite the passing baseline gate.
Captured pointer motion must follow the original surface when aim moves beyond its bounds.
Current input preserves drag release but does not implement that motion.
Full application-group authority must later use broker application identities.
The interim transient relationship is not an authorization boundary.
Native device trials, GPU qualification, and the remaining Spatial-M1 milestones remain pending.
The full project goal remains active.


## 2026-10-07: native input server component

XLibre now supplies compositor-owned actor device pairs and surface-local input routing.
The private interface uses a separate versioned protobuf schema.
The production C++ bridge validates reply framing and correlation.
Server generations invalidate stale window targets, including resize-away-and-back sequences.
Cancellation and controller disconnect release native held input.

Both hosts passed ten component checks with an independent native X client.
Those checks include overlapping windows, distinct device identities, independent modifiers, ordinary X clients, and off-screen coordinates.
Forty seat lifecycle cycles returned native device counts to baseline.
The preceding 80 acceptance tests, 56 core counterexamples, and 39 harness checks also passed on both hosts.
These results use authenticated disposable Xvfb displays and Debug builds.
See docs/NATIVE_INPUT.md and evidence/native-input-2026-10-07.

Public worldd actor actions do not call the input bridge yet.
The P06 pointer diagnostic therefore remains unresolved.
Legacy coverage remains 80 passed, one diagnostic error, and 60 unrun.
P06, full workspace security, native hardware qualification, and Spatial-M1 remain incomplete.

## 2026-10-07: pinned XLibre preparation on both hosts

Both hosts now build XLibre 25.1.9 from the recorded source commit.
The project keeps protocol headers and server binaries in private build directories.
Both builds passed the upstream unit target and modesetting symbol test.
The existing 80 acceptance tests, 56 core counterexamples, and 39 harness checks also passed on both hosts.
Those Debug runs use the pinned XLibre Xvfb binary with authenticated disposable displays.
All 70 recorded project source hashes match the reviewed files.

See docs/SERVER_PREPARATION.md for source pins, commands, limitations, package changes, and recovery.
Retained evidence is in evidence/server-2026-10-07.
The new server does not replace either operator desktop.

P06 native actor input remains unimplemented. The existing pointer diagnostic still has one error.
Legacy coverage remains 80 passed, one diagnostic error, and 60 unrun.
Native hardware and the additional mandatory Spatial-M1 gates remain pending.

The local inference endpoints currently refuse connections on port 8080 from both hosts.
Implementation continues under David's earlier instruction to work without the local agent.

## 2026-10-07: P06 native input baseline

P05 is complete at commit 36d54bad00219839ebc0de56922e0c8fc754229e.
Both hosts have that reviewed production implementation.
The independent native witness also builds with XI2 support on sb16.

The first P06 diagnostic reaches a real application surface and completes actor aim.
The pointer action returns UNKNOWN_METHOD because native actor input is not implemented.
This diagnostic has one error. It does not establish a passing P06 gate.
Current legacy coverage is 80 passed, one diagnostic error, and 60 unrun tests.
Evidence is in evidence/input-2026-10-07/sb16/baseline.

Next, add typed input actions and genuine actor-specific X server input routing.
Preserve native root coordinates, independent focus and modifiers, and balanced cancellation.
Do not substitute XSendEvent, a hidden window atlas, or a shared global input device.
The complete Spatial-M1 plan and its additional mandatory gates remain active.

## 2026-10-07: live application surfaces, P00 through P05

The compositor now imports actual X application backing pixmaps through Composite, Damage, and GLX.
worldd owns their spatial placement. Placement leaves native window coordinates unchanged.
Live identities distinguish compositor sessions and reused XIDs.
Resize, map, unmap, and destruction retire native resources and update generations.
Transients follow the parent room, rotation, translation, and scale.

Both sb16 and sb16-2 passed the normal build and the AddressSanitizer/UndefinedBehaviorSanitizer build:

- All 80 cumulative P00 through P05 acceptance tests.
- 39 harness self-tests.
- All 47 previous protocol, daemon, actor, and rendering counterexamples.
- Nine additional native surface lifecycle counterexamples.

The new checks exposed two production defects before correction.
Cyclic transient metadata could fail unrelated rendering.
Coalesced resize events could preserve an obsolete pixmap when the final dimensions matched.
Both reproductions now pass, together with the complete cumulative gate.
An isolated build with reversed texture orientation failed the independent quadrant assertion as expected.
The reviewed production sources remain unchanged by that mutation.
An initial pre-existing-client fixture also needed orderly compositor shutdown between test cases.

Retained reports, logs, and independent native pixel readbacks are in evidence/surfaces-2026-10-07.
All 69 recorded source hashes match the tested files on both hosts.
All 141 legacy acceptance IDs and assertions remain intact.
The remaining 61 legacy acceptance tests are unrun.
See docs/SURFACES.md for the implementation, protocol migration, and limits.

These results use authenticated disposable Xvfb displays and Mesa llvmpipe.
They do not qualify native GPU scanout, input devices, workspace isolation, or Spatial-M1.
P06 native actor input is next. The full project goal remains active.

Run these commands on either authorized host with new evidence directories:

~~~sh
python3 tools/qualify_core.py --through P05 --evidence-dir local/new-surface-run
python3 tools/qualify_core.py --through P05 --sanitize --build-dir build-sanitize --evidence-dir local/new-surface-sanitize
~~~

Both hosts reported these build identities:

- Debug: sha256:e575bf3eac831ec390086369cce5417f7fd10d821f45c87dd83cd70f694f4213
- Sanitizers: sha256:7061a06d9dc5edd20f3e6f67e99f4a43203679ea1a902c9df40da4325ce94857

The separate user-requested GNOME display changes are recorded in the root HOST_SETTINGS.md.
Both operator desktops have automatic locking, blanking, dimming, idle suspend, and DPMS disabled.
The normal login and SSH recovery paths remain available.

## 2026-10-07: compositor rendering, P00 through P04

worldd now starts a separate spatialwm process on an explicitly selected display.
The production compositor renders actual OpenGL frames from protobuf scene data.
Capture and visible presentation use the same framebuffer.
Actor aim uses the same room, panel poses, sizes, and clipping configuration.
No operator display is adopted automatically.
An existing window manager prevents compositor startup.

Both sb16 and sb16-2 passed:

- All 70 cumulative P00 through P04 acceptance tests.
- 39 harness self-tests.
- 13 protocol, 11 daemon, and 10 actor counterexamples.
- 13 additional rendering, configuration, process, and authorization checks.

The same checks passed with AddressSanitizer and UndefinedBehaviorSanitizer on both hosts.
An independent native X client read pixels identical to the compositor captures.
It checked three output sizes and varied panel colors.
The tests also cover clip boundaries, camera-plane crossings, and half-open panel edges.
A deliberately broken depth comparison failed the coplanar ordering counterexample.
The production depth comparison remains enabled.

Retained evidence is in evidence/render-2026-10-07.
Source hashes match the tested production files.
See docs/RENDERING.md for the implementation and protobuf migration.
All 141 legacy acceptance IDs and assertions remain intact.
The remaining 71 legacy acceptance tests are unrun.

These results use authenticated disposable Xvfb displays and Mesa llvmpipe.
They do not certify native GPU scanout, physical devices, or Spatial-M1.
P05 live application surfaces is the next milestone.
The full project goal remains active.
See docs/MILESTONES.md for the complete plan.

## Commands and build identity

Run on sb16 or sb16-2 from this folder:

~~~sh
python3 tools/qualify_core.py --through P04 --evidence-dir local/new-render-run
python3 tools/qualify_core.py --through P04 --sanitize --build-dir build-sanitize --evidence-dir local/new-render-sanitize
~~~

Use a new evidence directory.
Builds use two jobs and never acquire dependencies.
The qualifier creates authenticated disposable displays for graphics checks.
Reports record production, schema, configuration, test hashes, and binary build identity.

Both hosts reported these build identities:

- Debug: sha256:b9d3eeb9ea2bd3a2f5d47f7289d1ab8c0a2320b0a51ecd6a05486da5e07ea362
- Sanitizers: sha256:640f6bcf3e9d1c6e2b1ade298a5d385f019715c4a812a4f79ae355b71efe919d

The management channel uses trusted inherited pipes.
No public actor listener exists yet.
Use a private state directory.
Stop its authority before copying the complete directory for backup.
Restart from the same directory to resume durable world state.
Do not restore session tokens or held input.

## Previous checkpoints

Commit fd7c781 delivered actor sessions, grants, motion expiry, and atomic pose checkpoints.
Its 60 cumulative acceptance tests passed on both hosts, including sanitizer builds.
See docs/ACTORS.md and evidence/actors-2026-10-07.

Commit f0e1ef3 delivered the persistent world daemon and passing P00-P02 gates.
Commit d31fc59 delivered the protobuf P00/P01 core.
Commit 7283fd9 contains the earlier JSON P00 implementation.
Earlier evidence remains under evidence/worldd-2026-10-07 and evidence/core-2026-10-07.

P04 preparation installed graphics development packages and Xvfb on both hosts.
Its initial diagnostic failed at the missing render.capture operation.
See docs/GRAPHICS_PREPARATION.md for package changes and recovery records.
The later P04 counterexample exposed and corrected a top-edge rasterization mismatch.

David authorized continuation without the local agent after its hardware failure.
No login or desktop configuration changed during P04 implementation.

## Remaining work

P06 must supply genuine actor input identities and routing.
Later phases add rooms, manipulation, workspace Git storage, process context, recovery, and complete actor workflows.

The additional mandatory milestones include identity, delegation, confinement, cabinets, XLibre security,
shared worlds, model actors, application restoration, native devices, performance, and lifecycle soak.
None is implied by the current rendering result.
No Spatial-M1 or LOCAL_READY qualification is claimed.
