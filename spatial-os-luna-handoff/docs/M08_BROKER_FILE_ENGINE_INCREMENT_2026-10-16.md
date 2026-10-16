# M08 Broker File Engine Increment

Status: source checkpoint only, UNQUALIFIED, and not integrated. No runtime test or probe ran on the coding host.

## Scope and authority

Workspaced owns the FileEngine and its pinned project-root descriptors. Worldd receives no root descriptor or host path. The broker derives the project binding from its trusted workspace/worktree registry. The private FileActionRequest rejects every caller-supplied authority component. Workspaced creates the only internal component: the immutable registered worktree ID from WorkspaceReply.worktree(). All cabinet presentations of that workspace share this scope. An empty-component workspace-wide grant covers this descendant scope. The component is neither a filename nor a representation ID. Public ActorCabinetRequest remains unchanged and has no workspace, principal, scope, or host-path field.

The broker validates version 1, exact 16-byte operation ID, workspace ID, principal identity, one action, size limit, caller scope absence, and workspace state. It checks the exact FILE_* right and workspace policy before project, Git, or storage metadata. It preserves FileEngine's fresh per-action check. Create does not require browse or read. WORKSPACE_ACCESS never substitutes for a file right.

## Root lifetime and effects

Workspaced pins verified registered worktree roots using openat2 beneath the worktree registry with no symlink, magic-link, or mount crossing. It records the root device and inode in the separate file journal. The broker keeps at most 64 engines and refuses exhaustion instead of evicting live handles. Cached roots are checked against their registered path and inode on each request. A removed, replaced, or rebound root becomes stale. The request never silently rebinds it. Worktree/Git verification occurs on cache creation; cached requests use registry state and inode checks to avoid repeating Git work.

Filesystem mutation syscalls pass through a per-step FileEffectExecutor. The broker thread performs authority checks, handle/cursor changes, journal sequencing, and result mapping. The worker executes only the selected syscall and returns a value result. Broker observers are pumped while waiting for file I/O and journal durability. FileEngine copy execution remains explicitly UNIMPLEMENTED for the separate asynchronous-copy increment. During-effect and during-copy file requests return the existing BUSY result before filesystem effects.

Registered project directories remain under the existing StorageOwner qgroup quota boundary. This adapter does not add a second quota system. StorageOwner inspect validates the registered project object when a root is first pinned. The durable broker journal records effect intent and outcome; it does not claim atomicity between SQLite and filesystem operations.

## Durable operation journal

The private broker state contains `file-operations/file-operations.db`, format version 1. Its SQLite connection is owned only by a dedicated journal worker. It uses DELETE journaling, FULL synchronous mode, 4 KiB pages, and 0600 database mode in a 0700 directory. The database is capped at 16,384 pages (64 MiB) and 65,536 operation rows. Capacity exhaustion fails closed before an effect. Rows are never pruned because pruning could permit old operation IDs to replay.

The key is exact `(workspace, operation ID)`. Each row stores the canonical trusted-request SHA-256, principal, worktree, root device/inode, exact right, engine incarnation, intent/complete/uncertain state, and a bounded outcome. It never stores file contents. A principal or request digest mismatch returns CONFLICT after fresh authority admission. An unresolved intent stays UNCERTAIN; the broker never automatically replays it.

A same-incarnation terminal retry reuses its stored reply after fresh authority. Across broker or engine incarnations, a stored terminal FileActionError, trash receipt, or permanent-delete acknowledgement remains a historical outcome. An old process-local object, directory, or cursor handle returns STALE_HANDLE with the operation ID for recovery. No stored outcome claims a new filesystem observation. A terminal journal write failure after a possible effect returns UNCERTAIN with the operation ID.

## Verification

Named source fixtures now cover authorized browse/read/create and exact retry; create-only authority without browse/read; wrong and revoked principals; caller scope override; workspace policy denial; missing, replaced, symlinked, and stale roots; stale handles after engine reincarnation; malformed and oversized envelopes; copy remaining UNIMPLEMENTED; observer failure while I/O is pending; client lost/invalid acknowledgement returning UNCERTAIN with the original operation ID; journal intent recovery, identity mismatch, and historical outcomes; active-copy BUSY without effects; and an actual StorageOwner/qgroup project quota rejection, including zero-byte or partial artifacts after a possible create effect. A BUILD_TESTING-only journal fault fixture creates a real file, fails the terminal journal boundary, requires UNCERTAIN with the exact operation ID, and verifies retry does not replay. The active-copy check runs inside the frozen-copy callback. These fixtures are source only and remain UNRUN.

Still required at the test-host gate: revocation during durable intent and cached lookup; revocation between effect steps; allocation/finalization failure after a real effect; stalled-I/O authority responsiveness; a real broker lost-ack transport case; and the full FileEngine/original-copy regression matrix. The existing FileEngine oracle source remains owned by its separate worker. All M08 runtime fixtures, cancellation probes, quota scenarios, journal crash/recovery checks, test imports, and acceptance cases remain UNRUN. All four compile-only profiles must be rebuilt after final source freeze. Builds compile source only and do not qualify behavior. The next test-host task must run these regressions on sb16 and sb16-2 after primary review and source freeze.

Review04 repair fixtures also cover omitted principal, strict UNCERTAIN recovery identity, lost acknowledgment with preallocated fallback, actual FileEngine descriptor ownership, negative browse executor results, rollback fsync routing, and typed qgroup errors. Runtime remains UNRUN.
