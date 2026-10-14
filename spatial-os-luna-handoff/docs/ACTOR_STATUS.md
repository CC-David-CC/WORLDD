# Current actor status for authority checks

The grant store identifies subjects by issuer identity and actor identity.
It must check each ancestor without collecting that ancestor's credentials or private keys.
The issuer now provides a bounded service query for this check.
This query is a prerequisite for authenticated grant-store integration.
It does not connect the store to worldd or workspaced by itself.

## Protocol

Use IssuerRequest.actor_status with ActorStatusRead from proto/spatial/identity/v1/issuer.proto.
The issuer advertises identity.actor-status/v1.
Use the existing protected issuer service transport.
Service certificates permit service communication. They do not grant actor or administrator authority.
The trusted inherited local issuer channel uses the same handler.

Supply between one and 128 distinct 32-byte actor identities.
Empty batches, duplicates, wrong lengths, and unknown protobuf fields are invalid.
The issuer preserves request order.
The reply contains the pinned issuer identity, one security revision, and one result per requested actor.
Each result contains its actor identity and three flags: known, revoked, and active.

- An enrolled, unrevoked actor is known and active.
- A revoked actor is known and revoked, but inactive.
- An unknown actor has all three flags false.

Active means known and not revoked.
Pending enrollment identities, enrollment public keys, and credential identities are not actor identities.
Humans and agents use the same status rules.
The query returns no labels, keys, credentials, workspace memberships, or grants.

All statuses and their security revision come from one database transaction.
The query does not change state or create an actor.
Unknown actors do not make the whole batch fail.
Storage failures return typed errors. They never produce an active fallback.
Issuer database schema remains version 3. No migration is required.

## Credential and actor revocation

Credential revocation disables that credential.
It does not revoke the actor identity or grants assigned to that actor.
Actor revocation makes the actor inactive for every credential and dependent grant chain.
Reads observe committed revocation. Rolled-back revocation does not change status.
Committed status survives issuer restart.

Actor status does not prove possession of an actor key.
It cannot replace enrollment, fresh authentication, credential checks, admission, or scoped authorization.
A runtime must still authenticate the requesting actor and check its current credential.
It must also check the actors in each candidate grant chain.
Do not keep a positive status indefinitely or use it after an identity-service failure.

Batch results are point-in-time observations.
They do not make later grant, filesystem, process, or input effects atomic with issuer state.
Multiple batches do not automatically form one snapshot.
The runtime adapter must reconcile their revisions and the revocation feed.
Live cancellation and distributed revocation acknowledgement remain separate required work.

## Checked client

IssuerClient matches the response to the exact request and configured issuer pin.
It checks the count, order, actor identities, flag consistency, and security-revision range.
It also rejects unknown fields, malformed protobuf, and wrong outcome types.
An invalid successful reply disables that client connection.
The client does not reconnect or replay the request automatically.
A valid typed rejection keeps the connection usable.

Use the production client for runtime integration.
Do not substitute an unchecked HTTP call or trust response-provided identities as their own authority.
An older issuer rejects this additional operation. The caller must handle that failure explicitly.

## Evidence boundary

Tests use real identityd, real enrollment proofs, owner-authorized revocation, and the checked TLS client.
They also use independent TLS peers to submit malformed responses.
Those peers test response validation. They do not simulate issuer authority or a world.
SQLite witnesses verify read-only behavior and failed-transaction rollback.
Restart tests verify durable actor revocation.
Mutation tests must detect ignored actor revocation and an ignored issuer pin.

This component does not yet enforce world or workspace grants.
Persistent domain provisioning, live action checks, nonempty bootstrap profiles, and cancellation remain required.
M01, M02, and Spatial-M1 remain incomplete.
