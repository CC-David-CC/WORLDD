# Persistent issuer control

identityd owns one persistent issuer identity and its enrollment-policy configuration.
Its state is independent of worlds, workspaces, host names, and actor session tokens.
This document defines issuer control. ENROLLMENT.md defines the added credential-issuance component.

## Initialization and storage

Create an empty private state directory with mode 0700.
The directory must belong to the service user. Its final path component cannot be a symbolic link.
Generate the owner's key separately with spatial-key or an interoperable Ed25519 tool.
Save the framed public KeyReply from spatial-key public to an explicit public-key file.
Keep the owner's private key outside the issuer state directory.

Initialize with:

~~~sh
identityd --init --state-dir /absolute/issuer-state --owner-public /absolute/owner-public.pb
~~~

Initialization is a trusted local operation. There is no initialization method in the wire protocol.
It creates a distinct issuer signing key, random issuer identity, and SQLite identity.db.
The configured owner public key controls issuer administration.
Owner and issuer private keys are never sent through the service protocol.
The initial policy is MANUAL with the empty-v1 bootstrap-profile reference and revision 1.
Initialization never replaces existing state or credentials.

Start the existing issuer with:

~~~sh
identityd --state-dir /absolute/issuer-state
~~~

Normal startup cannot generate a missing key or initialize a missing database.
It verifies the issuer key against the durable public key.
It rejects unknown database schemas and unsafe file ownership, permissions, links, or file types.
The initial issuer-control database used schema version 1 and application identifier 0x53504944.
Enrollment adds schema version 2 through the explicit migration described in ENROLLMENT.md.
One process holds an exclusive nonblocking [flock](https://man7.org/linux/man-pages/man2/flock.2.html) on identity.lock.
This establishes one local authority for that state directory. It does not implement distributed consensus.

Policy writes use actual SQLite transactions with synchronous=EXTRA and DELETE journaling.
The database format uses SQLite's [application identifier and user version](https://www.sqlite.org/pragma.html#pragma_application_id).
No world or execution database migration is required.

## Protobuf service

The schema is proto/spatial/identity/v1/issuer.proto.
The service uses inherited stdin/stdout with four-byte length framing and the one-MiB transport limit.
The supervisor owns this channel. No network listener is installed.
Network transport authentication and world-session integration remain required work.

Requests carry protocol_version 1 and a request_id.
Replies preserve that request_id, including typed errors.
Malformed protobuf returns INVALID_ARGUMENT with request_id 0.
An oversized or truncated frame terminates the transport.
Unknown protobuf fields and enum values are invalid.

Hello reports the production implementation, source-derived build identity, and identity.issuer-control/v1 capability.
Info returns the issuer's public identity, public key, and current policy revision.
It does not return the enrollment profile or any private key.
Policy reads and writes require proof from the configured owner key.

## Owner challenges

Submit the intended IssuerOperation through challenge.
The service validates its shape before allocating a challenge.
The response contains a nonce and a SignRequest with purpose ISSUER_ADMINISTRATION.
Its exact payload is an IssuerChallenge protobuf containing:

- Protocol version and persistent issuer identity.
- Random service incarnation and random 32-byte nonce.
- The configured owner public key.
- The complete server-stored operation.
- Challenge lifetime and the identityd/admin/v1 audience.

Sign the exact returned SignRequest using the owner's private key.
Submit the nonce and KeyProof through execute.
The service chooses the stored owner key independently of the proof's claimed public key.
It executes only the operation stored with that challenge.
The caller cannot supply a replacement operation during completion.

Each well-formed completion consumes its available challenge before signature verification or execution.
A wrong proof, stale revision, or storage failure cannot leave that challenge reusable.
Malformed envelopes are rejected before operation dispatch.
Signatures with altered purposes, payloads, keys, issuers, or challenge nonces cannot authorize the operation.

Use the server's monotonic clock to enforce expiry.
The default lifetime is 30,000 milliseconds.
Trusted startup can set --challenge-ttl-ms between 50 and 60,000 milliseconds.
There is no actor request that changes the clock or lifetime.
At most 256 unexpired challenges may be pending. Capacity exhaustion returns BUSY.
Expired challenges are removed before new challenges and completions.
Restart generates a new incarnation and discards all pending challenges.

## Policy revisions and scope

PolicySet includes the complete EnrollmentPolicy and a positive expected_revision.
The mode must be MANUAL or APPROVE_ALWAYS.
The bootstrap_profile value is a nonempty opaque identifier, at most 128 bytes, with no zero byte.
It is a reference, not a grant, path, credential, or administrator capability.
Issuer control stores this reference. Enrollment resolves it before issuance.
The current resolver supports only empty-v1, which grants no authority. Nonempty profiles remain required work.
The enrollment component must resolve a configured profile before issuance and reject an unavailable profile.
Automatic enrollment must never infer grants from a profile's name.

An accepted policy update commits once and increments its revision once.
Two operations with the same prior revision cannot both succeed.
Rejected requests leave the durable policy unchanged.
A lost reply is resolved by a newly authenticated policy read. Do not replay a consumed challenge.
Changing policy does not retroactively alter grants issued under an earlier profile.
Grant issuance and its independent revocation records remain required work.

## Recovery and limits

Preserve issuer.pem, identity.db, and any SQLite journal after a crash.
Restart against the same state directory. Do not delete journals to force startup.
Acknowledged policy changes survive process death through the real database commit boundary.
Key creation and database initialization are separate durable effects.
Partial initialization fails closed and retains its files for explicit recovery.
Never regenerate a signing key to repair a missing or mismatched database automatically.
Keep the owner's private key in its separate private directory.

The host administrator remains trusted. File ownership checks do not defend against that administrator.
These checks do not establish physical power-loss durability, remote transport security, enrollment, or actor authorization.
M01 and the complete Spatial-M1 profile remain incomplete.

## Verification

The issuer checks use independent OpenSSL signatures and direct SQLite reads.
They exercise real daemon restart, SIGKILL, process locks, monotonic expiry, file permissions, replay, and revision conflicts.
Run them with ctest --test-dir build -V -R '^identity-issuer$'.
The existing P00-P10 acceptance gate must remain green on both hosts in Debug and ASan/UBSan.
No actual David key or issuer is initialized by these disposable tests.

## Component qualification, 2026-10-07

Both hosts passed all 18 issuer checks in Debug and ASan/UBSan.
Each build passed 269 total core checks, 51 harness checks, and all 151 P00-P10 acceptance cases.
There were zero acceptance failures, errors, skipped cases, or unrun baseline cases.
The initial missing-daemon failure and all final commands are retained in evidence/identity-issuer-2026-10-07.
An isolated mutation bypassed owner verification while a challenge was fresh.
The outsider-key counterexample detected the resulting unauthorized policy read.
The mutation never entered production.
All 118 source hashes match both hosts and the reviewed files.
The existing acceptance lock, assertions, core schema, oracles, and native witness remain unchanged.

The full build commands use tools/qualify_core.py --through P07 with the recorded private XLibre server.
Then tools/gate.py --through P10 runs through tools/with_test_display.py.
Sanitizer qualification adds --sanitize --build-dir build-sanitize.
Each host's debug/run.json and sanitize/run.json record exact arguments, source hashes, and server pins.
The adjacent p10-command.json files record the complete baseline gate commands.
Source audits record the installed OpenSSL package versions.

No host or operator-session change was required.
Stop only the supervised issuer process through its owned channel or service manager.
Restart with identityd --state-dir /absolute/issuer-state to recover the same committed identity and policy.
Request a new signed challenge after any restart or uncertain reply.
Do not initialize that directory again or replace its credentials.
Local-model review remains waived under David's hardware-failure instruction.
Enrollment, bootstrap grant issuance, revocation, and authenticated world sessions remained unimplemented at that qualification.
See ENROLLMENT.md for the subsequent enrollment interface and evidence.
M01 and full Spatial-M1 remain incomplete.
