# Project instructions

## Authorized hosts

`sb16` and `sb16-2` are the only authorized hosts for this project. Run Linux builds, tests, native sessions, and remote helper workloads only on these hosts. Do not use another host unless the user explicitly changes this restriction.

Each host has 6 GB VRAM, 16 GB RAM, and an SSD, as reported by the user. Plan build parallelism, model sizes, and runtime resource use within those limits.

SSH access as `dflanag3` and passwordless sudo have been verified on both hosts. Sudo access does not expand the authorized host list.

For work inside `spatial-os-luna-handoff` and 'sovereign-kernel-local-first-handoff-v0.1.0', also follow its `AGENTS.md` and referenced project instructions.
you can build the gnome and xlibre builds in parallel to ensure its not a desktop problem because much of the backend is agnostic

## Git hosting

The workspace root is the Git repository for both handoff folders and shared project notes. The default branch is `main`; `origin` is `dflanag3@box-git:/srv/data/git/spacial_desktop.git`. The bare repository exists on `box-git`. Local `main` tracks `origin/main`. The repository-local commit identity is `David <david@code.contractors>`; no GitHub remote is configured.

The user explicitly authorized `box-git` for Git hosting and repository administration. This exception does not authorize builds, tests, native sessions, or helper workloads on `box-git`; those remain restricted to `sb16` and `sb16-2`.

Push reviewed commits to `origin`. Preserve existing history and do not force-push. Keep local credentials, private keys, environments, and build outputs out of Git.

I would make the first **usable** milestone one step beyond “3D windows work,” but stop well short of a full security system.

The right abstraction is:

> **Room = spatial organization.  
> Workspace = execution/security domain.  
> Window = a surface owned by a workspace.  
> Actor = an identity allowed to act on workspaces.**

Do **not** make rooms themselves security boundaries. That will become awkward immediately.

## First usable milestone: “Spatial Workspace”

You should be able to log into the Spatial session and actually do a small piece of work entirely inside it:

1. Move with the SpaceMouse through several persistent rooms.
2. Launch real X11 applications.
3. See their real windows as live 3D surfaces.
4. Point, click, type, drag, resize, minimize/park, and close them.
5. Create/open a **workspace** rooted at a directory such as:
   ```text
   ~/work/keylab/
   ```
6. Launch a terminal/editor inside that workspace.
7. Edit files, compile something, run tests, and use Git.
8. Leave the room, enter another room, then come back.
9. Find the windows and spatial arrangement where you left them.
10. Restart the graphical session and restore the workspace/world association.

But I would also require one primitive security boundary in that milestone.

### A workspace is launched through a broker

Not:

```text
spatialwm
   ↓
fork("xterm")
```

Instead:

```text
                    ┌────────────────────┐
room ──────────────►│ Workspace          │
                    │                    │
                    │ root=/work/foo     │
                    │ policy=...         │
                    └─────────┬──────────┘
                              │
                         launcher
                              │
             ┌────────────────┼─────────────────┐
             │                │                 │
       mount namespace   process group      environment
             │                │                 │
             └────────────────┼─────────────────┘
                              ↓
                         application
                              ↓
                         X11 surface
```

That launch path should exist **from day one** even if its initial policy is permissive.

Otherwise you'll eventually have hundreds of places doing things like:

```python
subprocess.Popen(...)
os.system(...)
```

and adding authority boundaries later becomes a rewrite.

---

# Minimal kernel enforcement belongs in M1

I wouldn't postpone all enforcement.

For the first usable workspace, give it something like:

```text
workspace foo

READ/WRITE:
    /work/foo

READ:
    /usr
    /lib
    /lib64
    required system files

NO ACCESS:
    /home/david/other-projects
    ~/.ssh
    ~/.config/spatial/secrets
    other workspaces
```

Implemented using Linux primitives such as:

```text
user namespace
mount namespace
no_new_privs
Landlock / equivalent path restrictions
cgroup v2
optional seccomp policy
```

The exact mechanism can evolve.

The architectural invariant is more important:

> **Every workspace process enters the system through one launch authority that can constrain it.**

Then tightening security doesn't change how applications are created.

---

# Don't try to secure “commands” by command names

For example, don't invent:

```text
allowed_commands = [
    "git",
    "gcc",
    "python"
]
```

That's brittle and not really how Linux authority works.

Instead control:

```text
which executable files can be reached
which files can be read/written
which syscalls are available
which devices are visible
which network namespace is available
which processes can be signaled
```

So:

```text
/bin/bash
```

might be perfectly available, but inside it:

```bash
cat ~/private/file
```

fails because that pathname doesn't exist/isn't authorized in the workspace.

That's a much stronger model.

---

# There is one major X11 security caveat

This matters enough that I would put it directly into the project's architecture.

Normal X11 assumes that clients connected to the same X server largely share a trust domain.

Historically, an X client can do things we absolutely don't want an untrusted agent doing:

```text
inspect other windows
observe input
inject input
query global state
interact with unrelated clients
```

Therefore:

```text
Linux filesystem sandbox
          ≠
secure workspace
```

if every workspace is simply connected unrestricted to the same X server.

So the milestones should distinguish these.

### M1

**Filesystem/process isolation exists.**

But we explicitly say:

> GUI clients currently share the X server trust domain.

No false security claim.

### M2

Add **X-side workspace isolation**.

Since you're willing to modify XLibre and don't care about legacy compatibility, I would solve this in the server rather than piling hacks above it.

Conceptually each X client gets:

```text
client.workspace_id
client.actor_id
```

and XLibre enforces:

```text
client A cannot enumerate B's resources
client A cannot read B's pixels
client A cannot receive B's input
client A cannot inject into B
client A cannot manipulate B's windows
```

except through explicit permissions.

That becomes substantially cleaner than trying to retrofit desktop-era X security mechanisms onto what is effectively a multi-user 3D operating environment.

---

# So I would use these milestones

| Milestone | Result |
|---|---|
| **M0 — Spatial bring-up** | XLibre/Xorg + `spatialwm`; one real xterm on a 3D surface; SpaceMouse movement; keyboard/mouse interaction. |
| **M1 — Spatial Workspace** | Rooms, multiple real applications, persistence, workspace launch broker, Git/files/builds, basic kernel filesystem/process confinement. **First genuinely usable system.** |
| **M2 — Protected Workspace** | XLibre workspace/actor isolation, device rules, network policy, stronger syscall/process boundaries. Now “protected workspace” is a defensible claim. |
| **M3 — Agent Workspace** | Multiple actor identities, independent input seats, agents occupying rooms, explicit workspace permissions and leases. |
| **M4 — Git World** | Worktrees/branches become first-class objects; agents get independent worktrees; merges and branch topology represented spatially. |
| **M5 — Spatial OS** | Normal daily environment: recovery, login/session management, system UI, networking/audio/device surfaces, mature policy management. |

And M0 should be **very short**. It isn't intended to become a prototype branch that we throw away.

M0 proves this:

```text
REAL X CLIENT
    ↓
REAL WINDOW
    ↓
XLibre
    ↓
LIVE PIXELS
    ↓
spatialwm
    ↓
3D surface
    ↓
raycast
    ↓
REAL INPUT BACK TO CLIENT
```

Then immediately proceed to M1.

---

## What I would personally call “first usable”

I'd require this exact demonstration:

```text
Boot Ubuntu 24.04
      ↓
select "Spatial" at login
      ↓
enter Home room
      ↓
SpaceMouse through door into Project room
      ↓
open workspace "test-project"
      ↓
launch terminal
      ↓
launch editor
      ↓
create hello.c
      ↓
compile it
      ↓
run it
      ↓
git add / commit
      ↓
try reading a file outside workspace
      ↓
kernel denies it
      ↓
walk into another room
      ↓
return
      ↓
windows are where they were
      ↓
log out/restart Spatial
      ↓
return to workspace
      ↓
spatial state restored
```


> **This is now a small spatial desktop, rather than a graphics experiment.**

I would **not yet say it is a protected multi-agent OS**, because unrestricted shared-X11 behavior would make that claim false.

The next milestone would specifically close that gap by making **workspace/actor identity part of XLibre itself**.

first prove spatial desktop mechanics, while already forcing every process through the eventual security boundary; then make that boundary progressively stronger rather than redesigning the whole execution model later.

Yes. I’d make a **workspace much larger than “a collection of windows,” but smaller than a VM/container**.

The clean definition is:

> **A workspace is the durable execution and authority context for one body of work.**

Window surfaces are merely one visible consequence of processes running inside it.

A workspace should own these things:

| Workspace owns | Example |
|---|---|
| **Filesystem view** | `/work/keylab`, mounted dependencies, writable scratch |
| **Execution context** | processes, process groups, cwd, environment |
| **Security policy** | readable/writable paths, devices, network, IPC |
| **Project context** | repo, branch/worktree, build directory |
| **Application instances** | this VS Code instance, this shell, this debugger |
| **Surface groups** | all X11 windows produced by those application instances |
| **Workspace-local state** | terminal history/session state, editor workspace data, build caches |
| **Resources** | CPU/GPU limits, temporary storage, perhaps GPU allocation |
| **Actor permissions** | who can enter, manipulate, execute, write, administer |
| **Lifecycle** | active, suspended, stopped, restored, destroyed |

But there are important ownership distinctions.

## The hierarchy I'd use

```text
World
│
├── Room
│    ├── spatial objects
│    ├── portals
│    └── references to workspaces
│
└── Workspace
     │
     ├── ProjectContext
     │    ├── repository
     │    ├── branch/worktree
     │    └── working directory
     │
     ├── ExecutionContext
     │    ├── namespaces
     │    ├── environment
     │    ├── process tree
     │    └── resource policy
     │
     ├── ApplicationInstance
     │    ├── process(es)
     │    ├── state
     │    └── SurfaceGroup
     │         ├── main window
     │         ├── dialogs
     │         └── popups
     │
     └── ActorPermissions
```

That gives us very clean semantics.

A **room does not own VS Code**.

A room might contain a spatial representation of a workspace whose VS Code instance currently has surfaces positioned there.

---

# Application instance is important

I would explicitly introduce this object.

Suppose VS Code produces:

```text
main window
command palette
file picker
tooltip
context menu
```

Those aren't five applications.

They're:

```text
ApplicationInstance: vscode:keylab:17

    process tree
        └─ code
           ├─ extension host
           └─ language server

    SurfaceGroup
        ├─ surface 101
        ├─ surface 104
        ├─ surface 118
        └─ surface 119
```

Then the workspace owns the **ApplicationInstance**, and the application instance owns its X surfaces.

That's much better than:

```text
workspace
    ↓
random list of XIDs
```

Especially because XIDs disappear and change.

---

# Application state: yes, but indirectly

This one needs a distinction.

The workspace should **own the namespace/location in which workspace-specific application state lives**, rather than pretending it understands every application's internal state.

For example:

```text
workspace/keylab/
    project/
    state/
        vscode/
        terminal/
        debugger/
    cache/
    tmp/
```

Conceptually:

```text
XDG_CONFIG_HOME = workspace-specific overlay
XDG_STATE_HOME  = workspace state
XDG_CACHE_HOME  = workspace cache
TMPDIR          = workspace tmp
```

You don't necessarily have to isolate all of `$HOME` from the first version, but that's a useful model.

Then VS Code can save:

```text
open files
window state
workspace settings
extension session state
```

without `worldd` having to understand VS Code's serialization format.

So:

> Workspace owns the **state boundary**, application owns the **state format**.

That's an excellent invariant.

---

# But some application state should remain user-global

We shouldn't blindly shove everything into the workspace.

For example:

```text
user-global
    Git identity
    preferred font
    keyboard layout
    trusted CA certificates
    perhaps editor theme

workspace-local
    open repo
    open files
    terminal cwd
    debugger configuration
    build output
    branch
    agent session
```

Potentially:

```text
system
   ↓
user
   ↓
workspace
   ↓
application
```

with layered state.

That means a workspace can inherit your editor preferences without sharing its project state with another workspace.

---

# Project execution context is probably the most important thing it owns

Take your KeyLab example.

You might have:

```text
Workspace A
    project = KeyLab
    worktree = main
    actor = David
```

and:

```text
Workspace B
    project = KeyLab
    worktree = agent/cost-model
    actor = Codex
```

They could occupy neighboring rooms.

But internally they have different:

