# World application observation, version 1

Worldd filters application surfaces through the production broker.
The actor identity comes from the authenticated world session.
The application identity comes from the native server through the checked compositor channel.
Window properties cannot supply that identity.

The world scene includes a labeled application only when the broker permits observation.
World admission, placement, workspace availability, and workspace selection do not grant observation.
An authorized observer does not need filesystem access, execution authority, or a selected workspace.
The application must remain live and owned by the configured broker.

The broker client validates result count, order, identity, and explicit boolean presence.
It rejects control permission without observation permission.
A malformed reply closes the broker channel and fails the world authority.

Scene filtering groups surfaces by native server, workspace, and application identity.
One query contains at most 64 distinct application groups.
A room view with more groups returns RESOURCE_EXHAUSTED.
Several surfaces of one application use one group decision.
The limit fails explicitly. It does not silently hide excess authorized applications.

Worldd retains the handles and application identities used for each observation.
It checks authority again after the compositor computes visibility.
It checks authority before each write of queued observation or aim metadata.
These checks also validate surface generation, mapping, identity, and current room.
Loss of authority after rendering discards the observation.
Loss of authority while a reply is queued closes the actor channel.
Bytes already delivered under valid authority cannot be recalled.

Aim and metadata observation use the same filtered scene.
The compositor still calculates actual visibility from real application surfaces.
The integration tests launch the independent C witness through the production workspace broker.
The witness runs inside the application execution boundary.
The dedicated spatial-confined-xprobe target uses the unchanged native/xprobe.c source without sanitizer instrumentation.
ASan shadow memory cannot fit inside the production 4 GiB application address-space limit.
Worldd, workspaced, spatialwm, and their production libraries retain runtime sanitizer coverage.
This test does not relax application resource limits.
Test-only traffic relays inject malformed replies and real revocation at response boundaries.
These relays do not replace the world engine, broker decisions, or native renderer.

Authenticated application input remains disabled until broker control checks and live input cancellation are integrated.
Legacy static world interaction grants cannot control labeled applications.
Explicit diagnostic sessions retain their existing unlabelled-surface behavior.
The privileged management fixture retains its native regression capabilities.

The actor observation reply currently contains visibility metadata, not application pixel payloads.
Filtered pixel delivery to authenticated actors remains required.
Application restoration, session provisioning, physical input, native performance, and soak remain unqualified.
This component does not complete M04, M09, M10, or Spatial-M1.
No locked schema, acceptance test, native witness, or acceptance lock changes are required.
