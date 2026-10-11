# Project instructions

## Writing rules

Use short sentences and active voice.
Give one instruction per sentence where practical.
Use one term for each concept.
Prefer sentences with 20 words or fewer.
Allow required programming terms, technical verbs, adjectives, identifiers, and program names.
Preserve technical meaning when vocabulary restrictions would make an instruction unclear.

## Objective

Build a native spatial Linux/X11 operating environment.
Use real files, processes, application windows, input, and persistent state.
Do not substitute screenshots, simulated files, or a fullscreen launcher for the runtime.

The first usable milestone is Spatial-M1.
M1 includes multiple worlds, workspace authority, confined applications, and model-driven actors.
The first delivery supports David's personal evaluation.
Use fixture human identities and real model agents for acceptance.

Code-specific visualization is not an M1 requirement.
Fast-food, hardware-store, inverse-kinematics, and learning environments are later features.
M1 uses spatial geometry and semantic interaction, without articulated bodies or general rigid-body simulation.

Read the AGENTS.md instructions in spatial-os-luna-handoff and sovereign-kernel-local-first-handoff-v0.1.0 before work in those folders.
Read the current implementation status before you claim that a feature exists.
Use this consolidated model when older architectural notes conflict.
Record contract changes explicitly. Do not silently change locked acceptance tests.

## Hosts and responsibilities

Use sb16 and sb16-2 for Linux builds, tests, worlds, applications, and native sessions.
Run tool-enabled helper agents only on sb16 and sb16-2.
Each host has 6 GB VRAM, 16 GB RAM, and an SSD.
SSH access as dflanag3 and passwordless sudo work on both hosts.
Sudo does not authorize additional hosts or unrelated system changes.

World hosts run worlds. Inference hosts run models.
Do not run model inference on sb16 or sb16-2.

Use llm-49, llm-60, and llm-79 for inference services.
Use box-git only for Git hosting and repository administration.
These exceptions do not authorize world execution or project builds on those hosts.
Do not use another host unless the user explicitly changes these restrictions.

GNOME and XLibre development builds may run in parallel.
Use them to distinguish backend defects from desktop integration defects.
A GNOME development run does not replace native spatial-session acceptance.

## Core concepts

Use Protocol Buffers for production messages and component interfaces.
Keep versioned .proto schemas in Git. Generate bindings during the build.
Use Protocol Buffers for acceptance transport too.
Protocol Buffers do not replace authorization, validation, or transport authentication.

A world owns persistent spatial reality and its admission policy.
A room is a spatial subdivision of a world.
A workspace owns execution authority, project context, applications, and workspace-local persistent state.
An application instance owns its process tree and produces surfaces.
A surface is live application output.
A placement connects a representation to a world, room, and transform.
An actor is a persistent identity with location, controls, and explicit authority.
A host runs or presents these components. Its name does not define their identity.

Rooms and workspaces are independent concepts.
Rooms contain representations. Workspaces contain execution.
Do not give a workspace an owning room or default room.
A workspace may have no processes, surfaces, or spatial presentations.

Application state and spatial placement are separate.
The workspace owns the application-state boundary.
The application owns its state format.
The world owns spatial placement.

## Worlds and actor context

Each world has one entry room.
New actors use an explicitly requested valid spawn or the world entry spawn.
Persistent actors resume their last valid saved room and pose.

Actors in one world share its rooms, objects, placements, and actor population.
Entering a room or world does not grant workspace authority.
Entry does not create, join, select, or copy a workspace.

Public worlds admit registered actors.
Private worlds require explicit admission.
Home, business, and park are policy presets, not different world implementations.

Each M1 world has one authoritative worldd.
Both hosts may own independent worlds and connect to the same shared world.
World identity remains independent of its current authority host.
M1 does not require replicas, consensus, leader election, or automatic failover.
Authority-host failure may make that world unavailable.

Remember workspace selection per actor, per world.
On entry, restore the destination world's previous valid selection.
Select no workspace if the previous selection is unavailable or unauthorized.
Do not carry selection implicitly across a world boundary.
Room changes do not change workspace selection.

