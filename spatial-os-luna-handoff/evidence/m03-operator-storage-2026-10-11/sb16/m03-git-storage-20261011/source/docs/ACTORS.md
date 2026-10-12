# Actor runtime and P03 protobuf extension

## Runtime behavior

worldd owns actor state, session tokens, grants, input freshness, and checkpoints.
The test host forwards typed messages.
It does not implement actor motion or permission checks.

actor.open is a trusted management operation.
It creates an actor or reconnects a disconnected actor.
It does not expose enrollment to an untrusted caller.
A new actor uses its requested room and pose, or the world entry room and identity pose.
An existing actor resumes its last valid runtime or saved pose.
A second simultaneous controller receives IN_USE.
An actor ID cannot reuse another entity ID.

Each connection receives a fresh 256-bit token from Linux getrandom.
Tokens are bound to one actor in one running world.
Close and restart invalidate them.
Tokens and live grants are absent from the world database.
Public-key enrollment, delegation, and authenticated admission remain mandatory later work.

Actor actions require the current token and a positive sequence.
A repeated or older sequence returns STALE_SEQUENCE.
After token and sequence validation, the sequence is consumed before action checks.
Denied grants, invalid axes, and unknown actions do not permit sequence reuse.
Malformed protobuf is rejected before actor dispatch.

Input contains six independent normalized axes.
Navigation requires the navigate grant.
Stop requires a valid session but no navigation grant.
Invalid samples neither replace accepted input nor refresh its expiry.
Deadzone, gain, and input timeout come from a protobuf configuration file.

The default file is runtime.textproto beside worldd.
CMake copies config/conformance.textproto there for conformance runs.
Use worldd --config PATH to select another profile.
Unknown fields, missing settings, non-finite values, and invalid ranges cause startup failure.

worldd uses a monotonic clock by default.
Its poll loop continues ticking during partial requests and output backpressure.
It bounds buffered messages and management I/O deadlines.
The --fixture-clock option selects controlled time at the same actor scheduler boundary.
The test host selects that option.
A normal worldd rejects clock.advance with FORBIDDEN.

Motion integrates only until the accepted sample expires.
Each axis retains its independent magnitude.
Movement uses the existing production body-twist integrator.
Room collision and portal traversal remain P07 work.

## Durability

Actors use the existing durable entity table.
Only actor management can create the ACTOR entity kind.
Generic world create, update, and delete cannot bypass actor state ownership.

world.get returns an actor's current runtime pose.
world.snapshot returns its durable pose.
world.checkpoint commits changed actor poses in one SQLite transaction.
Each changed actor revision advances once.
The global world revision advances once for the checkpoint transaction.
An unchanged checkpoint does not advance revisions.

A failed checkpoint preserves every prior durable actor pose and revision.
The live poses remain available for a later retry.
Restart restores acknowledged checkpoints.
Uncheckpointed motion is not promised after a crash.
No axes, held input, session token, or controller grant is restored.

## Explicit acceptance migration

David authorized protobuf for production and acceptance.
The schema adds actor open, inspect, close, input, stop, checkpoint, and clock operations.
Existing field numbers remain unchanged.
The actor entity kind extends the existing entity schema.
Older binaries fail closed if they encounter an unknown persisted entity kind.

P03 keeps its 14 test IDs and all motion, permission, revision, and persistence assertions.
Two malformed-input encodings change:

- A JSON string cannot occupy a generated double field. The test sends a wrong protobuf wire type instead.
- An arbitrary JSON actor-selector key becomes an unknown protobuf field containing the other actor's ID.

Both requests still reach worldd and must return INVALID_ARGUMENT.
The second test still checks that the other actor did not move.
No client-only error replaces either server rejection.
Additional tests cover replay after denial, atomic checkpoint rollback, token isolation, configuration, and real-clock expiry.

The lock records the changed schema, codec, these two test encodings, and added counterexamples.
Its predecessor hash identifies the P02 baseline.
The local-agent review is waived by David's instruction after its hardware failure.
The primary agent reviews these changes and executes the real runtime checks.

## Scope and sources

Passing P03 establishes management-session isolation and motion behavior.
It does not establish hostile-process confinement, cryptographic actor enrollment, or native input delivery.
Those requirements remain in the full project milestones.

Implementation references:

- [Linux getrandom](https://man7.org/linux/man-pages/man2/getrandom.2.html)
- [Protobuf C++ text format](https://protobuf.dev/reference/cpp/api-docs/google.protobuf.text_format/)
