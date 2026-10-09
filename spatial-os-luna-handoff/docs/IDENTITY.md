# M01 identity contract

M01 replaces fixture identity assumptions with cryptographic actor enrollment and authentication.
Humans and model agents use separate keypairs and the same authority rules.
David controls the initial issuer. Enrollment never implies administrator authority.
Key material, issuer control, and manual enrollment are implemented and qualified as separate components.
See ISSUER_CONTROL.md and ENROLLMENT.md for their interfaces, evidence, and explicit migration.
Manual and automatic issuance now pin immutable enrollment profiles. See BOOTSTRAP_PROFILES.md for the qualified issuer and domain stores.
Issuer-side credential and actor revocation and fresh authentication are qualified. See IDENTITY_REVOCATION.md for their limits.
The issuer TLS service listener is qualified. See ISSUER_TLS.md for its interface and limits.
The production checked issuer client is qualified. See ISSUER_CLIENT.md for its interface and limits.
The ordered revocation feed and bounded watcher are qualified. See ISSUER_WATCH.md.
Authenticated local world channels and native credential cancellation are qualified. See WORLD_IDENTITY.md for their limits.
Persistent world grants now govern authenticated local actions. See WORLD_AUTHORITY.md.
Persistent workspace authority is qualified. See WORKSPACE_AUTHORITY.md for its evidence and limits.
Authenticated bootstrap delivery remains an isolated candidate under qualification. See BOOTSTRAP_DELIVERY.md for its current status.
Remote actor transport and distributed revocation acknowledgement remain required.

## Required integration

The production issuer owns persistent actor and credential records independently of world identity.
An actor identity is not a public key, credential, host name, or session token.
Several approved credentials may refer to one actor. Different actors cannot share a credential key.
Keep issuer private material separate from actor private material and workspace project data.
Never copy credentials during workspace copying.

The issuer supports manual approval and an owner-controlled approve_always policy.
Both policies use one issuance implementation and the configured bootstrap grant profile.
Automatic enrollment cannot grant administration or attach a new key to an existing actor implicitly.
Changing the profile does not rewrite previous grants silently.
Credential revocation disables that credential. Actor revocation disables all its credentials and dependent active sessions.
An independently valid credential or grant survives unrelated revocation.
M02 supplies grant delegation and scoped authorization. Cryptographic proof alone grants no workspace or world authority.
The persistent grant-store library is qualified separately. See AUTHORITY_STORE.md.
Worldd now integrates these records through authenticated local actor channels.
Workspace integration uses the production broker and persistent authority store.
Authenticated bootstrap delivery must finish both-host qualification before production integration.
The issuer now provides checked current actor-status batches for ancestor validation. See ACTOR_STATUS.md.
These queries grant no authority and do not replace fresh caller authentication.

Enrollment and authentication require fresh, bounded, single-use challenges.
Bind each challenge to the issuer, service incarnation, intended operation, request contents, key, and audience.
Enforce challenge expiry with the server's monotonic clock.
Reject replay, mismatched keys, altered requests, expired challenges, and pre-restart challenges.
Check credential and actor revocation before each consequential action.
Cancel affected held input and control when authentication or authority is revoked.
Do not terminate an application solely because its controller loses access.

World sessions must derive actor identity from the validated credential.
An untrusted request cannot choose another actor or provide its own grants.
World admission remains separate. A valid identity does not enter a private world automatically.
Authenticated human and model clients use the same production actor actions as the existing runtime.
Preserve the trusted fixture entry point for baseline tests. Do not expose it as an untrusted network API.
Transport authentication remains mandatory. Signatures do not replace a protected transport.

## Key material interface, version 1