Workspace availability and cabinet placement are separate operations.
A workspace must be explicitly available before an actor selects it.
World transitions must not restart XLibre or the spatial compositor.

## Workspace identity and copies

Each workspace has one authoritative execution and filesystem state.
Several authorized presentations may refer to that same workspace.
Presentation in another room or world does not create a copy or new authority.

A second independently mutable execution state is a different workspace.
The same repository, branch, commit, directory contents, or machine name does not imply identical workspace identity.
Use real Git rules. Do not force a branch into a second linked writable worktree.
Separate mount namespaces may expose different worktrees at the same visible path, such as /project.
The underlying writable directories remain separate. A shared directory has one checked-out file state.

An explicit copy receives a new workspace identity.
Copy authorized project data, application definitions, workspace state, and reproducible environment configuration.
Assign destination membership, delegation rights, credentials, and budgets separately.
Do not copy active processes or interaction leases.

Migration may preserve identity only when one authoritative workspace instance exists at a time.
Workspace migration is not an M1 requirement.

## Identity and delegation

David controls the initial identity issuer.
Humans and agents generate separate cryptographic keypairs.
Enroll each actor by public key. Do not use a shared actor password.

Provide manual enrollment and an issuer-controlled approve_always policy.
Automatic enrollment uses the same identity-issuance mechanism as manual enrollment.
Apply only the configured bootstrap grant profile.
Automatic enrollment does not grant administrator authority.
David can change the policy and revoke credentials or complete actor identities.

Treat humans and agents as actors under the same authority rules.
An actor may delegate only authority it holds and may explicitly delegate.
Record the parent grant.
Parent revocation invalidates dependent grants.
Independent grants remain valid.
Administration and delegation of administration are separate capabilities.

Keep world admission, workspace access, observation, spatial manipulation, application control, and execution authority separate.

## Cabinets and real files

A cabinet presents workspace filesystem authority. It does not own storage.
M1 starts with one real writable project directory per workspace.
Use a real Git worktree where appropriate.
Keep application state, cache, and temporary files within granted workspace boundaries.
Mount required system dependencies read-only.
Do not expose unrelated projects, private keys, or host secrets.

Cabinet placement is explicit.
Use the world entry room when the placement request omits a room.
Allow an explicitly requested room and valid free placement.
Preserve later placement changes.
Workspace availability and selection do not automatically create geometry.

No granted project-file access means no cabinet.
Granted access with no accessible files means an empty cabinet.
Unauthorized actors see shared cabinet geometry as a locked representation.
Do not reveal filenames, directory structure, branch names, application names, pixels, or other sensitive workspace metadata.

Separate permission to see, move, and use a cabinet.
A room administrator may rearrange a cabinet without gaining file access.

Cabinet actions require a visible, reachable target in the actor's current room.
Check workspace authority before each consequential operation.

Support browse, read, create, edit, rename, move, copy, delete, and restore.
Delete into workspace-local trash.
Require separate authority for permanent deletion.
Use real filesystem operations and report conflicting changes.
Do not maintain an alternate simulated filesystem.

## Shared actor actions

Human controls and agent controls use the same semantic actions.
Use the same authorization checks and side-effect implementation.
Different gestures may invoke the same action.

Agents receive structured, authority-filtered world metadata.
Agents receive real application pixels when authorized.
Application accessibility trees are not an M1 requirement.
Do not invent semantic application content.

Agents have location, view, reach, selection, and input identity.
Higher-level helpers orchestrate the same actor primitives.
Do not give agents a privileged file API, unrestricted SSH access, or an alternate execution path.

Keyboard, mouse, SpaceMouse, and agent adapters feed one actor-control layer.
Use continuous motion intent and discrete interaction events.
Expire unrefreshed motion commands.
Stop motion and release held input after cancellation, controller loss, or device disconnect.

