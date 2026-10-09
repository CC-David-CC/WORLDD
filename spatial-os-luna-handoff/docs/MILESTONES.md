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

All mandatory M01-M16 and H01-H08 requirements remain unfinished as complete qualification items.
Existing components provide partial implementation where CURRENT_STATE.md records their evidence.
The mandatory profile is tasks/spatial-m1.json.

The latest qualified increment adds authenticated enrollment delivery into world and workspace grants.
Both hosts passed complete Debug and runtime ASan/UBSan qualification on 2026-10-09.
Each profile passed 1,037 core checks across 55 suites, 51 harness checks, and all 151 P10 cases.
Six additional process-witness checks passed against each exact binary set.
No final profile failed or skipped a case.
See evidence/bootstrap-workspace-adapters-2026-10-09 for source pins, commands, and final results.
The original P00-P10 baseline and walkthrough evidence remain in evidence/walkthroughs-2026-10-07.

Qualified components now include actor identity, persistent grants, confined execution, and several native X isolation boundaries.
Worldd enforces broker-authorized application metadata, real pixels, and input through authenticated local actor channels.
Control loss cancels native input without terminating independently authorized application execution.
These component results do not establish complete milestone qualification.

Authenticated bootstrap grant delivery is qualified, committed, and adopted on both hosts at d4b025e.
Remote actor candidate 02 passed complete Debug qualification on both hosts.
Each host passed 1,089 core checks across 62 suites, 51 harness checks, and 151 P10 cases.
No Debug case failed or skipped. Direct cross-host actor scenarios passed in both directions.
Both runtime sanitizer profiles remain active. The remote candidate is not integrated yet.
Shared worlds, world transitions, complete cabinet actions, application restoration, and model tasks remain required.
Physical devices, native GPU output, timing, and soak evidence remain mandatory.
Native-server GLX sanitizer cleanup remains unqualified.
Read the newest CURRENT_STATE.md section before using historical entries as current implementation facts.
Next: finish authenticated world transport and distributed revocation checks before shared-world qualification.

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
