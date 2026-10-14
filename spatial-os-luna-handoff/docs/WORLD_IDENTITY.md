# Authenticated world sessions

This component connects issuer credentials to the production world actor engine.
It supplies local and remote actor channels, fresh authentication, and credential revocation handling.
Full shared-world workflows, distributed cancellation acknowledgement, and complete workspace authority remain required milestones.

## Invariant

Derive each session's identity from a fresh verified credential proof, and cancel its control when that credential becomes invalid.

## Channels and configuration

Run worldd with `--identity-config ABSOLUTE_FILE` and one or more `--actor-fd FD` arguments.
Keep the normal stdin/stdout channel for trusted management.
Each actor descriptor uses the separate `WorldSessionRequest` and `WorldSessionReply` interface.
The schema is `proto/spatial/identity/v1/world_session.proto`.
Existing core messages and database schema remain unchanged.

The supervisor creates unnamed AF_UNIX stream socket pairs and distributes their peer descriptors to controller processes.
Keep those descriptors outside workspace applications.
worldd verifies the socket type, unnamed endpoints, connection, and supervisor user through SO_PEERCRED.
The peer credentials describe socket creation, not the current holder after descriptor transfer.
Descriptor distribution therefore remains a trusted supervisor responsibility.
See [unix(7)](https://man7.org/linux/man-pages/man7/unix.7.html) and [socketpair(2)](https://man7.org/linux/man-pages/man2/socketpair.2.html).
Kernel socket possession protects the local transport. Actor signatures independently establish actor identity.
Descriptors receive close-on-exec before the compositor starts.
Do not expose the management pipe through a network relay.
Remote actor transport uses the explicit TLS listener described below.

Configure at most 32 actor channels.
Each channel has one active actor and at most one pending authentication challenge.
Frames have the existing four-byte length prefix and one-MiB payload limit.
Partial input and output have fixed ten-second deadlines.
Malformed protobuf returns a typed error.
Excessive frames, disconnects, and transport expiry close that actor channel and cancel its control.
Management operations are not part of the actor interface.

Use a private, owned WorldIdentityConfig textproto file.
It names the checked issuer-client configuration and an explicit admission mode.
REGISTERED_ACTORS admits valid registered identities.
EXPLICIT_ACTORS admits only configured actor identities.
The separate actor_grants entries supply server-controlled action grants.
Admission itself supplies no action, workspace, or administration grant.
Unsupported and duplicate policy entries fail startup.
Configuration is loaded at startup. Editing its file does not change a running world.
M02 must supply persistent scoped grants, delegation, and live authority changes.

These initial action grants use the existing baseline actor vocabulary.
They do not establish application or workspace isolation.
Qualify interaction and observation only in a dedicated test world with authorized fixture applications.
M02, M03, M04, M08, and M09 must complete their authority boundaries before full Spatial-M1 use.

## Remote actor channels

The supervisor supplies an inherited listening TCP socket through `--actor-listen-fd FD`.
It also supplies `--actor-tls-config ABSOLUTE_FILE` and the existing `--identity-config ABSOLUTE_FILE`.
The TLS listener may coexist with inherited local actor channels.
The listener cannot share a descriptor with an actor channel or the workspace broker.
Do not expose the trusted stdin/stdout management channel.

Use an owned WorldActorTlsConfig textproto file with protocol_version 1.
Specify absolute certificate_path, private_key_path, and client_ca_path values.
Keep each material file in an owned private directory.
The loader rejects unsafe ownership, symlink files, and certificates writable by other users.
It rejects private keys accessible by group or other users.
TLS 1.3 authenticates both transport peers.
Clients must verify the configured server identity and trust chain.
A valid transport certificate supplies no actor identity, world admission, or workspace grant.
Actors still complete the signed proof exchange below.

The optional io_timeout_ms accepts 50 through 60000 milliseconds. Its default is 5000.
The optional idle_timeout_ms accepts 50 through 300000 milliseconds. Its default is 60000.
The idle timeout applies only before actor authentication.
Authenticated idle actors remain connected without a periodic actor request.
The optional connection_limit accepts one through 128 connections. Its default is 32.
The total includes inherited local actor channels. Local inherited channels remain limited to 32.
Handshake, partial-frame, pending-write, and TLS read-retry deadlines remain bounded.
Repeated retries do not refresh a pending read-retry deadline.
The protobuf payload limit remains one MiB.
Slow or broken peers lose only their affected actor channel unless shared authority itself fails.

Remote channels use the existing WorldSessionRequest and WorldSessionReply messages.
They enter the same actor engine and authorization checks as local channels.
World entry does not create or select a workspace.
Remote geometry observation omits live application surfaces, pixels, and application metadata.
Remote application key, pointer, and scroll actions return UNIMPLEMENTED.
An authorized local reconnection restores local application observation through the normal checks.
M1 does not require remote live application streaming or remote application input.

## Fresh authentication

Begin with a SignedCredential and an optional room and pose.
The world verifies the issuer signature and checks current credential status through its pinned TLS issuer connection.
It requests a fresh issuer challenge with the world identity as audience and a random 32-byte binding.
The pending request stores its exact credential, spawn, nonce, binding, and monotonic expiry.
The actor signs the returned signing request with its own key.
Complete on the same actor channel using that proof and nonce.

Completion consumes the pending request before verification.
The world checks the issuer's returned subject, credential, actor kind, audience, binding, and active status.
It checks current credential status again before admission.
An untrusted client cannot choose an actor identity or submit grants.
The persistent actor identifier encodes the issuer and actor identities with explicit separators.
It does not depend on a host name, key, session token, or XID.

Admission then calls the existing Actors::open implementation with server-selected grants.
Existing actors resume their saved room and pose.
New actors use a valid requested spawn or the entry room.
Session tokens stay inside worldd. Actor requests cannot supply or transfer them.
The channel supplies its authenticated actor to the existing inspect and action handlers.
The existing action sequence and authorization checks still apply.
Observation also requires an explicit observation grant on this channel.
That check runs inside the actor engine after sequence consumption.
Trusted fixture sessions retain their existing baseline observation contract.
World entry does not create or select a workspace.

## Revocation and failure

Before inspect or action, query current credential status through the production checked issuer client.
Copy that credential context before network I/O because notifications can revoke a session during the wait.
The revocation watcher separately cancels affected active sessions and pending challenges.
Credential revocation affects one credential. Actor revocation affects every credential for that actor.
An independently valid credential can authenticate after another credential is revoked.

Cancellation calls the production Actors::close path.
That path releases native keys, buttons, focus, capture, and leases, and cancels motion and object previews.
It does not terminate the application solely because its controller lost access.
If cancellation cannot be confirmed, end the world process instead of acknowledging success with orphaned control.
Outstanding protected replies are discarded by closing their channel when necessary.
Issuer or watcher failure cancels dependent sessions and disables new authentication.
Recovery requires an explicit world restart with fresh authentication. There is no automatic reconnect or replay.
Safe channel close remains available after issuer failure.

The action connection sends a read-only keepalive after one second without a successful exchange.
Configure the issuer's service idle timeout above that interval.
The scheduler continues ticking during checked issuer I/O.
Callbacks are cleared before session cleanup leaves the server loop.

An issuer revoke reply confirms the issuer commit.
It does not acknowledge cancellation in every world.
Notification transport and scheduling still introduce latency.
A revocation barrier or equivalent authority protocol remains required before claiming immediate distributed revocation.
Per-action checks do not make an issuer check and an external effect one distributed atomic transaction.

## Verification

Run `ctest --test-dir build -V -R '^world-identity$'` with SPATIAL_TEST_XSERVER set to the qualified private XLibre server.
The suite uses real identityd, worldd, spatialwm, SQLite, signatures, and independent native X clients.
It covers admission separation, proof replay, channel and world binding, real actor actions, persistence, and issuer failure.
Native witnesses check held-key and held-button release without another actor request.
The full P00-P10 gate remains required in Debug and ASan/UBSan on both hosts.
Physical controls, visible native sessions, distributed world admission, and full Spatial-M1 remain pending.

## Remote transport qualification

Candidate 02 passed complete Debug and runtime ASan/UBSan profiles on sb16 and sb16-2.
Each profile passed 1,089 core checks, 51 harness checks, and all 151 P10 cases.
The seven remote suites include 46 checks. No final case failed or skipped.
Direct LAN scenarios passed with each host acting as authority for actors on both hosts.
See evidence/world-remote-2026-10-09/candidate-02 and the crosshost-02 and crosshost-03 records.
Run the focused suites with `ctest --test-dir BUILD -V -R '^world-remote($|-)'`.
Use the qualified private XLibre server through SPATIAL_TEST_XSERVER for native witnesses.
Focused results do not replace the complete profile or physical hardware acceptance.
