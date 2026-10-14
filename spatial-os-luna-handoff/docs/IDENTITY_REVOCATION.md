# Issuer revocation and fresh actor authentication

identityd now checks signed credentials against current issuer records.
It distinguishes actor identity, individual credentials, and the issuer's configured owner key.
Revocation persists in the issuer database. A signature alone cannot override that state.
This component supplies issuer authentication results. World-session integration remains required work.

## Current credential checks

credential_check accepts a SignedCredential through the supervisor-owned service channel.
The issuer verifies its purpose and signature with its own independently selected public key.
It checks the claims, issuer identity, audience, exact stored credential, actor, key, and enrollment record.
A valid signature for an unregistered credential is insufficient.
Malformed input, altered credentials, foreign issuers, and unknown records fail closed.

The resulting IdentityStatus identifies the actor and credential.
It reports actor_revoked, credential_revoked, active, and security_revision.
Only active=true permits further authentication. An omitted or false value cannot authorize a caller.
This result carries no workspace rights, admission, or execution grants.
It is a current service response, not a reusable credential or bearer token.
Consumers must use their protected issuer channel and verify each operation's current authority.
Do not expose trusted management methods as an untrusted network API.

## Fresh authentication

The consuming service selects an audience and a fresh 32-byte channel_binding.
The audience identifies the intended service or world. It is independent of the host name.
The binding identifies that connection or pending authentication context.
It is not a substitute for transport authentication or a claim that TLS is already implemented.
Call authentication_begin with those values and the issued credential.
The audience contains between 1 and 128 bytes, without zero bytes.

The issuer validates current credential and actor status before creating a challenge.
AuthenticationChallenge binds the issuer, service incarnation, nonce, credential, audience, binding, and lifetime.
Sign the exact SignRequest with the credential's actor key and purpose ACTOR_AUTHENTICATION.
Submit the nonce and proof through authentication_complete.
The issuer consumes the challenge before verification, then rechecks the current credential and actor status.
Only an active credential with a valid fresh proof produces AuthenticationResult.
The result derives actor identity from the registered credential. The caller cannot supply a replacement identity or grants.

The consuming service must match the returned audience and binding to its independently stored authentication context.
It must accept the result only from its issuer connection, not from a client-supplied message.
This result does not create a world session automatically.
World admission and scoped authorization remain separate checks.

All issuer challenge types share the same 256-entry bounded pool.
Each proof has one purpose. Enrollment, administration, and authentication cannot exchange challenges.
Monotonic expiry, consumed nonces, and service incarnation prevent replay and pre-restart completion.
Authentication does not survive process restart as a cached assertion.

## Owner-controlled revocation

The existing owner challenge supports IdentityReference reads and IdentityRevoke decisions.
Each reference selects CREDENTIAL_TARGET or ACTOR_TARGET and one opaque identity.
An owner signature binds the target, expected_security_revision, and reason.
The reason contains between 1 and 256 bytes, without zero bytes.
The reason remains in the private audit record. Status replies do not expose it.

The security revision starts at zero. It is independent of the enrollment-policy revision.
A stale expected revision returns REVISION_CONFLICT.
A missing target returns NOT_FOUND. Neither error changes the security revision.
A new revocation atomically increments the revision and inserts its durable record.
A fresh repeat for an already revoked target returns current status without replacing the first reason or incrementing the revision.
There is no implicit reinstatement operation.

Credential revocation disables only that credential.
Another valid credential for the actor remains usable, including its pending authentication challenge.
Actor revocation disables every credential belonging to that actor.
It also prevents attachment of another credential to that identity.
Other actors and their credentials remain valid.
Fresh enrollment recovery may return historical credential bytes. It never clears revocation.

After a successful commit, remove affected pending authentication challenges immediately.
Authentication completion also rechecks durable status before returning an identity.
A failed database write rolls back the revocation and revision together.
It does not invalidate unrelated or still-valid pending proofs.
After an uncertain reply, use a fresh owner read of the target status.

## Storage and migration

Schema version 3 adds security_state and revocation tables.
New issuers start at version 3.
Normal startup rejects versions 1 and 2 until explicit migration.
Stop the supervised issuer and preserve its state and private keys.
Run identityd --migrate --state-dir /absolute/issuer-state.
Migration from version 1 adds enrollment tables before security tables in one transaction.
Migration from version 2 preserves all actor, enrollment, and signed credential records.
Both paths preserve the issuer key, identity, owner, and policy.
Existing credentials start active because those earlier schemas had no revocation records.
Unknown schemas and mismatched keys remain errors. Migration cannot repair or replace them implicitly.
Restart with identityd --state-dir /absolute/issuer-state.
Retain SQLite journals after a crash. Never regenerate keys to bypass recovery errors.

## Qualification boundary

Tests use independent OpenSSL proofs, actual issuer processes, direct SQLite reads, and real aborted database writes.
They cover independently surviving credentials, complete actor revocation, stale decisions, replay, expiry, restart, and explicit migration.
The existing key, issuer, enrollment, and P00-P10 tests remain required on both hosts in both build profiles.

This backend does not yet create authenticated world sessions or notify a running world of revocation.
Immediate cancellation of held input, observation, and application control remains required world integration.
Grant delegation and dependent-grant revocation remain M02 work.
Protected network transport and nonempty bootstrap profiles remain unfinished.
Do not claim full credential revocation across the runtime from issuer-only results.
M01 and full Spatial-M1 remain incomplete.

## Backend qualification, 2026-10-07

Both hosts passed all 24 new revocation and authentication checks in Debug and ASan/UBSan.
Each build passed 318 total core checks, 51 harness checks, and all 151 P00-P10 acceptance cases.
There were zero acceptance failures, errors, skipped cases, or unrun baseline cases.
The initial counterexample failed because the preceding daemon rejected the new authentication operation.
An isolated mutation ignored credential revocation when computing active status.
The restart counterexample detected a new authentication challenge accepted for that revoked credential.
Production source never contained the mutation.
All 120 source hashes match both hosts and reviewed local files.
The acceptance lock, locked assertions, core schema, oracles, and native witness remain unchanged.

The existing enrollment assertions remain unchanged.
Their fixture helpers are shared with the new checks.
Their version-1 migration setup removes the later security tables to recreate the exact earlier schema.
The new version-2 migration check independently verifies preserved credential bytes, issuer key, policy, and subsequent authentication.

Evidence is in evidence/identity-revocation-2026-10-07.
Each host's debug/run.json and sanitize/run.json record exact commands, source hashes, and XLibre server pins.
Run focused checks with ctest --test-dir build -V -R '^identity-(issuer|enrollment|revocation)$'.
The full build uses tools/qualify_core.py --through P07 with the recorded private server.
The full acceptance gate uses tools/with_test_display.py and tools/gate.py --through P10.
Adjacent p10-command.json files preserve the complete acceptance commands.
Sanitizer qualification adds --sanitize --build-dir build-sanitize.
Source audits record installed OpenSSL package versions.

Only disposable test issuers were initialized or migrated. No actual David credential changed.
No host configuration or operator session changed.
Local-model review remains waived under David's hardware-failure instruction.
Authenticated world sessions and runtime-wide revocation remain unfinished.
