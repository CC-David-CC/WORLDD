# Actor enrollment and credential issuance

identityd stores actor identities, proven enrollment requests, and signed credentials in its private SQLite database.
Actors and credentials have distinct random 32-byte identities.
A key belongs to at most one enrollment request and one credential in an issuer.
Several explicitly approved credentials can belong to one actor.
Human and agent kinds follow the same proof, approval, and authority rules.
The kind and display label do not grant capabilities.

This component extends issuer.proto with enrollment operations and typed credential claims.
Existing issuer-control fields keep their numbers and meaning.
AdminResult extends the production library interface to return policy or enrollment results.
No locked baseline transport or assertion changes.

## Proving a key

Call enrollment_begin with a public key, actor kind, and label.
The label contains between 1 and 256 UTF-8 bytes, without zero bytes.
Owner administration keys and issuer signing keys cannot serve as actor credentials.
Generate a separate actor key.
The response contains a SignRequest with purpose ENROLLMENT_PROOF.
Its EnrollmentChallenge binds the issuer, service incarnation, nonce, complete intent, lifetime, and identityd/enroll/v1 audience.
Sign those exact bytes and submit the nonce and proof through enrollment_complete.

The server verifies against the public key stored with the challenge.
It consumes each available challenge before proof verification or execution.
Administrative and enrollment proofs cannot cross protocols.
The two protocols share one bounded pool of 256 pending challenges.
Expiry uses the server's monotonic clock. Restart discards challenges but preserves enrollment records.

A valid new proof under MANUAL creates a PENDING record.
There is no actor or credential until approval commits.
The service permits at most 1,024 pending enrollment records.
A completed request can be recovered with another fresh proof for its exact original intent.
This returns the same enrollment identity and any stored credential.
Replaying a consumed proof remains invalid.
Changing the intent for an existing key returns ALREADY_EXISTS.
The enrollment request cannot supply a target actor identity or grants.

## Owner decisions

Use the existing owner challenge and execute protocol for EnrollmentRead or EnrollmentDecision.
Each read identifies one enrollment request. It requires an owner signature.
Each decision binds that request, the decision, an optional target actor, and the expected policy revision.
Use APPROVE_NEW to create an actor and its first credential.
Use APPROVE_EXISTING to attach a proven key to an explicitly selected existing actor.
Only APPROVE_EXISTING accepts target_actor_id.
The requested kind must match that actor. Its original label remains unchanged.
Use REJECT to retain a terminal rejected request without creating an actor or credential.

Only a pending record can receive a decision.
Duplicate approval and changes to a final decision return REVISION_CONFLICT.
A stale policy revision also returns REVISION_CONFLICT.
An unknown actor or profile, an invalid decision, or a failed transaction leaves the request pending.
Rejected keys cannot bypass that decision by resubmitting or changing their labels.
Rejection is distinct from revocation of an issued credential or actor.
Credential and actor revocation remain required components.

## Policy and bootstrap boundary

New requests under APPROVE_ALWAYS use the same issuance function as owner-approved requests.
They always create a new actor. Automatic enrollment cannot attach keys to an existing actor.
Changing policy does not approve existing pending requests or rewrite issued credentials.
Manual owner approval still requires the current policy revision.

The current resolver supports the immutable empty-v1 profile, revision 1.
This profile contains zero grants. It gives no administration, world admission, workspace access, observation, or execution authority.
Any other configured profile returns NOT_FOUND before issuance commits.
An automatic enrollment with an unavailable profile leaves no partial enrollment, actor, or credential.
An owner-approved request stays pending when profile resolution fails.
Nonempty bootstrap profiles and their grant records remain required M01 and M02 integration work.
Do not treat this limited resolver as completion of identity.bootstrap_profile.

## Signed credentials

The issuer signs an ActorCredential protobuf through the existing purpose-bound key library.
The signing purpose is ISSUER_CREDENTIAL. The audience is spatial/actor-credential/v1.
Claims bind issuer identity, actor identity, credential identity, key, kind, enrollment identity, profile revision, and policy revision.
SignedCredential retains the exact signed payload and proof.
Store these bytes once. Fresh recovery requests return the same bytes after restart.

Independent clients can verify signatures using the issuer's separately trusted public key.
The proof's own public-key field cannot establish issuer trust.
A valid signature alone does not establish current credential status, actor status, admission, or workspace authority.
Revocation checks, authenticated world sessions, and protected network transport remain required.
The service still uses its supervisor-owned inherited channel.

## Transactions and explicit migration

Issuance creates the actor when needed, inserts the credential, and finalizes enrollment in one SQLite transaction.
Failed issuance rolls back all three effects.
After an uncertain reply, prove the same key and original intent through a new challenge.
Read the durable result instead of replaying the old completion or owner decision.

New initialization creates schema version 2.
Version 1 stores issuer control only. Normal startup rejects it without migration.
Stop the supervised service before migration. Preserve its state and private keys.
Run identityd --migrate --state-dir /absolute/issuer-state.
This explicit operation checks the existing issuer identity and migrates version 1 in a transaction.
It adds actor, enrollment, and credential tables without changing the issuer key, owner, identity, or policy.
It cannot initialize missing state or repair an unknown schema.
Restart with identityd --state-dir /absolute/issuer-state.
Retain SQLite journals after a crash. Never replace the issuer key to repair a database.

## Evidence requirements

Use independent OpenSSL signing and verification, real process restart, and direct SQLite reads.
Test unknown profiles, stale approvals, unauthorized attachment, replay, cross-purpose proofs, and database write failure.
The existing 18 issuer checks and all P00-P10 acceptance cases must remain green.
Run ctest --test-dir build -V -R '^identity-enrollment$' on both hosts and both build profiles.
This component does not establish full M01, Spatial-M1, or physical power-loss durability.

## Qualification, 2026-10-07

Both hosts passed all 25 enrollment checks and the existing 18 issuer checks in Debug and ASan/UBSan.
Each build passed 294 total core checks, 51 harness checks, and all 151 P00-P10 acceptance cases.
There were zero acceptance failures, errors, skips, or unrun baseline cases.
The new tests first failed because the preceding production daemon rejected enrollment requests.
An isolated mutation replaced the independently stored verification key with the proof-supplied key.
The unrelated-key counterexample detected the resulting unauthorized enrollment.
Production source never contained that mutation.
All 119 source hashes match both hosts and reviewed local files.
Existing issuer assertions remain unchanged after extracting their shared fixture helpers.
The acceptance lock, locked assertions, core schema, oracles, and native witness remain unchanged.

Evidence is in evidence/identity-enrollment-2026-10-07.
Each host's debug/run.json and sanitize/run.json retain exact commands, source hashes, and XLibre server pins.
The full build uses tools/qualify_core.py --through P07 with the recorded private server.
The complete acceptance gate uses tools/with_test_display.py and tools/gate.py --through P10.
The adjacent p10-command.json files contain those complete commands.
Sanitizer qualification adds --sanitize --build-dir build-sanitize.
Source audits record installed OpenSSL package versions.

Only disposable test issuers were initialized or migrated. No actual David credential changed.
No host configuration or operator session changed.
Local-model review remains waived under David's hardware-failure instruction.
Next: persistent credential and actor revocation, then authenticated actor sessions and bootstrap-grant integration.