The schema is proto/spatial/identity/v1/key.proto.
The production library uses OpenSSL 3 Pure Ed25519 with 32-byte public keys and 64-byte signatures.
Its implementation follows [OpenSSL's Ed25519 interface](https://docs.openssl.org/3.0/man7/EVP_SIGNATURE-ED25519/).
Use one-shot signing and verification with no external digest argument.

The signed bytes are exactly:

1. ASCII spatial.identity.key-proof/v1 followed by one zero byte.
2. The signature purpose as an unsigned four-byte big-endian integer.
3. The payload length as an unsigned four-byte big-endian integer.
4. The exact payload bytes.

The payload limit is 65,536 bytes. An explicitly present empty payload is valid.
The purposes are enrollment proof, actor authentication, issuer administration, and issuer credential issuance.
Unspecified and unknown purposes are invalid.
The signature binds the purpose even when a caller alters both protobuf purpose fields.
Verification requires an independently selected expected public key.
Never treat the key supplied in a proof as its own authority.
Unknown protobuf fields, missing fields, and invalid key or signature lengths fail closed.

spatial-key uses the same four-byte frame length and one-MiB transport limit as the runtime.
It returns one KeyReply with protocol_version 1.
Generate and public commands return only public material.
Sign consumes one SignRequest frame and EOF, then returns KeyProof.
Verify consumes one VerifyRequest frame and EOF, then returns explicit valid=true or valid=false.
Malformed input returns a typed error and a nonzero exit.
A well-formed false verification result exits zero. Callers must inspect valid.
The tool is a one-shot key utility. It is not an actor session or authorization service.

Use these forms:

~~~sh
spatial-key generate --key /absolute/private-directory/actor.pem
spatial-key public --key /absolute/private-directory/actor.pem
spatial-key sign --key /absolute/private-directory/actor.pem < signing-request.pb
spatial-key verify < verification-request.pb
~~~

Output is framed protobuf. Use generated key_pb2 bindings to decode it.
Do not send private key files to world hosts, inference providers, or workspace applications as actor requests.
Actors may keep keys on their own authorized client host. The issuer receives public keys and proofs only.

## Private key persistence

The selected parent directory must already exist, belong to the calling user, and deny group and other access.
Create a dedicated directory with mode 0700. Do not use a project worktree as the key store.
The final parent directory cannot be a symbolic link.
Open files relative to the checked directory descriptor.
Private keys use unencrypted PKCS8 PEM with mode 0600.
Loading also permits a private read-only file, such as mode 0400.
Reject symbolic links, additional hard links, nonregular files, foreign ownership, and group or other permissions.
Reject malformed, oversized, encrypted, non-Ed25519, or appended non-whitespace key content.
Never prompt for a password or generate a replacement during a load operation.

Generation writes and fsyncs a private temporary file.
It publishes with [renameat2 RENAME_NOREPLACE](https://man7.org/linux/man-pages/man2/rename.2.html), then fsyncs the directory.
Concurrent creation has one winner. Existing keys are never overwritten.
A filesystem without this no-replacement operation fails closed.
If directory fsync fails after publication, preserve the published file and report uncertain durability.
Inspect that key before retrying enrollment. Do not replace its identity blindly.
A crash before publication may leave a private .spatial-key-* temporary file.
Retain it for diagnosis. Remove only a confirmed unused temporary file inside the intended private directory.
These checks do not qualify physical power-loss recovery or protection against the trusted host administrator.

## Evidence requirements

Key checks use independent OpenSSL commands, real file permissions, and concurrent processes.
They verify stable public keys, distinct human/agent keys, purpose binding, altered proofs, malformed protobuf, and bounded input.
Core tests call the production key executable. They do not implement an issuer simulator.
The prior P00-P10 gate must remain green on both hosts.
M01 stays incomplete until enrollment, revocation, and authenticated production actor sessions have their own evidence.
The full profile is tasks/spatial-m1.json. M02-M16 and H01-H08 remain mandatory.
No actual David credential is generated by the disposable tests.

## Key component qualification, 2026-10-07

Both hosts passed all 16 key checks in Debug and ASan/UBSan.
Each build also passed 251 total core checks, 51 harness checks, and all 151 P00-P10 acceptance cases.
There were zero acceptance failures, errors, skipped cases, or unrun baseline cases.
An isolated mutation replaced the signed purpose with a constant.
The purpose-change counterexample detected the resulting false verification success.
Production source never contained that mutation.
All 113 source hashes match both hosts and the reviewed files.
Evidence is in evidence/identity-keys-2026-10-07, including the initial missing-tool failure and exact commands.
Run focused checks with ctest --test-dir build -V -R '^identity-keys$'.
The full build commands use tools/qualify_core.py --through P07 with the recorded private XLibre server.
Then tools/gate.py --through P10 runs through tools/with_test_display.py.
Sanitizer qualification adds --sanitize --build-dir build-sanitize.
The source audit records both installed OpenSSL package versions.
No host or operator-session change was required.
Local-model review remains waived under David's hardware-failure instruction.
Enrollment and authenticated world sessions remained unimplemented at that key-component qualification.
See ENROLLMENT.md for the subsequent enrollment evidence. M01 and full Spatial-M1 remain incomplete.
