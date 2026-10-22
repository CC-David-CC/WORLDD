# Spatial Desktop fleet storage

David authorized shared storage on `dflanag3@r730` on 2026-10-18.
The fleet guide is `C:\Users\dflanag3\Documents\fleet\FLEET_STORAGE.md`.
That guide records disk health and mount configuration.

| Use | Project directory | Filesystem UUID |
|---|---|---|
| Current packages, manifests, results, unresolved failures | `/mnt/data10tb/spatial-desktop` | `82287fcb-81a5-4f04-a0d8-886549cf7f90` |
| Expendable transfers, scratch files, caches | `/mnt/data2tb/spatial-desktop` | `9ba9ece9-7372-495a-a62d-052bc0a83adc` |

Verify the exact mount before writing. Check its UUID, ext4 type, and writable state.
Use `findmnt --mountpoint /mnt/data10tb` or `findmnt --mountpoint /mnt/data2tb`.
Check free space before a transfer. Never use an unmounted mount directory.

Both project roots contain `OWNER.json`. Preserve that marker and other agents' files.
The durable root contains `packages/current`, `failures/unresolved`, `summaries`, and `manifests`.
The scratch root contains `transfers`, `cache`, and `temporary-packaging`.
Use the 2 TB disk only for data that can be recreated.

The Windows controller reaches `dflanag3@r730` through its existing SSH configuration.
Middle-child uses the `spatial-storage-r730` SSH alias with a dedicated key and pinned host key.
Keep private keys outside Git and tool output.
R730 provides storage. It does not run project builds, tests, worlds, or inference.

Keep active build trees on the coding host. Keep current test packages and logs on the test hosts.
Remove temporary package stages after their retained replacement is verified.
Transfer useful retained artifacts with source pins and checksums. Verify destination bytes before deleting the source.
Keep concise qualification summaries in Git. Keep generated artifacts outside source checkouts.
Remove superseded reproducible outputs and obsolete logs. Do not copy all historical evidence onto these disks.
Retain unresolved failure evidence until the repair and replacement are verified.