```text
cwd
filesystem writable root
Git branch
process tree
terminal
editor
compiler processes
agent authority
```

So if Codex launches:

```bash
pytest
```

there is no ambiguity about where it executes.

It executes inside:

```text
Workspace B
```

which means:

```text
cwd =
    /work/keylab-agent-cost-model

filesystem =
    that worktree

environment =
    Workspace B environment

permissions =
    Workspace B policy

process ownership =
    Workspace B
```

This is much more fundamental than the window geometry.

---

# A workspace should own processes transitively

This becomes extremely useful.

If a terminal launches:

```text
bash
  ↓
python
  ↓
gcc
  ↓
test binary
```

we should still be able to say:

```text
all of these belong to workspace 42
```

not infer ownership from windows.

A process doesn't need a window to belong to a workspace.

That's important for agents and build systems.

For example:

```text
Workspace 42

GUI
├── VS Code
└── terminal

headless
├── language server
├── compiler
├── pytest
├── local model helper
└── git
```

All belong to the same execution domain.

The window surfaces are only the visual subset.

---

# Likewise, a workspace can exist with zero windows

For example:

```text
Agent build workspace
```

could be running:

```text
compiler
tests
local model
Git operations
```

with no displayed application.

You could walk into its room and see:

```text
┌───────────────────────┐
│ Agent Build Workspace │
│                       │
│ pytest       running  │
│ compiler     idle     │
│ branch       agent/x  │
│ dirty files  3        │
└───────────────────────┘
```

Then attach a terminal when you want.

So:

> **workspace ≠ GUI session**

That's important.

---

# Surfaces should have spatial state separate from application state

Consider a terminal.

Application state:

```text
shell process
cwd
environment
scrollback
jobs
```

Spatial state:

```text
room = Lab
position = (4.1, 1.5, -2.0)
orientation = (...)
size = (1.4m, 0.8m)
```

They shouldn't be serialized together.

I'd model:

```text
ApplicationInstance
    id = terminal-44
    workspace = keylab-main
```

and independently:

```text
SurfacePlacement
    surface_slot = terminal-44/main
    room = coding
    transform = ...
```

Why?

Because you might move the terminal into another room without changing anything about its execution context.

Or even show the same application through more than one visualization later.

---

# A workspace should not necessarily own a room

This is another distinction worth protecting.

Initially you might usually have:

```text
1 workspace ↔ 1 room
```

but don't encode that invariant.

You will quickly want:

### Several workspaces in one room

```text
Project Room

main branch workspace
Codex branch workspace
OMP branch workspace
```

or:

### One workspace represented in several rooms

For instance its terminal in:

```text
Coding
```

and a build-monitor surface in:

```text
Lab
```

So the relation is better modeled as:

```text
Room
   contains spatial representations

Workspace
   owns execution
```

They intersect, but neither is the other.

---

# Files are another important distinction

I'd say a workspace owns a **filesystem authority**, not necessarily the underlying files themselves.

For example:

```text
Repository:
    /srv/projects/keylab.git

Workspace A:
    /worktrees/keylab-main

Workspace B:
    /worktrees/keylab-codex
```

The Git repository exists outside either workspace.

Each workspace owns its particular:

```text
worktree
writable view
build directory
scratch
```

This matters when destroying a workspace.

Destroying:

```text
Workspace B
```

should not mean:

```text
delete the repository
```

It might mean:

```text
stop processes
remove temporary state
optionally remove its worktree
retain branch/commits
```

---

# I'd actually split workspace state into four buckets

This gives Luna a very easy model.

### 1. Identity/configuration

Durable:

```text
workspace_id
name
project
worktree
policy
owners
```

### 2. Execution state

Live:

```text
processes
namespaces
cgroups
environment
IPC
network context
```

Mostly recreated after reboot.

### 3. Application state

Durable or resumable:

```text
editor session
terminal session metadata
agent context references
debug configuration
application-specific files
```

### 4. Spatial presentation

World-owned:

```text
surface placements
workspace object placement
room memberships
camera bookmarks
visualization settings
```

That last one is actually better owned by the **world**, not the workspace.

The workspace is what exists.

The world decides **where/how you see it**.

---

# So I'd refine the ownership model to this

```text
                    WORLD
                      │
               presentation
                      │
          ┌───────────┴──────────┐
          │                      │
        Room                 Placement
                                 │
                                 ▼
                             SurfaceSlot
                                 │
                                 ▼
WORKSPACE ───── owns ───── ApplicationInstance
    │                            │
    │                            └── produces live X surfaces
    │
    ├── ExecutionContext
    │      ├── namespaces
    │      ├── processes
    │      ├── cgroup
    │      └── environment
    │
    ├── FilesystemAuthority
    │      ├── worktree
    │      ├── state dir
    │      ├── cache
    │      └── writable mounts
    │
    ├── ProjectContext
    │      ├── repo
    │      ├── branch
    │      └── build configuration
    │
    └── SecurityPolicy
           ├── actors
           ├── filesystem
           ├── devices
           ├── network
           └── process capabilities
```


The shortest definition is:

> **A workspace owns execution, authority, project context, applications, and workspace-local persistent state. The world owns spatial presentation. Applications produce surfaces; surfaces do not define the workspace.**

I would make them **independent, orthogonal concepts**, with a many-to-many relationship created only by spatial presentation.

The clean invariant is:

> **Workspace answers “under what execution and authority context does this work happen?”**  
> **Room answers “where in the spatial world is something presented?”**

So a workspace is **not inside a room**. Its surfaces and representations are.

That gives you:

```text
Workspace
   │
   ├── application A ── surface ── placement ── Room 1
   ├── application B ── surface ── placement ── Room 1
   └── application C ── surface ── placement ── Room 2
```

and also:

```text
Room 1

  Workspace A / VS Code
  Workspace A / terminal
  Workspace B / build monitor
  Workspace C / agent status
```

So the relationship is naturally **many workspaces ↔ many rooms**.

### Why not one workspace per room?

Because that would merge two things that will diverge almost immediately.

You will want something like:

```text
KEYLAB COLLABORATION ROOM

┌──────────────────────┐
│ David / main         │
│ VS Code              │
└──────────────────────┘

┌──────────────────────┐
│ Codex / agent-cost   │
│ terminal             │
└──────────────────────┘

┌──────────────────────┐
│ OMP / optimizer      │
│ test results         │
└──────────────────────┘
```

Those are three different execution/security domains in one physical room.

If `room == workspace`, that becomes impossible without inventing nested rooms or fake exceptions.

Likewise, one workspace will often need to appear in several places:

```text
KeyLab workspace

Coding room:
    editor
    terminal

Lab room:
    benchmark monitor
    GPU process surface

Project overview room:
    Git status board
```

Same processes, same worktree, same authority. Different spatial presentations.

---

## I would not even give `Workspace` a `room_id`

That's the simplest way to prevent the architecture from drifting.

Not:

```text
Workspace {
    room_id
}
```

Instead:

```text
Workspace {
    id
    execution_context
    filesystem_authority
    project_context
    policy
}
```

Then separately:

```text
ApplicationInstance {
    workspace_id
}
```

```text
Surface {
    application_instance_id
}
```

```text
SurfacePlacement {
    surface_id
    room_id
    transform
}
```

That chain is important:

```text
Room
  doesn't own
Workspace

Workspace
  doesn't own
Room
```

They meet here:

```text
Workspace
    ↓
ApplicationInstance
    ↓
Surface
    ↓
Placement
    ↓
Room
```

---

## A workspace can therefore be completely headless

This should be legal:

```text
Workspace: agent-build-17

processes:
    python
    pytest
    gcc

surfaces:
    none

room placements:
    none
```

It's still a perfectly valid workspace.

Then you could create a monitor for it in a room:

```text
WorkspaceStatusView
    target = agent-build-17
    room = Lab
```

without moving or changing the workspace.

That's another reason not to say the workspace “lives” in the room.

---

# I'd introduce one extra concept: a workspace anchor

Not as an ownership relationship, but as a useful spatial object.

For example:

```text
WorkspaceAnchor
{
    workspace_id
    room_id
    transform
}
```

Visually:

```text
┌───────────────────────────┐
│ KEYLAB / CODEX            │
│ branch: agent/cost-model  │
│ processes: 7              │
│ tests: running            │
│                           │
│ [enter] [terminal] [stop] │
└───────────────────────────┘
```

You can place that object in any room.

It is essentially a **portal/control object pointing at a workspace**.

You might have several:

```text
Coding room
    KeyLab workspace anchor

Agent room
    same KeyLab workspace anchor

Overview room
    same KeyLab workspace anchor
```

They don't duplicate the workspace.

They're just references.

---

# “Entering a workspace” should not necessarily mean entering a room

This distinction matters too.

An actor has at least two independent states:

```text
Actor {
    current_room
    active_workspace
}
```

Imagine you're standing in:

```text
Room = Collaboration
```

and interacting with:

```text
Workspace = keylab-main
```

Then you turn and use Codex's terminal:

```text
Room = Collaboration
Workspace = keylab-codex
```

You never moved rooms.

Your **execution context changed**, not your physical location.

That's useful.

An agent can similarly be:

```text
actor.room = Lab
actor.active_workspace = keylab-agent
```

The actor's location determines what it can spatially see/reach.

The workspace determines what happens when it launches commands/files/processes.

Those are different axes.

---

# But I would make active workspace explicit

You don't want commands accidentally executing according to whichever window happened to have focus.

So an actor should have something like:

```text
ActorContext {
    actor_id
    room_id
    active_workspace_id
}
```

Then:

```text
spawn terminal
```

means:

```text
spawn terminal
inside active_workspace
```

unless the actor explicitly targets another workspace.

For example:

```text
actor enters room
    ↓

room contains:
    Main workspace
    Codex workspace
    OMP workspace

actor selects Codex workspace anchor
    ↓

active_workspace = Codex
```

Now new processes are unambiguous.

---

# Rooms also shouldn't grant filesystem authority

Another useful invariant:

Walking into:

```text
"Secrets Room"
```

should not magically give a process access to secrets.

Room membership is presentation/location.

Workspace policy is authority.

Otherwise an attacker could conceptually gain filesystem permissions by changing spatial coordinates.

That's backwards.

Instead:

```text
Actor
  has permission
      ↓
Workspace
  has authority
      ↓
Filesystem/resources
```

Room determines whether you can spatially interact with the workspace's representation.

It doesn't create the underlying authority.

---

# The useful relationships become

```text
                    WORLD
                      │
        ┌─────────────┴─────────────┐
        │                           │
      Rooms                     Workspaces
        │                           │
   presentation                 execution
        │                       authority
        │                           │
        └──────── Placements ───────┘
```

More concretely:

```text
Room
├── ordinary object placements
├── surface placements
├── workspace anchors
├── portals
└── actors currently located here


Workspace
├── processes
├── files/worktree
├── application instances
├── security policy
├── state/cache
└── resources


ApplicationInstance
└── Surface(s)
       │
       └── zero or more Room placements
```

I would even allow **zero or more** placements for a surface rather than exactly one.

That opens a useful capability later:

```text
same build dashboard surface

    visible in Lab
    visible in Agent Control Room
```

Whether interactive surfaces may have multiple simultaneous presentations can be restricted later if input semantics make that undesirable. But the data model doesn't need to forbid it.

---

## One optional convenience: “home room”

I would **not** make this a core relationship.

But you could have:

```text
WorkspacePreference {
    default_room_id
}
```

meaning:

> When the user asks “take me to this workspace,” this is the preferred destination.

That is navigation metadata only.

It must **not** mean:

```text
workspace belongs to that room
```

or:

```text
surfaces must remain there
```

I'd probably call it `preferred_anchor` rather than `home_room` precisely to avoid accidentally turning the convenience into an ownership invariant.

---

# Example with your agents

Suppose the world contains:

```text
Rooms
├── Coding
├── Collaboration
├── Lab
└── Git Overview
```

and:

```text
Workspaces
├── keylab-main
├── keylab-codex
├── keylab-omp
└── gpu-benchmarking
```

Then:

```text
CODING
    keylab-main / editor
    keylab-main / terminal


COLLABORATION
    keylab-main anchor
    keylab-codex anchor
    keylab-omp anchor

    keylab-main / terminal
    keylab-codex / terminal
    keylab-omp / terminal


LAB
    gpu-benchmarking / monitor
    keylab-codex / test output


GIT OVERVIEW
    keylab-main branch representation
    keylab-codex branch representation
    keylab-omp branch representation
```

