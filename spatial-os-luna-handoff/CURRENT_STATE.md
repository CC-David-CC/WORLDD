# Implementation status

## 2026-10-07: P09 real Git completion crash recovery

Workspaced now exits after real worktree creation and its flushed completion receipt, before the ready acknowledgment.
The world authority detects broker death before sending an operation reply.
Recovery reconciles matching pending effects once and preserves uncommitted files.
Missing or corrupt receipts and changed Git effects remain uncertain. Recovery never replays those creations.
Fault controls require independent fixture enablement in worldd and workspaced.
Normal daemons reject them. Armed state never persists across restart.

Both hosts passed Debug and ASan/UBSan qualification:

- All 222 core checks, including six new Git recovery checks.
- All 51 harness checks.
- 146 acceptance cases, including all 138 preceding P00-P08 cases.

The full P00-P09 gate executed 148 of 151 cases.
It reports one assertion failure, one error, zero skipped, and three unrun P10 cases.
Eight P09 primitives pass. Durable slots and stale-handle validation remain incomplete.
P09, full Spatial-M1, and native hardware qualification remain unfinished.

Independent witnesses verify status 88, pending registry rows, completion receipts, and the real Git branch before restart.
They verify retry, fixture authority, isolation, lost pending arms, and conservative recovery after external changes.
All 108 source hashes match reviewed local files and both hosts.
The acceptance lock, schemas, locked assertions, oracles, and native witness remain unchanged.
Evidence is in evidence/recovery-git-2026-10-07.
See docs/GIT_CRASH_RECOVERY.md and docs/RECOVERY_BOUNDARIES.md for scope, commands, and recovery.
No database migration, host configuration, or operator-session change was required.
Local-model review remains waived under David's hardware-failure instruction.
Next: surface.durable_slot using durable world placement and current live native surfaces.
The full project goal remains active.

## 2026-10-07: P09 real SQLite write-failure recovery

Worldd now injects storage.next_write at SQLite's actual xWrite boundary.
The private VFS returns SQLITE_FULL with ENOSPC once.
Failed creates, updates, deletes, and checkpoints preserve the previous durable state.
A later valid write succeeds. Faults remain fixture-only and never persist across restart.
Normal daemons retain SQLite's default VFS.

Both hosts passed Debug and ASan/UBSan qualification:

- All 216 core checks, including six new storage checks.
- All 51 harness checks.
- 145 acceptance cases, including all 138 preceding P00-P08 cases.

The full P00-P09 gate executed 148 of 151 cases.
It reports one assertion failure, two errors, zero skipped, and three unrun P10 cases.
Seven P09 primitives pass. Git-effect faults, durable slots, and stale-handle validation remain incomplete.
P09, full Spatial-M1, and native hardware qualification remain unfinished.

A native component witness verifies sixteen VFS lifecycles with real SQLite files.
It checks isolated faults, retry, WAL forwarding, unchanged default registration, and database reopening.
All 108 source hashes match reviewed local files and both hosts.
The acceptance lock, schemas, locked assertions, oracles, and native witness remain unchanged.
Evidence is in evidence/recovery-storage-2026-10-07.
See docs/STORAGE_FAULTS.md and docs/RECOVERY_BOUNDARIES.md for scope, commands, and recovery.
No database migration, host configuration, or operator-session change was required.
Local-model review remains waived under David's hardware-failure instruction.
Next: recovery.git_side_effect_boundary after real Git worktree creation and before the ready acknowledgment.
The full project goal remains active.

## 2026-10-07: P09 committed state survives a lost reply

Worldd now supports a real exit after successful COMMIT and before sending its reply.
The committed data and revisions survive exactly once.
A stale retry cannot apply the edit again.
A failed COMMIT returns STORAGE_ERROR and preserves the armed post-commit fault.
Both commit hooks remain restricted to explicit fixture mode.

Both hosts passed Debug and ASan/UBSan qualification:

- All 210 core checks, including eleven recovery checks.
- All 51 harness checks.
- 144 acceptance cases, including all 138 preceding P00-P08 cases.

