# StorageOwner file-action fixture setup

This fixture repair remains UNQUALIFIED. Runtime is UNRUN on sb16 and sb16-2.
Host-independent tooling controls exercise ownership with substituted process handles.
They do not establish storage, cgroup, broker, or quota execution.

Only the dedicated owner test opts into StorageOwner through its broker factory.
Ordinary broker fixtures ignore storage environment inputs.
The resource delegate forwards these three explicit inputs:

- `SPATIAL_TEST_STORAGE_VOLUME`: one canonical, fresh, writable Btrfs top-level mount.
- `SPATIAL_TEST_STORAGE_LAUNCH_CONFIG`: readable binary launch policy for that exact mount.
- `SPATIAL_TEST_STORAGE_COPY_QUOTA_BYTES`: `100663296`.

Provisioning remains external. No provisioner is included in this repair.
Use sb16 and sb16-2. Do not run this fixture on development hosts.
Mount a dedicated single-device filesystem with inode 256 and rootid 5.
Use `compress=no`. Keep logs and unrelated files outside its root.
The initial root must contain no execution.db or unrelated state.
Recognized valid broker identity and lock metadata may exist together.
Give the broker UID the required write access.
Preserve failed images and logs before preparing replacements.

The protected owner policy must match the FSID, exact device capacity, and broker UID.
Supply valid control-plane and filesystem reservations.
Omit a stale broker PID. The launcher supplies the actual broker pidfd.
Attest the selected workspace `one` policy is exactly 96 MiB.
The environment string alone does not prove the kernel quota.
Attest matching kernel qgroup membership and enforced limits after workspace binding.
The default repository budget must permit import and workspace creation.
Keep the stable policy identical across both broker starts.
Policy changes on an existing catalog require explicit stopped migration.

Install the matching protected `/usr/local/libexec/spatial-storage-owner`.
The production launcher uses this fixed executable path.
Provide the launch probe, process-scope probe, Git worker, copy worker, and generated bindings.
Provide authorized systemd-run, delegated cgroup controllers, and required I/O device budgets.
Use the suite resource wrappers. Verify their exact configuration before execution.

The wrapper waits behind a pipe until the owner reports running.
A failed startup never sends its authorization byte.
The successful path then requires real broker management and an authorized browse.
The browse creates private staging through the production file engine.
Quota source data uses bounded random chunks to avoid compression artifacts.

Required runtime witnesses include both broker starts and exclusive authority initialization.
Require small-copy COMPLETE and quota-copy EXHAUSTED.
Require an absent rejected destination and empty private staging.
Require checked broker-scope and owner-service retirement.
Failure logs retain the returned exact owner unit and cleanup confirmation state.
If no response returns a unit, inspect the retained owner log for its exact service name.
An unconfirmed cleanup remains a failure and requires exact-unit recovery.
Close owners before unmounting and detaching the fixture device.
Do not reuse populated state with the fixture's fixed repository and workspace IDs.
