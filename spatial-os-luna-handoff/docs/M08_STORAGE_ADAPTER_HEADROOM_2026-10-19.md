# M08 adapter storage headroom correction - 2026-10-19

Status: UNQUALIFIED fixture correction. No production resource accounting or
acceptance oracle changes. No storage volumes or protected runtime configs were
modified or provisioned.

## Budget contract

The adapter fallback broker envelope funds the selected manager ceiling, the
standing 256 MiB Git parent, one held 128 MiB application, the next 128 MiB
writer application, and 256 MiB fixed overhead. The profile formula is
`manager_memory_mib(profile) + 256 + 2*128 + 256`. It yields 1024 MiB for
Debug and 1280 MiB for ASan-UBSan. Manager memory remains 256/512 MiB. Workspace,
actor, and application ceilings remain 512/256/128 MiB. The 1536 MiB native
outer cap, zero-swap rule, process/I/O limits, deadlines, and security checks
are unchanged.

Only the no-volume adapter fallback uses this formula. A provisioned runtime
may carry a different pinned broker policy. The shared exact-match helper binds
its pinned definition to the actual provisioned I/O devices and rejects runtime
resource drift; it does not impose the fallback formula on custom policies.

Future fixture definitions preserve every existing policy field and set only
SAN broker memory to 1280 MiB. Debug remains 1024 MiB. The definition remains
the source of truth. Provisioning binds actual device IDs and configured I/O
rates into runtime budgets.

## Storage evidence phases

The pinned provisioner verifies the mounted volume before assigning the
root owner from `owner_policy.broker_uid`. The separate plan verifier must
require UID 1000 and mode 0700 immediately after provisioning. It must not
chown the mounted root itself. The existing core #15 StorageOwner probe still
proves the owner launch lifecycle; the native broker launch probe is a separate
lane.

The revised read-only verifier requires exact runtime resource equality with
the pinned definition after actual-device binding. It checks the persistent
owner budget the same way. Existing protected-file and mount-identity guards
remain required.

## Selected tooling checks

1. `CopyFixtureProfileTests` runs all 17 profile tests. It checks Debug/SAN headroom arithmetic, unchanged fallback ceilings and zero swap, exact device binding, custom pinned resource equality, argument handling, and existing profile checks.
2. `python3 -m py_compile tools/copy_fixture_profiles.py tools/with_adapter_resources.py tools/test_copy_fixture_profiles.py` checks syntax without running native code.

Runtime and storage provisioning checks remain UNRUN. Primary review is required
before any host mutation or runtime launch.
