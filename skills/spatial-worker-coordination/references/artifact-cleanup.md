# Fleet artifact cleanup

Read the project's FLEET_STORAGE.md before selecting retention destinations.
Use only authorized hosts and project-owned paths. Preserve other agents' files.

Size known project roots first with bounded du summaries. Write results after each host.
Use sudo for root-owned project volumes. Label incomplete permission-limited totals as lower bounds.
Do not recursively stat every file before reporting the largest directories.
Stop an overbroad owned scanner before replacing it. Preserve its interruption record.

Select large obsolete paths before collecting detailed reference information.
Scan processes and mounts once per host, then map references to selected paths.
Include root processes, executable paths, working directories, open descriptors, and mapped files.
Recheck exact selected paths and live references immediately before deletion.

Keep current deployments, active builds, required runtime dependencies, and unresolved failure evidence.
Identify dependencies through qualified manifests and actual resolved paths, including graphics wrappers and libraries.
A historical mounted test volume is not automatically active work.
Map its backing image and live references before proposing unmount and loop-device removal.
Never remove a mounted filesystem tree recursively.

Delete obsolete reproducible outputs when concise retained summaries are sufficient.
Do not archive every historical log or complete build tree.
Retain useful artifacts centrally with source pins, manifests, and checksums.
Verify destination bytes before deleting a required source copy.
Keep disposable scratch on the authorized scratch disk.

Review exact absolute deletion paths and preserve the cleanup plan externally.
Check resolved paths, ownership, filesystem identity, and current mount state.
Use standard fd-safe removal, such as shutil.rmtree. Do not write a custom recursive remover for that operation.
Record each completed deletion and actual free-space change.
Resume interrupted cleanup from reconciled records rather than repeating the whole batch.
For partial removal, check the surviving paths and original journal. Do not require the original complete tree inventory.
If deletion fails, preserve the error and create the required machine-specific recovery script.

After verification, remove temporary extraction and packaging copies.
Keep test hosts limited to current runtime dependencies and active test data.
