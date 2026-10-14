# M07 focused integration candidate (UNQUALIFIED)

Status: source integration and coding-host build checks only. Runtime tests are UNRUN. This document does not mark M07 or Spatial-M1 qualified.

## Source inputs

- Base shared-interface bridge: `aeac1530060d38973c5a6ca7101038dfc8ceac15` on `m07/shared-interfaces-20261014`.
- Presentation partial: `61d99e716735a6ea1f7ec0ba14d162bdc84821d4` on `m07/workspace-presentations-20261014`.
- Snapshot partial: `9400f9beb7c28d1724853e3a1fc6615a076cf7d9` on `m07/storage-snapshots-20261014`.
- Integration branch: `m07/focused-integration-20261014`.
- Integration HEAD before the CMake and documentation changes: `b25d03538` (merge of both partials).

The partials are unqualified coordination checkpoints. Packaging remains a separate component-only task. Do not combine artifacts from different source pins.

A focused integration test-fixture correction expands only the new scoped-authorization test root with `WORKSPACE_PRESENTATION_ATTACH`. The base fixture's default `RIGHTS` remains unchanged. This preserves the intended positive exact-scope query and fresh revocation checks while avoiding accidental failure at grant delegation time. Presentation-store test corrections add captures for `rep_a` to two predicates and deny view access in the hidden-geometry case. The empty-geometry assertion remains unchanged.

The first Debug build attempt preserved the compile failure for those missing captures in `run-m07-integration-debug-build/`. The fresh Debug and ASan builds below include the two-capture correction. No runtime suite ran on the coding host.

## Focused checks selected

Before execution, select these component checks for the bridge, presentation persistence, and immutable snapshot owner changes:

- `workspace-authority`: exercises the production broker and `worldd` authority bridge, including fresh scoped authorization. Build `identityd`, `spatial-issuer-client`, `worldd`, and `workspaced`.
- `workspace-presentation-store`: exercises the presentation store's durable create/move/remove behavior. Build `workspace-presentation-store-test` and `spatial-core`.
- `storage-snapshot-owner`: exercises the production storage-owner handler through its private probe on a real Btrfs volume. Build `spatial-storage-owner-probe`, `spatial-storage-owner`, `broker-python-protobuf`, and `storage-python-protobuf`. It provisions a volume and writes evidence under the absolute `SPATIAL_SECURITY_EVIDENCE` path.

The snapshot CTest receives the production probe and source root. Its environment includes the build Python bindings and source root. Its 900-second outer timeout covers three real-volume setup/cleanup cycles, each of which has a 90-second provisioning deadline; the fixture itself has operation-specific deadlines and cleanup evidence. The test requires the runtime test host's Btrfs/loop/sudo prerequisites. Do not run it on a coding host.

The focused test selection does not replace cases in `M07_WORKSPACE_INCREMENT_PLAN.txt`. Copy and presentation handlers, cross-host behavior, recovery, packaging, and all later phase-gate requirements remain pending.

## Build and registration checks

On a coding host, configure and build Debug and ASan/UBSan for the affected production and test closure: `worldd`, `workspaced`, `spatial-core`, `spatial-storage-owner`, the storage client, authority/broker helpers, storage and broker probes, presentation store test, and generated Python bindings. Use the installed persistent runner and `-j2` with separate `flock` locks. `ctest -N` may verify registrations. Do not execute CTest on a coding host.

On `sb16` and `sb16-2`, after primary review and artifact pinning, run only the selected focused cases first. Preserve their evidence directories. This candidate still needs the full qualification plan on both required hosts.

## Artifact manifest

Freeze the final Git HEAD, changed-file list and SHA-256 hashes after review. Package only artifacts built from that exact HEAD. The worker source/test files are owned by their original checkpoints; integration edits are limited to CMake wiring and this handoff document.