Keyboard, mouse, and physical SpaceMouse support are mandatory for M1.
Measure all six signed SpaceMouse axes, deadzones, combined motion, and unplug recovery.
Debug navigation does not replace physical acceptance.

## Application authority and XLibre

Launch every workspace process through one production broker.
Child processes remain within the workspace execution boundary.

Calculate application authority from the intersection of:

- Workspace policy.
- Launching actor authority.
- Requested application profile.

Enforce resource access below the application.
Use Linux namespaces, no_new_privs, filesystem restrictions, resource limits, and required syscall/device policies.
Do not use command names as the security boundary.
Ordinary editors, terminals, Git, compilers, and interpreters use real POSIX resources.

Surface control must not amplify actor authority.
An actor with read-only authority cannot control a writable terminal.

Extend the native XLibre server.
Assign each X connection an immutable workspace and application security identity.
Server resources inherit that identity.
Deny unauthorized cross-workspace enumeration, pixel reads, properties, resource changes, grabs, and input injection.

Route authorized observation and control through worldd and spatialwm.
Do not give ordinary X clients generic access to another workspace.
Separate per-workspace X servers are not the chosen architecture.

Use genuine actor-specific input identities and server input routing.
Do not implement a hidden window atlas or simulated event delivery.

## Interaction leases and revocation

One actor controls an application instance and its surface group at a time.
The lease includes main windows, dialogs, menus, and popups.
Other authorized actors may observe.
Different application instances may have different simultaneous controllers.

Before control transfer, release keys, buttons, modifiers, capture, drags, and focus.
Reject stale surface generations.

Revocation takes effect immediately.
Remove affected control and observation rights.
Cancel held input and stop further action routing.

An application belongs to its workspace.
Do not terminate it merely because its controller loses access.
Re-evaluate process authority separately.
Stop the process if its own effective authority no longer permits execution.

## External inference

Support hosted OpenAI and OpenAI-compatible inference providers.
Connect to installed llama.cpp and Strata services through their compatible API.
Treat Strata as an existing service. Do not implement or redesign its inference engine.
Separate provider identity, endpoint, model, capabilities, and credentials from world state.
Keep the environment usable when no model server runs.
Scripted actors remain useful when model agents are unavailable.

Use existing inference installations and recorded launch procedures.
Start an authorized service when its procedure is known.
Otherwise ask David to start it when needed.
Do not assume a particular provider always runs.

Current llm-79 Strata deployment:

- Preferred base URL: http://100.95.43.101:8080/v1
- LAN base URL: http://10.0.7.63:8080/v1
- Request: POST /v1/chat/completions
- Model: qwen3.8-flash-next-iq3_s
- Reported configuration: IQ3_S, Q8 KV, 192K context.
- Reported startup: automatic after reboot.
- Example request settings: max_tokens = 128, reasoning_effort = none, timeout = 120 seconds.
- Both world hosts reached both health endpoints successfully on 2026-10-07.
- LAN health confirmed the configured model loaded, max_context = 192000, authentication enabled, and images = false.

Keep bearer tokens outside AGENTS.md and Git.
Read credentials from local configuration or environment variables.
Use the Authorization: Bearer header with the configured credential.
Defer further model testing until the implementation needs a model.
David authorized work without local-model review on 2026-10-08.
The primary agent reviews each changed file and runs applicable tests before integration.
Do not wait for local-model proposals or reviews.
Resolve identified defects before integration. Do not accept unsupported claims.
This review change does not remove real-model acceptance requirements for Spatial-M1.
The local model is a coding collaborator. Its response is not execution evidence.
The current server does not enable image input.
Verify image support later, before visual-agent acceptance.
Health success does not prove image support or task success.

Authorize workspace-data export to each provider.
Validate model responses as proposed actor actions.
A model response does not grant authority.
Provider failure must not change world state or trigger an unauthorized provider switch.

## Persistence, Git, and restoration

