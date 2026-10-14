# M07 presentation metadata contract - 2026-10-14

This change adds filtered presentation metadata to render and actor geometry messages. It reuses spatial.core.v1.PresentationView, already defined in core.proto and used by ActorActionReply.presentation.

The protobuf field assignments are:

- RenderScene.presentation_views: field 10.
- ActorObservation.presentation_views: field 9.
- ActorGeometryView.presentation_views: field 16.

These fields carry the five public values only: the exact 16-byte representation ID, room ID, canonical pose, positive generation, and locked flag. They do not carry workspace IDs, file data, private receipts, or a usable flag. Use remains subject to a fresh server-side authority check.

Actor observations carry presentation metadata only when exact WORLD_OBSERVE and REPRESENTATION_VIEW scopes permit the record and the renderer reports the object visible in the captured current room. Every reserved presentation OBJECT in the visible list must have one metadata entry, and every entry must match one visible OBJECT. Both directions require matching room, generation, and pose; malformed reserved IDs and reserved IDs on non-OBJECT entries are rejected. Occluded entries are omitted. A locked record remains eligible when the independent checks permit it. RenderScene and ActorGeometryView carry metadata for corresponding current-room geometry, including authorized occluded geometry in the native geometry view.

Projected object identifiers use the reserved prefix workspace-presentation.v1/ followed by 32 lowercase hexadecimal characters encoding the exact representation ID bytes. A malformed reserved identifier is rejected. The reserved namespace belongs only to top-level OBJECT geometry. Panels, surfaces and their handles, barriers, portals, and actors cannot use it. Each projected presentation object must have one matching view. Each view must have one matching object. If a projected Entity has a room, it must equal the view room; native DrawableGeometry has no room field and keeps that validation path. Transform poses must be canonical and equal the projected object pose. Generations must be positive; when the protobuf object includes its revision, it must equal the public generation.

Locked views retain the existing object geometry and use a complementary contrast treatment of the existing configured object color through the native renderer. Ordinary objects keep their previous appearance. Combined geometry remains limited to 4096 objects and other geometry records; metadata does not consume extra geometry slots.

Presentation request types stay in core.proto, where they can use canonical Pose. The private presentation.proto resolve messages do not import core. This keeps the schema dependency acyclic without relocating or duplicating geometry types.

The focused C++ regression checks codec reversibility, malformed IDs, two-way one-to-one validation, namespace collisions across geometry categories, optional Entity-room matching, view room and pose matching, generation checks, locked styling, generation preservation, and protobuf field numbers. Build this check in Debug and sanitizer profiles. Do not execute it on the coding host.

For runtime integration, select workspace-presentation-store and workspace-authority to cover persistent placement and authority filtering, actor-geometry-world-headless to cover broker-to-geometry transport, and geometry-presentation-native to witness locked and unlocked native appearance. Run those only on the approved test hosts after the combined runtime candidate is reviewed. This component checkpoint does not qualify M07.

The CMake protobuf producer graph also closes Python imports for direct target builds. broker-python-protobuf depends on its transitive issuer, key, native-scope, grants, core, security, process-scope, storage, and copy producers. provision-storage-python-bindings exposes that generated import closure as a named target and is included by the production host-session target, including BUILD_TESTING=OFF configurations.