The full P00-P09 gate executed 148 of 151 cases.
It reports one assertion failure, three errors, zero skipped, and three unrun P10 cases.
Six P09 primitives pass. Storage-write faults, Git-effect faults, durable slots, and stale-handle validation remain incomplete.
P09, full Spatial-M1, and native hardware qualification remain unfinished.

An isolated mutation moved the post-commit exit before COMMIT.
The locked acceptance witness rejected the unchanged durable label.
All 104 source hashes match reviewed local files and both hosts.
The acceptance lock, schemas, locked assertions, oracles, and native witness remain unchanged.
Evidence is in evidence/recovery-after-commit-2026-10-07.
See docs/RECOVERY_BOUNDARIES.md for exact commands, fixture controls, and recovery steps.
No database migration, host configuration, or operator-session change was required.
Local-model review remains waived under David's hardware-failure instruction.
Next: recovery.storage_failure at the actual SQLite write boundary.
The full project goal remains active.

## 2026-10-07: P09 real exit before world commit

Worldd can now exit at its actual SQLite boundary immediately before a write transaction commits.
The conformance supervisor detects the real worker death and closes transport.
Restart preserves the previous durable state.
Fault controls require explicit fixture enablement and remain unavailable through actor actions.
Reads and rejected writes preserve the pending arm. Restart never restores an armed fault.

Both hosts passed Debug and ASan/UBSan qualification for this primitive:

- All 206 core checks, including seven new recovery checks.
- All 51 harness checks.
- 143 acceptance cases, including all 138 preceding P00-P08 cases.

The full P00-P09 gate executed 148 of 151 cases.
It reports one assertion failure, four errors, zero skipped, and three unrun P10 cases.
Five P09 primitives pass: before-commit exit, preview rollback, repeated kills, worker identity, and worker death.
The remaining failures identify unimplemented post-commit, storage-write, Git-effect, surface-attachment, and stale-handle operations.
P09 remains incomplete. Full Spatial-M1 and native hardware qualification remain mandatory.

All 104 source hashes match reviewed local files and both hosts.
The acceptance lock, locked tests, schemas, oracles, and native witness remain unchanged.
Evidence is in evidence/recovery-before-commit-2026-10-07.
See docs/RECOVERY_BOUNDARIES.md for fixture controls, recovery, and exact qualification commands.
No schema migration, host configuration, or operator-session change was required.
Local-model review remains waived under David's hardware-failure instruction.
Next: recovery.after_commit_before_reply.
The full project goal remains active.

## 2026-10-07: P09 recovery transport migration

Migration 0.14.0 adds protobuf requests for the existing recovery contract.
The requests cover fault arming, durable slot attachment, and live surface-handle validation.
Fault controls remain separate from actor actions.
All 151 acceptance identifiers, existing assertions, independent oracles, and native witnesses remain unchanged.
The migration records each changed lock hash explicitly.

Both hosts passed Debug and ASan/UBSan regression qualification:

- All 138 P00-P08 acceptance tests.
- All 199 core checks.
- All 51 harness checks, including eight new transport checks.

Three P09 diagnostics now reach production instead of failing in the codec.
Fault arming and slot attachment return UNIMPLEMENTED errors.
The stale-handle assertion fails because validation still returns UNIMPLEMENTED instead of STALE_TARGET.
Coverage is 138 passed, one assertion failure, two errors, zero skipped, and ten unrun.
P09 remains incomplete. No recovery effect is claimed by this transport migration.

All 103 source hashes match reviewed local files and both hosts.
Evidence is in evidence/recovery-transport-2026-10-07.
See docs/RECOVERY_TRANSPORT_MIGRATION.md for the exact scope and qualification commands.
No host configuration or operator session changed.
Local-model review remains waived under David's hardware-failure instruction.
Next: implement recovery.before_commit at the actual world transaction boundary.
Full Spatial-M1, native hardware qualification, and the complete project goal remain active.

## 2026-10-07: P08 confined workspace processes complete

