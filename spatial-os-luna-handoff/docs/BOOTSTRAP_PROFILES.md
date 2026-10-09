# Enrollment bootstrap grants

Status: implementation in progress. Nonempty bootstrap grants are not qualified yet.

Enrollment applies only the profile selected by the issuer's current policy.
Manual and automatic enrollment use the same issuance implementation.
An actor cannot supply its profile, grant subject, or permissions through an enrollment intent.
Automatic enrollment must not issue a profile containing POLICY_ADMINISTER.
Manual approval may select administrative rights only when the destination domain separately authorizes them.

## Versioned issuer profiles

An issuer profile contains an opaque identifier and a bounded list of grant templates.
Each template has a distinct identifier and explicit grant terms without a subject.
The terms specify a world or workspace scope, held rights, delegable rights, and delegation depth.
Use the same scope and permission validation as the authority store.
Templates are requests for domain grants. They are not grant records or bearer capabilities.

Only signed issuer-owner operations can publish profiles.
Publication requires the current profile revision. Zero creates a new profile.
Every successful publication appends an immutable revision.
Each profile contains at most 32 templates and at most 32,768 serialized bytes.
Profile and template identifiers contain one to 128 bytes without zero bytes.
Old revisions remain readable. Publication does not rewrite existing credentials or grants.
Publishing the currently selected profile also advances the policy revision in the same transaction.
This invalidates pending manual approvals that referenced the previous policy revision.
Publishing an unrelated profile does not change policy.
The existing empty-v1 profile remains immutable at revision one with zero templates.

Issuance resolves the selected profile inside the enrollment transaction.
The signed credential records that exact profile identifier and revision.
Unknown profiles, invalid stored profiles, or disallowed automatic administration fail before issuance commits.
An automatic failure leaves no partial enrollment, actor, or credential.
A failed manual approval leaves the existing request pending.
Fresh proof can recover the original issued credential after restart or profile updates.
Approval for an existing actor must not replay already materialized grants or restore revoked grants.

Trusted service clients can read an exact profile revision through the existing authenticated issuer transport.
The checked client must validate the returned profile identity, revision, structure, and bounds.
Owner reads may request the latest revision through the signed administration interface.
Policy changes still use their existing compare-and-swap revision.
An unavailable configured profile continues to fail at issuance, preserving existing policy behavior.

## Domain authority and materialization

Enrollment authority alone cannot install a world or workspace root grant.
Each destination domain must explicitly authorize its accepted profile revision and template under a local parent grant.
The issuer's requested terms must fit that authorized delegation.
The domain derives the recipient from a currently valid, registered signed credential.
It validates the issuer, profile revision, template, parent grant, scope, rights, and current actor status.
The new grant records the real parent. Existing delegation checks enforce attenuation.
Parent revocation invalidates its dependent bootstrap grants. Independent grants remain valid.

Materialization must be idempotent and durable within the owning authority store.
An enrollment retry, reconnect, second credential, or service restart must not duplicate or revive a grant.
Record the materialization identity and resulting grant in the same domain transaction.
Do not claim one atomic transaction across the issuer, worlds, and workspaces.
Report pending or failed domain application explicitly. Do not invent successful grant delivery.
An unavailable or unconfigured domain grants nothing.
Profile updates must not silently replace existing permissions.

## Domain transaction contract

The store implementation is under production qualification. Authenticated domain adapters remain pending.
Store methods alone do not establish working grant delivery.

Keep accepted bindings and materialization receipts in the domain authority database.
A binding identifies the issuer, exact profile revision, template identifier, and parent grant.
Store the checked template terms with the binding.
The authenticated parent holder must authorize the binding through the normal actor authority path.
Validate the same scope attenuation, delegable rights, and depth rules used by ordinary delegation.
Administration alone does not authorize creation of a binding that exceeds held delegation rights.

Before materialization, check the registered credential with its issuer.
Match the returned actor and credential identities to the authenticated requesting actor.
Use the profile identifier and revision from that checked credential.
Fetch that exact profile through the checked client.
Require its template to match the accepted binding.
Refresh the parent chain identities before the domain transaction.
Recheck current revocation state before committing or routing a consequential action.

Within each domain, identify a receipt by issuer, actor, profile identifier, and template identifier.
Exclude credential identifiers, connection identifiers, and profile revisions from this uniqueness key.
Those values must not provide a way to duplicate or revive an existing bootstrap grant.
Record the actual profile revision, parent, and resulting grant alongside the receipt.
Insert a new receipt and its delegated grant in one transaction.
Use the ordinary grant ancestry and attenuation checks for the insertion.

