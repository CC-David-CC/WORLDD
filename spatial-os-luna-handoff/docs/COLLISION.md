# Swept collision and visible barriers

## Protobuf migration 0.10.0

The predecessor acceptance lock SHA-256 is b6df68d79e4959ee36094977aa990dea1ba63a70da779a5136726b376ae38098.
Add typed swept-sphere requests and replies.
Add optional actor_radius and barrier_color configuration.
Add private barrier geometry and public observation bounds.
The protobuf schema, acceptance codec, and fixture configuration hashes change in the lock.
The fixture makes the existing 0.2-meter contract radius explicit and sets the new barrier color.
All 141 acceptance IDs, assertions, oracles, and native witness sources remain unchanged.
The envelope version remains 1.

## Geometry invariant

The swept primitive finds the first contact between a moving sphere and an axis-aligned box.
It measures Euclidean distance to the actual box, including rounded edge and corner offsets.
It splits the segment where center coordinates cross box faces.
Each interval has one quadratic distance function.
The earliest valid root gives the contact fraction, sphere center, and outward unit normal.
Long-double intermediates reduce overflow and cancellation across finite double inputs.
Unrepresentable results return INVALID_ARGUMENT.

Initial overlap returns fraction zero and the original center.
An outside center uses the normalized vector from the closest box point.
An inside center uses its nearest face.
Equal-distance faces use the order -X, +X, -Y, +Y, -Z, +Z.
The internal result also supplies penetration depth for separation.
Zero displacement returns no hit when the sphere is outside contact.
Tangency counts as contact in the pure primitive.

## Actor motion

Barriers use local bounds transformed by their entity pose.
Actor motion transforms the sweep into each barrier's local coordinates.
It uses the same continuous primitive exposed through the protobuf geometry operation.
The actor radius comes from production configuration and defaults to 0.2 meters.
The fixture sets that radius explicitly.
Committed geometry changes invalidate the cached collision geometry through the world revision.
Other rooms do not participate in collision.

The resolver stops the inward component and preserves tangent displacement.
Separating contact does not block motion away from a wall.
An overlap receives deterministic normal separation.
A small skin is max(radius * 1e-9, 1e-12) meters.
The resolver permits at most 32 contact iterations per segment.
It discards unresolved remaining motion when that budget is exhausted.
Conflicting overlapping solids can require an explicit world edit; they do not justify tunnelling.

Turning motion retains exact body-twist integration for each subinterval.
For a subinterval, chord deviation is bounded by speed * angular_speed * seconds^2 / 8.
The sphere expands by that bound during its chord sweep.
This prevents a curved path from missing a thin barrier between sampled endpoints.
Normal configuration limits each bound to actor_radius * 1e-4.
The scheduler allows at most 4096 subdivisions per actor interval.
An excessive request stops that actor's motion and records a diagnostic.
Free motion without barriers uses the existing exact body-twist integration directly.

## Shared visible geometry

The renderer constructs six outward faces from the same bounds and pose used for collision.
Picking and observation use that same generated geometry.
Observation merges visible face counts into one barrier representation.
It reports the barrier's original pose and bounds, without inventing application pixels.
Barrier material uses configurable color, with default RGB 96,104,120.
Rendering, picking, and observation remain current-room operations.

## Qualification

The retained baseline fails because the swept-sphere protobuf operation is absent.
Both sb16 and sb16-2 passed Debug and ASan/UBSan runtime qualification.
Each run passed 98 cumulative P00-P06 acceptance tests, 95 core checks, and 39 harness checks.
The 16 collision counterexamples are included in the core count.
The focused P07 sweep, sliding, and observation acceptance tests passed in both modes on both hosts.
Legacy coverage is 101 passed, zero current failures, and 40 unrun.

Pure-function witnesses cover all faces, endpoint contact, edges, corners, tangency, overlap, and scale changes.
An independent convex-distance search checks 180 randomized sweeps.
Runtime tests cover fast motion, configured radius, sliding, separating motion, multiple contacts, and geometry updates.
They also cover curved motion whose endpoints miss the wall.
All six barrier faces render and pick correctly.
Multiple visible faces produce one observation record.
A real native application surface disappears from observation and picking behind a barrier.

The first harness run caught a missing fixture hash in the migration manifest.
The retained diagnostic includes that manifest and the failing integrity check.
The corrected manifest records the explicit radius and material settings.
No test assertion changed.

An isolated mutation passed an empty collider set to actor motion.
The fast-motion witness detected position 2.5 meters instead of the required 0.25 meters.
The main runtime retained the real collider set.

Evidence is in evidence/collision-2026-10-07.
Final runtime reports contain 79 source hashes, exact commands, and server identities.
All hashes matched the reviewed source and both execution hosts.
Sanitizers instrument worldd and spatialwm. The XLibre server remains a Debug build.
The tests use disposable authenticated software displays.
They do not qualify physical input or native GPU performance.
No system configuration or operator desktop changed.

P07 remains incomplete. Portals and object manipulation are next.
All additional Spatial-M1 and hardware milestones remain mandatory.
The full project goal remains active.
