# Implementation status

## 2026-10-07: protobuf core, P00 and P01

The C++20 production core now uses Protocol Buffers.
Frames use a four-byte big-endian length and a 1 MiB payload limit.
The acceptance harness sends the same binary protocol.
The thin host calls shared production functions for rigid transforms, ray-panel picking, pixel mapping, and exact body-twist motion.

Both sb16 and sb16-2 passed:

- 39 harness self-tests.
- 13 additional binary protocol and numerical boundary tests.
- All 32 cumulative P00/P01 acceptance tests.

The same checks passed with AddressSanitizer and UndefinedBehaviorSanitizer on both hosts.

The initial P00 gate was BLOCKED without a production executable.
The initial P01 gate failed before geometry implementation.
The earlier JSON P00 implementation remains in commit `7283fd9`.
David then authorized protobuf for production and acceptance.
See `docs/PROTOBUF_MIGRATION.md` for the explicit contract change.
P01 numerical assertions are unchanged.

## Build and check

Run these commands from this folder on sb16 or sb16-2:

```sh
sh tools/bootstrap_core.sh --install-deps
python3 tools/qualify_core.py --through P01 --evidence-dir local/new-core-run
```

The evidence directory must be new.
Builds use two jobs and never download dependencies.
The bootstrap installs protobuf-compiler, libprotobuf-dev, and python3-protobuf when explicitly requested.
It records changes and recovery commands in `local/system-changes.md` before installation.
CMake generates C++ and Python bindings from the tracked schema.

Raw results are under `local/first-core` on each host.
Retained final reports are also in `evidence/core-2026-10-07`.
Local-model requests and proposals are under `local/peer` on sb16.
Credentials are absent from these records.
The local model proposes changes per file. The primary agent compares proposals and runs the tests.

The core is linked from `src/core` into `src/testing/main.cpp`.
This adapter is a development boundary, not a desktop session or untrusted actor endpoint.
`world.get` validates its arguments and then returns UNIMPLEMENTED.

## Pending

Next: a durable world daemon, followed by actor state and workspace authority.
Define the later-phase protobuf operations before implementing those handlers.
Use the root AGENTS.md workspace model when older room/worktree bindings conflict.
Record those semantic contract changes separately.

World persistence, execution confinement, rendering, XLibre changes, and physical device input remain unimplemented.
Native session and hardware acceptance remain unrun.
No Spatial-M1 or LOCAL_READY qualification is claimed.
