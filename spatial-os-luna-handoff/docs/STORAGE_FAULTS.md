# SQLite write-fault boundary

Worldd implements storage.next_write through a private SQLite VFS wrapper.
The wrapper forwards real file operations to SQLite's existing VFS.
An armed fault consumes the next positive-length xWrite call and returns SQLITE_FULL with errno set to ENOSPC.
This is an injected I/O failure. It does not fill a physical disk.

The request requires point storage.next_write, action error, and storage_error STORAGE_FAULT_ENOSPC.
The semantic acceptance API names the last parameter errno="ENOSPC".
Fault enablement still requires --fixture-faults on the trusted management daemon.
Normal daemons use SQLite's default VFS and reject fault arming.
No actor action can enable this mechanism.

## Scope and lifetime

Each fixture world registers a separate VFS name without replacing SQLite's default VFS.
The wrapper covers files opened through that world's SQLite connection, including its rollback journal.
It does not inject into the workspace broker or another world's connection.
The database closes before the wrapper unregisters.
The wrapper runs on the world daemon's SQLite thread.

Storage and commit faults share one arm limit.
A pending fault prevents another supported fault from replacing it.
Reads without writes and rejected edits leave the next-write fault pending.
The injected xWrite consumes it. A later valid write can succeed without restarting.
Armed state never enters persistent storage.

SQLite reports the failed write through its normal transaction machinery.
Worldd returns STORAGE_ERROR and acknowledges no durable change.
The failed transaction preserves the prior entity values and world revision.
A failed actor checkpoint preserves the live pose and the previous durable pose.
The actor can checkpoint again after the one-shot error.

## Interface references

The wrapper uses SQLite's documented [VFS interface](https://www.sqlite.org/c3ref/vfs.html)
and [file methods](https://www.sqlite.org/c3ref/io_methods.html).
It forwards locking, synchronization, file controls, shared-memory methods, and mapped reads.
It preserves optional methods and the supported method version.
Registration follows the [VFS lifecycle API](https://www.sqlite.org/c3ref/vfs_find.html).
The injected result uses SQLite's [SQLITE_FULL error code](https://www.sqlite.org/rescode.html#full).

## Verification and recovery

The focused storage acceptance case passed on both authorized hosts.
Six added checks cover failed create, update, delete, checkpoint, retry, fixture authority, and world isolation.
A native component witness runs sixteen VFS lifecycles against real SQLite files.
It verifies write-fault consumption, independent registrations, the unchanged default VFS, WAL forwarding, and database reopening.
No new acceptance schema or database migration is required.

Both hosts passed Debug and ASan/UBSan qualification with 216 core checks and 51 harness checks.
The P00-P09 gate reports 145 passes, one assertion failure, two errors, and zero skips.
Three P10 cases remain unrun. P09 and full Spatial-M1 remain incomplete.
All 108 source hashes match both hosts and the reviewed local files.
The acceptance lock, schemas, locked assertions, oracles, and native witness remain unchanged.
Evidence is in evidence/recovery-storage-2026-10-07.
Each host directory includes the failing baseline, focused checks, complete commands, logs, and source hashes.
Use ctest --test-dir build -V -R '^storage-faults$' for the focused checks.
The full commands remain in debug/run.json, sanitize/run.json, and their p09-command.json files.

Retain the database and journal after a failure. Let SQLite recover through normal startup.
Use the existing world supervisor and the same state directory.
Do not replay uncertain external Git or application effects as a consequence of a database error.
The workspace broker has a separate effect journal and recovery boundary.
See RECOVERY_BOUNDARIES.md for daemon commands and the remaining P09 requirements.
