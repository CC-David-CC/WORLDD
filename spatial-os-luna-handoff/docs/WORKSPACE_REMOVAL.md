# Worktree removal and retained data

Workspaced now removes clean, unbound Git worktrees through the existing protobuf management interface.
It unregisters the worktree from real Git and preserves the branch.
It retains the actual project files in private execution storage.
This operation does not permanently delete project data.

Workspace creation, membership, and bound-worktree protection are now qualified in [WORKSPACE_CONTEXT.md](WORKSPACE_CONTEXT.md).
Removing a workspace-bound worktree returns IN_USE before Git or filesystem changes.
The subsequent [process increment](WORKSPACE_PROCESSES.md) adds confined execution and records current P08 qualification.

## Preconditions

Removal requires a registered worktree with a completed creation effect.
The existing administrative-link and repository-configuration checks apply.
An existing worktree lock or index lock returns IN_USE.
The broker acquires the real index lock during retirement.
This excludes ordinary concurrent Git index writers.

Tracked changes, untracked files, and ignored data return DIRTY_WORKTREE.
Assume-unchanged and skip-worktree flags also prevent removal.
The broker does not clear those flags or alter dirty files.
Submodule index entries require explicit retirement and return IN_USE.
Automatic submodule retirement remains unsupported.

Precondition failures do not create a removal journal intent.
The broker releases only the index lock that it created.
An interrupted broker can leave its index lock for explicit inspection.
Do not remove another Git process's lock.

## Effects and recovery

The broker records the request and an opaque retirement directory before moving files.
Git moves the worktree into that private directory and updates its administrative links.
The broker checks the moved worktree again.
It preserves its current commit through a private Git ref, including detached commits.
It copies worktree administration and flushes retained data before unregistering the worktree.

The retirement layout is:

- `staging/project`: the temporary Git registration path.
- `data/project`: the retained project directory after reservation.
- `git`: preserved worktree administration, including the index and reflog.
- `complete`: a private completion receipt.

Linux renameat2 exchanges the staging directory with a deliberately broken symlink.
The symlink is a parent of the registered path, not the registered path itself.
Git then unregisters the inaccessible private path without traversing retained files.
The broker never runs recursive deletion against the original project directory.
Files recreated at the original path remain untouched.
Writes through existing file descriptors still reach the retained directory.

The receipt follows the filesystem and Git flushes.
SQLite marks the operation ready only after the receipt is durable.
These are separate effects. They are not one cross-system transaction.

On restart, the broker checks the receipt, retained directories, commit ref, and real Git registration.
Matching completed effects become ready without repeating removal.
Missing or contradictory evidence becomes uncertain.
The broker preserves uncertain data and does not force deletion, retry a move, or prune other worktrees.
A changed request using an existing operation ID returns OPERATION_CONFLICT.
Creation and removal share the operation-ID namespace.

A successful retry returns the original result.
It does not inspect or delete a replacement directory at the old project path.
The removed worktree identity remains reserved in the execution database.
The original creation result remains historical operation evidence.

## Explicit execution schema migration

The removal increment introduced execution schema version 2 and its removals table.
The subsequent workspace and process increments advance execution storage to version 4.
It preserves existing repositories, worktrees, creation operations, and removal records.
See [WORKSPACE_CONTEXT.md](WORKSPACE_CONTEXT.md) for current execution and world migration procedures.

Normal startup refuses older databases with an explicit migration command.
A Git update does not silently migrate a running execution authority.
Stop the owning supervisor before migration. Keep the SSH or TTY recovery route open.
Set state to the exact execution directory that you intend to migrate.

```sh
state=/absolute/execution/directory
sqlite3 -readonly "$state/execution.db" 'PRAGMA user_version; PRAGMA quick_check;'
cp --no-clobber -- "$state/execution.db" "$state/execution-v1.backup.db"
/absolute/build/workspaced --state-dir "$state" --migrate-v1
sqlite3 -readonly "$state/execution.db" 'PRAGMA user_version; PRAGMA foreign_key_check;'
```

For an existing version 2 database, use --migrate-v2 instead.
Both commands migrate to the current execution schema, version 4.
For an existing version 3 database, use --migrate-v3.
The migration acquires the execution lock and validates the existing database.
It adds required tables in one SQLite transaction.
Repeated migration of the current version succeeds without replaying Git effects.
Normal startup performs effect reconciliation after migration.
Keep the backup. Do not restore it over a database that contains later operations.

## Inspecting an uncertain operation

Stop its supervisor before attempting repair.
Read the exact retained path and repository path from the execution registry:

```sh
sqlite3 -readonly "$state/execution.db" \
  'SELECT id,worktree,retired,state FROM removals; SELECT id,path FROM repositories;'
git -C /exact/bare/repository worktree list --porcelain
git -C /exact/bare/repository for-each-ref refs/spatial/retired/
ls -ld -- /exact/retired/directory/staging /exact/retired/directory/data
```

An interrupted move can leave files under staging/project.
A completed reservation leaves files under data/project.
Preserve both locations during inspection.
The copied administration can include the broker's former index.lock.
That copy is evidence, not permission to restore an active Git lock.
This increment does not implement automatic restoration or permanent cleanup of retired directories.
Allocate any restored independent execution state a new workspace identity under the project policy.

## Qualification

Both hosts passed 39 focused broker checks.
Twelve new checks cover dirty data, hidden index flags, locks, submodules, and detached commits.
They also cover open file descriptors, retries, operation conflicts, migration, interrupted effects, and changed recovery paths.
The partial-effect test inserts an unsupported FIFO into Git administration.
The resulting storage error leaves the moved project preserved and the operation uncertain after restart.
No test substitutes a simulated filesystem or Git implementation.

Both hosts passed Debug and ASan/UBSan qualification.
Each build passed 174 core checks, 43 harness checks, and 126 acceptance tests.
The full P00-P08 gate ran all 138 current cases.
Twelve P08 cases report UNIMPLEMENTED errors for workspace context and process execution.
There are no assertion failures or skips. Thirteen later acceptance tests remain unrun.
P08 remains incomplete.

Evidence is in evidence/workspace-removal-2026-10-07.
It includes baseline failures, focused tests, the filesystem experiment, and full qualification reports.
All 96 source hashes match the reviewed local files and both execution hosts.
Each run records exact commands, build identity, and the private XLibre server hash.
Qualification uses tools/qualify_core.py --through P07 and then tools/gate.py --through P08.
Sanitizer runs add --sanitize --build-dir build-sanitize.
Sanitizers cover the broker, runtime, and compositor. XLibre remains a private Debug build.
The tests use authenticated disposable software displays.
No host configuration or operator session changed.

Bound-worktree protection is covered by the subsequent workspace-context qualification.
Workspace confinement, full Spatial-M1, and hardware qualification remain pending.

Git behavior was checked against the installed Git 2.43 implementation and the real-host experiment.
Reference: [Git worktree implementation](https://github.com/git/git/blob/v2.43.0/builtin/worktree.c).
