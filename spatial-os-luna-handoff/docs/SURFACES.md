# Live application surfaces

## Production boundary

P05 imports real X11 application windows into the existing compositor.
The compositor owns native resources. worldd owns their spatial placement.
Both use the same surface identity and generation through Protocol Buffers.
The test host forwards requests to these production processes.

The compositor selects the X root's exclusive window-manager event mask.
It also owns manual Composite redirection for root children.
Its output window uses the Composite overlay, outside the redirected application windows.
Startup requires Composite 0.3, Damage, and GLX_EXT_texture_from_pixmap.
An existing window manager or compositor prevents startup.

The compositor enumerates existing root children and follows subsequent native events.
InputOutput windows are registered independently of their title or probe property.
The probe property supports test discovery only.
It does not create a window, a pixmap, or an application identity.

## Pixels and resource lifetime

XCompositeNameWindowPixmap supplies the application's actual backing pixmap.
The compositor binds that pixmap through GLX_EXT_texture_from_pixmap.
It copies damaged content into a compositor-owned OpenGL texture.
It finishes the copy and releases the GLX binding before allowing further X drawing.
It detaches the imported texture from the copy framebuffer before release.
This prevents the framebuffer from retaining a deleted texture.
See the [Khronos object lifetime rules](https://wikis.khronos.org/opengl/OpenGL_Object#Deletion_orphaning).
No CPU screenshot or witness color substitutes for application pixels.

The copy interval briefly grabs the X server to keep X drawing outside the GLX binding interval.
This implementation prioritizes correct ownership over throughput.
Native GPU performance and the 60 FPS target still require later qualification.

Texture orientation follows the FBConfig's GLX_Y_INVERTED_EXT attribute.
Panel UV starts at the top-left.
The rendered surface and actor aim use the same world geometry and native pixel dimensions.
Application alpha is currently rendered as opaque output.

Each compositor session creates a random incarnation prefix.
Each observed window receives a new surface ID within that incarnation.
A reused XID cannot inherit a destroyed surface ID.
Resize and map transitions advance the generation and retire the old pixmap and textures.
Queued lifecycle events invalidate resources even when the final dimensions match the previous dimensions.
This covers a resize away and back before the compositor drains its event queue.

A graphics fence finishes work before retired resources are destroyed.
Resource statistics count actual live and retired resources and invalid bindings.
Destroy removes the surface. Unmap removes its visible and selectable representation.
Remap preserves the live ID with a new generation.

## Placement and transient relationships

surface.place validates the room, pose, and positive physical dimensions before changing placement.
Placement does not move native root coordinates or resize the application.
Overlapping native windows can occupy separate positions in the spatial world.

WM_TRANSIENT_FOR connects a transient to its parent surface.
The child inherits the parent's room, orientation, and pixel-to-world scale.
Its native offset passes through the parent's world transform.
The child sits 0.001 meters in front of the parent in the parent's local frame.
Place the parent to move the application group.

A missing parent, cyclic relationship, or ancestry exceeding 32 surfaces has no visible world placement.
Such metadata affects only the unresolved surface group.
Unrelated applications still render and remain selectable.
Valid replacement metadata restores the relationship.
An unmapped parent also makes its transients unselectable and invisible.

## Protocol migration

acceptance.lock.json handoff version 0.6.0 extends the existing spatial-protobuf/1 transport.
The predecessor lock SHA-256 is:

`9b1379aaffa8dc1496ddeed5a40bc83ec7e31040d48c1258029d1875a42a4710`

Only these previously locked files change in this migration:

- proto/spatial/core/v1/core.proto adds typed surface requests, replies, scenes, and native snapshots.
- harness/wire.py maps the existing semantic surface API to those messages.

The schema adds surface.find, surface.get, surface.place, surface.children, runtime.fence, and runtime.resources.
The private compositor channel adds a synchronization command and a native-resource snapshot reply.
Compositor command IDs increase independently of rendered frame IDs.
Exactly one scene or synchronization operation is accepted in a command.
Stale scene generations do not bind replacement textures.

All 141 legacy acceptance IDs, assertions, oracles, and native witness code remain unchanged.
The existing protobuf authorization and framing rules remain in force.
The management channel still uses trusted inherited IPC.
There is no public actor listener or workspace X security boundary yet.

## Qualification and remaining scope

Both authorized hosts pass the cumulative P05 gate in normal and sanitizer builds.
Each run includes 80 acceptance tests, 39 harness self-tests, and 56 core counterexamples.
Retained evidence is in evidence/surfaces-2026-10-07.
See CURRENT_STATE.md for source and build identities.
The dedicated surface counterexamples cover overlapping native coordinates, rotated transients,
pre-existing clients, client death, concurrent damage and resize, transient cycles,
coalesced resize events, invalid placement, and graceful resource cleanup.
The retained before-fix logs demonstrate the transient-cycle and coalesced-resize failures.
The accompanying source reports identify the production inputs before each fix.
The mutation evidence reverses texture orientation in an isolated source copy.
The locked quadrant test detects blue output where red is required.
The production files still match the successful qualification reports.

The tests run on authenticated disposable Xvfb displays with Mesa llvmpipe.
They do not qualify physical input, GPU scanout, application confinement, or XLibre workspace isolation.

The compositor currently renders requested actor views.
Continuous session presentation and physical input remain later work.
P05 fences drain native lifecycle events and finish graphics work.
P06 must add the actual input-routing fence and actor-specific native input identities.
The live placements in this phase are session state.
P09 adds durable surface slots and explicit attachment recovery.
Application restoration must use an explicit application identity, not a reused XID or window title.
