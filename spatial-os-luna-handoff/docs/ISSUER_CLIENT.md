# M01 checked issuer client

IssuerClient connects a trusted service to the existing issuer TLS listener.
It uses the same versioned IssuerRequest and IssuerReply messages as identityd.
spatial-issuer-client adapts framed stdin and stdout to this production library.
The client does not create an actor session, grant authority, or replace world admission checks.
World integration and immediate cancellation after actor revocation remain required.

## Configuration and trust

Use spatial-issuer-client --config ABSOLUTE_FILE.
The file contains an IssuerTlsClientConfig in protobuf text format.
Its fields are:

| Field | Meaning |
|---|---|
| protocol_version | Must be 1 |
| address | Numeric IPv4 or IPv6 address |
| port | TCP port from 1 to 65,535 |
| server_name | Expected DNS subject alternative name and TLS SNI |
| server_ca_path | Explicit PEM trust file for the issuer service |
| certificate_path | This service's PEM client certificate chain |
| private_key_path | This service's PEM private key |
| issuer_id | Expected 32-byte issuer identity |
| issuer_public_key | Expected 32-byte issuer signing key |
| io_timeout_ms | Optional timeout from 50 to 60,000 milliseconds; default 5,000 |

Get issuer pins through the trusted initialization output or existing configuration administration.
Do not learn or replace pins from an unverified network response.
Keep service keys separate from actor, owner, and issuer signing keys.
Keep private configuration and keys outside Git and workspace application boundaries.

The shared TLS loader enforces the file and directory rules in ISSUER_TLS.md.
The client and server use one checked loader and TLS configuration implementation.
No default CA search path, implicit DNS lookup, alternate endpoint, or provider fallback is used.
An address names the network destination. server_name supplies the independent TLS identity.
Use an ASCII DNS name with valid labels. Empty names, address literals, wildcards, and parent-domain patterns are invalid configuration.
Only DNS subject alternative names authenticate the server. Common-name fallback is disabled.
The client sets [OpenSSL hostname verification](https://docs.openssl.org/3.0/man3/SSL_set1_host/) before connecting.
It separately sets [TLS SNI](https://docs.openssl.org/3.0/man3/SSL_CTX_set_tlsext_servername_callback/).

Require mutual TLS 1.3 and the explicit service trust chain.
Reject older TLS versions. Do not use early data or resume earlier TLS sessions.
After TLS succeeds, exchange hello and info before sending any caller request.
Check the production protocol, implementation, source build ID, and required identity capabilities.
Require unique capability names and the issuer-control, enrollment, revocation, and authentication capabilities.
Compare both issuer pins and require a valid policy revision.
Every later info reply must still match those pins.
The pins identify the configured issuer. The verified TLS certificate authenticates the service connection.

## Checked exchanges

Permit one outstanding request per client instance.
Assign increasing internal request IDs, including the startup hello and info exchanges.
Do not use caller-supplied IDs to correlate network replies.
Return the original caller ID only after checking the network reply.

Require a complete frame of at most one MiB.
Check protobuf decoding, protocol version, exact internal request ID, unknown fields, and enum values.
Require a known, nonzero error code or the outcome allowed for the request type.
An execute request refers to a server-held challenge. Its envelope permits policy, enrollment, or identity-status results.
The operation's caller must also check the expected admin result and its subject.
Authentication consumers must compare returned actor, credential, audience, and binding against their own pending request.
Transport checks do not replace these semantic checks or current authorization.

A valid issuer error is a completed exchange. It leaves the client usable.
Malformed replies, wrong IDs, unsupported versions, unexpected outcomes, oversized replies, truncation, and transport errors disable the client.
Close its socket before propagating the error. Do not reconnect or replay.
A lost reply does not prove that the issuer rejected or rolled back the operation.
Reconcile durable state explicitly before retrying through a newly constructed client.

One fixed deadline covers TCP connection, TLS negotiation, hello, and info during startup.
Each later call has one fixed deadline covering its complete write and reply read.
Partial progress does not refresh deadlines.
Use nonblocking sockets and poll waits of at most ten milliseconds.
Invoke the configured tick callback during I/O and waits.
A callback exception cancels the exchange and disables the client.
This permits a world caller to maintain input deadlines while waiting for issuer I/O.
It does not itself implement live world revocation or asynchronous event delivery.

Use the library from one thread. Do not reenter it from the tick callback.
The calling process must ignore SIGPIPE before constructing the client. The constructor checks this precondition.
The command-line adapter sets it at startup.
failed() reports an already observed fatal failure. It is not a remote liveness guarantee.

## Command-line behavior

stdin and stdout use four-byte big-endian lengths and issuer protobuf messages.
stderr carries fatal diagnostics. The adapter never prints private key content.
Malformed local protobuf returns INVALID_ARGUMENT and keeps the stream usable.
Decoded local errors retain the caller's request ID. Unsupported versions return UNSUPPORTED_VERSION.
Excessive or truncated input frames close the adapter with exit code 2.
Fatal network failures also exit 2 without inventing a success or rollback reply.
Normal input EOF closes the connection and exits zero.
Valid typed issuer errors remain protobuf results for the caller to inspect.

## Qualification

Run the actual C++ client against production identityd for enrollment, authentication, and revocation.
Use independent Python TLS peers only for transport faults and envelope counterexamples.
Exercise DNS verification, explicit trust, certificate expiry, issuer pins, IPv4, IPv6, framing, and deadlines.
Verify that lost replies do not reconnect or replay.
A thin C++ probe must verify callback cancellation in the production library.
Run a direct sb16-2 to sb16 connection through the production client.
Detect an isolated mutation that disables hostname verification.
Preserve existing issuer and baseline assertions during Debug and ASan/UBSan qualification on both hosts.