Actors now launch real applications through the production workspace broker.
The broker intersects workspace policy, actor membership, and the requested application profile.
Applications see their real worktree at /project in separate namespaces.
Read-only authority applies to both project files and writable Git administration.
Ordinary confined Git changes the actual worktree and its bare backing repository.

The namespace supervisor confirms launch and actual child exit through Protocol Buffers.
Launch records bind actor, operation ID, workspace, arguments, and profile.
Retries cannot duplicate execution or silently change workspace context.
Lost supervisors and crash-interrupted operations remain uncertain and are never replayed automatically.
Revocation stops affected application namespaces, including detached descendants.
Actor disconnect preserves the workspace application. Other actors' applications remain independent.

Both hosts passed final Debug and ASan/UBSan qualification:

- All 138 P00-P08 acceptance tests, including all 25 P08 primitives.
- All 199 core checks, including twelve process counterexamples.
- All 43 harness self-tests.

There are zero assertion failures, errors, or skips. Thirteen later acceptance tests remain unrun.
An isolated writable-mount mutation failed the locked read-only acceptance witness.
All 102 source hashes match reviewed local files and both hosts.
Evidence is in evidence/processes-2026-10-07.
P08 is complete for automated software qualification.

Execution storage advances to schema version 4 through explicit migration.
Both hosts now have a project-specific Bubblewrap copy and an AppArmor user-namespace profile.
Their global namespace restrictions, ordinary login sessions, displays, and SSH recovery remain unchanged.
See docs/WORKSPACE_PROCESSES.md for exact bootstrap, migration, rollback commands, and resource limits.
Installed files and the bootstrap installer have recorded hashes.

Aggregate quotas, confined GUI access, and full M03/M04 qualification remain incomplete.
Full Spatial-M1 and native hardware qualification remain mandatory.
The acceptance lock, all locked files, and public protobuf schemas remain unchanged.
Local-model review remains waived under David's hardware-failure instruction.
Next: P09, starting with recovery.before_commit.
The full project goal remains active.

## 2026-10-07: P08 workspace authority and actor context

Workspaced now owns workspace definitions and membership in its execution database.
Worldd owns explicit workspace availability and durable selection per actor.
Rooms do not own execution context. Room transitions preserve selection.
Creation, membership, availability, selection, and cabinet placement remain separate operations.

Selection requires the intersection of workspace policy and member authority.
Actor reconnect revalidates saved context. Revocation and unavailability clear affected selections.
Broker revocation remains effective if the separate world cleanup transaction fails.
Actor requests recheck authority before using saved context.
Workspace-bound worktrees cannot be retired or shared with another independent workspace.

A new private protobuf envelope carries broker management and authorization queries.
Public core and input schemas remain unchanged.
Explicit migrations advance execution storage to version 3 and world storage to version 2.
See docs/WORKSPACE_CONTEXT.md for migration commands, boundaries, and recovery notes.

Both hosts passed Debug and ASan/UBSan qualification:

- All 113 preceding P00-P07 acceptance tests and seventeen P08 acceptance tests.
- All 187 core checks, including thirteen new workspace checks.
- All 43 harness self-tests.

The full 138-case P00-P08 gate ran in each build on both hosts.
Coverage is 130 passed, zero assertion failures, eight errors, zero skipped, and thirteen unrun later tests.
All eight errors report unimplemented process execution.
P08 remains incomplete. Full Spatial-M1 and native hardware qualification remain pending.
All 98 source hashes match reviewed local files and both hosts.
Evidence is in evidence/workspace-context-2026-10-07.

The first qualification attempt detected an accidental locked-test edit.
That edit was restored exactly. A separate unlocked test covers corruption under the current world schema.
The acceptance lock and all locked files remain unchanged. Evidence preserves the failed integrity run.
No host configuration or operator session changed.
Local-model review remains waived under David's hardware-failure instruction.
Next: confined process execution, starting with process.workspace_cwd.
The full project goal remains active.

## 2026-10-07: P08 conservative worktree removal

Workspaced now removes clean Git worktrees while preserving their actual files in private retained storage.
It unregisters the worktree from real Git and preserves the branch.
Private Git refs also preserve detached commits.
Dirty files, ignored data, hidden index flags, active locks, and submodule entries prevent removal.