No duplication of execution environments.

No artificial requirement that every worktree needs its own room.

No problem showing three branches beside one another.

And no problem having one branch's applications distributed spatially.

---

## So I'd make this a hard architectural rule

**Rooms contain representations. Workspaces contain execution.**

And:

```text
Workspace ↔ Room
```

has **no direct ownership relationship**.

The bridge is:

```text
workspace-owned thing
        ↓
presentation / placement
        ↓
room
```

That is substantially cleaner than either “one workspace per room” or “a workspace spans rooms” because even saying that a workspace *spans* rooms implies the workspace itself has spatial extent.

It doesn't.

**Its representations can span rooms.**

That small distinction will keep the world model much cleaner once you have dozens of agents, worktrees, terminals, monitors, dashboards, and rooms.

For your system, I’d choose 4. None of the above and make the rule simpler:
The world has one entry room. Actors own their current location.

A “default room” should not belong to a workspace, project, code environment, Git branch, or application environment. We just established that rooms are spatial presentation while workspaces are execution/authority; tying a default room to an “environment” would quietly re-couple those abstractions.
For a normal persistent actor, there usually isn’t even a “default” decision:
David logs out while in Lab
        ↓
actor.current_room = Lab   (persisted)
        ↓
David logs back in
        ↓
returns to Lab

Same for an agent:
Codex is in KeyLab Collaboration
        ↓
process/session restarts
        ↓
Codex resumes in KeyLab Collaboration

The world entry room only answers the exceptional question:
Where does an actor go when it does not already have a valid location?

So actor creation is simply:
create actor
    ↓
explicit spawn room supplied?
    ├─ yes → put it there
    └─ no  → world.entry_room

After that, location belongs to the actor:
Actor {
    id
    current_room
    pose
    ...
}

and the world has:
World {
    entry_room
}

I would not add default_room to Actor or Workspace unless a future requirement proves we need it.
Why I don't like “per actor”
It initially sounds right because actors have locations, but “personal starting room” creates a second concept next to their actual persisted location:
actor.current_room
actor.starting_room

Then you immediately need semantics for:
- logout/login
- crashes
- changing the starting room
- teleporting
- deleted rooms
- whether an agent returns home after a job
None of that is inherent to the problem.
The actor simply is somewhere.
Persist that.
Why I especially don't like “per environment”
Suppose:
Workspace: keylab-codex

has:
terminal in Coding
test dashboard in Lab
Git representation in Git Overview
workspace anchor in Collaboration

Which one would be its “starting room”?
There is no correct answer because the workspace itself isn't spatial.
That's evidence that the abstraction is wrong rather than evidence that we need a selection field.
What the entry room should be
For your system I'd make it a real neutral part of the world, something like:
                         LAB
                          │
                          │
CODING ─────────── ENTRY / HUB ─────────── COLLABORATION
                          │
                          │
                    GIT OVERVIEW

It can contain things such as:
network/system status
available workspace anchors
running agents
recent workspaces
doors/portals
shutdown/logout

But it doesn't own those workspaces.
It's just the natural origin of the world's spatial graph.
And you personally won't constantly be thrown back there. If you walked ten rooms deep into:
Projects
   → KeyLab
      → Agent Work
         → Benchmark Room

then log out, the next login should put you right back there, at your saved pose.
So my answer to that installer question would be:
4 — None of the above.
And I would put in the notes:
One entry room belongs to the world. Persistent actors resume their last valid room and pose. New actors spawn in an explicitly requested room or, if none is specified, the world entry room. Workspaces have no default room.

I would choose 4. None of the above again.
The clean rule for your system is:
Actors in the same environment share the same world. Workspaces are shared only by explicit authority.

So neither “everything shared” nor “everything copied per actor.”
The hierarchy should be:
Environment / World
│
├── shared rooms
├── shared geometry
├── shared portals
├── shared object placements
├── shared actors and their locations
│
└── Workspaces
     ├── david-main
     ├── codex-feature-a
     ├── luna-driver
     └── shared-debug

Each actor independently has:
Actor
├── current_room
├── pose
├── active_workspace
└── authorities

What is shared automatically
If you and Luna are standing in the same room, you should both inhabit the same room, not private copies:
David                  Luna
  │                       │
  └────── same room ──────┘

door moved        → both see it
board updated     → both see it
agent walks by    → both see it
surface moved     → both see it
new object placed → both see it

That's one persistent world.
Otherwise you don't really have multiple actors inhabiting an OS; you have separate desktops rendered similarly.
What is not shared automatically
Execution authority:
David
  ↓
workspace: keylab-main

Luna
  ↓
workspace: keylab-luna

Codex
  ↓
workspace: keylab-codex

They may all stand beside each other in:
KeyLab room

while operating three different Git worktrees and three different process trees.
That is probably your common agent case.
But a workspace can deliberately be shared
This is why I wouldn't choose option 3 either.
Sometimes you will explicitly want:
Workspace: debugging-keylab

members:
    David  = admin
    Luna   = write
    Codex  = inspect

Now those actors really are interacting with the same:
processes
files
application instances
terminal session
debugger
window surfaces

There aren't three copies.
Authority determines what each can do.
So:
same world
    ≠
same workspace

but:
same workspace + sufficient permission
    =
same work

Surfaces follow workspace authority
Suppose a room contains:
┌──────────────────────────┐
│ VS Code                  │
│ workspace: luna-feature  │
└──────────────────────────┘

David might be allowed to see and interact with it.
Another agent might only see:
┌──────────────────────────┐
│ Luna workspace           │
│ busy                     │
└──────────────────────────┘

and not its framebuffer or files.
A third actor might not see that object at all.
I'd therefore separate:
World visibility
Workspace visibility
Workspace interaction
Workspace administration

rather than one giant can_access bit.
For example:
observe_world
observe_workspace_metadata
observe_surfaces
interact_surfaces
read_files
write_files
launch_process
manage_workspace

We don't need all those ACL details in M1, but the model should allow them.
There is also no automatic “copy”
This is important for Git.
If Luna wants independent work, we explicitly create:
Workspace
    ↓
Git worktree
    ↓
branch luna/foo

We do not say:
Luna entered the room, therefore clone/copy everything.

Actor location shouldn't cause filesystem duplication.
Instead:
spawn agent
     ↓
assign/create workspace
     ↓
possibly create Git worktree
     ↓
place agent somewhere in world

Those are separate operations.
What two actors in one room might actually see
Imagine:
                  KEYLAB ROOM

 David                                  Luna
   👤                                     🤖
    │                                     │
    │     ┌──────────────────────┐        │
    ├────►│ main / VS Code       │        │
    │     └──────────────────────┘        │
    │
    │                 ┌──────────────────────┐
    │                 │ Luna branch / shell  │◄────┤
    │                 └──────────────────────┘
    │
    └──── both ─────► ┌──────────────────────┐
                      │ shared test dashboard │
                      └──────────────────────┘

All three surfaces occupy one real room.
But:
VS Code
    workspace = keylab-main

Luna shell
    workspace = keylab-luna

Test dashboard
    workspace = shared-integration

Spatial proximity doesn't imply execution ownership.
That's the model you want once lots of agents exist.
Collision and object state are shared
If Luna moves a physical/spatial object:
terminal panel
x = 2m

to:
x = 5m

you should see it move.
The world does not fork.
Similarly:
David standing at x=3
Luna standing at x=4

both actors exist simultaneously.
That makes collaboration genuinely spatial.
For conflicting changes to spatial state, worldd handles concurrency.
For example:
David grabs monitor
Luna grabs same monitor

could give David an interaction lease:
monitor
owner = David
lease = 4.2 sec

rather than allowing two competing transforms.
Same general mechanism can protect a legacy application surface from simultaneous input where necessary.
The strongest invariant
I'd put this directly into the design documents:
Environment sharing determines shared reality. Workspace membership determines shared execution authority.

That gives us three independent questions:
Where are you?
    → Room

What are you working in?
    → Workspace

What are you permitted to do?
    → Authority

Those should never be collapsed.
So for that questionnaire I'd select:
4. None of the above
and put something like this in the notes:
Shared persistent world, explicit workspace sharing. Actors in one environment inhabit the same rooms, objects, placements, and actor population. Each actor independently selects or is assigned workspaces. A workspace may be private or shared among authorized actors; entering the same room or environment never implicitly copies, joins, or grants authority to a workspace.

Choose 1. Locked cabinet as the default.
That follows directly from the model we have built:
The room is shared reality. The workspace is separately protected authority.

If a cabinet exists at (x,y,z) in a shared room, everyone in that room should normally see that something occupies that space. Otherwise different actors inhabit geometrically different worlds, which causes ugly consequences for navigation, collision, coordination, and agents reasoning about each other.
So:
Shared room
│
├── David sees:
│     [ KeyLab / Codex ]
│     branch: agent/foo
│     files...
│     running processes...
│
└── Unauthorized actor sees:
      [ Restricted workspace ]
      🔒

The unauthorized representation should reveal essentially no workspace contents:
NO:
    filenames
    directory structure
    branch name
    Git status
    window pixels
    application names
    process list
    command output
    workspace-local object names

Potentially not even the workspace's real name. It could just render:
┌──────────────────────┐
│ 🔒 Restricted        │
│ Workspace            │
└──────────────────────┘

Why I would not choose “No cabinet”
Because then:
David's world:
    wall ─ cabinet ─ desk

Agent's world:
    wall ─────────── desk

Now you have actor-relative geometry.
Questions immediately appear:
- Can the unauthorized actor walk through its location?
- Can it place another object there?
- Does another actor appear to interact with empty air?
- If David moves it, what does the other actor observe?
- Does physics differ by observer?
Those are all signs that hiding the physical representation by default is the wrong model.
Keep shared geometry shared.
But visibility isn't authorization
The cabinet is not the security mechanism.
This must still fail at the real boundary:
unauthorized actor
       ↓
open workspace
       ↓
worldd checks authority
       ↓
DENIED

And independently:
workspace process
       ↓
open("/workspace/private/file")
       ↓
kernel policy
       ↓
DENIED

So even if someone modifies their renderer to remove the little lock symbol, they gain nothing.
The rendering expresses the policy. It does not enforce it.
There should actually be three independent permissions:
1. perceive representation
2. manipulate spatial representation
3. access represented workspace

For example, an actor could have:
see cabinet        YES
move cabinet       NO
open workspace     NO

A room administrator might instead have:
see cabinet        YES
move cabinet       YES
open workspace     NO

That makes sense: they can rearrange the room without gaining access to somebody else's project.
And the workspace owner:
see cabinet        YES
move cabinet       YES
open workspace     YES

That separation will be useful.
One exception is worth supporting
There are legitimate cases where even the existence of something should be secret.
For those, later support an explicit presentation rule such as:
representation_visibility = restricted

Then unauthorized actors genuinely don't receive that representation.
But that should be an intentional policy distinction, not what automatically happens whenever someone lacks workspace access.
It also means you have to define what happens to occupied space—perhaps such hidden representations cannot participate in shared collision, or get represented as anonymous opaque geometry.
That's additional complexity, so don't make it the normal case.
Therefore, for the questionnaire
Select:
1. Locked cabinet
And if notes are available, I'd put:
Shared-room geometry remains visible. Actors without workspace authority see an opaque locked representation with workspace contents and sensitive metadata redacted. Representation visibility, spatial manipulation, and workspace authority are separate permissions. Hiding the representation entirely is an explicit exceptional policy, not the default.

Choose 1. Real isolated directory, with one refinement:
The cabinet does not own storage. It represents a workspace's filesystem authority. For the first implementation, that authority has one primary real writable directory.

That gives you real Linux semantics immediately.
Workspace: keylab-codex
│
├── primary writable root
│      /srv/spatial/workspaces/keylab-codex/project/
│
├── application state
│      /srv/spatial/workspaces/keylab-codex/state/
│
├── scratch/cache
│      /srv/spatial/workspaces/keylab-codex/tmp/
│
└── explicitly granted system resources
       /usr       read-only
       /lib       read-only
       ...

