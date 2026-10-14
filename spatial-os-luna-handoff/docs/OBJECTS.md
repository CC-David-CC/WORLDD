# Object manipulation

## Protobuf migration 0.12.0

The predecessor acceptance lock SHA-256 is 298936c84aefc56143d2be157cd5b2d736a0cc649290bc8195b3f79801407460.
Add typed object.grab, object.move, and object.release actions.
Add NOT_VISIBLE and OUT_OF_REACH errors.
Add private scene objects, interaction_reach, object_size, and object_color.
Update the acceptance transport codec and explicit fixture configuration.
Only those three locked file hashes change.
All 141 acceptance IDs, assertions, oracles, and native witness sources remain unchanged.
The envelope version remains 1.

## Object authority and geometry

Each object has one live editing lease. Each actor can hold one object.
All three object actions require world.write under the current fixture authority profile.
This fixture grant does not replace the mandatory Spatial-M1 workspace and delegation policies.
Acquisition checks the current room, center distance, and real compositor visibility.
Default center reach is 3 meters, including the exact boundary.
At least one rendered pixel must belong to the object.
A visible fragment permits acquisition even when another object covers its center.
Foreign-room, offscreen, and fully occluded objects cannot be acquired.
If motion changes actor context during the compositor check, return STALE_TARGET.
Also recheck the visible target pose before granting an unleased object.
Another actor can cancel its draft during that renderer wait.
The actor can stop motion and retry with a new sequence.

Objects have non-solid cube proxies. Default side length is 0.2 meters.
Default color is RGB 224,174,72. Size and color use production configuration.
The proxy uses the same six-face geometry for rendering, picking, and observation.
Observation reports the object pose, local proxy bounds, and durable entity revision as generation.
Live preview poses can change without a durable revision change. Observation frame IDs identify each rendered view.
These proxies are not application pixels or rigid bodies.
They cannot receive native application input.

## Preview and persistence

A held object has one authoritative live preview shared by all actors in its room.
Movement validates the complete pose. It can place a held preview beyond acquisition reach.
The object's room remains fixed during editing.
Rendering, picking, and observation use the live preview.
Management world.get and durable snapshots retain the saved object state until commit.
Actor checkpoints do not save object previews.

An explicit commit uses the original entity revision and the normal transactional world update.
Successful commit advances the object and world revisions once, then releases the lease.
Storage failure preserves the draft and lease for an explicit retry or cancellation.
A world edit, deletion, or replacement invalidates the old lease.
Validation compares both the entity revision and its durable world revision.
Reusing an ID with revision 1 cannot revive a lease from a deleted object.
Invalidated edits report STALE_TARGET. They cannot overwrite replacement data.

Cancellation discards the preview without a storage write.
Repeated cancellation is safe. Stop, actor close, and portal crossing also discard the preview.
Crossing back into the same room still cancels the old interaction.
Unfocus only releases application input and keeps an object preview.
A crash restores saved data with no object leases or previews.
Other actors retain independent object and application leases.

## Qualification

The retained baseline rejects object.grab as UNKNOWN_METHOD.
The preceding cumulative P00-P06 gate and harness self-tests passed on the committed portal source.
Both hosts now pass the full unfiltered P00-P07 gate in Debug and ASan/UBSan runtime builds.
Each final run passes 113 acceptance tests, 135 core checks, and 39 harness self-tests.
The core count includes 21 independent object checks.
Legacy coverage is 113 passed, zero current failures, and 28 unrun.

Checks cover shared previews, actual rendering and picking, center reach, partial visibility, and occlusion.
Panels, barriers, and an independent native client can obstruct an object.
Object proxies cannot inject native application input.
Lease checks cover competing actors, independent objects, close, stop, and portal transitions.
Revision checks cover management edits and deletion followed by ID reuse.
Storage failure retains a draft for an explicit retry.
A crash restores saved placement without previews, leases, or old session tokens.
The real-time production clock also passes with custom reach, cube size, and material color.

Review added a final target-pose check after compositor I/O.
That check prevents a cancelled foreign preview from authorizing acquisition at its old visible pose.
The first Debug reports retain the earlier source hashes and 20-case object suite.
Final reports include this correction and the additional real-time configuration check.
All locked acceptance assertions remain unchanged.

An isolated mutation writes each preview into durable storage during object.move.
The crash-recovery witness detects restored X = 0.5 instead of the saved X = 0.
The main implementation stores previews only in live actor state.

Evidence is in evidence/objects-2026-10-07.
Final reports record commands, private server identities, and 84 reviewed source hashes.
All final hashes match both execution hosts and the source prepared for commit.
The tests use disposable authenticated software displays.
Sanitizers instrument worldd and spatialwm, not the private XLibre server.
No system configuration or operator session changed.

P07 is complete for automated software qualification.
P08 must use workspace-owned execution and Git context under the consolidated project instructions.
Record that correction explicitly before changing the older room-owned contract and acceptance cases.
All additional Spatial-M1 and hardware milestones remain mandatory.
The full project goal remains active.