Removal journals bind operation IDs and preserve uncertain effects.
Completion receipts allow recovery after a failed acknowledgment without repeating filesystem changes.
Retries preserve new files created at the original path.
Open file descriptors continue writing to retained files.
Interrupted retirement and contradictory recovery paths remain uncertain and preserved.
Automatic restoration and permanent cleanup of retired data remain unimplemented.

The execution registry advances to schema version 2 through an explicit migration.
Normal startup refuses a version 1 database instead of silently changing it.
See docs/WORKSPACE_REMOVAL.md for the migration command and recovery inspection procedure.
Workspace-bound removal protection remains pending with workspace integration.

Both hosts passed Debug and ASan/UBSan qualification:

- All 113 preceding P00-P07 acceptance tests and thirteen P08 acceptance tests.
- All 174 core checks, including thirty-nine broker checks.
- All 43 harness self-tests.

The full 138-case P00-P08 gate ran in each build on both hosts.
Coverage is 126 passed, zero assertion failures, twelve errors, zero skipped, and thirteen unrun later tests.
All twelve errors report UNIMPLEMENTED for workspace context or process execution.
P08 remains incomplete. Full Spatial-M1 and native hardware qualification remain pending.
All 96 source hashes match reviewed local files and both hosts.
Evidence is in evidence/workspace-removal-2026-10-07.

Locked tests, oracles, native witness sources, and protobuf schemas remain unchanged.
No host configuration or operator session changed.
Local-model review remains waived under David's hardware-failure instruction.
Next: workspace creation, membership, availability, and actor selection through the production broker.
The full project goal remains active.

## 2026-10-07: P08 live Git status

Workspaced now reads the current commit, branch, and file status from real Git.
External commits, branch changes, detached HEAD, and unborn branches remain observable.
NUL-delimited parsing preserves unusual UTF-8 filenames and both sides of renames.
Tracked changes, merge conflicts, untracked files, and ignored data remain visible.
The broker rejects changed administrative authority links before reading status.
Oversized results return a bounded error without changing files or closing the channel.

Both hosts passed Debug and ASan/UBSan qualification:

- All 113 preceding P00-P07 acceptance tests and eleven P08 acceptance tests.
- All 162 core checks, including twenty-seven broker checks.
- All 43 harness self-tests.

The full 138-case P00-P08 gate ran in each build on both hosts.
Coverage is 124 passed, one failed, thirteen errors, zero skipped, and thirteen unrun later tests.
The failure and errors cover unimplemented removal, workspace context, and process execution.
P08 remains incomplete. Spatial-M1 and native hardware qualification remain pending.

The first implementation rejected valid Git common-directory links with a trailing separator.
The corrected implementation passed all focused checks and full regression qualification.
Evidence preserves both runs in evidence/workspace-status-2026-10-07.
All 96 final source hashes match reviewed local files and both hosts.
See docs/WORKSPACE_STATUS.md for exact commands, evidence, and limitations.

The current status interface rejects filenames containing invalid UTF-8 bytes.
Git status does not lock external writers or reveal changes hidden by every index flag.
Safe removal requires separate protection and remains the next primitive.
Locked tests, oracles, native witness sources, and protobuf schemas remain unchanged.
No host configuration or operator session changed.
Local-model review remains waived under David's hardware-failure instruction.
The full project goal remains active.

## 2026-10-07: P08 real Git creation through workspaced

The production workspaced process now owns a separate durable execution registry.
The session supervisor starts it independently of worldd and supplies a private protobuf channel.
Repository import creates real bare backing stores with independent Git objects.
Worktree creation follows real branch rules and records durable operation identities.
Matching retries preserve uncommitted files. Changed requests return OPERATION_CONFLICT.

A private completion receipt distinguishes finished checkout from partial Git metadata.
Recovery reconciles witnessed effects against actual Git state.
Uncertain imports and worktrees remain preserved for explicit repair.
Administrative Git uses controlled configuration, a restricted environment, and supervised process groups.
The wrapper does not implement generic application confinement.