The cabinet in the room can represent the project/ tree:
        ┌─────────────────────┐
        │ KEYLAB / CODEX      │
        │                     │
        │ src/                │
        │ tests/              │
        │ README.md           │
        │ pyproject.toml      │
        │                     │
        │ branch: agent/foo   │
        └─────────────────────┘
                  │
                  ▼
      real Linux filesystem

No simulated copies. No database pretending to be a filesystem. No special kernel-object filesystem.
For Git, make the directory a real worktree
This fits your agent model particularly well:
repository
    │
    ├── main worktree
    │      workspace: david-main
    │
    ├── luna/foo worktree
    │      workspace: luna
    │
    └── codex/bar worktree
           workspace: codex

So when Codex opens the cabinet, edits src/foo.c, opens a terminal and runs:
git status
make
pytest

those are ordinary commands operating on ordinary files.
Nothing has to translate between a spatial-specific storage model and Linux.
“Isolated directory” should mean authority, not necessarily a special disk
I would not require:
one partition per workspace
one filesystem per workspace
loopback image per workspace

The actual bytes can all live on the same ext4 filesystem.
Isolation comes from what the processes are allowed to see:
host filesystem
│
├── /home/...
├── /srv/projects/...
├── /srv/spatial/workspaces/A/
└── /srv/spatial/workspaces/B/

Workspace A's namespace sees something closer to:
/
├── project/       ← A's real directory, RW
├── state/         ← A's state, RW
├── tmp/           ← A's scratch
├── usr/           ← system, RO
├── lib/           ← system, RO
└── ...

It doesn't get:
workspace B
~/.ssh
other projects
arbitrary host files

That lets ordinary software continue thinking it is operating on a normal Unix filesystem.
Don't choose kernel-object storage
Option 3 would be a major architectural mistake for this project.
You would end up needing:
spatial kernel object
       ↓
materialize file
       ↓
run gcc/git/python
       ↓
detect filesystem changes
       ↓
translate back into object

Then symlinks, hard links, file descriptors, mmap, rename semantics, advisory locks, Git, build systems and every other POSIX behavior become your problem.
Linux already has the correct primitive:
files.
Your spatial OS should govern who can reach them, not replace them.
And don't start with imported simulation files
Option 2 makes sense for a game or safe demo.
It undermines the thing you're building because the first successful milestone would prove:
“We can manipulate a model of work.”

You need it to prove:
“We can do actual work inside the spatial operating environment.”

So opening a file in the cabinet should mean opening the real file.
Deleting it should really delete it, subject to workspace policy.
Running the project should execute the real project.
A Git commit should be a real commit.
One thing I would change in the questionnaire wording
I wouldn't permanently encode:
each workspace uses filesystem roots on sb16 or sb16-2

into the architecture.
Those are deployments.
The invariant should be:
Workspace
    ↓
FilesystemAuthority
    ↓
one or more explicitly granted real filesystem roots

Today they may be local directories on those machines.
Later one could be:
local SSD
NFS mount
Git worktree
removable disk
remote filesystem

without changing the workspace model.
For the first cabinet, though, deliberately keep it simpler:
one workspace
one real writable project root
one cabinet

Then add multiple roots only when there's a real use case.
So select 1.
For the notes I'd use:
The cabinet presents a workspace filesystem authority, not a separate storage system. The first implementation exposes one real persistent writable directory, normally a real Git worktree. Ordinary POSIX file operations act directly on it. Workspace processes receive only explicitly granted filesystem roots through the execution/security boundary; system dependencies may be mounted read-only. The architecture permits additional roots later without changing cabinet semantics.

Choose 1. Workspace-authorized grantors.
That fits your system better than “you alone” or “human owners,” because agents are intended to be genuine actors that can delegate work. The important rule is not whether the grantor is human or AI. It is:
An actor may delegate only authority that it explicitly possesses and that is marked delegable.

So permissions should behave more like capabilities than conventional “owner/admin/user” roles.
For example:
David
└── keylab-codex workspace
    permissions:
        read          YES
        write         YES
        execute       YES
        grant_read    YES
        grant_write   YES
        grant_execute YES

You can give Luna:
Luna
    read          YES
    write         YES
    execute       YES

    grant_read    YES
    grant_write   NO
    grant_execute NO

Then Luna can authorize a helper model to inspect the workspace:
Luna
  ↓ delegates READ
Helper
  ↓
read = YES

but cannot give that helper write authority.
The critical invariant should be:
delegated authority
    ⊆
grantor's delegable authority

Never:
Luna has write
therefore Luna can grant write

Those are different capabilities.
Revocation should follow the same authority chain
Suppose:
David
  ↓ grants
Luna
  ↓ grants
Helper A

If David revokes Luna's underlying authority, Helper A should not keep authority that existed solely through Luna.
Conceptually:
grant #1
David → Luna : read/write

grant #2
Luna → Helper : read
parent = grant #1

Revoking grant #1 makes dependent grant #2 ineffective.
That gives you clean delegation trees rather than trying to manually hunt down derived permissions.
You can still explicitly give Helper A an independent grant:
David → Helper A : read

which would survive Luna losing access.
I would avoid a special permanent “owner” concept if possible
You probably need a bootstrap authority, but that can simply be:
workspace administrator capability

rather than:
owner = David

Then a workspace initially gets something like:
David:
    administer_workspace
    grant_authority
    revoke_authority
    destroy_workspace

but those are actual capabilities.
That matters later because you may deliberately create a workspace administered by:
David + Luna

or eventually transfer administration altogether.
The model doesn't need a human-shaped exception.
One power should not automatically be delegable
Administration itself.
For example:
administer_workspace = YES
delegate_administration = NO

unless explicitly granted.
Otherwise:
David → Luna
Luna → Agent A
Agent A → Agent B
...

could silently propagate full control.
The same pattern works for every sensitive authority.
So for this screen
Select 1. Workspace-authorized grantors.
For the notes, I would write:
Authority is capability-based, not human/agent-role based. An actor may grant only permissions it both possesses and is explicitly allowed to delegate. Delegated grants record their parent authority and become ineffective when that parent is revoked. Independent grants remain valid. Workspace administration and the ability to delegate administration are separate capabilities.

That gives your agents real delegation power without accidentally turning “has access” into “may reproduce that access arbitrarily.”
I would choose **4. None of the above** and tighten the model.

The problem with **1** is the phrase **“file actions through a checked API.”** That quietly gives agents a second way to operate the computer that humans do not use. The problem with **2** is the opposite: forcing an LLM to infer everything from pixels and physically mouse through every operation is unnecessarily brittle. And **3** preserves both pathways, which is exactly the kind of parallel architecture we have been trying to avoid.

The cleaner rule is:

> **Agents get structured perception and structured actor controls, but all consequential interaction occurs through the same world/application mechanisms as any other actor.**

So an agent does not need computer vision to discover that a door exists:

```text
observe()
    ↓
room: Coding
visible:
    door: Lab
    surface: terminal-17
    cabinet: keylab
    actor: David
```

And it can issue:

```text
move(...)
turn(...)
look_at(...)
approach(...)
grab(...)
release(...)
activate(...)
click(...)
type(...)
scroll(...)
```

Those are **embodied actions expressed structurally**.

The agent still has:

```text
position
orientation
room
reach
line of sight
workspace authority
input seat
```

and those actions change the same world that you inhabit.

### Application interaction remains real

If Luna wants to edit a file:

```text
Luna
  ↓
approach terminal
  ↓
activate terminal
  ↓
type("vim src/foo.c")
  ↓
real shell process
  ↓
real editor
  ↓
real workspace filesystem
```

Or it can operate a graphical editor through its surface.

It shouldn't have a magic system call:

```text
edit_file("/project/foo.c", ...)
```

that bypasses the execution environment.

Likewise:

```text
git commit
```

should normally mean the agent caused a real Git process to execute within its workspace, not that `worldd` secretly edited repository state on its behalf.

That keeps the spatial environment meaningful.

---

There can still be higher-level convenience commands later, but they should **compile to the same underlying actor operations**, not become an alternate implementation path.

For example:

```text
agent:
    open_terminal(workspace=keylab)
```

could internally mean:

```text
locate authorized workspace representation
→ activate it
→ request terminal application instance
→ workspace launcher starts terminal
→ surface appears
→ focus surface
```

The higher-level request is just orchestration.

It doesn't bypass authority or invent another kind of state.

---

## Structured perception is important

I would definitely *not* require agents to operate solely from rendered frames.

Give them things like:

```text
observe_room()
observe_visible_objects()
inspect_object(id)
inspect_surface_metadata(id)
get_actor_pose()
```

subject to authority.

Rendered images should also be available when they actually need visual information:

```text
observe_surface_pixels(surface)
observe_actor_view()
```

So perception can be:

```text
semantic structure
+
actual pixels when necessary
```

That will make the low-end Luna driver dramatically more reliable without compromising the embodied model.

---

## The same abstraction should eventually take human input too

Conceptually:

```text
SpaceMouse ─┐
mouse ──────┤
keyboard ───┤
            ▼
         ActorInput
            ▲
            │
Agent API ──┘
```

Not literally the exact same network protocol, but the same domain operations.

For example:

```text
SpaceMouse movement
        ↓
actor.move_velocity(...)
```

and:

```text
agent move command
        ↓
actor.move_velocity(...)
```

The world shouldn't care whether the actor's movement originated from USB hardware or an AI command.

Likewise:

```text
human mouse click
        ↓
raycast
        ↓
surface input
```

and:

```text
agent click(surface, point)
        ↓
same validation/raycast/interaction machinery
        ↓
surface input
```

One underlying path.

---

### What I would put in the notes

Select **4. None of the above**, then:

> **Agents receive structured, authority-filtered perception and control an embodied actor through structured movement and interaction commands. These commands operate the same rooms, objects, application surfaces, input routing, and workspace execution contexts used by humans. Agents do not receive a separate direct file-manipulation universe. Files, Git, builds, and other work occur through real workspace processes and applications. Higher-level convenience commands may orchestrate these same primitives but must not create parallel state or bypass authority. Pixel observations remain available when visual inspection is required.**

For your first usable milestone, I’d go even narrower than the wording suggests:
Use spatial geometry and interaction, not general physical simulation.

You need enough “physics” to make the world coherent:
actor pose
room geometry
collision / no walking through walls
ray casting
surface picking
object bounds
grab / move / release
doors/cabinets opening
simple spatial occupancy

But not:
arms
hands
joints
inverse kinematics
rigid-body stacks
gravity-driven objects
friction
mass
ragdolls
learned locomotion
robotics simulation

The actor abstraction should initially be:
Actor
├── pose
├── velocity
├── view
├── reach / interaction range
├── selected object
└── input seat

An agent can do:
move(...)
look_at(...)
approach(...)
select(...)
activate(...)
grab(...)
move_held_object(...)
release(...)

without needing a simulated shoulder, elbow, wrist, and hand to justify the action.
That is important because otherwise the first desktop milestone becomes partly a robotics project.
Cabinets especially should not require hands
Opening a workspace cabinet should be something like:
actor within interaction range
        +
line of sight / valid target
        +
authority check
        ↓
activate cabinet

Then its contents/presentation change.
Not:
calculate shoulder IK
→ move elbow
→ orient hand
→ detect handle contact
→ apply torque
→ hinge simulation

That adds enormous complexity without making the operating system more correct.
You can still visually animate a door swinging open. The distinction is that the animation represents the state transition rather than determining whether the operation succeeds.
Keep the state semantic
For example:
Cabinet {
    state = closed | open
}

and:
activate(cabinet)
    ↓
authority + reach checks
    ↓
state = open
    ↓
render hinge animation

Rather than storing cabinet truth as:
hinge angle = 87.391°
angular velocity = ...

and trying to infer whether it is “open.”
Same philosophy for doors, drawers, buttons, window panels, etc.
The semantic state should be authoritative; physical animation can visualize it.
Later, articulated actors can plug into the same action layer:
grab(object)

today:
reach check
→ attach object to actor interaction frame

later:
IK planner
→ hand trajectory
→ contact
→ grasp

The world-level action doesn't change.
That gives you a clean upgrade path instead of forcing robotics assumptions into the OS foundation.
So select 1.
For notes, I’d use:
The first milestone uses spatial navigation, collision, ray-based selection, interaction range, and semantic object controls. Actors do not require articulated bodies or IK. Cabinet, door, grab, and similar operations are authoritative world-state transitions with optional visual animation, not outcomes inferred from rigid-body simulation. Later articulated or learning-based actors may implement the same actor actions without changing world semantics.

