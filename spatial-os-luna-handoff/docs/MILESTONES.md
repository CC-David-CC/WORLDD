# Full project milestones and evidence

This plan follows root AGENTS.md.
The active goal covers each milestone and the complete tested Spatial-M1 plan.
A completed phase does not complete the overall goal.
CURRENT_STATE.md records the latest implemented phase.
Generated reports provide execution evidence.

The 151-test acceptance suite is one qualification layer.
It does not cover every Spatial-M1 requirement.
Keep native hardware, authority, confinement, shared worlds, cabinets, and provider tests mandatory.

## Current qualification

M02 is qualified and adopted on both hosts at b5902eaaad0a9d0ce3cb272d37f0d0936a6575cd.
Read evidence/m02-gate-2026-10-10/adoption-final-01/primary-review.json for exact scope and pins.
Both hosts passed complete Debug and runtime-sanitize gates. All eight cross-host scenarios passed.
Five mutations were detected. The additive timeout-policy increment passed 132 focused checks.
The runtime matrix uses frozen source 1f5088e026573d86be8388cf1f41dfce20189db9.
The adopted source changes only CMake test registration and adds one test file relative to that runtime candidate.
Current-source binaries were not rebuilt after that test-only addition.

M01 is qualified at the same adopted source after its identity audit and M02 cancellation dependency review.
Read evidence/m01-audit-2026-10-10/primary-review.json. The review reuses complete gates; it does not count new runs.
M04-M16 and H01-H08 remain incomplete. Existing components have the evidence recorded in CURRENT_STATE.md.
The mandatory profile remains tasks/spatial-m1.json.

M03 is qualified and adopted at a99b7e46c5151ba47d0a53797b470cae6aaa5d13 in dedicated worktrees on both hosts.
Read evidence/m03-contention-2026-10-11/M03-final-primary-review.json.
All nine requirements, eight cross-host cases, and focused source-adoption checks pass.
Current development advances to M04. Ordinary checkout changes and normal login remain preserved.

M04 atom metadata filtering passes focused Debug and core-X sanitizer checks on both hosts.
The increment has 636 passed checks. Full GLX-enabled sanitizer runs remain failed and preserved.
M04 still needs selection privacy, asynchronous metadata, and its complete phase gate.
Read evidence/m04-atom-2026-10-11/primary-review.json for the component limits.

Selection namespaces and ConfigureNotify metadata also pass focused review on both hosts.
The combined candidate has 744 passed checks in Debug and core-X native-server sanitizers.
All forty-one locked files remain unchanged. Original failures and rejected interface drafts remain preserved.
Other asynchronous metadata, independent bypass mutations, and graphics qualification remain pending before the M04 gate.
The private Mesa cleanup candidate now passes actual GLX rendering and selected sanitizers on both hosts.
Each host passes 186 selected native checks, 100 shutdown repetitions, and 32 pixel-checked context cycles.
The original shutdown timeout remains unexplained. Native GPU and full M04 gates remain pending.
Read evidence/m04-glx-validation-2026-10-11/primary-review.json.
Parent query and core child-event metadata now pass 214 selected checks per host and profile.
Three isolated bypasses are detected on each host. CMake registers 140 core suites.
Read evidence/m04-parent-metadata-2026-10-11/primary-review.json.
Additional event paths, broader integration, native graphics, and the complete M04 gate remain pending.
The existing five-second client default remains. Bounded disk pressure defeated 700 ms and two-second clients.
One successful five-second pressure trial does not establish a worst-case latency bound.
Storage-pressure availability remains mandatory M03 and M1 performance work.
Read docs/M02_FAILURE_DISPOSITION.txt for preserved failures and unknown historical causes.
Read docs/M03_QUALIFICATION_AUDIT.txt and tasks/m1/workspace.resources.json for the next implementation boundary.

The following paragraphs record historical checkpoints. Their remaining-work statements describe those checkpoints.
The retained-launch control-owner binding checkpoint added exact recovery ownership.
Both hosts passed Debug and runtime sanitizers: 1,480 core checks across 101 suites per profile.
Each profile also passed 51 harness checks, 151 P10 cases, and three native-world checks.
No final case failed or skipped. All 41 locked files remain unchanged.
Read evidence/control-recovery-2026-10-09/integration-primary-review-binding-01.json.
Both hosts adopted all 298 qualified source hashes at 0fb875454df3cc582c35e076c96043035312fdff.
No session restarted or operator state migrated.
Read evidence/control-recovery-2026-10-09/binding-adoption-review-01.json.
Session coordination, administrative transport, selective recovery release, and issuer retirement remain required.
Reply validation qualification is active on both hosts. Handoff qualification waits behind it.

