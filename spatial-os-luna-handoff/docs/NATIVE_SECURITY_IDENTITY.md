# Native connection identity

This increment supplies authentication and immutable identities inside the pinned XLibre server.
It is one component of M04. It does not qualify workspace resource isolation.

## Invariant

In strict mode, each accepted native connection receives its identity before ordinary X requests can run.
The identity includes role, workspace, application, credential ID, connection ID, and server generation.
Applications cannot change that identity through the extension.

## Bootstrap and authentication

Start a private project server with `-spatial-security-fd N`.
The inherited descriptor must name a sealed anonymous regular file.
Require WRITE, GROW, SHRINK, and SEAL seals. Read from offset zero and close the descriptor.
The file contains one `spatial.security.v1.Bootstrap` message, at most 4,096 bytes.
Require version 1 and a 32-byte supervisor cookie. Reject unknown fields.
Keep this file and all cookies outside Git and runtime world records.

The supervisor connects with standard `MIT-MAGIC-COOKIE-1` authentication and that cookie.
Ordinary Xlib applications use the same native mechanism with their own issued cookies.
A private Xauthority file can deliver an application cookie without changing Xlib.
Strict authentication runs before native authorization and host-access fallback.
The `-ac` option cannot bypass it. Launch-socket exceptions cannot bypass it.

The server uses kernel randomness for credential IDs, cookies, and its generation ID.
It assigns a monotonically increasing connection ID within that generation.
Each connection copies its immutable label into a server-owned client private.
Native authorization ID zero is bookkeeping here. It is not an identity or authorization decision.

Servers without the bootstrap option retain the existing trusted diagnostic behavior.
That mode is not a workspace security boundary.
Do not connect untrusted workspace applications until resource isolation and broker provisioning are qualified.

## Roles and credential lifetime

The supervisor can issue broker, compositor, and application credentials.
It cannot issue another supervisor credential.
A broker can issue application credentials only.
Compositors and applications cannot issue or revoke credentials.
Application labels require nonempty workspace and application bytes, each at most 256 bytes.
Labels are opaque bytes without NUL. Other roles require empty labels.

One server holds at most 255 issued credentials, plus its bootstrap credential.
Each credential records its issuing connection and parent credential.
Connections that use one credential share its application label but have distinct connection IDs.
Closing one application connection does not revoke that application's credential.

The issuing connection must remain connected while its credentials are needed.
Its disconnect revokes those credentials and their descendants.
Explicit revocation closes live dependent connections before returning success.
It also blocks subsequent authentication with those cookies.
Independent credentials remain valid. Separate supervisor connections have separate issuance lifetimes.
A broker can revoke only credentials issued by that connection.
The supervisor can revoke any issued credential. No caller can revoke the bootstrap credential.
Revoked slots are reusable. Reuse generates new IDs and cookies.

A server generation change discards issued credentials and connection IDs.
The process bootstrap cookie remains available to its supervisor across an X server reset.
A process restart requires an explicitly supplied bootstrap configuration.
Do not restore application cookies, interaction leases, or held input from world persistence.

## Protocol

The versioned schema is `proto/spatial/security/v1/security.proto`.
The build generates C, C++, and Python bindings from that schema.
The native `SPATIAL-SECURITY` extension carries protobuf messages under minor opcode zero.
Each request has an eight-byte X header, including a CARD32 payload length.
Each reply has a 32-byte X reply header, including a CARD32 payload length at offset eight.
Native header integers use the client's X byte order. Protobuf bytes retain their standard encoding.
Payloads are at most 4,096 bytes and use native four-byte framing alignment.

Requests contain version 1, a positive increasing request ID, and one operation.
Operations inspect the caller's identity, issue a credential, or revoke a credential.
Replies preserve the request ID and report a typed result or error.
Identity inspection does not reveal any cookie or another connection's identity.
Malformed protobuf, unknown nested fields, and unsupported versions do not consume request IDs.
Semantically denied valid requests consume their request IDs.
Replays receive STALE_SEQUENCE. Protocol errors do not change connection identity.

## Qualification and limits

`tests/native/test_security_identity.py` implements an independent native protocol client.
`native/security_auth.c` checks an ordinary Xlib application through its real Xauthority file.
Tests use disposable project Xvfb servers. They preserve the operator's desktop and SSH route.
The swapped-client fixture explicitly enables XLibre's `+byteswappedclients` option.
No assertion is removed to accommodate the upstream default rejection of swapped clients.

The server builder supports a separate `--sanitize` build directory.
Core qualification records server inputs, native witnesses, and native tests with source hashes.
Existing locked acceptance assertions and the locked native witness remain unchanged.

The default and cumulative native identity runs retain GLX.
The focused server sanitizer profile sets `SPATIAL_SECURITY_TEST_CORE_ONLY=1` and disables GLX.
It retains all identity assertions and enables address, undefined-behavior, and leak checks.
This profile qualifies core X authentication only. It does not qualify instrumented GLX.

Initial instrumented GLX runs exposed an upstream server-client allocation leak and repeated Xvfb cleanup.
The maintained shutdown patch releases the GLX server-client allocation and clears freed Xvfb pointers.
The server-client leak was independently reproduced with strict security disabled.
Starting a second server also exposed leaked listener records after a display-address conflict.
The patch closes that unbound socket without removing the existing server's socket path.
Installed graphics libraries still leak during GLX shutdown, including the NVIDIA GBM path.
Selecting Mesa EGL also exposed driver allocations that remain unreleased.
Those failing runs remain evidence. Full instrumented GLX lifecycle qualification remains pending.
No sanitizer suppression or graphics-driver system change is applied.

Driver-selection diagnostics used the documented
[GLVND EGL selection mechanism](https://github.com/NVIDIA/libglvnd/blob/master/src/EGL/icd_enumeration.md)
and [Mesa software renderer settings](https://docs.mesa3d.org/envvars.html).
These diagnostics do not establish native GPU acceptance.

Resource inheritance, enumeration, pixels, properties, grabs, input injection, and extension access remain required.
Broker launch credential delivery and compositor authorization remain required.
Retained resources need the forthcoming resource lifetime policy.
No authenticated actor receives native application observation or control from this increment.
M04, Spatial-M1, physical controls, native GPU performance, and the complete project remain incomplete.
