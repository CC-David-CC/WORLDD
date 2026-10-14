# Native surface identity migration 0.15.0

The private compositor snapshot needs authenticated application attribution.
The existing NativeSurface schema has no field for that attribution.
The integrity gate correctly rejected its unrecorded schema change.

This migration adds NativeSurface.application_identity at field 10.
It imports the shared ApplicationIdentity message from security.proto.
ApplicationIdentity contains workspace, application, server ID, and connection ID.
It contains no cookie or credential identifier.
Input Inspect returns the same message with native geometry and generation.
Input replies also use explicit DEVELOPMENT and ENFORCED security modes.
An absent mode does not select either mode.

Every existing message field retains its name, number, type, cardinality, and oneof membership.
Every existing enum value retains its number.
All 151 acceptance identifiers, assertions, oracles, and native witnesses remain unchanged.
The normative contract and acceptance codec remain unchanged.
No existing assertion becomes optional, skipped, or less strict.

Only core.proto changes among previously locked files.
The lock also adds security.proto because core.proto now imports its attribution type.
The companion native-surface-identity-migration-0.15.0.json records predecessor and resulting hashes.
The predecessor is commit 972b61f9a3275647ca264c447dd81f27037d5d0e and lock version 0.14.0.

The primary agent reviewed this additive migration under David's local-model review waiver.
There is no local-model review claim.
Commit this schema and build-binding migration separately from production attribution enforcement.
The migration alone does not make server replies carry identity or authorize actor access.

The combined implementation adds independent native and real compositor witnesses.
Both hosts must pass the complete Debug and runtime sanitizer matrix after this migration.
Native-server sanitizer checks remain a separate profile without GLX.
The descriptor audit passed on both hosts.
It verified 481 existing fields, 71 enum values, 135 existing messages, and 151 unchanged acceptance identifiers.
The combined implementation passed the complete Debug and runtime ASan/UBSan matrix on both hosts.
Each profile passed 729 core checks, 51 harness checks, and 151 P10 cases.
Both native-server sanitizer profiles passed 210 focused checks without GLX.
These results qualify the combined implementation. The schema-only commit does not claim production attribution behavior.
Evidence is in evidence/native-surface-identity-2026-10-08.
M04, M09, and Spatial-M1 remain incomplete.
