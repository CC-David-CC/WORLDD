# M08 required runtime cases and gate inputs

Status: tooling changes only. M08 qualification remains UNQUALIFIED. No project runtime, deployment, build, or full gate ran.

The gate requires these registrations in both Debug and ASan-UBSan inventories:

- `copy-worker-stream`
- `workspace-copy-worker-helpers-tooling`
- `process-scope-named-regression`
- `workspace-file-action-storage-owner-btrfs`
- `storage-owner-fixture-tooling`
- `workspace-cabinet-native-crosshost`

The first five run in each profile core CTest lane, including the profile-matched copy-helper tooling regression. CTest skip properties are rejected for every mandatory registration. The cabinet native cross-host test is excluded from per-profile core execution and uses the dedicated M07 workspace cross-host runner. Invoke `m08_deployed_gate.py workspace-cabinet-crosshost` for each profile and direction on the authorized host. Reconcile with `m08_deployed_gate.py reconcile-cabinet-crosshost`, supplying separate `--workspace-geometry-results` and `--cabinet-crosshost-results` indexes. Each index must contain the full host/profile/direction matrix for its named test. Both matrices reconcile against M08_REQUIRED and the exact three-test M08 dedicated exclusion set. The report preserves separate geometry and cabinet evidence under an M08 reconciliation identity. The runner validates the same eight nonempty geometry role values, source/deployment pins, native resource wrapper, and cleanup receipts. M06 and M07 default dedicated schedules remain unchanged.

Harness contract: the M08 tooling lane requires exactly 89 discovered harness cases: the frozen 51-case M06 baseline plus 38 M08 additions. The baseline ID list is pinned by SHA-256 e4c14f05414e9cbd39f881fbb5d6fe45ffd5301f8f5c0a693bd36ec1808853f2. The inventory must match unittest discovery and rejects missing, extra, duplicate, skipped, or failed cases. M06 and M07 default schedules retain their existing 51-case and five-storage-result contracts. M08 adds the sixth production_application_copy_stage_mask storage result.

The StorageOwner lane requires a fresh, distinct root-provisioned volume and matching launch configuration for each profile. Set all six values on each qualification host:

- `SPATIAL_TEST_STORAGE_VOLUME_DEBUG`
- `SPATIAL_TEST_STORAGE_LAUNCH_CONFIG_DEBUG`
- `SPATIAL_TEST_STORAGE_COPY_QUOTA_BYTES_DEBUG`
- `SPATIAL_TEST_STORAGE_VOLUME_ASAN_UBSAN`
- `SPATIAL_TEST_STORAGE_LAUNCH_CONFIG_ASAN_UBSAN`
- `SPATIAL_TEST_STORAGE_COPY_QUOTA_BYTES_ASAN_UBSAN`

Each profile's gate stage maps its suffixed values to the three canonical `SPATIAL_TEST_STORAGE_*` variables. The copy quota must equal `100663296`. Preflight requires an absolute readable launch file and an absolute writable top-level Btrfs mount with inode 256 and treeid 5. Mount options may omit compression or state `compress=no`; active compression and all `compress-force` options are rejected. It rejects missing values and shared profile volumes or launch files.

The external fixture handoff must provide a fresh single-device Btrfs top-level volume for the hardcoded fixture identities, without compression, and enough free data and metadata space. Its launch config must match the volume FSID and size, broker UID, reservations, and immutable StorageOwner policy. Workspace `one` must enforce the 96 MiB quota. The host also needs delegated broker configuration/controllers, generated bindings and helper binaries, btrfs-progs, authorized `systemd-run` and I/O policy, plus the protected installed StorageOwner and matching launch probe. The required root provisioner and complete per-host fixture inputs are not yet available. The gate refuses to launch when these inputs are absent.

Focused tooling evidence: `/home/dflanag3/spatial-artifacts/m08-gate-required-runtime-cases-20261018/tooling-test-22` records 26 passing harness tests, including M08 orchestration routing, separate result hashes/receipts, and refusal to publish after second-matrix failure. It also covers storage-owner-fixture-tooling, all M08 registration omissions, both dedicated-set omissions, and missing/empty result indexes. Earlier assertion failures remain preserved in tooling-test-14, tooling-test-15, and tooling-test-20. Earlier setup and assertion failures remain preserved in tooling-test-01 through tooling-test-09. Tooling-test-24 passed 27 harness tests with mocked Btrfs command results for absent compression and `compress=no`; compressed and `compress-force` cases remain rejected. Native and storage runtime checks remain UNRUN.
