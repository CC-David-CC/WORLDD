# M07 immutable snapshot copy worker contract

This increment adds a private data-copy stage below the actor interface.
Actors provide only an operation ID and opaque registered object IDs.
The storage owner resolves every source snapshot and destination staging object.
The owner rejects mismatched operation IDs, digests, and object kinds.

The broker pins source and destination directory handles through the authenticated
storage client. It launches the compiled tree-copy helper inside the existing
bubblewrap and ProcessScope boundary. The source mount is read-only. The destination
mount is writable. The worker sees no network, unrelated host paths, or credentials.
The worker rejects traversal, devices, sockets, escaping symlinks, and hard links.
It enforces byte and entry ceilings and returns a content digest and actual byte count.

The storage owner records an immutable receipt keyed by operation ID, request digest,
source snapshot identity, and destination stage identity. An identical retry returns
the same receipt. A changed binding conflicts. A lost or incomplete result is marked
uncertain and requires explicit inspection; SQLite does not make the filesystem copy
atomic. Publication remains a separate journaled operation.

The helper runs with an absolute 300 second deadline and ProcessScope resource budget.
Source snapshots remain read-only. Destination stages use fresh storage identities.
Typed application catalog integration remains a later M07 increment.
