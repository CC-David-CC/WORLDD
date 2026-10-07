# Recovery transport migration 0.14.0

## Reason and invariant

P09 requires real crash boundaries, durable surface slots, and stale-handle checks.
The existing contract defines these operations. Its protobuf transport cannot encode them.
The first recovery test therefore fails inside the harness before reaching production.

This migration adds typed transport for the existing requirements.
It preserves all 151 acceptance identifiers, assertions, oracles, and native witnesses.
It does not change the normative contract or any existing wire field number.

The predecessor is commit a4b46f190f71ea4fa02b1fbdbb5110033d908dbd and acceptance lock 0.13.0.
The companion recovery-transport-migration-0.14.0.json records exact file hashes.

## Interfaces

FaultArmRequest uses enums for the point, action, and storage error.
The semantic errno parameter maps to the storage_error field.
Unspecified enum values cannot select a valid fault by default.
Fault controls remain outside ActorActionRequest.
Production fault execution must require explicit fixture authority.

SurfaceAttachSlotRequest names a live surface and a durable slot separately.
SurfaceValidateRequest carries the complete surface identity and uint64 generation.
Acknowledgments retain boolean presence. Missing status cannot become an implicit result.

The production dispatcher returns UNIMPLEMENTED for all three new operations.
No crash hook, storage fault, attachment, or validation success is claimed by this migration.

## Review and checks

The primary coding agent reviewed this addition under David's hardware-failure waiver.
No local-model review is claimed.
Eight added transport checks cover enum conversion, missing fields, invalid values, and identity preservation.
They also check response presence and separation from actor actions.
These checks validate transport only. Production effects require independent acceptance evidence.

Both hosts passed Debug and ASan/UBSan regression qualification:

- All 138 P00-P08 acceptance tests.
- All 199 core checks.
- All 51 harness checks, including the eight new transport checks.

Three P09 diagnostics now reach the production dispatcher.
Fault arming and slot attachment report UNIMPLEMENTED errors.
Stale-handle validation fails its assertion because UNIMPLEMENTED is not the required STALE_TARGET result.
Coverage is 138 passed, one assertion failure, two errors, zero skipped, and ten unrun.
P09 remains incomplete. These failures are preserved as implementation requirements.

The diagnostic runner initially classified every unimplemented operation as an error.
The stale-handle assertion disproved that classification. The runner now records the actual failure separately.
The original test results remain intact. No acceptance assertion changed.

Evidence is in evidence/recovery-transport-2026-10-07.
Both host directories retain the predecessor baseline and final Debug and sanitizer results.
Each run records commands, build identity, source hashes, logs, and acceptance reports.
All 103 source hashes match reviewed local files and both hosts.
The source audit verifies all 151 identifiers and every unchanged locked file.

Qualification uses tools/qualify_core.py --through P07 with each host's recorded private XLibre server.
Sanitizer runs add --sanitize --build-dir build-sanitize.
The P08 cumulative gate and three P09 diagnostics run through tools/with_test_display.py.
The diagnostic-commands.json files preserve exact commands.

The tests use authenticated disposable software displays.
Sanitizers cover the runtime and compositor. They do not cover the XLibre server.
No operator-session or system configuration changed.
Full Spatial-M1 and hardware qualification remain pending.
Next: implement the production world.before_commit boundary with explicit fixture enablement.
