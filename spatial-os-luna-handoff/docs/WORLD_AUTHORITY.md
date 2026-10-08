# Persistent world authority

This contract adds persistent world grants to authenticated local actor channels.
It preserves the P00-P10 fixture contract and its locked assertions.
It does not complete M02 or Spatial-M1.

## Provisioning

Create a private, owned authority directory with mode 0700.
Write a private WorldAuthoritySeed text protobuf outside Git.
Set protocol_version to 1.
Set authority_directory to the absolute directory path.
Set each root scope to WORLD and the existing world identity.
Specify actor references with the issuer identity and actor identity.
Include only explicit rights, delegation rights, and delegation depth.

Run worldd --state-dir STATE --init-authority SEED on the world host.
This command accepts no session, display, broker, fixture, or migration options.
It installs one to 64 roots in one grant transaction.
An invalid seed installs no partial roots.
A failed seed can leave an empty authority database for inspection.
Reinitialization requires an empty directory. It never overwrites an existing grant store.
No actor request can install root grants.

Set WorldIdentityConfig.authority_directory for the runtime.
Do not combine this setting with actor_grants or admitted_actor_ids.
The configured store must exist and match the current world identity.
Missing stores and mismatched domains fail startup.
An omitted setting retains the earlier static configuration for baseline fixtures.
Static configuration is not persistent Spatial-M1 authority qualification.

## Authenticated requests

WorldSessionRequest adds grants, delegate, and revoke operations.
All operations use the channel's verified issuer and actor identity.
The caller cannot supply an alternative principal or actor session token.

The grants operation lists only records whose subject matches the caller.
The default and maximum page size is 16.
Use the returned next_after cursor for another page.
An empty cursor ends pagination.
Pages report their authority revision. Separate pages are not one database snapshot.
Revoked records remain available to their subject for inspection.

Grant writes require an expected authority revision and a positive actor sequence.
Grant writes share the sequence counter with ordinary actor actions.
A consumed sequence cannot be replayed, including after a rejected grant operation.
The persistent store validates the parent, subject, domain, scope, rights, and depth.
Administration alone does not permit minting or delegation of other rights.
Foreign issuers and unknown recipients receive no grants through this adapter.

## Actor engine checks

The existing actor engine performs movement, observation, and object actions.
Its authority callback performs no external issuer I/O.
Before dispatch, worldd reads current status for the caller and candidate grant ancestors.
Delegation also checks the proposed recipient.
Each status request contains at most 128 actors.
The bounded store permits at most 4096 grant records.
All batches for one preparation must describe the same issuer security revision.
Older or inconsistent snapshots disable the session service.
Each batch uses the configured checked-client I/O deadline.

The adapter requires world-root scope for WORLD_ENTER, WORLD_NAVIGATE, and WORLD_OBSERVE.
Object manipulation uses WORLD_MANIPULATE at the scope components [room ID, object ID].
Scope components are opaque bytes. They are not path prefixes or SQL patterns.
Object actions also require observation, visible pixels, reach, and the existing object lease checks.
Narrower observation or navigation grants do not authorize a world-wide operation.

Public admission accepts registered actors without creating action grants.
Private admission requires an active WORLD_ENTER grant.
World grants never authorize workspace selection, Git administration, application control, or process execution.
Persistent world actors cannot observe native application surfaces through this adapter yet.
Workspace authority must authorize those surfaces in a later integration.
Static world geometry remains available through authorized observation.

## Revocation and recovery

Committed grant changes invalidate cached decisions.
Before the grant reply, worldd rechecks admission and cancels affected motion and object drafts.
Cancellation releases object leases. Safe stop and discard operations remain available.
Independent grants remain valid.
Revocation does not terminate workspace applications.

Issuer actor-revocation events invalidate ancestor authority even when that ancestor has no world session.
Credential revocation closes the affected credential session. It does not revoke that actor's grants.
In-progress authentication remains visible to the revocation handler during ancestor-status requests.
An observed credential revocation prevents that pending admission.
The issuer watcher fails closed on disconnect, invalid data, or an unavailable service.
Queued observation responses are discarded when observation authority disappears.
Partially transmitted responses require channel closure.

Grant records survive worldd restart.
Actor sessions, held input, motion, object drafts, and tokens do not resume.
Stop worldd before backing up its world and authority directories.
Keep both complete directories, their ownership, and their private permissions.
Restart with the same paths and world identity.
Do not restore an older grant database while clients rely on newer grants or revocations.

Issuer status and world effects remain separate transactions.
This change does not provide distributed revocation acknowledgement or an atomic issuer-to-world barrier.
Remote actor transport, workspace grants, bootstrap profiles, and full native acceptance remain required.

## Qualification

Run the world-authority CTest with the project XLibre test display.
The fixture uses real issuer enrollment, cryptographic proofs, production worldd, and persistent SQLite storage.
Native cases use the independent X client and real compositor.
Retain Debug and sanitizer evidence on sb16 and sb16-2.
Run the full P00-P10 baseline and harness checks.
Keep the acceptance lock and its assertions unchanged.
