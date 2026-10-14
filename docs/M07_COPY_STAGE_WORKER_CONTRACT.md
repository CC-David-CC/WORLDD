# M07 immutable snapshot copy worker contract

This increment adds a private data-copy stage below the actor interface.
The actor-facing WorkspaceCopyRequest carries workspace IDs, selected data classes,
profile and policy revisions, and an operation ID. It carries no filesystem paths or
storage object IDs. The private broker resolves registered snapshot and staging IDs
for the storage owner.
The owner rejects mismatched operation IDs, digests, and object kinds.

The broker pins source and destination directory handles through the authenticated
storage client. It launches the compiled tree-copy helper inside the existing
bubblewrap and ProcessScope boundary. The source mount is read-only. The destination
mount is writable. The worker sees no network, unrelated host paths, or credentials.
The worker rejects traversal, devices, sockets, every symlink (including contained
symlinks), and hard links. Symlink copying is unsupported in this increment.

The broker supplies a versioned WorkerRequest in a sealed memfd. It carries only the
maximum byte and entry counts. The helper returns a four-byte big-endian length followed
by a versioned protobuf WorkerResult. Success carries actual counts and a raw SHA-256
digest. Failure carries a typed reason such as symlink, hard link, special entry, byte
limit, entry limit, or destination conflict. The broker rejects bad lengths, versions,
unknown fields, enums, counts, and digest sizes. The former ASCII result protocol is
superseded and was never a qualified component interface.

The storage owner records an immutable receipt keyed by operation ID, request digest,
source snapshot identity, and destination stage identity. The broker verifies every
receipt against the exact intent, opaque IDs, quotas, and kernel subvolume IDs from
the pinned descriptors. Any malformed binding fences the private channel. An
identical retry returns the same receipt. A changed binding conflicts. A lost or
incomplete result is marked uncertain and requires explicit inspection; SQLite does
not make the filesystem copy atomic. Publication remains a separate journaled effect.

The broker starts its 300 second absolute deadline before registered-object inspection,
descriptor pinning, and worker setup. Ancestry checks fail closed when descriptor
traversal cannot be proven. Cleanup has a separate bounded ProcessScope stop interval.
Source snapshots remain read-only. Destination stages use fresh storage identities.
Typed application catalog integration remains a later M07 increment.

The production probe provisions source and staging records through a live storage owner.
It runs only on sb16 or sb16-2, as root, with a private evidence directory.
The command is:
python3 tests/native/test_copy_worker_production.py BUILD_DIR SOURCE_DIR EVIDENCE_DIR

It packages the tested helper, runs the authenticated storage channel, checks
ProcessScope limits and private networking, verifies independent copied inodes, and
retries the same intent. The symlink case must leave an UNCERTAIN receipt and assert
the typed symlink rejection. Run the existing storage-snapshot-owner test for owner
restart recovery from COPYING to UNCERTAIN. Coding-host builds do not execute either
runtime test. Runtime evidence is pending the primary's test-host schedule.
