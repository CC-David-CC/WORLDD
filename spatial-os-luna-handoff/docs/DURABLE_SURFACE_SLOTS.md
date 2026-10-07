# Durable surface slots

A surface_slot entity owns its durable room, pose, and size in the world database.
A live surface attachment refers to that slot.
The attachment does not store another authoritative placement or change the slot revision.
Surface observation, rendering, picking, and transient placement read the current slot placement.

## Explicit attachment

surface.attach_slot attaches a current native surface to an existing slot.
Another live attachment returns BUSY, including while that attachment is unmapped.
Repeating the same attachment is idempotent.
An explicit attachment to another free slot releases the previous attachment.
The previous slot and its saved placement remain intact.
Invalid surface IDs, missing slots, and incorrect entity kinds leave both placements unchanged.

Resize and remap retain the slot association while native resource generations change.
The compositor retains identity when native input inspection rejects an unmapped window.
Only actual destruction evidence removes that native identity.
Destroying the native surface releases its live association and preserves the slot.
A replacement window requires its own explicit attachment.
The runtime never matches a replacement by title, reused XID, or stale surface identity.

Transient windows follow their native parent and cannot hold independent slot attachments.
If an attached window becomes a transient, it releases its slot association.
Its former durable slot remains intact.
An orphan transient stays unplaced until its native relationship is repaired.
Broker application identity and workspace X isolation remain separate mandatory Spatial-M1 work.

## Placement and deletion

surface.place updates the attached slot through WorldStore's revision-checked transaction.
It does not move native root coordinates or change application pixels.
Direct world.update edits to the slot affect the same live placement.
A failed durable write leaves the slot and live placement unchanged.
The caller can retry after a one-shot storage failure.

A successful world.delete removes the slot and clears its live association.
The application remains alive, with no world placement until another explicit placement or attachment.
A rejected delete retains the association.
Creating another entity with the same ID does not restore the deleted association.

## Restart and recovery

Restart with the same world state directory to restore durable slot entities.
Keep the world database and its SQLite journal together.
Do not copy a running database without its normal backup procedure.
No database migration or host configuration change is required.

Live associations are process state. They do not survive world or compositor restart.
An existing X window receives a new runtime surface identity after compositor restart.
Explicitly attach its new surface identity to the saved slot.
Held input, leases, and actor session tokens are not restored.
Configured application launch and state restoration remain mandatory Spatial-M1 work.
This increment does not infer application identity from window properties.

## Verification

The locked acceptance case replaces a real native window and reattaches its replacement.
Additional tests use independent X clients and the production compositor.
They inspect real pixels, native geometry, generations, world revisions, and persistent snapshots.
They cover exclusive attachment, failed writes, deletion, restart, rebinding, and native transients.
One check repeats real resize, unmap, and remap operations for 64 cycles.
The initial run exposed a native inspection race that incorrectly discarded an unmapped surface identity.
The corrected implementation preserves identity and invalidates the unavailable native resource generation.
The original failing evidence is retained with its tested source manifest.

Run focused checks with ctest --test-dir build -V -R '^surface-runtime$'.
The locked case is test_replacement_x_window_attaches_to_durable_slot_not_old_xid in tests/acceptance/test_09_recovery.py.
Both hosts passed Debug and ASan/UBSan qualification with 231 core checks and 51 harness checks.
The P00-P09 gate reports 147 passes, one assertion failure, zero errors, and zero skips.
Three P10 cases remain unrun. P09 and full Spatial-M1 remain incomplete.
The remaining P09 requirement is stale-handle validation.

All 108 source hashes match both hosts and the reviewed local files.
The acceptance lock, schemas, locked assertions, oracles, and native witness remain unchanged.
Evidence is in evidence/recovery-slots-2026-10-07.
Each host directory includes the baseline, initial lifecycle failure, corrected focused checks, commands, logs, and source hashes.
The full commands remain in debug/run.json, sanitize/run.json, and their p09-command.json files.
Tests use private software displays. Native GPU and physical-device qualification remain pending.
No host configuration or operator session changed.
Local-model review remains waived under David's hardware-failure instruction.
