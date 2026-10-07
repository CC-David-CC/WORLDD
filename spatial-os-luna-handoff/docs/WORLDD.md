# Persistent world daemon

## Scope

P02 adds the production worldd process.
It owns one SQLite database and one world identity per state directory.
The test host forwards binary protobuf frames to worldd.
It does not implement world behavior or open the database.

Run worldd directly with an absolute state directory:

```sh
build/worldd --state-dir /absolute/private/world
```

Standard input and output carry framed protobuf messages.
This is a trusted management interface.
It has no network listener or actor authentication.
Actor authority and shared-world network connections remain later work.
Do not expose these pipes to an untrusted actor.

## State and durability

The directory contains world.db and world.lock.
Use a private directory on the authority host's local SSD.
Do not place active state on a shared network filesystem.
Do not remove the lock file while the authority runs.
An exclusive flock rejects a second cooperating authority.

New state receives a random world identity and one entry room.
The entry room cannot be deleted.
Each separate state directory creates an independent world.
Restart preserves world identity, entity identity, and revisions.
A host name does not define world identity.

SQLite uses DELETE journaling, synchronous EXTRA, and foreign keys.
A write transaction validates the complete edit before commit.
The daemon acknowledges success only after commit.
Entity revisions support compare-and-swap updates and deletes.
Each accepted mutation increments the global world revision.
Rejected edits leave both the entity and world revision unchanged.
Snapshots contain entities in binary ID order.

Startup checks the schema version, database integrity, references, and stored protobuf values.
Unknown schema versions require an explicit migration.
Invalid persisted state causes startup failure.
The daemon does not reset or repair unknown state automatically.
Stop the authority before copying its complete state directory.
Retain the original directory until a restored copy passes validation.

Process-kill tests verify acknowledged edits after restart.
They do not simulate sudden SSD power loss.
Filesystem, Git, process, and X effects are outside this database transaction.

## Protobuf extension and acceptance lock

David authorized protobuf for production and acceptance.
This additive extension retains all existing field numbers.
The wire version remains spatial-protobuf/1.
Capability world.durable/v1 identifies world operation support.
Older binaries reject operations they do not recognize.

The schema adds typed world create, get, list, snapshot, update, delete, info, and runtime identities operations.
List and snapshot both return WorldSnapshotReply.
Entity fields retain presence for patch validation.
ID, kind, and revision fields are immutable, including explicit default values.
Room references are mutable.
Message-valued patches replace the complete field.
This preserves explicit zero vector components.
Room, panel, and surface-slot creation require a label; empty labels are permitted.

Database schema version 1 stores entity protobuf bytes and indexed references.
Revisions cannot exceed signed 64-bit storage limits.
Labels have a 65,536-byte limit; IDs have a 256-byte limit.
A snapshot over the frame limit returns RESOURCE_EXHAUSTED.
It never returns a truncated success.
Pagination remains future work.

The P02 semantic assertions are unchanged.
The harness now maps their calls to typed protobuf messages.
The additional protocol boundary test changes missing-world retrieval from UNIMPLEMENTED to NOT_FOUND.
Retrieval now exists and must report a missing entity accurately.
The lock records these schema and codec changes, this note, and added daemon tests.
Its predecessor hash identifies the prior locked baseline.

Root AGENTS.md supersedes older room/worktree coupling.
World persistence does not implement workspace Git storage.
Workspaces will own real worktrees backed by host-local bare repositories.
Separate mount namespaces can expose distinct worktrees at the same /project path.
The underlying writable directories stay separate.

## Process lifecycle

The test host starts worldd with private pipes and an inherited process group.
A pidfd monitors worker death, including while the client is idle.
The worker receives SIGKILL if its parent dies.
A startup parent check closes the fork-to-parent-death race.
The proxy bounds frame size and active I/O deadlines.
A worker failure closes the client connection.
It does not retry a mutation with an uncertain outcome.

## Verification

Run on sb16 and sb16-2:

```sh
python3 tools/qualify_core.py --through P02 --evidence-dir local/new-worldd-run
python3 tools/qualify_core.py --through P02 --sanitize --build-dir build-sanitize --evidence-dir local/new-worldd-sanitize
```

Use a new evidence directory for each run.
CMake runs direct protocol and daemon lifecycle checks.
The gate then runs cumulative P00, P01, and P02 acceptance.
Native graphics, input, workspace confinement, and Spatial-M1 remain outside this stage.
