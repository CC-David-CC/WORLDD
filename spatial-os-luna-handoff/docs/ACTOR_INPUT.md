# Actor input integration

This increment connects actor actions to the production compositor and XLibre input extension.
The integrated baseline passes on both authorized hosts. Captured motion subsequently completed automated P06 qualification; see CAPTURED_MOTION.md.

## Protobuf migration 0.7.0

The predecessor acceptance lock SHA-256 is 2db1c72a735b98bd10b172c00429e34db59c428c93fe74af5cc6f5653c448794.
The public protobuf envelope version remains 1.
Add pointer, key, scroll, and unfocus alternatives to ActorActionRequest.
Preserve field presence for button, pressed, keysym, and scroll steps.
Add NO_TARGET, STALE_TARGET, NO_FOCUS, and BUSY error values.
Add focus_generation to ActorStateReply.
The acceptance codec returns the existing contract's complete surface handle for focus.
Add private compositor commands and native actor-state snapshots.
Record the server generation separately from the compositor surface generation.

Only the schema and acceptance codec hashes change in the acceptance lock.
All 141 acceptance test IDs, assertions, oracles, and native witness sources remain unchanged.
The normative contract remains unchanged.
The user authorized Protocol Buffers for acceptance transport.

## Routing and boundaries

worldd checks the actor session, sequence, and interaction grant.
It validates the saved aim against current geometry and surface generations.
A held button keeps the original target.
The compositor resolves an opaque handle to a current native window.
XLibre checks that window's server generation before input delivery.
Each actor uses its own native device pair and keyboard state.
Cancellation releases native keys, buttons, focus, and the control lease.

The initial lease group follows native transient-parent relationships.
This is an interim P06 grouping, not the mandatory broker-owned application identity.
Workspace authorization, immutable X connection identities, and full application-group security remain later mandatory milestones.
Captured pointer motion beyond surface bounds is implemented and qualified in CAPTURED_MOTION.md.
Use the separate motion and release-coordinate witnesses as capture evidence.
Native physical input and GPU acceptance remain pending.

## Validation

Use tools/qualify_core.py --through P06 with an explicit project-built Xvfb.
The CMake witness target enables XI2 using the unchanged independent C source.
No operator display is used.
Retain focused diagnostics before running the cumulative gate.

Both hosts passed the final Debug and AddressSanitizer/UndefinedBehaviorSanitizer runs.
Each run passed 98 acceptance tests, 65 core counterexamples, and 39 harness checks.
Reports and selected independent native witness logs are in evidence/actor-input-2026-10-07.
The nine new counterexamples check authorization order, invalid fields, changed geometry, focus transfer,
actor isolation, transient grouping, unmap cancellation, scroll during capture, and device reclamation.
The conflicting-scroll counterexample failed before the production correction.
No acceptance assertion was weakened.
The private XLibre binary remains the previously qualified Debug server.
No physical-device or GPU result is claimed.

Run these commands separately on each host:

~~~sh
python3 tools/qualify_core.py --through P06 --xserver /absolute/path/to/project/Xvfb --evidence-dir local/new-input-debug
python3 tools/qualify_core.py --through P06 --xserver /absolute/path/to/project/Xvfb --sanitize --build-dir build-sanitize --evidence-dir local/new-input-sanitize
~~~

Use the server path recorded in the retained run.json.
Use new evidence directories.
No system packages, login settings, or operator display settings changed for this increment.
