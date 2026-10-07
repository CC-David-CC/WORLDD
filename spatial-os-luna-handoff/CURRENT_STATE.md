# Implementation status

## 2026-10-07: persistent world daemon, P00 through P02

The production worldd process owns persistent world state in SQLite.
The thin test host forwards framed protobuf messages to that process.
Direct worldd execution uses the same handlers and storage.
Each state directory has one authority, one durable world identity, and one entry room.

World create, get, list, update, delete, and snapshot now work.
Whole-edit validation and compare-and-swap revisions prevent partial and stale edits.
The daemon commits each durable mutation before acknowledging success.
A real process restart preserves acknowledged edits and world identity.
Worker death closes the proxy connection.
Parent death terminates its owned worker.

Both sb16 and sb16-2 passed:

- 39 harness self-tests.
- 13 binary protocol and numerical boundary tests.
- 11 additional daemon lifecycle and storage tests.
- All 46 cumulative P00/P01/P02 acceptance tests.

The same checks passed with AddressSanitizer and UndefinedBehaviorSanitizer on both hosts.
The remaining 95 locked acceptance tests were not selected.
Native-session and physical-device acceptance remain unrun.

The initial P02 diagnostic failed at world.create before implementation.
Its failure record is retained with the final evidence.
All locked P02 assertions remain unchanged.
The protobuf codec now supports their world operations.
See docs/WORLDD.md for the explicit schema extension and acceptance-lock lineage.

## Build and check

Run these commands from this folder on sb16 or sb16-2:

```sh
sh tools/bootstrap_core.sh --install-deps
python3 tools/qualify_core.py --through P02 --evidence-dir local/new-worldd-run
python3 tools/qualify_core.py --through P02 --sanitize --build-dir build-sanitize --evidence-dir local/new-worldd-sanitize
```

The evidence directory must be new.
Builds use two jobs and never download dependencies.
The bootstrap installs protobuf and SQLite development packages when explicitly requested.
It records changes and recovery commands in local/system-changes.md before installation.
CMake generates C++ and Python bindings from the tracked schema.

Final reports and logs are retained in evidence/worldd-2026-10-07.
Each run records production and test source hashes plus the binary build identity.
Local-model requests, proposals, and corrective rounds are retained under local/peer/worldd-* on sb16.
Credentials are absent from those records.
The model's initial storage and schema proposals had defects.
The primary agent sent concrete critiques and received corrected reviews.
Only executed checks establish test results.
Final review requests failed after the local agent developed a hardware problem.
David authorized continuation without the local agent.

The daemon uses a trusted inherited-pipe management interface.
Network transport and actor authentication remain pending.
Use a private local state directory on an authorized host.
Stop the authority before copying the complete state directory for backup.
Restart from the same directory to resume that world.
No login, desktop, or service configuration changed in this stage.

## Earlier stages

Commit d31fc59 delivered the protobuf P00/P01 core.
It passed 32 cumulative acceptance tests and sanitizer checks on both hosts.
Commit 7283fd9 contains the earlier JSON P00 implementation.
David then authorized protobuf for production and acceptance.
See docs/PROTOBUF_MIGRATION.md for that migration.
Geometry and motion numerical assertions remain unchanged.

## Pending

Next: actor state, actor authority, and workspace authority.
Define their protobuf operations before implementing those handlers.
Use the root AGENTS.md workspace model when older room/worktree bindings conflict.
Record those semantic contract changes separately.

Workspace Git backing stores and mount-namespace /project views remain pending.
Execution confinement, rendering, XLibre changes, and physical device input remain unimplemented.
Shared-world networking and model-driven actors remain pending.
No Spatial-M1 or LOCAL_READY qualification is claimed.
