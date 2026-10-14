# Current native surface handles

surface.validate checks a live surface identity and its current resource generation.
Worldd synchronizes the production compositor before checking the handle.
Validation succeeds only while that native surface has a mapped resource with the requested generation.
Unknown identities, old generations, and unmapped resources return STALE_TARGET.
A missing handle message returns INVALID_ARGUMENT.
Unknown protobuf fields remain invalid.

Resize and remap replace resource generations while preserving the live window identity.
Destroy removes the live identity.
Compositor restart assigns new runtime identities, including for X windows that remain alive with the same XID.
A reused XID never validates a previous runtime handle.
World placement changes do not replace native resource identity or generation.

Validation does not mutate world state, transfer a lease, or inject input.
It does not grant observation, control, workspace access, or execution authority.
Actor input still requires its normal grants, location, target, generation, visibility, and lease checks.
The result describes the current resource at validation time.
A later action must revalidate its own target because native state can change afterward.

## Recovery and verification

Restart with the existing world directory to retain durable slots and other committed world state.
Discover current native surface identities after restart.
Discard old handles and reattach configured live surfaces explicitly.
Do not restore held input, old tokens, or expired leases.
No schema migration or host configuration change is required.

Four added checks use independent X clients, real lifecycle changes, and the production compositor.
They cover read-only validation, unchanged actor authority, malformed handles, resource generations, and restart identity.
They inspect received native input and reject stale handles without sending keys or buttons to replacement windows.
Run focused checks with ctest --test-dir build -V -R '^surface-runtime$'.
The locked case is test_old_surface_handle_cannot_control_new_window in tests/acceptance/test_09_recovery.py.
Both hosts passed 148 P00-P09 acceptance cases, 235 core checks, and 51 harness checks in Debug and ASan/UBSan.
There were zero failures, errors, or skipped cases. The three P10 cases remain unrun.
An isolated mutation accepted stale handles. The locked native witness failed with RpcError not raised.
The first mutation build failed because it left an unused variable. Its corrected build reached the expected failing assertion.
Both attempts remain in evidence/recovery-complete-2026-10-07/sb16/mutation.
Production source never contained the mutation.
Each host's evidence directory records exact commands, build identities, source hashes, and logs.
All 108 source hashes match both hosts and reviewed local files.
P09 is complete. Full Spatial-M1 and native hardware acceptance remain unfinished.
The checks use authenticated private software displays and do not change the operator session.
Local-model review remains waived under David's hardware-failure instruction.
