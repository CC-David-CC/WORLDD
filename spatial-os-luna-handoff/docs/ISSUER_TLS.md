# M01 issuer TLS service transport

The issuer accepts framed Protocol Buffers over mutually authenticated TLS 1.3.
This listener uses the same dispatch and issuer implementation as the inherited management pipe.
One process owns the issuer state. All connections share its policy, challenges, credentials, and revocation records.
TLS service certificates authorize transport access. Owner and actor proofs still authorize their respective operations.
Transport access does not grant world admission, workspace authority, or issuer administration.

This component supplies the server endpoint. The checked production client is specified in ISSUER_CLIENT.md.
The endpoint does not expose the trusted world fixture API.
Authenticated world entry and immediate cancellation of live world control remain separate integration work.
M01 and Spatial-M1 remain incomplete.

## Supervisor interface

Initialize or migrate the issuer separately with the existing local commands.
The supervisor binds a TCP socket and calls listen before starting identityd.
Pass that descriptor explicitly through process creation.
Use identityd --state-dir ABS --listen-fd FD --tls-config ABS.
The descriptor must exceed 2. It must be an IPv4 or IPv6 TCP listening socket.
Do not combine TLS serving with initialization or migration.
The supervisor selects the listening address. identityd does not open an implicit public listener.

Keep stdin open as the supervisor lifetime channel.
EOF stops the listener and closes all connections.
Any stdin data is an error in TLS mode. stdout does not carry RPC replies in that mode.
stderr carries startup failures without private key content.
Without TLS arguments, the existing trusted framed management pipe remains available.
No system service, firewall, login setting, or permanent certificate installation is part of this increment.

The versioned IssuerTlsServerConfig message is in proto/spatial/identity/v1/issuer.proto.
Use protobuf text format for the local configuration:

~~~text
protocol_version: 1
certificate_path: "/absolute/private-directory/server-chain.pem"
private_key_path: "/absolute/private-directory/server-key.pem"
client_ca_path: "/absolute/private-directory/service-ca.pem"
io_timeout_ms: 5000
idle_timeout_ms: 60000
connection_limit: 32
~~~

All paths must be absolute. Each final parent directory must belong to the service user and deny other access.
Files must be regular, singly linked, and owned by that user.
Reject final symlinks, group or other writes, and files larger than 65,536 bytes.
Private keys must also deny group and other reads.
Configuration and certificate files may have read permissions such as 0644 within their private directory.
Files are opened relative to the checked directory descriptor.
OpenSSL reads those same descriptors through Linux /proc/self/fd paths.
Encrypted keys fail without prompting. The certificate and private key must match.
Keep TLS keys separate from issuer and actor signing keys.
Keep configuration, credentials, and private keys outside Git.

## TLS and framing

Require a verified client certificate against the explicit service CA file.
Do not load default system or environment trust paths.
Use the standard OpenSSL verification callback and a maximum intermediate chain depth of four.
The [OpenSSL verification flags](https://docs.openssl.org/3.0/man3/SSL_CTX_set_verify/) require both peer verification and a client certificate.
Use TLS 1.3 only. Disable early data, session tickets, session caching, and renegotiation.
Trust is loaded at startup. Replacing the trust file requires a controlled issuer restart.
This component does not provide online service-certificate revocation or trust hot reload.
Actor and credential revocation continue to use current issuer records on each relevant operation.

Each frame has a four-byte unsigned big-endian length and one issuer protobuf payload.
The maximum payload is one MiB. Reject excessive lengths before allocating the payload.
Keep request IDs and reply versions unchanged from the issuer contract.
Malformed protobuf returns INVALID_ARGUMENT with request ID zero.
Decoded invalid requests preserve their request ID. The channel remains usable after typed operation errors.
Partial frames and pipelined complete requests are supported. Replies preserve request order on each connection.
Truncation, transport errors, and excessive lengths close only the affected connection.
Do not replay requests after a lost reply. Durable issuer records support explicit reconciliation.

## Resource bounds

Use nonblocking sockets and a single poll loop.
Perform one I/O step per peer before accepting another connection.
A handshake and a reply each have a fixed I/O deadline.
A request deadline starts with the first frame byte and includes both header and payload.
Progress does not refresh that deadline.
Idle time starts after a successful handshake or completed reply.
The I/O timeout range is 50 to 60,000 milliseconds. Its default is 5,000.
The idle timeout range is 50 to 300,000 milliseconds. Its default is 60,000.
The connection limit range is 1 to 128. Its default is 32.
Reject new connections above the limit. Expiry frees their slots.
Untrusted peers cannot allocate issuer challenges before TLS authentication succeeds.
Existing issuer challenge and enrollment quotas remain enforced across all connections.

Clear the OpenSSL error queue before each TLS I/O call.
Inspect its result immediately with SSL_get_error.
Retry WANT_READ or WANT_WRITE with the same buffer and length.
The [OpenSSL I/O contract](https://docs.openssl.org/3.0/man3/SSL_write/) requires stable retry arguments.
Fatal TLS errors close the connection without further protocol I/O.
These limits control individual connections. They do not claim resistance to host or network resource exhaustion.

## Qualification

The independent Python ssl peer connects to the production identityd executable.
Its OpenSSL-generated fixture certificates remain separate from actor and issuer keys.
Tests cover TLS policy, peer trust, current issuer operations, framing, deadlines, and supervisor shutdown.
Cross-host evidence must use a direct TCP connection between sb16 and sb16-2.
An isolated mutation must demonstrate that the missing-certificate check detects an unauthorized connection.
Run all existing issuer suites and the complete P00-P10 baseline on both hosts.
Run Debug and ASan/UBSan builds. Preserve the acceptance lock and existing assertions.

Production clients must verify the server certificate, expected server name, and pinned issuer identity before sending actor data.
The client contract records reply-envelope and failure qualification. Authenticated world integration remains required.
