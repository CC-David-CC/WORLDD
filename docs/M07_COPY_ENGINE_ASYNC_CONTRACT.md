# M07 explicit workspace copy engine contract proposal

Status: design for primary review. No full-engine actor or catalog API has been
implemented from this note.

## Public operation binding

WorkspaceCopyRequest remains the actor request. It carries the source workspace ID,
selected data classes, destination profile name, expected revisions, and 16-byte
operation ID. It never carries paths, storage object IDs, a manifest, or owner receipts.
begin stores the exact serialized request bytes and authenticated principal. The same
principal, operation ID, and byte-identical request resumes the same operation. Reusing
the ID with different bytes conflicts. Class validation rejects duplicates but never
sorts or rewrites actor bytes.

The request must include both PROJECT_TREE and GIT_HISTORY. It may request
APPLICATION_DEFINITION, APPLICATION_STATE, and ENVIRONMENT_CONFIG individually.
No request silently adds a class.

expected_source_authority_revision binds the grant-store revision returned by the
scoped source authority check. Fence incarnation and epoch are separate freshness
stamps. Each authority check must observe the same exact incarnation and epoch.
expected_source_policy_revision binds the workspace execution-policy store revision.
expected_destination_profile_revision binds the configured destination profile.
The trusted owner records the versioned catalog revision in its private immutable
operation intent. The actor schema does not gain a catalog revision field. Every
revalidation compares the current owner-config revision with that pinned intent.

## Owner catalog and profiles

Only the trusted storage owner configuration/private authenticated channel supplies
the versioned copy catalog and destination profiles. No new signer or self-enrollment
path is introduced. Catalog entries bind workspace/object identity, class, typed
schema ID/version, registered mutable source object, declared limits, and the
application-owned consistency contract. The storage owner creates immutable snapshots
of those mutable source objects for the copy operation. Unknown schema versions and absent entries
deny by default.

The operator config declares exportable application definitions, application state,
and environment configuration as typed registered domains. Application state remains
application-owned bytes; the engine does not infer file names or state semantics.
Known supported contracts include their version and consistency level. Unsupported
contracts deny explicitly. Environment records contain reproducible data and reject credential-bearing fields
by schema. Typed application definitions and state exclude separately registered
credentials, grants, delegation, leases, tokens, process metadata, application
instances, input, caches, and autostart directives. Authorized project bytes remain
arbitrary user data; the copy engine does not guess secret filenames or claim to scan
and redact secrets from project content.

The destination profile separately declares its execution policy, storage roots,
resource ceilings, application policy, and catalog revision. Use existing grant rights.
WORKSPACE_CREATE is scoped only to the reserved workspace-registry domain and checked
against the configured destination profile. It is independent of all source rights.
For PROJECT_TREE, every project ancestor needs FILE_BROWSE, the exact project object
needs FILE_READ, and the source workspace needs WORKSPACE_COPY_PROJECT. GIT_HISTORY
needs WORKSPACE_COPY_GIT on the source workspace. APPLICATION_DEFINITION needs
WORKSPACE_COPY_DEFINITION, APPLICATION_STATE needs WORKSPACE_COPY_STATE, and
ENVIRONMENT_CONFIG needs WORKSPACE_COPY_ENVIRONMENT, each scoped to the source
workspace. The trusted owner class ceiling and registered schema are checked as
additional default-deny conditions. Actor admission, selection, or presentation grants
none of these rights.

## Async phases and durable effects

Actor begin durably inserts REQUESTED and returns an operation ID and status without
waiting for the copy deadline. The world actor exchange must stay within its normal
short request budget. The broker advances phases on its existing event loop and polls
bounded worker jobs. status is also prompt and requires the same authenticated
principal and fresh authority. While authority is revoked, status omits destination ID
and manifest digest.

