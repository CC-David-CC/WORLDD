# M08 production coverage gaps from current source review

Current status, 2026-10-19: this report describes historical source44c2bf2.
Source8672cb0 now contains cabinet file-authority projection, positive actor operation paths, and a dedicated registered native cabinet witness.
See tests/core/test_actor_cabinet_gateway.py and tests/core/test_cabinet_native_crosshost.py.
The authorized owner performs the positive operation cases through the actor endpoint.
These cases do not prove a separately delegated peer succeeds with every individual right.
Source coverage does not establish qualification. The full candidate gate and cross-host reconciliation remain pending.
The historical findings below remain for provenance; do not treat them as current implementation blockers.

Review date: 2026-10-18
Current integrated source reviewed read-only: branch `m08/gateway-storage-integration-20261018`, commit `44c2bf26336769cb65680edefd46fcd95ae2d5c9`. The copy-job lifecycle branch remains separate and dirty; its changes are not treated as integrated evidence.

1. Cabinet appearance and authority states remain incomplete. In `src/world/actors.cpp`, presentation create, replay, and move derive `locked` from `WORKSPACE_ACCESS` only (current lines 356, 385, 425, and 492). I found no presentation projection check for project-file rights. Thus current source does not establish “no cabinet without project-file authority,” an empty cabinet for granted empty storage, or locked metadata privacy for the cabinet as distinct from workspace access. The actor gateway tests include a locked-view case and a create-only-without-workspace-access case, but no complete three-state geometry test.

2. Actor-mediated file-operation coverage remains partial. `test_actor_cabinet_gateway_authorized_browse_read_create_and_retry` exercises browse, read, and create. Direct `workspace-file-engine` coverage does not prove the actor-to-broker path for edit, rename, move, trash, restore, or permanent delete. The current gateway test `test_actor_cabinet_gateway_copy_is_typed_unimplemented_and_bounds_input` expects `UNIMPLEMENTED`. The separate lifecycle branch cannot satisfy that gap until reviewed and combined, with successful and denied actor-path cases.

3. No registered cabinet-specific native geometry/pixel witness was found. Existing presentation geometry suites are generic. Their presence does not prove the cabinet representation, locked state, visible target, or metadata privacy through the production compositor.

These are qualification gaps, not proposed contract changes. Keep all M08 acceptance checks locked. Native geometry/privacy and cross-host evidence remain subject to independent qualification review.
