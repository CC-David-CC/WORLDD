# Issuer revocation feed

This component supplies ordered revocation notifications to trusted service consumers.
It does not create world sessions or implement world admission.
World integration must still cancel live input, observation, and control.

## Invariant

Return committed revocations in order, or report failure without silently skipping history.

## Wire contract

Use the versioned messages in `proto/spatial/identity/v1/issuer.proto`.
The issuer advertises `identity.revocation-feed/v1`.
Existing fields keep their meanings. Database schema 3 remains unchanged.

`RevocationRead` has an optional `after_revision` and a `wait_ms` value.
Omit the cursor to obtain the current baseline.
That response has `initial = true`, no events, and equal start, end, and head revisions.
This baseline does not describe earlier revocations.
Check each credential before admitting a new session.
Do not preserve existing sessions across a failed watcher without fresh verification.

Set the cursor to read committed events after that revision.
Each event contains its revision, target kind, and opaque target identity.
The feed excludes labels, keys, credentials, and revocation reasons.
Actor and credential revocations share one ordered revision sequence.
Duplicate revocations do not create another event.
Rolled-back writes do not create events.

Each batch contains at most 128 events.
`from_revision` equals the requested cursor.
`through_revision` equals the last returned revision, or the cursor when no event follows.
`head_revision` records the committed head from the same database transaction.
Events must be contiguous, starting at the cursor plus one.
A short batch must reach the recorded head.
An empty batch must have equal start, end, and head revisions.
Read another batch immediately when its end precedes its head.

Reject future cursors and values above the signed 64-bit storage range.
Reject waits above 1,000 milliseconds.
Report `STORAGE_ERROR` when requested durable history has missing or invalid entries.
Use cursor zero to read the complete retained history.
The issuer does not compact revocation history in this version.

## Transport and waiting

The management pipe returns immediately.
The existing mutually authenticated TLS listener supports bounded waiting.
Wait only when a valid explicit cursor has reached the current head.
Committed revocation wakes the waiting request during the next service-loop iteration.
Wait expiry returns an empty heartbeat batch.
Other peers remain able to use the issuer while a read waits.
Existing frame limits, connection caps, and fixed I/O deadlines still apply.
Waiting adds no second mutation path.

The production `IssuerClient` checks each feed reply.
It verifies the pinned issuer, requested cursor, batch range, event order, and target identities.
Malformed replies disable the connection. It does not retry or replay them.

## Production consumer

`IssuerWatcher` owns one worker thread and one checked client.
Only that thread accesses its client and TLS objects.
The world-facing `poll()` method never waits for network I/O.
The first update establishes a checked baseline or supplies the requested backlog.
Later empty heartbeats do not fill the consumer queue.

The queue holds at most 32 updates.
Overflow discards queued updates and publishes a permanent `RESOURCE_EXHAUSTED` failure.
Transport, protocol, and issuer errors also discard queued batches and publish permanent failure.
Consumers must cancel sessions that depend on a failed feed.
There is no automatic reconnection or provider switch.
Shutdown uses cooperative cancellation between client I/O calls.
The calling process must ignore SIGPIPE before construction.

`spatial-issuer-watch --config ABSOLUTE_FILE [--after-revision N]` exposes this library through framed Protocol Buffers.
Its configuration uses the existing `IssuerTlsClientConfig` format.
Its output contains `RevocationUpdate` frames.
EOF on its supervisor input stops it. Supervisor data is an error.
Output is nonblocking and has a fixed five-second write deadline.
The process exits with code 2 after feed or output failure.
A consumer must treat EOF or an incomplete frame as failure unless it requested shutdown.

## Integration limits

A successful revoke response confirms the issuer commit.
It does not confirm that every world has consumed that event.
Notification latency includes transport and consumer scheduling.
World integration must define current action checks and revocation acknowledgement before claiming immediate cancellation.
This component alone does not satisfy M01 or Spatial-M1.

## Verification

Run `ctest --test-dir build -V -R '^(identity-(issuer|enrollment|revocation|tls|watch)|issuer-client)$'`.
The watch suite uses real issuer storage, transactions, restarts, and mutually authenticated TLS.
Independent TLS peers supply invalid replies and a queue-overflow counterexample.
The thin C++ probe exercises the production watcher without implementing another issuer.
Full qualification also requires both hosts, Debug and ASan/UBSan, and the complete P00-P10 baseline.
Keep the acceptance lock unchanged.
