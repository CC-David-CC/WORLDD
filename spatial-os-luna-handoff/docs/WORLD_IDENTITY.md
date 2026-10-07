# Authenticated local world sessions

This component connects issuer credentials to the production world actor engine.
It supplies local actor channels, fresh authentication, and live credential revocation handling.
World networking, delegated grants, and full workspace and application authority remain separate required milestones.

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
Remote actor transport remains required for shared-world networking.

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