Choose **1. Shared semantic action**.

That is the clean interpretation of “same mechanisms”:

> **Humans and agents do not need the same gestures. They must invoke the same authoritative operation.**

For a rename, both paths converge here:

```text
Human
  ↓
cabinet UI → Rename
                \
                 → workspace.rename(file, new_name)
                /
Agent
  ↓
structured action → Rename
```

Then exactly one implementation performs:

```text
validate actor
validate workspace authority
validate file belongs to granted filesystem root
validate destination
perform real POSIX rename
publish resulting world/filesystem change
```

So this is **not**:

```text
human → real filesystem
agent → special agent filesystem API
```

and it is also not:

```text
agent must literally:
open cabinet
point at icon
click
press F2
type name
press Enter
```

That second version confuses **human motor interaction** with **operating-system semantics**.

### The cabinet is effectively part of the spatial shell

This slightly refines what we discussed earlier about agents not getting a magical file API.

A workspace cabinet is itself a first-class OS file-management interface. Therefore it is legitimate for it to expose semantic operations such as:

```text
open
rename
move
copy
delete
create_directory
```

provided those operations are **the same operations available through its human UI**.

The agent isn't bypassing the world. It is invoking the cabinet's action model directly.

For example:

```text
Cabinet
  │
  └── FileObject
         id
         workspace_id
         relative_path
```

Then:

```text
rename(
    actor = luna,
    object = file:127,
    new_name = "foo2.c"
)
```

ultimately becomes something like a real:

```text
renameat2(...)
```

against the workspace's kernel-constrained filesystem authority.

The OS doesn't maintain a simulated filename and later synchronize it.

The real filesystem change is the state change.

---

## “Same mechanism” should mean three things

For every semantic operation:

```text
same command semantics
same authority check
same side-effect implementation
```

It does **not** require:

```text
same UI
same number of actions
same physical motions
```

Thus:

```text
Human:
select file
→ context menu
→ Rename
→ type name
→ confirm
```

and:

```text
Agent:
rename(file_id, "new.c")
```

are equivalent because both converge on:

```text
FileRenameAction
```

before anything consequential happens.

That pattern should generalize nicely:

```text
Human cabinet UI       Agent action
       │                    │
       └────────┬───────────┘
                ↓
        semantic operation
                ↓
         authority layer
                ↓
       real Linux mechanism
```

For files:

```text
rename → renameat2
delete → unlinkat
directory → mkdirat
```

For processes:

```text
launch application
        ↓
workspace launcher
        ↓
real process
```

For spatial objects:

```text
move object
     ↓
world transaction
```

So **option 1 is exactly the architecture I'd want**.

If adding notes, I'd use:

> **“Same mechanisms” means humans and agents converge on the same semantic action, authorization checks, and real side-effect implementation, not that they reproduce the same physical/UI gesture sequence. Cabinet file actions operate directly on the real workspace filesystem through the workspace authority boundary. Agents receive no alternate file state or privileged bypass.**

Keyboard, mouse, and physical SpaceMouse are required for the first usable milestone. Keyboard and mouse must support ordinary real application interaction. The SpaceMouse must provide real six-axis actor navigation with correct signed axes, deadzones, simultaneous motion, and cancellation. Keyboard/mouse fallback navigation may exist for debugging, but does not substitute for passing physical SpaceMouse acceptance.

Yes — but I would not make the agent pretend to be a SpaceMouse device.
You want an event-driven actor input layer underneath both the physical SpaceMouse and the agent.
The clean model is:
Physical SpaceMouse ──┐
Keyboard / mouse ─────┤
                      ▼
                 Actor Input
                      ▲
Agent commands ───────┘

So the physical SpaceMouse has a device adapter that converts USB/device events into normalized actor-control events:
motion {
    actor_id
    tx, ty, tz
    rx, ry, rz
    timestamp
    sequence
}

The agent can emit the exact same kind of actor intent:
set_motion(
    tx=0,
    ty=0,
    tz=-0.6,
    yaw=0.2
)

Both reach the same movement implementation.
But continuous motion should be stateful
I would not model 6DoF movement as thousands of independent “move 1 mm” commands.
Instead:
event:
    actor.motion = {
        tx: ...
        ty: ...
        tz: ...
        rx: ...
        ry: ...
        rz: ...
    }

The world/render loop integrates that state until another event changes it:
SpaceMouse pushed forward
        ↓
motion.z = -0.7

world ticks
        ↓
actor keeps moving

SpaceMouse released
        ↓
motion.z = 0

For an agent:
set_motion(z=-0.7)
wait / observe
set_motion(z=0)

or a bounded version:
move_velocity(
    linear=[0,0,-0.7],
    angular=[0,0.2,0],
    duration=0.8
)

which internally still uses the same actor-motion state.
That also makes cancellation easy:
cancel_actor_input(actor)

must immediately produce:
translation = 0
rotation = 0
buttons = released
keys = released

Your test suite should absolutely verify that.
Discrete things are events
For example:
button_down
button_up

key_down
key_up

click
scroll

grab
release
activate

Those are naturally event-based.
So I'd split actor input into:
continuous state
    movement axes

discrete events
    buttons / keys / actions

rather than forcing everything into one model.
For X11 applications, you also need virtual input devices
This is a separate layer.
When an agent is interacting with a real application window, it eventually has to generate things that the X client really receives:
pointer movement
button press/release
keyboard press/release
scroll

For that, I'd have each agent own a virtual Linux input seat/device set:
Agent
  ↓
Actor Input
  ↓
surface interaction
  ↓
virtual pointer + keyboard
  ↓
XI2 master pair
  ↓
XLibre
  ↓
real application

Likely using uinput or your own XLibre-side equivalent if you modify the server.
That gives the agent a genuine input identity rather than funneling every agent through the same global pointer.
So:
David:
    physical mouse/keyboard
    master pointer 1

Luna:
    virtual mouse/keyboard
    master pointer 2

Codex:
    virtual mouse/keyboard
    master pointer 3

This will matter a lot once agents operate simultaneously.
Do not make two movement implementations
Avoid this:
SpaceMouse
    ↓
camera.cpp

Agent
    ↓
agent_navigation.cpp

They will diverge.
Instead:
SpaceMouse adapter ─┐
                    │
Agent adapter ──────┼──> ActorControl
Keyboard adapter ───┘
                         │
                         ▼
                   movement engine

The adapters answer only:
What input did this actor request?

The movement engine answers:
What happens to the actor in the world?

That's the abstraction you want.
I would define an actor input frame
Something roughly like:
ActorInputState {
    actor_id

    linear_velocity_intent:
        x
        y
        z

    angular_velocity_intent:
        pitch
        yaw
        roll

    sequence
    timestamp
    expires_at
}

The expiration field is useful for agents.
If Luna dies while commanding:
forward = 1

you do not want Luna's body to fly forever.
So every remote/agent motion command gets a short lease:
motion valid for 250 ms

unless refreshed.
Physical input can similarly zero naturally on device release/disconnect.
That gives you a powerful safety invariant:
No actor continues moving because its controller disappeared.

The agent API then stays small
Something like:
observe()
set_motion(...)
stop_motion()

look_at(...)
activate(...)
grab(...)
release(...)

pointer_move(...)
pointer_button(...)
key_event(...)
scroll(...)

Higher-level helpers can exist:
approach(object)
focus(surface)
click(surface, uv)

but they should compile down into the same primitives.
So yes: you need an event-driven control path, but I would call it an actor input/control layer, not an “agent driver.”
The SpaceMouse driver, human keyboard/mouse adapter, and agent controller all feed it.
And for actual X applications, agents additionally get real virtual keyboard/pointer devices attached to independent XI2 seats.
That gives you one coherent system:
              ACTOR INTENT
                   │
      ┌────────────┼────────────┐
      │            │            │
SpaceMouse      Human I/O      Agent
      │            │            │
      └────────────┼────────────┘
                   ▼
             ActorControl
                   │
        ┌──────────┴──────────┐
        ▼                     ▼
  world movement        application input
                              │
                           XI2 seat
                              │
                           X client

That is the point where an agent can actually inhabit the desktop, rather than merely automate it.

The deterministic actor programs should absolutely exist, but they are test fixtures, not sufficient evidence that the first usable system supports agents.

So M1 acceptance should include both, but the thing that earns the phrase working agent participation is a real model closing the loop:
local LLM
   ↓
observe authorized world state
   ↓
reason
   ↓
actor command
   ↓
move / select / interact
   ↓
real application or cabinet operation
   ↓
observe result
   ↓
continue

For example, give it a bounded task:
Enter the project room, locate your workspace cabinet, open a terminal, create hello.txt, rename it to agent.txt, inspect it, and report completion.

The important part is that the model must use the same actor/world mechanisms we defined:
observe
move / approach
activate
semantic cabinet action
surface interaction
type / click

and its workspace authority actually constrains what it can do.
It should not receive SSH access or an unrestricted shell outside its workspace and then merely report that it succeeded.
Deterministic actors remain mandatory
I'd still have acceptance tests like:
scripted actor A moves through portal
scripted actor B manipulates another workspace
concurrent actors remain isolated
revocation takes effect
motion lease expires
input cancellation releases held state

Those are much better regression tests than asking an LLM to behave identically every run.
So the layering is:
automated acceptance
        ↓
deterministic actor programs

usable-agent acceptance
        ↓
real local LLM

later
        ↓
external/remote agents
multiple heterogeneous agents

I would not require external agent connectivity yet. That's deployment plumbing, not a foundational primitive. A model process on the same machine or LAN using the authorized actor interface is enough.
Given that you already intend to have a fast stronger local model available, there isn't much reason to declare the milestone complete without exercising one real agent. 
Select 2
And add this note if the questionnaire permits it:
A real local model must complete at least one bounded task through the authorized actor interface and workspace mechanisms. Deterministic actor programs remain required as reproducible acceptance fixtures but do not alone count as working agent participation. External agent connectivity is not required for the first usable milestone.
scripted agents to prove pereption shared controls, isolation, collaboration, and recovery without requiering a model are the first test before live model testing. you can use ssh llm-70 or llm-49 or llm-60 whichever is not busy for server iq3 or q8 flash 

Choose 1. Confine workspace applications, but I would tighten the rule in the notes.
The important invariant is:
An application instance has its own effective authority, derived when it is launched. Controlling its surfaces must never let an actor exercise more authority than that actor possesses.

So if a workspace permits read/write but Luna only has read access, Luna should not be allowed to take over David's writable terminal. A terminal is effectively an authority amplifier if you ignore who may control it.
I’d model launch authority as:
effective app authority
    =
workspace maximum authority
∩ launching actor authority
∩ requested application profile

Example:
Workspace: keylab
    allows: read, write, execute

David:
    read, write, execute

Luna:
    read

David launches:
terminal-A
authority = read + write + execute

Luna may perhaps see that surface, but cannot control it.
If Luna launches its own editor:
editor-B
authority = read

then the same underlying files can be presented, but kernel enforcement prevents that editor from modifying them.
That is much cleaner than trying to inspect every mouse click or editor command to determine whether it is “read-only.”
The application process itself must be confined
The real enforcement happens below the GUI:
Actor
   ↓
workspace launcher
   ↓
effective authority calculated
   ↓
Linux process created
   ↓
mount/filesystem restrictions
Landlock / namespace policy
cgroup
no_new_privs
device/network policy
   ↓
real editor / terminal / compiler

So even if the application misbehaves:
editor tries:
open("/home/david/.ssh/id_ed25519")

the kernel says no.
The editor does not have to cooperate with Spatial OS.
That is crucial.
Then X-side authority is separate
Because you're using X11/XLibre, process confinement alone is insufficient if an application can use the X server to snoop or interfere with unrelated clients.
So I'd have the first protected version tag every X client/application instance with:
workspace_id
principal_id
authority_set

and enforce that clients cannot arbitrarily inspect or manipulate clients outside that permitted domain.
Similarly, spatialwm/worldd should independently enforce:
observe surface
control surface
move surface
close application
inspect metadata

as separate permissions.
This gives you three enforcement layers:
             ACTOR AUTHORITY
                    │
        ┌───────────┴───────────┐
        ▼                       ▼
 world/surface policy      app launch policy
        │                       │
        ▼                       ▼
 who can control it        process authority
                                │
                                ▼
                         Linux kernel boundary

