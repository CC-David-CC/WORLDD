# Portal navigation

## Protobuf migration 0.11.0

The predecessor acceptance lock SHA-256 is 58ca56d4f42f625b19495ac35ce23e9943ff14a150c445a4c2ceb720dd098116.
The private RenderScene message gains portal geometry.
RenderConfig gains an optional portal_color.
Only the schema hash changes in the acceptance lock.
All 141 locked acceptance IDs, assertions, oracles, and native witness sources remain unchanged.
The envelope version remains 1.

## Crossing invariant

An actor crosses the aperture from its front side into its back side.
The mapping is destination_pose * inverse(entry_pose).
Apply that mapping to remaining displacement and orientation.
Do not insert an additional half-turn.
Room entry does not grant authority or create execution context.

The plane belongs to the front side.
A step ending exactly on the plane defers crossing until the next forward step.
The aperture includes its left and top edges. It excludes its right and bottom edges.
Select the earliest crossing along the actual collision-resolved displacement.
A collision wins an equal-time tie.
Source barriers can prevent crossing. Destination barriers constrain remaining motion.
Reject a landing that overlaps a destination barrier beyond the collision skin.
Stop just in front of that entry and clear motion intent.

The navigation resolver permits 64 combined collision and crossing iterations per segment.
Each actor scheduler interval permits 32 crossings across all subdivisions.
Exhaustion stops motion and records a diagnostic.
Curved motion uses the existing bounded chord subdivision when barriers or portals are present.
Exact body-twist integration remains within each subdivision.
Portal crossing uses each center chord. Its spatial approximation is bounded by actor_radius * 1e-4.
The sphere expands by the chord deviation bound for collision checks.
Committed geometry changes invalidate the room geometry cache.

## Interaction and presentation

Every crossing clears the saved aim and schedules native input cancellation.
Cancellation runs outside compositor wait callbacks to prevent recursive renderer requests.
It releases held keys, buttons, focus, capture, and the application lease.
Other actors retain their native input state and leases.
Applications remain alive. Worldd, spatialwm, and the display remain running.
Live actor room references prevent room deletion before a checkpoint.
A checkpoint saves the destination room and pose through existing transactional storage.

Four non-solid marker strips surround each aperture.
Both sides are visible. Default color is RGB 80,180,220.
The marker width is min(0.1, 0.04 * min(aperture_width, aperture_height)) meters.
Rendering, picking, and observation use the same marker geometry.
Observation merges strips into one portal record with the original aperture pose and size.
An aperture does not expose destination-room geometry or application output.

## Qualification

The retained baseline fails because the actor stays in the source room.
Both hosts pass Debug and ASan/UBSan runtime qualification.
Each run passes 98 cumulative P00-P06 acceptance tests and eight focused P07 tests.
The focused cases cover five portal requirements, sweep, sliding, and observation.
Each run also passes 114 core checks and 39 harness self-tests.
The core count includes 19 new portal counterexamples.
Legacy coverage is 106 passed, zero current failures, and 35 unrun.

Counterexamples cover rotated mappings, exact-plane endpoints, aperture edges, chains, cycles, source collision, and destination collision.
They cover curved motion, live geometry updates, room deletion, checkpoint restoration, and navigation authority.
Native clients witness balanced key and button release without disturbing another actor.
A same-room portal also cancels previous interaction.
Front and back marker observations share open aperture geometry.
Destination content becomes visible only after entry.
Runtime process identities and the display remain unchanged across entry.

The first new test setup omitted required room labels.
The runtime rejected that setup. The corrected fixture supplies those labels.
The retained first-run reports and fixture preserve the diagnostic.
No locked acceptance assertion changed.

An isolated mutation discards remaining movement after crossing.
The rotated-portal test detects destination Z = 3 instead of Z = 1.
The production source retains the remaining displacement.

Evidence is in evidence/portals-2026-10-07.
Reports record exact commands, server identities, and 82 source hashes.
All hashes match the reviewed files and both execution hosts.
Sanitizers cover worldd and spatialwm. The private XLibre server remains a Debug build.
Checks use disposable authenticated software displays.
They do not qualify native GPU performance, physical controls, or full workspace security.
No system configuration or operator session changed.

P07 remains incomplete. Object manipulation is next.
The remaining Spatial-M1 and hardware milestones are mandatory.
The full project goal remains active.