Both hosts passed Debug and ASan/UBSan qualification for this increment:

- All 113 preceding P00-P07 acceptance tests and eight P08 acceptance tests.
- All 152 core checks, including seventeen broker checks.
- All 43 harness self-tests.

The full 138-case P00-P08 gate ran in every build on both hosts.
Coverage is 121 passed, one failed, sixteen errors, zero skipped, and thirteen unrun later tests.
Dirty removal fails because its handler still returns UNIMPLEMENTED.
The other errors cover unimplemented Git status, removal, workspace context, and process execution.
P08 remains incomplete.

An isolated mutation removed independent Git object copying.
The external inode witness detected the resulting shared object storage.
All 94 final source hashes match local reviewed files and both hosts.
Evidence is in evidence/workspace-git-2026-10-07.
See docs/WORKSPACE_GIT.md for boundaries, recovery behavior, exact commands, and limitations.
Locked tests, oracles, native witness sources, and the protobuf contract remain unchanged.

No host configuration or operator session changed.
Local-model review remains waived under David's hardware-failure instruction.
Full Spatial-M1, shared-world service attachment, and hardware qualification remain pending.
Next: implement git.actual_status and external-change observation from real Git output.
The full project goal remains active.

## 2026-10-07: P08 workspace contract migration

Migration 0.13.0 assigns Git project context and process execution to workspaces.
Rooms no longer determine execution context.
Workspace creation, membership, availability, selection, and cabinet placement remain separate.
The production broker, workspace registry, and confined execution effects are still unimplemented.
New typed protobuf requests return explicit UNIMPLEMENTED errors.

The migration preserves all 141 earlier requirements through two documented test-ID changes.
Ten additional acceptance tests cover workspace authority and confined execution.
Four new harness checks validate typed transport boundaries.
The inventory now contains 151 acceptance tests and 140 primitive tickets.
See docs/WORKSPACE_MIGRATION.md and docs/workspace-migration-0.13.0.json for the correction and exact hash changes.

Both hosts passed Debug and ASan/UBSan runtime regression qualification:

- All 113 cumulative P00-P07 acceptance tests.
- All 135 core checks.
- All 43 harness self-tests.

The targeted P08 real-worktree case returned UNIMPLEMENTED from production on both hosts.
Coverage is 113 passed, one diagnostic error, and 37 unrun.
P08 is not complete. Full Spatial-M1 and physical qualification remain pending.
Evidence is in evidence/workspace-contract-2026-10-07.
Final audits verify 85 source hashes, with a documented formatting-only normalization of the acceptance lock after qualification.
The original qualification lock remains in evidence. Final harness and diagnostic checks use the normalized lock.

Namespace prerequisite checks found working passwordless sudo and privileged mount/PID namespaces on both hosts.
Unprivileged user-namespace creation failed while writing uid_map.
Bubblewrap 0.9.0 is installed. These prerequisites do not prove application confinement.
No system configuration or operator session changed.
Continue without local-model review under David's hardware-failure waiver.
Next: implement git.real_worktree through the production workspace broker.
The full project goal remains active.

## 2026-10-07: P07 object manipulation complete

Actors can acquire visible, reachable objects through typed protobuf actions.
Each object has one editing lease. Each actor can hold one object.
Live previews appear consistently in rendering, picking, and observation for other actors.
Only explicit commit writes the pose through transactional, revision-checked world storage.
Cancellation, stop, close, and portal crossing discard previews and release object leases.
Conflicting edits and replacement objects invalidate old leases.
Storage failures preserve drafts for an explicit retry or cancellation.

Both hosts passed Debug and ASan/UBSan runtime qualification:

- All 113 cumulative P00-P07 acceptance tests.
- All 135 core checks, including 21 new object checks.
- All 39 harness self-tests.

Independent evidence covers native occlusion, preview rendering, reach, competing leases, storage failure, and crash recovery.
A real-time run verifies configurable reach, object size, and material color.
An isolated mutation incorrectly saved live previews.
The crash witness detected that changed durable placement.
All 84 final source hashes match the reviewed source and both hosts.