And XLibre prevents the application itself from escaping sideways through X.
Avoid option 3
“Managed file applications only” would require specially modified applications or a virtual file service.
Then ordinary:
vim
gcc
git
python
bash

would either stop working or need compatibility adapters.
That's solving the wrong problem.
Let applications use ordinary POSIX APIs. Make the kernel restrict what those APIs can reach.
I would put this in the notes
Select 1. Every application instance runs with an explicit authority set derived from the intersection of workspace policy, launching actor authority, and the requested application profile. Linux confinement enforces filesystem, process, device, and other resource access without requiring application cooperation. Surface observation and control are separately authorized; controlling an application must never amplify an actor’s authority. XLibre also isolates clients so shared X-server access cannot bypass workspace/actor boundaries.

That last rule — surface control must never amplify authority — is probably one of the most important security invariants in the whole design.

The clean deployment model is:
sb16
└── spatial environment dev/test host

sb16-2
└── spatial environment dev/test host

llm-49
llm-79
llm-60
└── external inference servers

The important rule is:
World hosts run worlds. Inference hosts run models. Do not mix those responsibilities.

For the first usable milestone, I would not require sb16 and sb16-2 to participate in the same live world yet. That would introduce distributed-world synchronization before it is necessary.
Instead:
sb16
    run current spatial build
    physical interaction testing
    XLibre/spatialwm/worldd
    applications/workspaces

sb16-2
    independent second deployment
    clean-install validation
    regression testing
    destructive experiments
    alternate build / recovery verification

Either host should be capable of running the whole spatial environment by itself.
Then an agent in a world can call an external model:
Actor on sb16
    ↓
agent controller
    ↓
authorized inference request
    ↓
llm-60 / llm-79 / llm-49
    ↓
model output
    ↓
agent controller on sb16
    ↓
validated actor action

The inference server does not become part of the world authority model. It doesn't own:
rooms
workspaces
files
X clients
actor state
permissions

It just computes a response.
That also means the world should be completely functional with:
no model server connected

Human use, scripted actor tests, workspace enforcement, applications, rooms, and persistence should all still work.
Later
Once the single-host environment is solid, two shared-world clients can become a deliberate later milestone:
sb16 client ──┐
              ├── shared world service
sb16-2 client ┘

But that's a real distributed-system feature and deserves its own tests for:
- concurrent actors
- network loss
- object ownership
- state ordering
- reconnect
- stale actions
- surface locality
It should not sneak into M1 merely because two test machines exist.
So for the notes I'd put:
sb16 and sb16-2 are independent development and validation targets for the spatial environment. Either must be able to run the complete first usable world locally. They are not inference hosts and are not required to share one live world in M1. Model inference is provided by separate authorized services on llm-49, llm-79, or llm-60. Multi-host shared-world operation is a later distributed-runtime milestone.

sb16
└── complete Spatial OS dev/test deployment

sb16-2
└── complete Spatial OS dev/test deployment

llm-49 / llm-79 / llm-60
└── external inference services


Pixels and world metadata
For the first usable version, I think this is cleaner than option 1.
The agent gets structured knowledge about the Spatial OS:
surface id
application instance
workspace
authority
3D transform
pixel dimensions
visibility/occlusion
focus/control status

plus the actual framebuffer:
surface pixels

So:
observe_surface()
→ {
    surface: editor-7,
    workspace: keylab-agent,
    size: 1280x720,
    ...
    pixels: ...
  }

The model interprets the actual application UI.
I would not make AT-SPI/accessibility semantics part of the M1 contract. That's another subsystem, and support quality varies by application. More importantly, the fundamental spatial desktop works perfectly without it.
Later:
pixels
+
AT-SPI semantics when available

is an excellent optimization for agents.
But M1 shouldn't fail because some application's accessibility tree is incomplete.
Notes:
Expose authoritative world/surface metadata and real rendered pixels. Application semantic/accessibility data may be added later as an optional observation channel, but is not required for an application to be usable by an agent. Never synthesize semantic UI state and present it as application-provided truth.

That keeps the primitive very small.
3/3 — Shared application control: 1. One controller at a time
Strong yes.
This is one of the places where serialization is the genuine domain distinction rather than an architectural workaround.
A normal application has state like:
keyboard focus
mouse drag
button-down state
modifier keys
selection
menus
IME state

Two actors simultaneously injecting events into that one application would produce nonsense:
David: types "hello"
Luna:  types "git"

result:
hgeil tlo

or worse:
David holds Ctrl
Luna presses W
→ application closes

So an application instance/surface group gets an explicit interaction lease:
ApplicationInstance
    controller = Luna
    lease = active

Everyone else can still:
observe pixels
observe metadata
watch the actor work
request control

but only the lease holder injects input.
The lease should encompass the application instance/surface group, not merely one X surface. Otherwise Luna could own the editor window while David accidentally interacts with its popup/dialog.
So:
VS Code application instance
├── main surface
├── popup
├── dialog
└── context menu

       one controller

And control transfer must cancel outstanding state:
release mouse buttons
release keys/modifiers
end drag
clear old pointer ownership
transfer lease

This should be heavily tested.
Notes:
One actor holds an explicit interaction lease for an application instance/surface group at a time. Other authorized actors may observe. Lease transfer releases all held keys, buttons, modifiers, drags, and focus state before the new controller can act. Separate application instances may be controlled concurrently by different actors.

So the answers I'd send Luna are:
Q1: 4 — None of the above
Q2: 2 — Pixels and world metadata
Q3: 1 — One controller at a time

That leaves M1 with a particularly clean agent model: structured world perception + real application pixels + serialized control of ordinary application instances.

(later an additional view a semantic view might be added)

Yes — then the right model is not “shared worlds or separate worlds.” It is multiple persistent worlds, some of which are shared across hosts and some of which are private to one host or actor.
The key abstraction should be:
A world is a persistent authority/state domain. A host is merely a machine capable of running or presenting one or more worlds.

So:
sb16
├── Home-David
├── Dev-Lab
└── Public-Park ─────────┐
                         │
sb16-2                   │
├── Home-Test            │
├── Private-Business     │
└── Public-Park ─────────┘

Public-Park is one shared persistent world. Both machines connect to the same authoritative state.
Home-David and Home-Test are independent persistent worlds.
That maps very naturally to your physical analogy.
Think of worlds like places
Private home
World: david-home
visibility: private
membership: David + explicitly invited actors

Contains:
rooms
workspace cabinets
personal application surfaces
private world objects
agents

It can exist only on sb16 if that's where you use it.
Private business
World: keylab-company
visibility: members
membership:
    David
    Luna
    Codex
    selected agents

Both sb16 and sb16-2 could enter it.
The world remains the same world regardless of which machine renders it.
Public park
World: commons
visibility: broadly visible

Actors from either host can enter and share:
same rooms
same object placements
same actors
same surface representations

with permissions controlling what they can actually access.
A host should not own a world semantically
Avoid:
world.host = sb16

as the identity.
Instead:
World {
    world_id
    persistence_authority
    policy
}

A runtime host has something more like:
WorldAttachment {
    host_id
    world_id
    role
}

Possible roles later:
authority
interactive_client
worker
replica
observer

For M1, though, keep this much simpler.
First implementation
I would support:
one authoritative worldd per world

That world can be:
Local
sb16
  worldd(home)
  spatialwm

or shared:
sb16
  worldd(public-park)
       ▲
       │ network
       ▼
sb16-2
  spatial client

Both hosts inhabit the same public-park, but only one worldd is authoritative.
That avoids immediately requiring distributed consensus.
Later:
worldd replica
leader election
failover
multi-node persistence

can be added if you actually need high availability.
Do not make M1 a Raft project.
Actors can move between worlds
This gives you a useful higher-level structure:
Actor: David
   │
   ├── david-home
   │
   ├── keylab-business
   │
   └── public-park

At any moment:
actor.current_world
actor.current_room
actor.pose

So a portal could lead not merely between rooms but between worlds:
Home
   │
   🚪
   │
   ▼
Public Park
   │
   🚪
   │
   ▼
KeyLab

Crossing a world boundary is therefore a real authority transition.
You might be:
admin in Home
member in KeyLab
visitor in Park

The actor identity persists, but authority changes according to the destination world.
Workspaces still remain independent
Do not couple workspace ownership to a world either.
A workspace may be represented in one or more worlds subject to policy.
For example:
Workspace: keylab-main

could have:
full cabinet in KeyLab world
status-only object in Public Park
no representation in Home

Or your private home could contain a portal/reference to that workspace without copying it.
So we retain the earlier invariant:
World = shared persistent reality.
Room = spatial subdivision of a world.
Workspace = execution/authority context.
Host = machine executing/presenting worlds.

Those four concepts stay separate.
Inference should be completely outside this topology
And yes, given your clarification:
llm-49
llm-60
llm-79

are simply inference providers.
The world runtime should have something like:
InferenceProvider
    generate(...)
    health(...)
    capabilities(...)

with adapters for:
OpenAI-compatible
llama.cpp
Strata

without the world model knowing which is underneath.
Conceptually:
Agent
  ↓
agent runtime
  ↓
InferenceProvider interface
  ├── OpenAI
  ├── llama.cpp
  └── Strata

The world doesn't care whether inference comes from:
llm-49
llm-60
llm-79

or somewhere else later.
It should only know:
provider_id
capabilities
availability

and the agent controller handles the request.
If none are running, the environment should still work normally; model-driven agents just become unavailable.
And if development reaches the point where an inference service is needed but isn't running, having Luna stop and ask you to start one is preferable to baking assumptions about which server must always be online.
I'd encode the deployment this way
Hosts
├── sb16
│   ├── private worlds
│   └── may attach to shared worlds
│
└── sb16-2
    ├── private worlds
    └── may attach to shared worlds


Inference
├── llm-49
├── llm-60
└── llm-79


World types
├── private
├── restricted/shared
└── public

But private/public/business/home should be policy presets, not different world implementations.
Internally they're all:
World

with different:
membership
visibility
join policy
authority grants

That avoids introducing PrivateWorld, PublicWorld, BusinessWorld, etc.
For the questionnaire
I would answer:
Both. Hosts may maintain independent persistent worlds and may also attach to the same shared persistent world. A world is a persistent authority/state domain independent of the machine presenting it. In M1, each shared world has one authoritative worldd; multiple hosts may connect as interactive clients. Private and public/business/home behavior are policy differences, not separate world types. Inference is external and provider-agnostic through OpenAI-compatible, llama.cpp, or Strata adapters using llm-49, llm-60, or llm-79.

checking out the same branch twice should create two different workspaces.
The branch is part of the project state. The workspace is the execution/authority instance around that project state.
So:
Repository: KeyLab
Branch: main

                ┌────────────────────┐
                │ Workspace A        │
                │ host: sb16         │
                │ worktree: /a       │
                │ processes: ...     │
                │ app state: ...     │
                └────────────────────┘

                ┌────────────────────┐
                │ Workspace B        │
                │ host: sb16-2       │
                │ worktree: /b       │
                │ processes: ...     │
                │ app state: ...     │
                └────────────────────┘

Both can start at the exact same commit and both can say branch = main, but they are not the same workspace.
Immediately they can diverge:
A:
    foo.c modified
    compiler running
    VS Code open

B:
    clean tree
    pytest running
    vim open

Even before Git commits differ, the workspace state differs.
Therefore workspace identity should never be derived from
repository URL
branch name
commit hash
filesystem path
machine name

It gets its own durable identity:
workspace_id = UUID

Something like:
Workspace {
    id
    project_id
    worktree
    branch/ref
    filesystem_authority
    execution_context
    application_state
    policy
}

Same branch is a relationship, not identity
You might have:
Workspace A ─┐
Workspace B ─┼── Project = KeyLab
Workspace C ─┘

A.branch = main
B.branch = main
C.branch = agent/foo

A and B are related because they currently reference the same branch, but they're separate workspace instances.
This is similar to two terminals SSH'd into two machines:
same repository + same branch does not make them the same running environment.

Then when is it the same workspace in several worlds?
Only when there is still one underlying workspace instance, and multiple worlds merely contain presentations of it.
For example:
                  Workspace W
                authoritative on sb16
                      │
            ┌─────────┴─────────┐
            │                   │
     Home world             Business world
     cabinet for W          cabinet for W