All SQLite owner access stays serialized on its existing owner thread. No shared
NOMUTEX SQLite handle crosses worker threads. Workers receive immutable protobuf
job inputs and pinned descriptors only. One absolute 300-second operation deadline starts when begin accepts the request.
It includes queue time, quiescence, source resolution, snapshots, worker setup, copying,
validation, and publication. Persist the boot ID and monotonic start. On a same-boot
restart, enforce the remaining deadline. After a host reboot, operations with unresolved
effects become UNCERTAIN until exact receipts reconcile; do not reset their budget or
repeat an unproven effect. ProcessScope cleanup has its own bounded interval.

Every external allocation, snapshot, worker launch, seal, validation, and publication
has a durable effect intent before it can happen and an exact owner receipt after it.
A lost acknowledgment, worker death, or unprovable receipt becomes UNCERTAIN. Restart
reconciliation compares registered object IDs and exact receipts. It does not guess,
delete, publish, or duplicate a workspace. SQLite does not make Btrfs, Git, filesystem,
and registry effects atomic.

## Coherent capture and data classes

The current tree has no workspace-quiescence API. Full-engine implementation must add
one to the trusted process supervisor before it can claim coherent multi-domain capture.
The broker acquires a workspace-scoped lease that blocks new launches, records the
complete tracked process inventory as PID plus start-time identity, freezes each owned
workspace process cgroup, and verifies the same inventory remains frozen. It snapshots
all selected mutable domains while that lease remains held, then releases only the
freezes and launch barrier acquired by that lease. It never sends signals to unrelated
processes or terminates source jobs. Snapshot objects are immutable; catalog entries
still identify registered mutable owner objects. If enumeration, freeze, identity
recheck, snapshot, or thaw fails, the supervisor returns a typed conflict/uncertainty
with the exact process receipt and restores every freeze it can prove it acquired.
After reboot, unresolved leases become UNCERTAIN. Separate sequential Btrfs snapshots
alone do not prove cross-domain consistency.

An application-state domain is eligible only when its registered application contract
establishes the declared consistency level under this quiescence boundary. If the
application cannot quiesce or export consistently, copy fails explicitly.

The project snapshot preserves dirty, staged, and untracked bytes. Git history and
administration data are copied into a fresh independent repository and worktree. The
destination has no alternates or hardlinks to source objects. It never forces a branch
into a second writable worktree. Definition, state, and environment domains retain
their versioned schema identities and get independent destination storage identities.

## Publication and postconditions

Before the publication effect, the broker revalidates source authority, source policy,
destination profile, the private catalog revision, and the stable fence stamp. It writes
a durable publication intent before the external owner call. That call can yield. A
successful owner call must return an exact receipt that the journal stores. If authority
changes while publication is in flight, the broker records the actual receipt and
reconciles the resulting operation without claiming rollback or cross-system atomicity.
A missing or mismatched receipt is UNCERTAIN. SQLite publication of the workspace
registry and the Btrfs storage effect are separate recoverable operations.

A successful copy receives a new workspace ID, new repository/worktree/admin links,
independent state and configured destination policy. It has no processes, surfaces,
input state, credentials, grants, delegation, memberships, leases, tokens, or copied
budgets. It is not made available, selected, presented, or autostarted. Later ordinary
authority operations assign destination grants. The source PID/start identities remain
unchanged.

## Required regression families

Tests must exercise byte-identical retry and mismatched operation IDs, each of the five
classes, dirty/staged/untracked Git state, fresh repository/admin links, default-deny
catalog behavior, independent destination policy, and zero destination processes.

Authority tests cover separate create and source rights, all project ancestors, selected
class rights, owner class ceilings, revision binding, fence changes, revocation before
each effect and before publication, and redacted status after revocation.

Storage tests cover malformed receipts, symlink and hardlink rejection, no host effects,
byte/entry ceilings, absolute deadline behavior, source PID continuity, destination
identity independence, crash/lost acknowledgment, restart reconciliation, and exact
uncertainty. Runtime qualification remains reserved to sb16 and sb16-2.