P07 is complete for automated software qualification.
Legacy coverage is 113 passed, zero current failures, and 28 unrun.
Evidence is in evidence/objects-2026-10-07.
See docs/OBJECTS.md for migration 0.12.0, lease behavior, and qualification limits.
The schema, codec, and fixture configuration hashes change explicitly.
Locked acceptance assertions, oracles, and native witness sources remain unchanged.

Next: workspace-owned Git worktrees and process execution.
The older room-owned P08 contract must first be corrected through an explicit recorded migration.
Fixture grants and software-display tests do not establish full Spatial-M1 authority, confinement, or physical qualification.
All remaining Spatial-M1 and hardware milestones remain mandatory.
No system configuration or operator session changed.
The full project goal remains active.


## 2026-10-07: P07 portal navigation

Actors now cross portal apertures through the production motion and collision path.
Crossing preserves remaining displacement and maps orientation without an extra turn.
Source walls block entry. Destination walls constrain movement and reject obstructed landings.
Portal chains and zero-distance cycles have bounded processing budgets.

Crossing releases native keys, buttons, focus, capture, and application leases.
Other actors retain their input state. Applications and runtime processes remain alive.
Live actor locations prevent room deletion before checkpointing.
Checkpoints restore destination rooms and poses with fresh session tokens.
Portal outlines render from both sides with open apertures.
Rendering, picking, and observation use the same outlines.

Both hosts passed Debug and ASan/UBSan runtime qualification:

- All 98 cumulative P00-P06 acceptance tests.
- All five focused portal acceptance tests and the three previous P07 cases.
- All 114 core checks, including 19 portal counterexamples.
- All 39 harness self-tests.

An isolated mutation discarded remaining movement after crossing.
The rotated-portal witness detected that error.
All 82 final source hashes match the reviewed source and both execution hosts.
Evidence is in evidence/portals-2026-10-07.
See docs/PORTALS.md for migration 0.11.0, numerical boundaries, and qualification limits.
Only the documented protobuf schema hash changes in the acceptance lock.
All locked acceptance assertions, oracles, and native witness sources remain unchanged.

Legacy coverage is 106 passed, zero current failures, and 35 unrun.
P07 remains incomplete. Object grabbing, preview movement, commit, cancellation, and leases are next.
These software-display results do not establish physical-device, native GPU, or full workspace-security acceptance.
All additional Spatial-M1 and hardware milestones remain mandatory.
No system configuration or operator session changed.
The full project goal remains active.


## 2026-10-07: P07 swept collision and visible barriers

Actor motion now uses a continuous sphere sweep and preserves sliding motion along barriers.
The same transformed bounds supply collision, rendering, picking, and observation.
Observation combines visible barrier faces into one entity record.
Turning motion uses bounded chord subdivision to prevent missed collisions along an arc.

Both hosts passed Debug and ASan/UBSan runtime qualification:

- All 98 cumulative P00-P06 acceptance tests.
- The focused P07 sweep, sliding, and observation tests.
- All 95 core counterexamples, including 16 collision checks.
- All 39 harness self-tests.

The new checks cover rounded contacts, tangency, overlap, 180 randomized sweeps, fast motion, and curved paths.
They also cover geometry updates, all six visible faces, and native surface occlusion.
An isolated mutation removed barriers from actor motion while preserving their visible geometry.
The fast-motion witness detected the resulting wall crossing.
All 79 final source hashes matched the reviewed files and both execution hosts.

Legacy coverage is 101 passed, zero current failures, and 40 unrun.
P07 remains incomplete. Portals and object manipulation are next.
Evidence is in evidence/collision-2026-10-07.
See docs/COLLISION.md for migration 0.10.0, numerical limits, contact budgets, and qualification details.
The explicit migration changes schema, codec, and fixture configuration hashes.
All locked acceptance assertions, oracles, and native witness sources remain unchanged.

These software-display results do not qualify physical input, native GPU timing, or full workspace security.
All additional Spatial-M1 milestones remain mandatory.
No system configuration or operator session changed in this increment.
The full project goal remains active.

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