Those two cabinets point to the same:
workspace_id
filesystem
process tree
application instances
authority

That's genuinely one workspace shown in two places.
If someone edits a file through the Home representation, the Business representation sees the same workspace change because there was only ever one execution/filesystem context.
But cloning it onto another host changes the answer
Suppose:
sb16
    workspace W

gets copied to sb16-2 and started independently.
That should become:
sb16:
    workspace W

sb16-2:
    workspace X
    derived_from = W

Even if every byte initially matches.
That's a fork/snapshot/clone, not a second presentation.
This distinction prevents a nasty distributed-state problem where the system pretends two independently mutable execution environments are one object.
I'd define an invariant
A workspace has exactly one authoritative execution/filesystem state at any instant. Multiple spatial presentations may reference it. Creating another independently mutable execution/filesystem state creates another workspace.

That's the clean line.
It also leaves room for migration:
workspace W
sb16
   ↓ suspend/migrate
sb16-2

could remain the same workspace if there is only one authoritative instance before and after.
But:
workspace W on sb16
+
independent live copy on sb16-2

cannot both be W.
One becomes a new workspace.
So for the questionnaire
The first option is valid only with that interpretation.
Yes, the same workspace can be presented in several worlds, but only when those presentations refer to one authoritative workspace instance. Checking out the same repo/branch again, copying its directory, or independently running it on another host creates a distinct workspace, even if its project state initially matches exactly.

So I'd choose 1, but definitely add that note.
The distinction becomes:
same workspace
    → multiple views of one state

same branch
    → multiple workspaces may happen to share project lineage/state
A workspace existing in or being authorized for a world does not automatically create spatial geometry. A cabinet is a presentation object and must be deliberately placed.

That preserves the distinction we have been building:
Workspace
    = execution + authority + state

World
    = persistent shared reality

Cabinet
    = one spatial presentation of a workspace

So I would avoid option 1's implication:
workspace introduced to world
        ↓
automatically create cabinet

because later a workspace may be represented by:
cabinet
status board
terminal surface
portal
Git object
nothing at all

and it may have several representations in the same world.
Instead:
place_workspace_cabinet(
    workspace = W,
    world = X,
    room = entry_room,
    transform = default_free_position
)

creates the cabinet explicitly.
For convenience, the UI can offer:
“Present this workspace here”

and that command can create a cabinet, but that is still an explicit presentation operation rather than an automatic consequence of workspace membership.
Why the entry room?
For the first placement, using the entry room as the default destination is fine:
authorized actor:
    "place cabinet for KeyLab"

no location specified
        ↓
world.entry_room
        ↓
find valid free placement
        ↓
create cabinet

After that, moving it persists its actual placement.
But an actor should also be able to say:
place cabinet in Coding room

without first creating it in the entry room and moving it.
So the entry room is a default argument, not a lifecycle rule.
This also handles multiple worlds correctly
Same workspace:
Workspace W

can have:
World: Home
    cabinet W at Home/Office

World: Business
    cabinet W at Lobby/Dev

World: Park
    no presentation

Nothing gets generated merely because W is accessible from those worlds.
Notes I'd enter
Cabinets are explicit spatial presentations, not automatic properties of workspaces. An authorized actor places a cabinet for a workspace in a chosen world/room. If no room is supplied, the world's entry room is the placement default. Subsequent placement is persistent. Workspace availability or access alone does not create geometry.


make workspace selection world-local but remembered.
Keeping one global active_workspace across world transitions is a little too dangerous. If you walk from your private home into a public park, then say “open terminal,” you do not want that silently launching against the private workspace you happened to be using before.
The cleaner model is:
Actor
├── current_world
├── current_room
└── world_contexts
     ├── Home
     │    └── active_workspace = keylab-main
     ├── Business
     │    └── active_workspace = keylab-codex
     └── Public Park
          └── active_workspace = none

So crossing worlds works like:
leave Home
    ↓
remember Home.active_workspace

enter Public Park
    ↓
restore Park's last valid workspace selection
    ↓
if none exists:
    active_workspace = none

When you return home:
Home.active_workspace = keylab-main

is restored.
That gives you continuity without accidental cross-world authority.
A workspace can still be the same durable workspace in several worlds if it has been explicitly made available there. For example:
Workspace W

Home:
    available
    cabinet in Office

Business:
    available
    cabinet in Dev Room

Park:
    unavailable

If both Home and Business select W, they refer to the same workspace instance. But the selection is separately remembered in each world.
I would also separate workspace availability in a world from cabinet placement:
WorldWorkspaceBinding
{
    world_id
    workspace_id
    policy
}

means the workspace is usable from that world.
Then separately:
CabinetPlacement
{
    world_id
    room_id
    workspace_id
    transform
}

means it has a spatial representation there.
This preserves our earlier rule that simply making a workspace available does not automatically create geometry.
So the transition invariant becomes:
On world entry, restore that actor's last selected workspace for the destination world only if it is still authorized and available there. Otherwise select no workspace. Never implicitly carry a workspace across a world boundary.

I’d put this in the notes:
Workspace selection is remembered per actor per world. Leaving a world preserves its selection; entering another restores that world's previous valid selection, or none if no valid selection exists. A workspace must be explicitly available in the destination world before it can be selected there. Cabinet placement is independent of workspace availability.

Yes. I would do multi-world from the first usable delivery and use sb16 + sb16-2 specifically to prove that the abstraction is real.
So for that question, choose 2. Multi-world from first delivery, but I would narrow what “multi-world” must prove so Luna does not accidentally turn M1 into a distributed-database project.
The reason is architectural: world_id is going to affect rooms, placements, actor location, permissions, workspace presentation, events, persistence, portals, and restoration. If we implement everything assuming one singleton world and retrofit that later, it will touch nearly every subsystem.
The M1 topology I would require
Something like:
sb16
├── Private world: Home-A
└── Shared world: Park  ← authoritative worldd here
                         ↑
                         │
                         │ shared world protocol
                         │
sb16-2                   │
├── Private world: Home-B
└── Shared world: Park ──┘

This proves both things we care about:
separate persistent worlds
+
one genuinely shared persistent world

without requiring replication or consensus.
For the first version, Park has exactly one authoritative worldd. Say it runs on sb16:
sb16:
    worldd(Park) = authority

sb16-2:
    connected interactive client

If sb16 dies, Park can simply be unavailable. That's acceptable for M1.
Do not add:
Raft
leader election
multi-master world DB
automatic failover
distributed SQLite

None of that is necessary yet.
What should be demonstrated
On sb16:
David enters Home-A
    ↓
works with real confined workspace
    ↓
real editor + terminal surfaces
    ↓
walks through portal
    ↓
enters Park

On sb16-2 simultaneously:
another actor enters Home-B
    ↓
walks into Park

Now both actors must see:
same Park rooms
same shared objects
same object placements
each other's actor positions
same changes to shared geometry/state

For example:
sb16 actor moves bench

             ↓ network/worldd

sb16-2 immediately sees bench moved

That is enough to prove a shared world.
Then each actor goes home:
David:
    Park → Home-A

Agent:
    Park → Home-B

and each private world retains independent state.
Workspaces need not become distributed yet
This is an important boundary.
On sb16:
Workspace A
repo = KeyLab
branch = main
host = sb16

On sb16-2:
Workspace B
repo = KeyLab
branch = main
host = sb16-2

Those are different workspaces, even if initialized from the same Git state.
M1 does not need to magically make a running workspace span two machines.
Likewise, I would not require remote live X11 application surfaces across hosts in the first shared-world proof.
In other words, Park can prove:
actors
rooms
portals
geometry
shared objects
world persistence
authority
cabinet representations

while real application execution stays local to its workspace host.
Later we can add:
remote application surface streaming
remote input routing
workspace migration
same workspace presented interactively from another host

as explicit features.
That avoids sneaking an entire remote-desktop system into M1.
This also gives us a very good acceptance test
Cold start both machines.
Create:
World A = private on sb16
World B = private on sb16-2
World P = shared

Then:
1. Actor A starts in World A.
2. Actor B starts in World B.

3. A changes something in A.
4. Verify B cannot observe it.

5. B changes something in B.
6. Verify A cannot observe it.

7. Both enter P.

8. A moves shared object X.
9. B observes exactly that change.

10. B moves shared object Y.
11. A observes exactly that change.

12. Both leave P.

13. Restart both world hosts.

14. Private A state remains.
15. Private B state remains.
16. Shared P state remains.

17. Each actor restores its per-world location/context.

That's an excellent primitive test because it proves that world_id isn't merely decorative.
Inference remains completely orthogonal
Neither world host becomes an inference server.
sb16 / sb16-2
        ↓
agent controller
        ↓
InferenceProvider
        ↓
llm-49 / llm-60 / llm-79

OpenAI-compatible, llama.cpp, and Strata adapters can all sit behind that provider boundary.
If no inference server is running, deterministic actors and humans still work. When an actual model-agent acceptance test is reached, Luna can check the configured endpoints and ask you to start one if none is available.
So I would select 2, with this note:
Multi-world is required from the first usable delivery. sb16 and sb16-2 must each support independent persistent worlds, and both must be able to inhabit at least one shared persistent world simultaneously. A shared world initially has one authoritative worldd; high availability, replicated world authority, remote application-surface streaming, and cross-host workspace execution are not M1 requirements. Workspaces remain distinct execution instances unless explicitly migrated or remotely presented. Inference remains external on llm-49, llm-60, or llm-79.

Yes — **the software and declarative world definitions should be Git-based**, but I would **not make Git the live database for a running world**.

That gives you easy setup/change without forcing every actor movement or cabinet relocation into commits.

The clean split is:

```text
Git
├── Spatial OS source
├── world definitions
├── room geometry
├── policies
├── default placements
├── assets
├── migrations
└── deployment config

Runtime persistence
└── world.db
    ├── current actor positions
    ├── moved objects
    ├── current room state
    ├── leases
    ├── presentations
    └── other live persistent state
```

So a world could start from a repo like:

```text
worlds/
└── public-park/
    ├── world.toml
    ├── rooms/
    │   ├── entrance.toml
    │   ├── plaza.toml
    │   └── workshop.toml
    ├── policies/
    ├── assets/
    └── migrations/
```

Then creating it is basically:

```bash
spatial world create \
    --from ./worlds/public-park \
    --name park
```

which creates:

```text
declarative definition
        +
fresh world.db
```

### Changing worlds should be cheap

For the actor:

```text
Home
  ↓ portal / command
Park
  ↓
Business
```

That should **not restart XLibre or the desktop**.

`spatialwm` remains running.

It changes which world graph it presents:

```text
Actor
    current_world = park
    current_room = entrance
```

and loads/subscribes to that world's state.

For a remote shared world:

```text
enter park
    ↓
connect to park's authoritative worldd
    ↓
receive authorized state
    ↓
render it
```

For a local private world:

```text
enter home
    ↓
attach local world.db
```

Same interface.

---

## Setting up sb16-2 should also be almost trivial

Ideally:

```bash
git clone <spatial-os>
cd spatial-os
./bootstrap.sh
./configure-host.sh
```

and then something like:

```bash
spatial host init
spatial world create --from worlds/test-home
```

The machine-specific configuration should be small:

```toml
host_id = "sb16-2"

[display]
backend = "xlibre"

[inference]
providers = [
    "llm-49",
    "llm-60",
    "llm-79",
]
```

Everything else comes from the same source tree.

That makes sb16 and sb16-2 excellent validation machines:

```text
sb16
    git checkout main
    build
    run

sb16-2
    git checkout same commit
    bootstrap cleanly
    verify same tests
```

---

# World definitions can branch too

This is where Git becomes especially useful.

You could have:

```text
world repo

main
├── stable public park
│
├── experiment/new-room-layout
│
└── experiment/new-policy-model
```

Then instantiate a test world from a branch:

```bash
git switch experiment/new-room-layout

spatial world create \
    --from . \
    --name park-experiment
```

Now:

```text
Park
Park-Experiment
```

are **separate world instances**, even though their definitions came from related Git branches.

Exactly analogous to our workspace distinction:

> Same Git source does not imply same runtime identity.

---

## Updating a running world

I would not have:

```text
git pull
→ silently mutate live world
```

Instead:

```text
git pull
    ↓
spatial world update park
    ↓
compare definition version
    ↓
run explicit migrations
    ↓
commit world transaction
```

So if the repo adds:

```text
new room
new portal
new default object
```

the migration can add those without destroying:

```text
actor positions
existing moved cabinets
workspace presentations
persistent object state
```

That distinction is critical.

---

# Templates make new worlds easy

You could eventually have:

```text
templates/
├── empty/
├── home/
├── office/
├── public-space/
├── development-lab/
└── classroom/
```

Then:

```bash
spatial world create \
    --template home \
    --name david-home
```

or:

```bash
spatial world create \
    --template public-space \
    --name commons
```

All still produce the same `World` type.

`home`, `business`, and `park` remain policy/config presets rather than separate classes.

---

# Git also fits workspace creation

A project workspace could be just as simple:

```bash
spatial workspace create \
    --repo ~/repos/keylab.git \
    --branch agent/foo
```

Internally:

```text
create workspace UUID
      ↓
create real Git worktree
      ↓
create filesystem authority
      ↓
create execution domain
      ↓
optional cabinet presentation
```

And on another machine:

```bash
spatial workspace create \
    --repo <same repo>
    --branch agent/foo
```

creates another workspace identity.

That stays consistent with what we just established.

---

## So I would make three things Git-friendly

**1. Spatial OS itself**

```text
source + tests + XLibre patches
```

**2. World definitions**

```text
rooms + policies + assets + migrations
```

**3. Project/workspace source**

```text
ordinary repositories + branches + worktrees
```

But keep live runtime state outside Git:

```text
world.db
workspace runtime state
processes
leases
actor poses
```

That gives you the best combination:

> **Cloneable and reproducible environments through Git, but a normal transactional runtime instead of abusing Git as a realtime state database.**

And yes — with that model, bringing up a new world or a clean second host should become very cheap. The acceptance test should eventually include **“fresh Ubuntu 24.04 host → clone repo → bootstrap → create world → log into Spatial”** so setup reproducibility is something Luna has to prove, not just documentation.

The system has one initial identity issuer controlled by David. Humans and agents generate cryptographic keypairs and are explicitly enrolled by public key. 


Yes. I’d keep **1. Explicit enrollment**, but make the first version much less annoying with an issuer-controlled **“Approve always” mode**.

The important point is that “approve always” changes the **enrollment policy**, not the identity model.

```text
new human/agent
    ↓
generate Ed25519 keypair
    ↓
submit public key + requested actor name
    ↓
David's issuer

Enrollment policy:
    manual       → ask David
    approve_once → approve this request
    approve_always → automatically approve future requests
    deny         → reject
```

When **Approve always** is enabled, the issuer automatically signs each new actor identity:

```text
new public key
    ↓
issuer signs credential
    ↓
persistent actor_id created
    ↓
actor may authenticate normally
```

Each actor still gets its **own identity and key**. We are not creating one shared password or disabling authentication.

### I would make the first-version switch literally this simple

```text
Enrollment:
    [ Manual approval ]
    [ Approve always ]
```

And you can switch it back whenever you want.

Internally:

```text
enrollment_policy = "manual"
```

or:

```text
enrollment_policy = "approve_always"
```

No complicated registration system yet.

### Revocation still works normally

Even if Luna was admitted automatically:

```text
Luna
    actor_id = ...
    key = ...
    status = active
```

you can later do:

```text
revoke actor luna
```

or only revoke one credential:

```text
revoke key luna/workstation-1
```

and the next authorization check fails.

Existing sessions should be terminated or lose authority immediately.

---

## I would also let automatic enrollment assign a bootstrap grant profile

Because otherwise “Approve always” only answers:

> “Yes, this identity exists.”

and you would still have to manually configure every new actor.

So have:

```text
auto_enrollment_profile = "default-agent"
```

For example:

```text
default-agent
├── may authenticate
├── may enter public worlds
├── may create/use its own workspace
├── may launch confined processes there
└── may request/delegate only explicitly permitted authority
```

You could instead configure:

```text
default-human
default-agent
trusted-agent
```

later.

But I'd start with one profile.

This gives you the behavior you're after:

```text
start new agent
    ↓
agent generates key
    ↓
Approve always is enabled
    ↓
identity automatically issued
    ↓
default grants applied
    ↓
agent comes online
    ↓
agent can start doing work
```

You don't babysit every startup.

---

## Later controls fit naturally

We don't need to redesign identity later. We just add enrollment policies around the same issuance operation:

```text
                    Enrollment Request
                           │
             ┌─────────────┴─────────────┐
             │                           │
         policy now                  policy later
             │                           │
      approve always              manual approval
                                  invitation token
                                  key allowlist
                                  host allowlist
                                  expiration
                                  per-world admission
                                  requested grant review
                                  device restrictions
```

All of them eventually perform the same action:

```text
issue_actor_identity(public_key)
```

So there aren't separate identity paths.

### One thing I would not do

Don't make:

```text
Approve always
    =
everyone gets administrator rights
```

Keep these distinct:

```text
enrollment
    "You are a recognized actor."

default grants
    "Here is what a newly recognized actor may initially do."

delegation
    "Here is what another authorized actor may subsequently give you."
```

You can make the default grants broad if that's what you want on your development worlds, while retaining the ability to tighten them later.

---

So I would still answer the questionnaire:

**1. Explicit enrollment**

But replace the note with:

> **David is initially the sole identity issuer. Actors generate their own cryptographic keys and request enrollment. The issuer supports an administrator-controlled `approve always` policy that automatically signs future enrollment requests and applies a configurable default grant profile, allowing new humans or agents to come online without repeated manual approval. David may disable automatic approval, revoke individual credentials or entire actor identities, and change default grants at any time. Later policies such as manual approval, invitations, allowlists, expirations, and per-world admission use the same identity issuance mechanism.**

That gives you **frictionless development now without baking “everyone is trusted forever” into the architecture.**

Revocation changes what the actor may do now, not later.

If an actor loses workspace access while controlling an application instance, the system should immediately:
1. revoke the application interaction lease
2. synthesize release for held keys/buttons/modifiers
3. cancel active drag/grab state
4. stop further input routing
5. stop further surface observation if that permission was lost
6. leave the application/process running

The important part is the last one. The application belongs to the workspace, not to whichever actor happened to be controlling it.
So if Luna loses access:
Workspace
└── editor instance
      controller = Luna

becomes:
Workspace
└── editor instance
      controller = none

David or another authorized actor can take over afterward.
I would not choose 2 by default, because killing the application conflates:
actor lost authority

with:
application should cease to exist

Those are separate events.
And I would not choose 3. “Finish the current operation” is too ambiguous for an interactive application. The “current operation” might be:
- a mouse button held down,
- a destructive command half-entered,
- a terminal shell,
- a modal dialog,
- a long-running editor action.
Immediate detachment gives a much cleaner invariant.
One extra detail: if the application process itself was launched with authority derived solely from that actor, losing actor authority may require re-evaluating the process's effective authority. If the process cannot continue validly under workspace policy, then it can be stopped separately. But that is a process-authority decision, not a side effect of losing the input lease.
So I’d select 1, with notes like:
Revocation takes effect immediately. Cancel all held input and interaction state, revoke the actor’s control lease and any observation rights it no longer holds, and stop routing further actions. The application remains running for the workspace and other authorized actors unless its own effective process authority has become invalid, in which case process policy handles termination separately.

Select 1. Define a mandatory Spatial-M1 qualification profile containing every kernel, XLibre, authority, confinement, recovery, and transport invariant required by the first usable spatial environment. These gates must pass with real enforcement before M1 is accepted. Unrelated broader LOCAL_READY kernel targets may remain pending and must not be represented as passed. Shared-world transport and external inference are implemented and tested against the focused profile rather than waiting for full unrelated kernel qualification.

A workspace copy is a new workspace identity. Project data, application definitions, and reproducible environment configuration may be copied, but membership, delegation authority, active leases, credentials, and resource budgets are newly assigned for the destination. Merely presenting the same workspace on another machine or in another world is not a copy and retains the original workspace authority. A true migration may preserve workspace identity and grants provided only one authoritative instance exists at a time.

Choose 1. Extend the native server.
For your system, that is the cleaner foundation because workspace isolation and actor-aware input are part of the OS semantics, not something we should emulate by launching a pile of separate X servers.
The invariant should be:
Every X client and every sensitive X resource belongs to a workspace security domain, and the X server enforces cross-domain access before the request reaches the target.

Conceptually:
X client
├── workspace_id
├── application_instance_id
└── effective_authority

X resources
├── window
├── pixmap
├── input context
├── selection
├── property
└── other server objects

Then XLibre checks things such as:
Can client A enumerate window B?
Can A read B's pixels?
Can A inspect B's properties?
Can A send synthetic events to B?
Can A select/input-route into B?
Can A grab another workspace's input?
Can A manipulate B's windows?

Default answer across workspace boundaries:
NO

unless an explicit authority relationship permits it.
Input should use the same boundary
The actor side should also terminate here:
David ───── physical devices ──┐
Luna ───── virtual seat ───────┤
Codex ──── virtual seat ───────┤
                               ▼
                            XLibre
                               │
                    actor/workspace check
                               │
                               ▼
                       permitted X client

So a compromised application cannot simply bypass worldd and grab another actor's keyboard or another workspace's application.
That matters enormously with X11.
Why I would not use separate X servers
Option 2 initially looks security-clean:
workspace A → X server A
workspace B → X server B

but then the compositor has to bridge:
pixels
damage
resize
input
clipboard
popups
focus
cursor state
application lifecycle

across multiple X servers into one spatial world.
You would effectively build a miniature remote-display protocol between every workspace X server and spatialwm.
It would work, but the complexity is produced by our design, not inherent to the problem.
Since:
- modifying XLibre is allowed,
- compatibility with existing desktop assumptions is not required,
- workspace identity is fundamental to your environment,
putting the isolation boundary inside the server is the smaller architecture.
I would make workspace identity immutable for a client connection
Don't allow:
client connects as workspace A
→ later says "actually I'm workspace B"

The workspace/security domain should be established when the process connects to X and remain attached to that client connection.
Something like:
process launched inside Workspace W
        ↓
authenticated/tagged X connection
        ↓
XLibre ClientRec.security_domain = W

Any child X resources inherit that domain automatically.
That replaces hundreds of later checks like:
Which workspace does this pixmap really belong to?

The answer is structurally known.
Surface presentation remains outside that security identity
Important distinction:
X resource owner = Workspace A

doesn't mean its surface can only appear in Workspace A's room.
spatialwm may legally present that surface:
Home world
Business world
Lab room

subject to presentation authority.
But another application in workspace B still cannot directly read the underlying X resource.
So:
XLibre
    protects application resources

worldd / spatialwm
    controls authorized presentation

Those are complementary boundaries.
Shared observation should go through the compositor
Suppose David's editor belongs to Workspace A, and Luna is permitted to observe its surface but not control it.
Don't grant Luna's X client permission to inspect David's X window directly.
Instead:
Workspace A app
      ↓
XLibre
      ↓
spatialwm/compositor
      ↓ authorized presentation
Luna observes pixels

That keeps X-client isolation intact.
Same for controlled interaction:
Luna
  ↓ actor action
worldd/spatialwm
  ↓ authority + lease check
XLibre input routing
  ↓
Workspace A application

The other X client never gains generic access to A.
The first tests should attack this boundary
I would require tests where malicious native X clients deliberately attempt:
enumerate foreign windows
read foreign pixels
query sensitive properties
send synthetic keys
inject mouse buttons
grab pointer
grab keyboard
alter foreign properties
redirect/inspect foreign resources

and verify:
same workspace + authority     → permitted
different workspace            → denied
explicit compositor mediation  → permitted only through checked path

Also test both directions and with several simultaneous agents.
The test must use independent native X clients, not ask worldd whether it thinks isolation happened.
For the questionnaire
Select 1. Extend the native server.
I’d put:
The selected XLibre server is extended with workspace-aware security domains and actor-aware input routing. Each X client receives an immutable workspace/application security identity at connection time; server resources inherit that identity. Cross-workspace enumeration, pixel access, resource manipulation, grabs, and input injection are denied unless explicitly mediated by trusted spatial components. Authorized cross-workspace observation and control occur through spatialwm/worldd, not by granting ordinary X clients direct access. Separate per-workspace X servers are not required.