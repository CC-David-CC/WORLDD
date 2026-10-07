# Actor observation

## Protobuf migration 0.9.0

The predecessor acceptance lock SHA-256 is b371207d39d09561a61c0cd3d26ad8f891bf3b8c1a97244aefdc49e7f06674d3.
The envelope version remains 1.
Add ActorObserveRequest and ActorObservation to the public actor action interface.
Add a private compositor visibility reply and an observation flag.
Only the schema and acceptance codec hashes change in the lock.
All 141 acceptance IDs, assertions, oracles, and native witness sources remain unchanged.
The user authorized Protocol Buffers for acceptance transport.

## Invariant

Observation describes the actor camera and representations that contribute pixels to that camera's frame.
worldd authenticates the actor and consumes its action sequence.
It synchronizes native lifecycle state and constructs the current-room scene.
It retains that scene's pose and revision while the scheduler continues during compositor I/O.

The compositor writes representation indices alongside color through the same fragment shader and depth buffer.
Zero identifies the background.
Each surviving index supplies a visible-pixel count and one top-left-origin sample.
The compositor skips stale native texture generations in both rendering and observation.
worldd converts indices into typed spatial metadata from the submitted scene.
It does not expose native XIDs, probe IDs, labels, credentials, or application semantic content.

Observation uses a separate framebuffer.
It does not map, resize, replace, or repaint another actor's presented view.
Its frame ID shares the compositor's increasing frame sequence.
It does not capture images to disk or obtain an input lease.
It does not refresh motion deadlines.

## Viewport and limits

The optional width and height must appear together.
An omitted viewport uses 512 by 512 samples.
A controller can supply its actual viewport.
Existing render extent limits apply: at most 4096 per axis and 8 Mi pixels total.
The reply identifies the viewport, camera pose, world revision, and frame ID.
Visibility requires a surviving raster sample at that resolution.
Subpixel geometry can be absent. This is raster visibility, not continuous geometric coverage.
A sample direction uses the renderer's focal value and camera-local coordinate convention.
Later actions still require fresh authority, generation, and geometry checks.
An observation does not reserve geometry or promise future visibility.

Current scenes contain solid panels and mapped application surfaces.
Other object geometry, barriers, portals, and actor representations remain later P07 work.
Authenticated fixture actors can observe current-room geometry without receiving navigation or interaction grants.
The fixture has no workspace observation policy yet.
This increment does not qualify workspace confidentiality, separate workspace observation grants, or Spatial-M1 security.

## API references

Use typed integer clearing for the unsigned representation attachment.
See [Khronos glClearBuffer](https://registry.khronos.org/OpenGL-Refpages/gl4/html/glClearBuffer.xhtml).
Route fragment outputs through explicit draw-buffer attachments.
See [Khronos glDrawBuffers](https://registry.khronos.org/OpenGL-Refpages/gl4/html/glDrawBuffers.xhtml).
Read the integer attachment with GL_RED_INTEGER and GL_UNSIGNED_INT.
See [Khronos glReadPixels](https://registry.khronos.org/OpenGL-Refpages/gl4/html/glReadPixels.xhtml).

## Qualification

The retained baseline reports UNKNOWN_METHOD before this implementation.
Both sb16 and sb16-2 passed Debug and ASan/UBSan runtime qualification.
Each run passed 98 cumulative P00-P06 acceptance tests, 79 core checks, and 39 harness checks.
The nine observation counterexamples are part of those core checks.
The focused P07 observation acceptance test also passed in both build modes on both hosts.

The tests compare metadata with rendered pixel counts and sample colors.
They cover room, frustum, facing, clipping, partial occlusion, combined occluders, and coplanar ties.
Rotated cameras, narrow visible strips, and changing viewport aspect ratios also pass.
Independent native clients witness observed targets, input coordinates, surface generations, and unmapping.
An expose event confirms that observation preserves another actor's displayed view and dimensions.
Session, sequence, unknown-field, extent, permission, and motion-expiry checks also pass.

An isolated mutation disabled depth testing only during observation.
The occlusion counterexample detected the missing visible foreground representation.
The main runtime retained depth testing.

Evidence is in evidence/observation-2026-10-07.
Each final runtime report contains 77 source hashes and exact commands.
All hashes matched both execution hosts and the reviewed local files.
The build modes share the same source; sanitizers cover worldd and spatialwm.
The private XLibre Xvfb server remains a Debug build.
These software-display results do not qualify physical controls or native GPU performance.

Legacy coverage is 99 passed, zero current failures, and 42 unrun.
P07 remains incomplete. Swept collision, portals, and object manipulation are next.
The full Spatial-M1 goal remains active.