Use Git for source, tests, XLibre patches, world definitions, policies, assets, and migrations.
Use transactional runtime storage for live persistent world and workspace state.
Back workspace project directories with real Git worktrees on their execution host.
Use local bare repositories as worktree backing stores and box-git as the shared Git remote.
Uncommitted files persist on the host filesystem. Commits provide versioned checkpoints.
Do not use Git as the realtime world database.
Do not commit credentials, runtime databases, environments, or build outputs.

A Git pull must not silently change a running world.
Apply definition updates through explicit, validated migrations.
Preserve actor context, moved objects, and existing placements.

Provide reproducible host bootstrap and world creation from declarative definitions.
The same definition can create separate world identities.

Restore explicitly configured applications into durable placements.
Do not replay uncertain commands or infer identity from reused XIDs.
Do not restore held input, expired leases, or old session tokens.
Journal external effects and report uncertainty.
Do not claim that SQLite makes filesystem, Git, process, and X effects one atomic transaction.

## Development checks and phase gates

Use focused tests during implementation. Run full qualification at the phase gate.
Select tests from the changed behavior, its callers, and the authority boundaries it affects.
Record the selected tests and their purpose before running them.
Add a regression check for each reproduced defect.
Run related integration scenarios when components connect.
Run focused sanitizers for process, descriptor, memory, input, and lifetime changes.
Broaden testing early when shared infrastructure changes or focused failures reveal wider effects.
Do not repeat the complete cumulative matrix after every small change.
Implement substantial, coherent milestone increments.
Run related regressions across the combined change before moving to the next milestone.
Keep reviewable commits and preserve history so reproduced regressions can be isolated.
Use git bisect only with a reproducible failure and known good and bad commits.
Preserve failing evidence. Do not treat unrun tests as passed.
Run the full required Debug, sanitizer, harness, and acceptance suites on both hosts at the phase gate.
Run required cross-host, native, physical-input, recovery, and performance checks for the milestone.
Freeze the gate candidate. Reassess affected evidence after any source change.
Focused checks do not prove phase completion.
This schedule supersedes older instructions requiring a cumulative gate after every primitive.
It does not change acceptance contracts or locked tests.

## Spatial-M1 acceptance

Require real authority, confinement, X isolation, recovery, and checked transport.
Define these requirements in the mandatory Spatial-M1 qualification profile.
Keep unrelated LOCAL_READY targets pending.
Do not claim full LOCAL_READY from M1 results.

Require:

- Independent private worlds on both hosts.
- One shared persistent world with actors from both hosts.
- Authorized world transitions and per-world actor context.
- Explicit workspace availability, selection, sharing, and presentation.
- Real cabinet operations and recoverable deletion.
- Confined editor and terminal execution.
- Independent native X-client attacks against workspace isolation.
- Application-group leases and immediate revocation.
- Scripted actor regression tests before live model tests.
- A real model completing a bounded task through actor mechanisms.
- Configured application restoration and session recovery.
- Physical keyboard, mouse, and SpaceMouse evidence.
- A 60 FPS target at 1080p, with recorded timing and resource measurements.
- At least 1,000 surface lifecycle cycles and a 30-minute native session.

M1 does not require remote live application-surface streaming.
Application execution and interactive application surfaces remain local to the workspace host.
Shared-world geometry, actors, objects, placements, and authority must still work across hosts.
Remote application streaming, remote application input, and cross-host workspace migration are later features.

Use independent witnesses and the production runtime.
Do not weaken tests to obtain PASS.
Report passed, failed, blocked, pending, and unrun requirements separately.

## Recovery and Git hosting

Preserve Ubuntu's normal login and recovery session during development.
Validate a dedicated spatial session before changing the normal login path.
Record system changes and exact recovery commands.
Do not close the working SSH or TTY recovery route.

The workspace root is the local Git repository.
The default branch is main.
Local main tracks origin/main.
Origin is dflanag3@box-git:/srv/data/git/spacial_desktop.git.
The repository-local commit identity is David <david@code.contractors>.
No GitHub publishing is authorized by this task.

Push reviewed, coherent commits to origin.
Preserve history. Do not force-push.
