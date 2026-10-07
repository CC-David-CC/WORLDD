# Native input server component

## Current boundary

The pinned XLibre server now provides the SPATIAL-INPUT extension.
The production client library is src/x11/input.cpp.
Its versioned interface is proto/spatial/input/v1/input.proto.
The build generates C bindings for XLibre and C++ bindings for the client.

This component supplies native device identity and event routing.
Public worldd actor actions do not call it yet.
P06 acceptance remains incomplete.
The full workspace security and application-group lease requirements remain pending.

## Protocol and authority

An X request carries a bounded protobuf message.
The maximum protobuf payload is 4,096 bytes.
The X header carries the payload length. Replies carry a correlated request ID.
Version 1 requests have a strictly increasing, nonzero ID for each X connection.
Malformed messages, unknown fields, unsupported versions, and replayed IDs are rejected.

The requesting connection must own SubstructureRedirect on the server root.
Each seat belongs to that connection.
Another connection cannot operate that seat.
Losing the connection destroys its seats and releases held input.

This controller check is the current private compositor boundary.
It does not establish workspace identity or complete X client isolation.
worldd must check actor authority before it requests native input.
The mandatory M04 profile must qualify immutable connection identity and independent application attacks.

The interface provides seat creation, inspection, button input, key input, cancellation, status, and closure.
Inspection returns a server window generation and current pixel dimensions.
Button requests include the expected generation and surface-local pixel coordinates.
Resize, unmap, and destruction invalidate the generation and cancel affected input.
Resizing away and back also invalidates old requests.

## Native routing

Each seat has a genuine XInput master pointer and master keyboard.
The server also creates the standard companion virtual devices required by XLibre's XKB machinery.
The extension submits events through GetPointerEvents, GetKeyboardEvents, and mieqProcessDeviceEvent.
Normal DIX and XKB processing supplies device state, modifiers, delivery, and implicit grabs.

The small DIX patch selects the authorized window before ordinary desktop hit testing and coordinate clamping.
The selected window's descendants use the native input-shape traversal.
The actor's virtual sprite retains the selected surface's coordinates.
Native window positions remain unchanged.
Different seats retain independent focus and modifier state.

Cancellation releases buttons and keys through the original devices before clearing capture and focus.
Seat closure removes slave devices before their master devices.
Generic device mutation requests cannot change these extension-owned devices.
Application-specific grabs and full application-group control still need integration and qualification.

## Build and qualification

server/base.lock.json records the upstream commits, patch list, and source overlays.
tools/build_xserver.py creates a separate worktree for each distinct patch and overlay set.
The builder verifies cached source content before reuse.
It preserves the pristine upstream checkout and records binary hashes.
The normal desktop server remains installed and running.

Run on sb16 or sb16-2 from this project folder:

~~~sh
python3 tools/build_xserver.py --evidence-dir local/new-input-server
python3 tools/qualify_native_input.py --server-report local/new-input-server/run.json --evidence-dir local/new-native-input
~~~

Use a new directory for each evidence report.
The native qualifier builds the production bridge and the independent C witness.
The diagnostic relay forwards protobuf requests into the production bridge.
The witness observes events directly from XLibre.

The ten component checks cover:

- Surface-local coordinates with overlapping native windows.
- Separate device IDs, focus, and modifier state.
- Ordinary core-X11 clients.
- Surface-local coordinates outside the physical desktop rectangle.
- Balanced cancellation and controller disconnect.
- Destruction and resize-generation invalidation.
- Replayed request rejection.
- Forty seat lifecycle cycles with native device counts returning to baseline.
- Rejection of a connection that does not own the compositor boundary.

The component checks and preceding P05 regressions pass on both hosts.
Retained reports, native witness observations, and source hashes are under evidence/native-input-2026-10-07.
The preceding regressions include 80 acceptance tests, 56 core counterexamples, and 39 harness checks.
These Debug results use authenticated disposable Xvfb displays.
They do not establish physical-device, GPU, performance, or complete P06 acceptance.

The diagnostics exposed two implementation defects before correction.
ActivateDevice returns an X error code, despite the Bool declaration in its header.
DIX reads hotPhys after routing, so that virtual position must match the selected surface.
Retained failure reports include the corresponding source versions.
An additional experiment confirmed XKB's dependency on the standard companion virtual devices.
The final implementation preserves that native device structure.

## Next integration

Add typed pointer, key, scroll, and unfocus actions to the public actor protocol.
Route them through worldd authorization and the compositor's production bridge.
Connect server generations to compositor surface handles.
Maintain application-group control leases and balanced revocation.
Run the focused P06 assertions, then the complete cumulative gate.
Run sanitizer and physical-device qualification before the corresponding completion claims.