The previous qualified increment is native world handoff and authority crash fencing at a3a926d.
Both hosts adopted all 294 qualified source hashes without restarting sessions or migrating operator state.
Both hosts passed full Debug and runtime ASan, LeakSanitizer, and UBSan qualification.
Each profile passed 1,453 core checks across 99 suites, 51 harness checks, and 151 P10 cases.
Four unchanged supervised-world integration checks also passed in every host/profile combination.
No final case failed or skipped. All integrated source hashes match the qualified manifests.
Read evidence/supervisor-crash-fencing-2026-10-09/integration-primary-review-04.json and adoption-review-04.json in that folder.

A reproduced authority timeout came from durable sync latency during concurrent compilation.
The final qualification used unchanged source, assertions, deadlines, and durability settings without compiler overlap.
Original failed runs remain preserved. The diagnosis does not individually prove every historical failure's cause.
Read evidence/world-bootstrap-runtime-failure-2026-10-09/issuer-sync-review-09.json.

Both hosts reproduced the remaining control-reservation recovery gap after world death and verified native input release.
The application remained responsive. Its broker reservation still required explicit diagnostic release.
Next: implement exact recovery ownership and dependent control cleanup, then verified issuer retirement and cross-host cancellation.
Read docs/CONTROL_RECOVERY_BASELINE.txt. M02 remains incomplete.
The original P00-P10 baseline and walkthrough evidence remain in evidence/walkthroughs-2026-10-07.

Qualified components now include actor identity, persistent grants, confined execution, and several native X isolation boundaries.
Worldd enforces broker-authorized application metadata, real pixels, and input through authenticated local actor channels.
Control loss cancels native input without terminating independently authorized application execution.
These component results do not establish complete milestone qualification.

Authenticated bootstrap grant delivery is qualified, committed, and adopted on both hosts at d4b025e.
Remote actor transport is qualified, integrated, and adopted on both hosts at 62a617a.
Both hosts passed complete Debug and runtime ASan/UBSan profiles.
Each profile passed 1,089 core checks across 62 suites, 51 harness checks, and 151 P10 cases.
No final case failed or skipped. Direct cross-host actor scenarios passed in both directions.
Distributed cancellation acknowledgement remains required. A real delayed-event diagnostic reproduces the gap.
Shared worlds, world transitions, complete cabinet actions, application restoration, and model tasks remain required.
Physical devices, native GPU output, timing, and soak evidence remain mandatory.
Native-server GLX sanitizer cleanup remains unqualified.
Read the newest CURRENT_STATE.md section before using historical entries as current implementation facts.
Next: finish distributed revocation, then qualify persistent shared worlds and world transitions.

## Ordered implementation

| Milestone | Required behavior | Required evidence |
|---|---|---|
| P00-P02 | Checked protobuf, geometry, durable world authority | Cumulative gate on both hosts; protocol and daemon counterexamples; sanitizer runs |
| P03 | Independent actor sessions, grants, motion expiry, durable checkpoints | Cumulative P03; denied-action replay tests; atomic multi-actor checkpoints; real-clock expiry |
| P04 | Production compositor rendering and matching selection | Dedicated display; real spatialwm worker; camera, depth, projection, fence, and pixel tests |
| P05 | Live X application surfaces and resource lifecycle | Independent X client; damage, resize, map, transient, and destruction witnesses |
| P06 | Genuine actor input identity and routing | Native XI2 event witnesses; overlapping root windows; independent focus, modifiers, capture, and cancellation |
| P07 | Spatial rooms, portals, collision, and manipulation | Swept collision and portal counterexamples; visibility, reach, leases, rollback, and commit tests |
| P08 | Workspace-owned Git storage and process context | Real local bare repositories and worktrees; independent branches; same visible /project in separate mount namespaces; real process cwd; Git conflict and recovery tests |
| P09 | Failure recovery and durable application placements | Actual worker deaths; SQLite I/O faults; lost replies; Git reconciliation; stale live-handle rejection |
| P10 | Complete actor workflows | Production walkthroughs with live applications, real files, observation, action, cancellation, and independent witnesses |

