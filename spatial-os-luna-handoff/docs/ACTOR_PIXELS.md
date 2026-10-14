# Authenticated actor pixels

An actor can request pixels with ActorObserveRequest.include_pixels.
The reply contains an optional PixelImage beside the existing observation metadata.
The image and metadata describe the same camera, viewport, world revision, and rendered frame.
Metadata-only requests retain their previous behavior.

The production compositor renders the actor's authorized scene into its isolated observation target.
It reads the real color attachment after rendering the live application textures.
It returns packed RGB8 bytes in top-left row order.
The image dimensions match the requested observation viewport.
The compositor does not present this observation or create an image file.

Pixel viewports contain at most 262,144 pixels.
The default 512 by 512 viewport fits that bound.
The complete protobuf message still has the existing one-MiB limit.
Excessive dimensions fail before renderer allocation.
Oversized combined metadata and image replies fail with RESOURCE_EXHAUSTED.
Metadata-only observations retain their larger existing viewport limits.

The checked compositor client rejects missing or unsolicited images.
It also rejects changed dimensions, incorrect byte lengths, unknown fields, and mismatched frame identity.
Only a validated image reaches the actor reply.

Application observation requires the existing workspace and application grants.
Observation does not require workspace selection or grant application control.
Read the existing APPLICATION_OBSERVATION.md contract for authority scope.
Worldd checks authority before rendering, after rendering, and before each queued channel write.
A revoked observation closes the channel before further queued image export.
Previously delivered authorized bytes cannot be recalled.
Revoking observation does not terminate independently authorized application execution.

## Evidence and current limits

The first baseline passed three checks and failed eleven on both hosts.
The failures showed missing images and missing pixel-specific viewport limits.
The first implementation passed thirteen of fourteen native checks.
The remaining fixture omitted its occluding panel's required label.
That fixture correction retains all pixel and authority assertions.

Both hosts then passed fourteen real native pixel checks and nine checked-client protocol checks.
The same focused run passed twenty-two existing rendering and observation checks.
The native tests use signed actors, confined processes, the production compositor, and the unchanged C witness.
They verify real quadrants, live repaint, resize, room filtering, occlusion, and application scope.
They also verify revocation before export and during a partially written image reply.
The protocol tests use an independent adversarial peer to test RenderWorker validation.
That peer provides transport counterexamples. It does not qualify native rendering.

The schema migration is committed separately as 71386ba45d9f55368e5b1bb53913bbf30bcdce27.
Both hosts passed complete Debug and runtime ASan/UBSan qualification.
Each profile passed 914 core checks across 47 suites, 51 harness checks, and all 151 P10 cases.
No final profile failed or skipped a case.
Evidence is in evidence/actor-pixels-2026-10-08.
Native-server GLX sanitizer coverage remains unrun for this component.
Physical input, native GPU timing, provider export, model tasks, and full M1 qualification remain incomplete.
