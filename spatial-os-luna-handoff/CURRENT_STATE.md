# Implementation status

## 2026-10-07: production core P00

The C++20 production core and thin conformance host now exist.
They implement strict JSON-line framing, request correlation, validation, typed errors, and source-derived build identification.
Malformed frames do not prevent the next valid request.
The parser rejects duplicate keys, invalid UTF-8, excessive nesting, and non-finite numbers.
Frames have a 1 MiB limit.

Both sb16 and sb16-2 passed:

- 39 existing harness self-tests.
- 7 additional protocol boundary tests.
- All 5 locked P00 acceptance tests.

The preceding P00 gate reported BLOCKED because no production executable existed.
Raw evidence is under each host's `local/first-core` directory.
The existing acceptance files and lock are unchanged.

## Build and check

Run these commands from this folder on sb16 or sb16-2:

```sh
sh tools/bootstrap_core.sh --install-deps
python3 tools/qualify_core.py --through P00 --evidence-dir local/new-core-run
```

The evidence directory must be new.
Builds use two jobs and do not download dependencies.
The bootstrap installs `nlohmann-json3-dev` when explicitly requested.
It records the change and recovery command in `local/system-changes.md` before installation.

The core is linked from `src/core` into the thin `src/testing/main.cpp` adapter.
The adapter is a development boundary, not a desktop session or untrusted actor endpoint.
`world.get` validates its arguments and then returns `UNIMPLEMENTED`.

## Pending

Next: P01 geometry and constant body-twist motion.
World persistence, workspace authority, execution confinement, rendering, XLibre changes, and device input remain unimplemented.
Native session and hardware acceptance remain unrun.
No Spatial-M1 or LOCAL_READY qualification is claimed.
