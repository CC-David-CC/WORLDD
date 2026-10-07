# Implementation status

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
