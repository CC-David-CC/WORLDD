# Persistent authority store

This component implements persistent grants for M02 and later bootstrap-profile integration.
It is a production C++ library. Its test adapter is not an actor endpoint.
Worldd and workspaced do not yet use this store for live authorization.
Static world grants and existing workspace memberships remain unchanged at this increment.
M01, M02, and Spatial-M1 remain incomplete.

## Ownership and trust boundary

Each store belongs to one world or one workspace.
The domain contains its kind and opaque persistent identity.
World and workspace domains remain independent, even when their identity strings match.
One process owns the store lock. A second authority-store instance cannot open it.
Call store methods from one authority thread.
Identity-check callbacks must not reenter the store.

The schema is proto/spatial/authority/v1/grants.proto.
The API is src/authority/store.hpp.
The authority owns grant records. They are not bearer capabilities or signed credentials.
Copying a record does not grant authority.

The caller must supply an authenticated actor identity from the checked session layer.
Actor identity contains both issuer identity and actor identity.
An actor with matching bytes from another issuer is a different actor.
The library does not authenticate actor references supplied by an untrusted request.
The caller must also supply a current identity-status checker.
Every evaluated grant chain checks its subject identities through that checker.
Missing checkers fail. Checker exceptions propagate; they never grant authority.
Identity-check failure policy remains the runtime adapter's responsibility.

Install roots only through explicit trusted domain provisioning.
Do not expose install_root through ordinary actor transport.
Enrollment authority alone cannot install a domain root.
Runtime integration must pin each domain's provisioning authority.
That integration and nonempty bootstrap profiles remain required work.

## Scope and rights

A scope contains its domain and at most 32 opaque components.
Each component contains one to 256 bytes.
Compare components exactly. Do not parse paths, separators, wildcards, or regular expressions.
The sequence [room, cabinet] contains [room, cabinet, item].
It does not contain [room, cabinet-backup] or [room/cabinet].

Components identify resources in the owning authority's namespace.
They are not filesystem paths or filenames.
Future filesystem adapters must map checked resources into this namespace.
This component does not establish protection against filesystem alias or rename races.

World rights cover admission, navigation, observation, and spatial manipulation.
Workspace rights separate access, file operations, application observation, application control, and execution.
Permanent deletion is separate from recoverable deletion.
Policy administration is explicit within either domain.
World grants cannot contain workspace rights.
Unknown rights, duplicate rights, malformed identities, and unknown protobuf fields fail closed.

Rights do not imply other rights.
Administration does not imply observation, execution, or delegation of administration.
An empty rights set grants no operational authority.

## Delegation

Each grant has immutable terms and one optional parent grant identity.
Terms contain the subject, scope, held rights, delegable rights, and remaining delegation depth.
Delegable rights must be a subset of held rights.
Depth is between zero and 32.
Depth zero requires an empty delegable-rights set.

Delegation requires all these conditions:

- The authenticated caller is the parent's subject.
- The parent and all its ancestors remain valid and unrevoked.
- Each ancestor's subject remains active.
- The destination subject is active.
- The child's scope is within the parent's scope.
- Every child right is explicitly delegable by that parent.
- The child's remaining depth is lower than the parent's depth.

The store generates grant identities and derives issuer and parent fields.
It does not accept caller-selected grant identities or replacement parent records.
Each decision must have one complete valid chain.
Independent grants cannot be combined to manufacture a wider delegation chain.
The store revalidates every ancestor. Corrupt links, cycles, and missing ancestors produce storage errors.

## Revocation and transactions

A subject can relinquish its own grant.
A grant issuer can revoke the grant it issued.
A current policy administrator can revoke grants within that administrator's scope.
All revocation callers must have active identities.
Revocation does not grant the caller any right held by the revoked grant.

Parent revocation disables all descendants at the next store decision.
Independent grants remain usable.
The store does not cache positive decisions across calls.
Repeated revocation preserves the original revocation revision.

Writes require an explicit expected authority revision.
Stale revisions reject the whole write.
Grant creation, parent validation, revision changes, and record insertion share one SQLite transaction.
Revocation and its revision change share one transaction too.
Database failure rolls back the record and revision changes together.

This transaction covers the authority database only.
It does not make identity-service checks, filesystem operations, process effects, or X input atomic.
The store does not publish a runtime revocation feed or cancel active control yet.
Live cancellation and distributed acknowledgement remain mandatory integration work.

## Storage and bounds

Use an existing absolute private directory owned by the authority user.
Initialization requires an empty directory except for the authority lock.
Do not initialize implicitly while opening existing state.
Files must be private regular files with one link.
Reject final symlinks, foreign ownership, extra hard links, and group or other permissions.
Keep this directory outside workspace project mounts.

The database is authority.db, schema version 1, application ID 0x53504752.
Unknown schemas require a future explicit migration. Opening never rewrites them.
SQLite uses DELETE journaling and synchronous EXTRA.
Initialization fsyncs the directory after the schema commit.
Keep partial initialization for diagnosis if initialization fails.

SQLite's NOFOLLOW option rejects symbolic links anywhere in the database filename.
Use the kernel-resolved directory name and retain NOFOLLOW.
Check its device and inode against the opened private directory before and after opening SQLite.
See [SQLite's open interface](https://www.sqlite.org/c3ref/open.html).
These checks do not protect against a malicious trusted host administrator.

A store accepts at most 4,096 grant records, including revoked records.
Chains contain at most 33 records, including the root.
Exhaustion returns an error. Do not discard revocation history automatically.
Grant records are bounded to 16 KiB when read.
Time limits, resource budgets, quota consumption, and garbage collection are not implemented here.
Unknown future constraints cannot silently enter the current protobuf schema.

## Qualification boundary

The test adapter uses grants_probe.proto and is built only with BUILD_TESTING.
It forwards requests directly to the production library.
Its explicit fixture principals do not prove enrollment or transport authentication.
Existing identity-service tests provide separate evidence for those components.

Required tests include restart persistence, process death, transactional failure, and independent SQLite record inspection.
Authority tests include scope escape, rights expansion, sibling union, depth expansion, and administration separation.
They also cover ancestor revocation, inactive ancestors, independent grants, malformed records, and private storage.
Generated chains test transitive narrowing. They do not establish an exhaustive mathematical proof.
Qualification must retain the full P00-P10 baseline and the acceptance lock.
Native application confinement, live authority enforcement, and physical devices require their own evidence.