An exact retry returns the original grant record without advancing the authority revision.
A revoked grant stays revoked. Its receipt remains durable.
A changed revision, template, or parent cannot replace a previously materialized grant through enrollment.
Report that conflict explicitly. Use ordinary authorized delegation for a deliberate later permission change.
Reject corrupt or missing receipt targets instead of creating replacement grants.
Do not bypass an exhausted grant limit or a failed receipt write.

Required counterexamples include receipt-write rollback, retry after restart, second credentials, and updated profiles.
Also cover revoked parents, revoked recipients, insufficient delegation depth, cross-domain terms, and mismatched profile replies.
Finally test real world admission and workspace operations, including independent surviving grants.

## Implementation and evidence sequence

First implement the issuer registry, immutable revisions, and shared enrollment resolution.
Then implement checked profile retrieval and explicitly authorized domain materialization.
Finally exercise real world and workspace actions under those grants and their revocation.
The registry alone does not complete identity.bootstrap_profile, M01, or M02.
The new issuer.proto messages are additive. Existing field numbers and credential bytes remain valid.
The core protobuf schema, acceptance lock, baseline assertions, and native witness remain unchanged.

Use independent actor signatures and real persistent issuer and domain stores.
Test manual and automatic enrollment, forged or stale owner operations, unknown profiles, and changed revisions.
Test malformed rights, administration rejection for automatic enrollment, restart recovery, and failed transactions.
Test parent revocation, independent surviving grants, retries, duplicate credentials, and unavailable domains.
Run the existing enrollment, authority, native action, and cumulative acceptance suites on both hosts.
Keep physical-session, provider, and complete Spatial-M1 qualification separate.

## Explicit issuer storage migration

The implementation uses issuer schema version four.
Version four adds immutable profile history to the issuer database.
Normal startup must refuse older versions until identityd --migrate is explicitly requested.
Migration must preserve issuer keys, owner identity, policies, actors, enrollment records, credentials, and revocations.
It must be transactional and repeatable on an already current database.
The immutable empty-v1 profile retains the meaning of all previously issued credentials.

Stop the issuer before backing up its complete private state directory.
Preserve the backup outside Git with the original private ownership and permissions.
Run `identityd --state-dir /absolute/private/issuer-state --migrate` with the new binary.
The migration requires exclusive ownership of that state directory.
Do not run the old binary against the migrated database.
For rollback, stop the issuer and restore the complete saved state with its matching binary.
No existing operator issuer directory has been migrated by these fixture tests.

Existing migration fixtures must remove the new profile table when constructing a historical database.
Their latest-schema assertion changes from three to four.
Their data-preservation, authentication, and explicit-migration assertions remain unchanged.
No locked acceptance assertion, oracle, or native witness needs to change.

## Current implementation checkpoint

Issuer publication, profile history, enrollment resolution, and checked TLS retrieval are qualified on both hosts.
Each Debug and runtime ASan/UBSan profile passed 941 core checks, 51 harness checks, and 151 P10 cases.
No final profile failed or skipped a case. Both final 212-file source audits passed.
The separate native fixture correction is committed at 388d500. Original failed reports remain preserved.
An isolated domain-store candidate passed 43 existing and 32 new tests in Debug and runtime sanitizers on sb16.
It includes the production migration command. It does not yet connect authenticated world or workspace actions.
Issuer integration completed at 9e21cdf on both hosts, with matching source hashes.
The domain store passed complete Debug and runtime sanitizer qualification on both hosts.
Each profile passed 973 core checks across 50 suites, 51 harness checks, and 151 P10 cases.
No final profile failed or skipped a case. All 214 tested source hashes match.
Domain commit c470a4e is pushed to box-git and adopted on both hosts, with unchanged tested source hashes.
The isolated world adapter passed 81 regression checks and six additional cases per sb16 profile.
Both focused Debug and runtime sanitizer profiles passed. Full cumulative and second-host adapter qualification remain pending.
Workspace delivery remains unimplemented. Neither adapter is integrated into production.
These results do not complete bootstrap delivery, M01, M02, or Spatial-M1.

## Explicit domain storage migration

The domain store uses authority schema version two.
Version two adds accepted bootstrap bindings and durable grant receipts.
Normal startup refuses version one until an explicit migration succeeds.
Migration preserves domain identity, authority revision, existing grants, and revocations.
Failed migration rolls back its schema changes. Repeating a completed migration is safe.

Stop the world or workspace authority that owns the store.
Back up its complete private authority directory outside Git.
Preserve private ownership and permissions on the backup.
Run `spatial-authority-migrate --state-dir /absolute/private/authority --domain-kind world --domain-id WORLD_ID` for a world.
Use `--domain-kind workspace --domain-id WORKSPACE_ID` for a workspace store.
The command refuses a live store lock or a mismatched domain identity.
For rollback, stop the authority and restore its complete backup with the matching old binary.
Do not run the old binary against the migrated store.
These tests migrate temporary fixtures only. No operator authority directory has been migrated.
