# Actor pixel transport migration 0.16.0

Authenticated actor observations currently contain metadata only.
M10 also requires real, authority-filtered application pixels.
This migration adds an explicit pixel request and a bounded image payload.

## Additive fields

- ActorObserveRequest.include_pixels uses field 4.
- RenderCommand.include_pixels uses field 10 on the private compositor channel.
- RenderVisibility.image uses field 4.
- ActorObservation.image uses field 8.
- PixelImage contains width at field 1, height at field 2, and rgb8 bytes at field 3.

Every existing field retains its name, number, type, cardinality, and oneof membership.
Every existing enum value retains its number.
ActorObserveRequest field 3 remains unknown. Its existing rejection regression stays unchanged.
All 151 locked acceptance identifiers, assertions, oracles, and independent native witnesses remain unchanged.
Only core.proto changes among previously locked files.
The companion migration JSON records the previous and resulting hashes.

## Pixel contract

Pixels are optional. The default request returns metadata without an image.
An explicit pixel request returns both metadata and an image from one filtered frame.
The image uses tightly packed RGB8 bytes, with no row padding or alpha channel.
Rows run from top to bottom. Pixels within each row run from left to right.
The byte count is exactly width multiplied by height multiplied by three.
Image dimensions match the observation viewport.

Pixel observations allow at most 262,144 pixels.
The existing general viewport limits also apply.
The default 512 by 512 viewport fits this bound.
Reject excessive pixel dimensions before allocating or rendering an observation target.
The complete protobuf reply must still fit the existing one-MiB frame limit.
Reject an oversized combined image and metadata reply with RESOURCE_EXHAUSTED.
The pixel limit does not reduce existing metadata-only viewport limits.

The compositor reads the live offscreen color attachment used for that observation.
It does not present the observation on another actor's display.
It does not create a capture file or export a host path.
The checked compositor client rejects missing, unsolicited, truncated, or incorrectly sized images.

Apply the existing application-observation checks before rendering and after rendering.
Revalidate authority before each queued actor-channel write.
Revocation prevents further export of queued image bytes.
Bytes already delivered to an authorized actor cannot be recalled.
Pixel observation does not grant application control, workspace selection, or execution authority.

## Review and qualification

The primary agent reviews this migration under David's local-model review waiver.
There is no local-model review claim.
Record descriptor preservation and unchanged acceptance identifiers on both hosts.
Commit the reviewed schema migration separately from production pixel delivery.
The schema-only commit does not claim that an image is produced.

Run real native pixel and revocation regressions before integration of production delivery.
Run the complete Debug and runtime ASan/UBSan matrix on both hosts.
Keep native-server GLX sanitizer, physical-input, provider, and full M1 qualification separate.

Both hosts passed the descriptor audit.
It preserved 488 existing fields, 74 enum values, 136 messages, and all 151 acceptance identifiers.
Both hosts passed the schema-only Debug build, 35 focused core checks, 51 harness checks, and 151 P10 cases.
There were no failed or skipped cases in those profiles.
See evidence/actor-pixels-schema-2026-10-08.
Production pixel delivery remains unimplemented at this schema checkpoint.