P08 and P10 must use workspace execution context.
Migration 0.13.0 replaces the older room/worktree binding assertions with explicit workspace context.
Preserve their isolation, real-filesystem, Git, and recovery guarantees.
Room entry must not grant or select workspace authority.

## Additional mandatory Spatial-M1 qualification

| ID | Requirement | Evidence needed before completion |
|---|---|---|
| M01 | Cryptographic human and agent identities | Separate keypairs, enrolled public keys, issuer control, manual and approve_always enrollment, bounded bootstrap grants, invalid-proof and revoked-identity denial |
| M02 | Delegation and immediate revocation | Structural scope and attenuation, explicit delegation rights, parent revocation, independent surviving grants, separate admission/workspace/observation/control/execution decisions |
| M03 | Workspace execution confinement | One production launch broker, inherited process boundaries, filesystem restrictions, namespaces, no_new_privs, syscall/device/resource limits; independent escape attempts |
| M04 | XLibre workspace security | Immutable X connection identity; resource inheritance; independent attacks against enumeration, pixels, properties, resource mutation, grabs, and input injection |
| M05 | Persistent worlds across both hosts | Independent private worlds on sb16 and sb16-2; one shared world; public/private admission; stable world identity; unavailable-authority behavior |
| M06 | Actor world transitions | Saved room/pose, entry spawn, per-world workspace selection, invalid-selection clearing, unchanged selection across room moves; no X server or compositor restart |
| M07 | Workspace identity and presentation | One execution authority, multiple authorized representations, explicit availability and copying, new identity for independent mutable copies, no copied credentials/processes/leases |
| M08 | Cabinets and real files | No cabinet without file authority; empty cabinet for granted empty storage; locked geometry without metadata leaks; independent see/move/use rights; real browse/read/write/rename/move/copy/trash/restore and permanent-delete authority |
| M09 | Application-group control | One controller per application group, observers, concurrent independent applications, balanced input release on transfer, stale-generation denial, immediate observation/control revocation |
| M10 | Shared human and agent actions | One semantic action path for keyboard, mouse, SpaceMouse, and agents; authority-filtered metadata and real pixels; no privileged agent file or execution path |
| M11 | Provider-independent model operation | OpenAI and compatible provider adapters, llama.cpp/Strata configuration, separately held credentials, authorized data export, provider failure without unauthorized actions, useful operation with no model |
| M12 | Actual model task | A real model completes a bounded task through actor mechanisms after scripted regressions pass; record supported image capability before visual-agent claims |
| M13 | Restoration and explicit migrations | Declarative bootstrap, separate world creation from one definition, preserved moved placements, application-defined state boundaries, configured application restoration, effect uncertainty, no stale input or tokens |
| M14 | Native devices and visible session | H01-H06 with actual GPU output, physical keyboard/mouse/SpaceMouse, twelve signed axis trials, combined motion, calibration, drift, unplug recovery, real actor devices, session recovery and cold reboot |
| M15 | Performance and lifecycle soak | At least 1,000 surface cycles and 30 minutes in the native session; 1080p 60 FPS target; recorded frame/input timing and CPU/RAM/VRAM/resource trends |
| M16 | Reproducibility and recovery | Source/build/configuration pins, explicit system changes, usable SSH/TTY recovery, repeatable qualification, reviewed commits on box-git, no credentials or runtime databases in Git |

Run docs/HARDWARE_ACCEPTANCE.md as a separate mandatory layer.
Reconcile its old branch-room wording with workspace ownership explicitly.
A software display can establish diagnostic evidence.
It cannot establish physical scanout, GPU performance, or SpaceMouse behavior.

## Completion rules

Record passed, failed, pending, blocked, and unrun items separately.
Each mandatory item needs evidence that covers the whole stated requirement.
Do not use reference-only kernel tests as production confinement evidence.
Do not accept a helper's claimed execution as a test result.
Do not turn missing hardware trials into passed or optional items.

The root plan excludes remote live-surface streaming, cross-host workspace migration, articulated bodies, and store simulations from M1.
Unrelated LOCAL_READY targets remain pending.
These exclusions do not remove any requirement listed above.

