# P04 rendering and selection

worldd owns the world, actors, and scene revisions.
spatialwm owns an actual X window and OpenGL context.
The test host only forwards requests to worldd.
worldd sends camera and panel data through a private protobuf channel.
No actor tokens, SQLite handles, or alternate world engine enter spatialwm.

## Explicit display and process ownership

worldd starts spatialwm only with an explicit --display argument.
The test host forwards SPATIAL_TEST_DISPLAY as that argument.
Neither process adopts the operator's DISPLAY.
The compositor requests exclusive SubstructureRedirect ownership.
An existing window manager causes startup to fail.

Each parent owns and monitors its child with a pidfd.
Parent death kills the child through PR_SET_PDEATHSIG.
The processes remain in the test-owned process group.
The daemon monitors compositor death while idle.
The private channel has a 1 MiB limit and a ten-second I/O deadline.
A deadline kills the stalled compositor.
The actor scheduler continues ticking while the daemon waits for a frame.
Management requests remain ordered.

## One OpenGL output path

The normal protobuf configuration sets vertical FOV, clipping planes, and background color.
Frame dimensions are limited to 4096 per axis and eight million pixels.
OpenGL 3.3 and ARB_gpu_shader_fp64 are required.
Startup records the actual OpenGL version and renderer.

The shader renders panels into an RGBA8 framebuffer with 32-bit floating-point depth.
It uses reversed depth and explicit near/far clipping.
Equal-depth panels use stable entity-ID order.
Capture reads that framebuffer.
Presentation blits the same framebuffer into the output window and swaps buffers.
Both operations finish GPU work and synchronize the X connection before acknowledgment.
An independent native C witness reads the presented window with XGetImage.

A solid panel has no application pixel extent.
Its aim result contains identity, generation, UV, and distance.
It cannot receive a successful application click.
Live application textures and input belong to P05 and P06.

## Boundary correction

An additional pixel-center test exposed an OpenGL rasterization mismatch.
Ordinary triangle coverage excluded the top edge that panel selection included.
Keep this counterexample in tests/core/test_render.py.

Panel rendering now draws a conservative projected rectangle.
The fragment shader computes panel-local intersection and half-open UV coverage.
This also applies backface and clip rejection.
The rectangle is only a draw bound; it does not supply authoritative geometry.
Clip the panel before perspective division to handle camera-plane crossings.
Pad the bound by one pixel, then let the shader reject pixels outside the panel.
Use double-precision intersections and camera-depth parameterization.
Avoid an unnecessary direction normalization at the coverage boundary.
Projection retains the float precision of the OpenGL vertex pipeline.

The production geometry library supplies both paths with the same poses and panel extents.
Picking chooses the closest front-facing panel in the actor's room.
It applies the configured clip planes.
No test oracle is imported into production.

## Protobuf migration

RenderRequest implements render.capture and render.present.
FrameReply returns path, frame ID, world revision, and output XID.
ActorAimRequest extends the canonical actor action.
RuntimeIdentitiesReply adds the real compositor PID and selected display.
RenderCommand and RenderResult define the private compositor channel.

The lock version advances from 0.4.0 to 0.5.0.
Its predecessor hash identifies the previous complete lock.
Only schema, fixture configuration, and dictionary/protobuf codec hashes change.
All 141 acceptance IDs, test assertions, native witness code, and independent oracles remain unchanged.
The user authorized protobuf for production and acceptance.
The user's hardware-failure instruction waives local-model review for this work.

## Qualification scope

Run tools/qualify_core.py --through P04 with a new evidence directory.
The qualifier creates authenticated disposable displays for graphics tests and P04 acceptance.
It retains source hashes, build identity, commands, and outcomes.
Use --sanitize --build-dir build-sanitize for the sanitizer configuration.

Software OpenGL evidence does not certify native GPU scanout or physical input.
The runtime does not yet import application surfaces or route application input.
It does not yet provide a complete interactive desktop or Spatial-M1.
P05, later phases, and the additional mandatory milestones remain required.

## API references

GLX context creation follows the [Khronos extension specification](https://registry.khronos.org/OpenGL/extensions/ARB/GLX_ARB_create_context.txt).
Double-precision shader operations follow [ARB_gpu_shader_fp64](https://registry.khronos.org/OpenGL/extensions/ARB/ARB_gpu_shader_fp64.txt).
X ownership and drawable operations follow the [Xlib reference](https://xorg.freedesktop.org/archive/current/doc/libX11/libX11/libX11.html).
