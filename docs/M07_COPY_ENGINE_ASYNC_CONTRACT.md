# M07 explicit workspace copy engine contract

Status: production implementation draft, UNQUALIFIED. The first actor-route project/history copy passed on sb16 Debug in frozen11a. The four focused cases also passed admission, retry, malformed-receipt fencing, source process continuity, and independent Git checks. The standalone journal regression passed on sb16-2. Multi-application collection exports, actual all-class registration/copy, Git-state changes, policy restart, recovery, sanitizer/OFF closure, and full qualification remain required. Earlier runtime failures remain preserved.

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
authorization binding column. The actor schema does not gain a catalog revision field. Every
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

The owner protobuf configuration carries a versioned catalog and named destination
profiles. Catalog entries use supported schema IDs: spatial.workspace.project-tree,
spatial.workspace.git-history, spatial.workspace.application-definition,
spatial.workspace.application-state, and spatial.workspace.environment-config.
Project, Git history, and environment use schema1.
Application definitions accept typed schema1 or configured collection schema2.
New application-state admission requires explicit configured collection schema2.
The earlier raw state schema1 is retained only for legacy journal decoding.
These schemas map to distinct registered owner object kinds. The currently supported
state contract is CRASH_CONSISTENT_AFTER_QUIESCENCE. Catalog and destination ceilings
cannot exceed 4 GiB and 200,000 entries. Larger copies need a separately reviewed
chunking design. A private initial query may use catalog revision zero to discover the
owner's current revision. The broker pins the returned revision in the operation intent
and requires it on later lookups. The owner exposes only selected registered entries.
The operator owns this configuration; owner changes require a stopped policy update.

The destination profile declares independent storage policy, execution-resource profile,
application profile, allowed classes, and byte/entry ceilings. The broker resolves its
execution-resource reference against current WorkspaceResources config and rejects
unknown profiles before accepting REQUESTED. The owner validates storage policy and
application-profile references. Destination policy never comes from source policy.
Use existing grant rights.
WORKSPACE_CREATE is scoped only to the reserved workspace-registry domain and checked
against the configured destination profile. It is independent of all source rights.
PROJECT_TREE copies the registered project root. It requires FILE_BROWSE and FILE_READ
at the workspace root, both with empty relative components, plus WORKSPACE_COPY_PROJECT.
The opaque storage object ID is never an authority path component. GIT_HISTORY
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
and manifest digest. The transport accepts only phases REQUESTED through UNCERTAIN.
ABORTED and UNCERTAIN require a known nonzero error code. Other phases forbid an error.
Destination IDs and manifest digests may appear only after VALIDATED, PUBLISHED, or
UNCERTAIN; a digest requires a destination ID. Unknown protobuf fields, wrong operation
IDs, invalid phase values, and malformed digests fence the private broker channel.

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

The draft adds payload quiescence to the trusted process supervisor.
The broker acquires a workspace-scoped lease that blocks new launches and Git mutations.
It records the complete tracked payload inventory as PID plus start-time identity.
It freezes only owned payload cgroups and verifies the same inventory remains frozen.
The supervisor, initializer, manager, broker, and issuer remain outside those freezes.
Existing application authority queries remain available. It snapshots
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

Authority tests cover separate create and source rights, workspace-root project read, selected
class rights, owner class ceilings, revision binding, fence changes, revocation before
each effect and before publication, and redacted status after revocation.

Storage tests cover malformed receipts, symlink and hardlink rejection, no host effects,
byte/entry ceilings, absolute deadline behavior, source PID continuity, destination
identity independence, crash/lost acknowledgment, restart reconciliation, and exact
uncertainty. Runtime qualification remains reserved to sb16 and sb16-2.


Private resource-policy clarification (2026-10-14): execution_resource_profile names an explicit
versioned BrokerResourceConfig.copy_profiles policy. It does not name a default application ceiling.
The selected independent policy is pinned privately and persisted with registry publication.
The current execution registry schema is v8 and requires explicit stopped migration from v7.
The v7 resource-policy table was introduced by the earlier unqualified source increment.
Existing v6 databases still require their explicit migration.
See M07_COPY_RESOURCE_POLICY_SCHEMA_V7_2026-10-14.txt for that historical lineage.
Schema v8 adds persistent configured-state and process bindings without changing assigned budgets.


## V2 configured-state source draft (unqualified)

The next source increment separates persistent configured state from each fresh application runtime.
It supports at most 32 configured application keys.
Each typed state contract explicitly selects data, config, or both.
Undeclared runtime subdomains remain fresh.
Owner mappings bind exact key, contract, object, and revision.
A live or uncertain prior instance blocks another writer for that key.
A validated terminal instance permits a fresh process with the same persistent state.
The owner reserves both first-state and runtime quotas before allocation.
The broker pins the independently selected storage policy and exact actor/profile quota intersection.
The owner checks that policy digest and ceiling before effects.
Both actual allocation receipts must match the pinned ceiling.

Application-definition schema2 stores a bounded configured collection.
Application-state schema2 declares contracts and references independently allocated companion objects after publication.
New raw application-state schema1 catalog admission is rejected before effects.
Schema1 exposed whole runtime roots and has no qualified state-copy lineage.
Legacy journal records remain decodable.
Unresolved legacy copy effects remain uncertain and are never automatically exported.
Existing owner catalogs require an explicit stopped schema migration to version6.
Existing execution catalogs require explicit migration to version8.
Operator registration of schema2 contracts remains a separate stopped catalog update.

One quiescence lease covers every selected mutable source object and all payload descendants.
The allocation and final validation bind each companion source, snapshot, new object, contract, digest, and count.
All companions share the absolute 300-second operation deadline.
Destination policies, grants, credentials, process identities, and budgets remain independently assigned.
Publication stores the original exact owner receipt.
Later configured-state quota transfers do not rewrite that receipt.
Fresh object inspection supplies current live state separately.
Copied workspaces resolve subsequent export catalogs from durable owner publication records and current state mappings.

Preflight12 compiled workspaced, worldd, and the storage owner.
Its journal target failed to link the new shared protobuf validator.
The failure remains preserved externally.
The next CMake increment links the normal spatial-scene framing implementation.
All V2 builds, fixtures, migration checks, state-copy checks, restart checks, and recopy checks remain unqualified.
The earlier 11a four-case Debug result qualifies only the original project/history pair.
The eleven-case V2 suite has a separate 900-second service profile.
That profile does not establish operation-deadline behavior.


The full engine CTest registration is workspace-copy-engine.
Build workspace-copy-engine-fixture before packaging that registration.
It depends on the real world, broker, issuer, process-scope probe, application initializer, both copy helpers, and generated transport bindings.
The test invokes run_workspace_copy_engine.py.
That launcher installs and verifies candidate helper bytes, then restores their exact prior state.
It verifies all eleven method results and every declared volume cleanup receipt.
It creates a unique evidence directory below the runtime SPATIAL_SECURITY_EVIDENCE parent.
The earlier direct workspace-copy-engine-admission registration is replaced.
The M07 qualification sequence must include workspace-copy-engine on both hosts in Debug and sanitizer profiles.
Copy journal, explicit migrations, worker/finalizer, authority/control, presentation, fault/recovery, and production-only closure checks remain separate required families.
This source registration does not record any V2 runtime pass.
