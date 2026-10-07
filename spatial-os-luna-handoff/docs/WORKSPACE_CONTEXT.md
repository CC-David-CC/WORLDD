# Workspace authority and actor context

Workspaced now owns workspace definitions and membership in its execution database.
Worldd owns workspace availability and actor selection in its world database.
Rooms do not own workspaces or select execution context.

## Creation and ownership

Workspace creation binds one existing managed worktree and its writable bare Git administration.
The execution database enforces one workspace owner for each worktree and bare repository.
An independent workspace cannot reuse either mutable resource.
Creation does not enroll an actor, make the workspace available, select it, or create cabinet geometry.

The existing Git implementation now belongs to ExecutionStore in src/workspace/store.cpp.
That store owns one execution lock and one transactional database.
It contains no world ID or owning-room field.
Its workspaces table records identity, worktree, repository, policy, and revision.

A bound worktree cannot be retired.
Broker-created worktrees cannot add another mutable checkout to a workspace-owned repository.
This restriction also applies to actor Git actions with the broad fixture Git grant.
Ordinary Git inside a confined workspace remains part of the pending process implementation.

## Membership and selection

Management membership requests require an actor registered in the calling world.
The broker stores explicit actor membership independently of room presence and broad actor grants.
Effective workspace rights are the intersection of workspace policy and member rights.
Unknown and duplicate rights fail before the membership changes.
Changed membership advances the workspace revision. An identical update preserves it.

Availability is an explicit, durable world operation.
Making a workspace available does not select it.
Selection requires both availability and effective workspace-select authority.
Each actor has a separate durable selection in each world's database.
Actor reconnect restores the previous selection only after checking current authority.
Unavailable or unauthorized selections are cleared.

Room transitions preserve selection.
Explicit clearing removes the selection without changing project files or runtime process identities.
Workspace unavailability clears all affected selections in one world transaction.
Membership revocation clears that actor's affected selection, including an offline actor's selection.
Re-enabling availability or membership does not silently reselect a workspace.

Broker membership and world context use separate database transactions.
If world cleanup fails after revocation, the broker's revocation remains effective.
Actor inspection and execution requests recheck authority before using saved context.
They cannot expose the old selection as authorized when cleanup fails.
The operation reports its storage error. It does not restore the revoked grant.

## Private protobuf authorization

The inherited broker channel now uses proto/spatial/workspace/v1/broker.proto.
It wraps management requests and defines a separate actor-authorization request.
Worldd constructs that request from the authenticated actor identity.
An actor cannot supply the trusted identity through a public workspace action.

The private envelope has its own version, request correlation, and checked result types.
Worldd rejects private envelopes on the public interface.
Workspaced rejects public frames that omit the private envelope.
The broker also refuses world operations outside its responsibility.
Build worldd and workspaced together from the same reviewed source.

Public protobuf schemas and locked acceptance assertions remain unchanged.
Bindings for the new private schema are generated during the build.
The inherited channel still supplies trusted fixture administration.
Cryptographic enrollment, delegation, external authentication, and shared service attachment remain mandatory later work.

## Explicit database migrations

Current database versions are execution version 3 and world version 2.
Execution version 3 adds workspace ownership and membership.
World version 2 adds availability and actor selection.
The migrations preserve existing Git records, entities, world identity, and revisions.
Normal startup refuses older versions instead of migrating them silently.

Stop the owning supervisor before migration.
Use the exact state directories and binaries from the installation that you intend to update.
Keep the working SSH or TTY recovery route open.

```sh
execution=/absolute/execution/directory
world=/absolute/world/directory
sqlite3 -readonly "$execution/execution.db" 'PRAGMA user_version; PRAGMA quick_check;'
sqlite3 -readonly "$world/world.db" 'PRAGMA user_version; PRAGMA quick_check;'
cp --no-clobber -- "$execution/execution.db" "$execution/execution-before-context.backup.db"
cp --no-clobber -- "$world/world.db" "$world/world-before-context.backup.db"
```

For an execution database currently at version 2, run:

```sh
/absolute/build/workspaced --state-dir "$execution" --migrate-v2
```

For an execution database currently at version 1, use --migrate-v1 instead.
Both commands migrate to the current execution schema.
For a world database currently at version 1, run:

```sh
/absolute/build/worldd --state-dir "$world" --migrate-v1
sqlite3 -readonly "$execution/execution.db" 'PRAGMA user_version; PRAGMA foreign_key_check;'
sqlite3 -readonly "$world/world.db" 'PRAGMA user_version; PRAGMA foreign_key_check;'
```

Repeated migration commands accept the current version without replaying effects.
Keep the backups. Do not restore them over databases that contain new context or execution operations.
Uncertain Git effects retain the inspection procedure in WORKSPACE_REMOVAL.md.

## Qualification and remaining work

The new checks use real broker processes, Git, SQLite, public actor actions, and deliberate storage failures.
They verify ownership, policy intersection, selection persistence, room transitions, revocation, and explicit migration.
They also verify private-envelope rejection and corruption rejection under the current world schema.

The first qualification attempt detected an edit to the locked worldd test's schema fixture.
That edit was restored exactly. A separate unlocked counterexample now checks the current schema.
The acceptance lock remains unchanged. Evidence preserves the failed integrity run.

Both hosts passed Debug and ASan/UBSan qualification for this increment.
Each build passed 187 core checks, including thirteen new workspace checks, and 43 harness checks.
The full P00-P08 gate ran 138 cases: 130 passed, zero assertion failures, eight errors, and zero skips.
The eight errors report unimplemented process execution. Thirteen later acceptance tests remain unrun.
All 113 preceding P00-P07 acceptance tests also passed separately.
P08 remains incomplete.

Evidence is in evidence/workspace-context-2026-10-07.
It includes the baseline, focused checks, failed integrity run, and corrected full qualification.
All 98 source hashes match reviewed local files and both execution hosts.
Each run records exact commands, build identity, and the private XLibre server hash.
Qualification uses tools/qualify_core.py --through P07 and then tools/gate.py --through P08.
Sanitizer runs add --sanitize --build-dir build-sanitize.
Sanitizers cover the broker, runtime, and compositor. XLibre remains a private Debug build.
The tests use authenticated disposable software displays.
No host configuration or operator session changed.
Local-model review remains waived under David's hardware-failure instruction.

Confined process launch, shared-world service attachment, cabinet operations, and cross-world transitions remain incomplete.
Application-group revocation and X workspace isolation remain mandatory Spatial-M1 work.
These context checks do not establish those boundaries or physical hardware qualification.
Next: process.workspace_cwd through the production confinement broker.
The workspace.room_independence acceptance case remains pending because it also requires real process execution.
