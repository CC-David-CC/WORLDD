# M07 schema-baseline migration - 2026-10-15

Status: DRAFT, UNQUALIFIED. No project build, runtime, harness, acceptance, native, or sanitizer check ran for this draft.

## Recorded correction

The acceptance lock at d2dd4f1c92457d7f543b235ec973be48eaa46c9d expected core.proto SHA-256 c88d7cf22e7b0ea45509433cdf8c2063fde1c8b376d48101130022e2bcbd68a1. That exact file remains available at baseline commit f019cf77e5a69cd331526624815ef90024368e32. The reviewed M07 candidate file has SHA-256 8241b30befeda1d11215e7faa1145c88b9fdbcbf14711140135233481ddb9873. The original lock bytes have SHA-256 04e97ac6bc7bc7004b684e10c26c86692f7b4f0f60fe23f25d3255d4e5290634.

This draft changes only the core.proto digest among the lock's 41 file digests. The other 40 digests and all 151 unique acceptance IDs remain byte-for-byte equal as parsed JSON values. The lock notice points to this migration record. The source pin and production guard remain unchanged. The guard must continue rejecting every changed locked file.

The independent descriptor review compared the candidate schema with the f019 baseline using libprotoc 3.21.12 and imported descriptors. It preserved all 119 original core messages, six original core enums, the full original security descriptor, and every old field and nested declaration. The only permitted dependency addition is the copy.proto import. The candidate adds four core messages and ten fields:

- Messages: PresentationCreateRequest, PresentationMoveRequest, PresentationRemoveRequest, and PresentationView.
- Fields: RenderScene.presentation_views = 10; ProcessLaunchRequest.configured_application_key = 4; ActorActionRequest fields 25-29; ActorObservation.presentation_views = 9; ActorActionReply.presentation = 6; and ActorActionReply.copy_status = 7.

Descriptor evidence is retained outside Git at /home/dflanag3/spatial-artifacts/m07-schema-lock-independent-review-20261015/. The imported protobuf descriptor-set binary hashes are baseline.pb SHA-256 87ba5906c5900120614a98e2dee01c3efe7b588cad78f61f1838cf304a2fcd78 and candidate.pb SHA-256 844520c25d7dd93fc453e7a157bbb17411a1fbeaa497364e09a2d2c82bd8ba18. Their JSON descriptor-view hashes are baseline-descriptor.json SHA-256 7cb559ba58fe6150d7fa62d1724fc08eea0459cf74d75d61fc3f51ef5cd5e068 and candidate-descriptor.json SHA-256 9b4c045037b6e0df50b90963a0ff000de0be42700a3c13bd74f57f07c84184a6. The report records 18 rejected descriptor mutation controls; negative-controls.json SHA-256 is 47dff6cf6c7403992e13c2f819d2f9cfdbd8b0d776584eb3cda197a31919f74a. The review report SHA-256 is 4376886bb7f41f4c2f762d319c3cc886a729adfb027a407ed4022edeb95343d8.

The descriptor run and mutation controls are independent review evidence. They do not qualify runtime behavior or mixed-version compatibility. Unknown fields and wrong wire types remain rejected. Deploy one complete matching schema, binary, and generated-binding set.

## Semantic limits

ProcessLaunchRequest.configured_application_key selects a registered application definition. It grants no authority. ExecutionStore::launch checks workspace, actor, and profile authority independently. A nonempty key requires a current registered definition and matching profile, executable, and arguments. It binds the key into serialized retry identity. An empty key retains the existing development and read-only launch path.

PresentationView contains presentation identity, room, pose, generation, and locked state. It does not expose workspace identity, paths, application names, file data, receipts, or a usable flag. The existing presentation metadata contract remains in force.

WorkspaceCopyStatus.authorized_destination_workspace_id is field 3 and authorized_manifest_digest is field 4. ExecutionStore::copy_status_for emits these only when fresh authorization succeeds, the phase is VALIDATED, PUBLISHED, or UNCERTAIN, and destination data exists. After revocation, fields 3 and 4 remain absent. Optional error_code is field 6 and may remain visible without destination identity or manifest data. This clarification does not grant additional data access or relax policy.

## Focused counterexamples required before qualification

These are selectors to execute against a frozen combined candidate. This draft records no new runtime pass.

- Unknown fields and wrong wire types: tests.core.test_protocol.ProtocolBoundaryTests.test_unknown_fields_rejected_at_both_levels and tests.core.test_protocol.ProtocolBoundaryTests.test_wrong_wire_type_is_rejected. New actor-action oneof boundaries still need a targeted check if existing transport coverage does not reach them.
- Malformed copy action before effects: tests.core.test_workspace_copy_engine.WorkspaceCopyEngineTests.test_async_actor_copy_rejects_authenticated_malformed_begin_before_start. This exact selector appears in the source file's load_tests list.
- Presentation generation and authorization replay: tests.core.test_workspace_presentation_actor.WorkspacePresentationActorTests.test_cross_room_move_and_remove_replays_are_authorized_and_idempotent and tests.core.test_workspace_presentation_actor.WorkspacePresentationActorTests.test_move_replay_rechecks_world_grants_after_blocking_workspace_query. These must be checked on the corrected candidate.
- Revoked copy-status redaction: tests.core.test_workspace_copy_engine.WorkspaceCopyEngineTests.test_actor_copy_publishes_independent_dirty_git_and_preserves_source_process contains the revoke-and-query assertions at which fields 3 and 4 must be absent. Run the exact method and inspect its named result.
- Configured definition/profile mismatch: the profile-mismatch path inside tests.core.test_workspace_copy_engine.WorkspaceCopyEngineTests.test_copied_policy_mapping_and_exact_publication_receipt_survive_reassignment_restart_and_recopy is relevant. It does not by itself prove key, executable, or argument mismatch coverage.
- Missing focused coverage to add or identify before claiming complete counterexample coverage: valid actor request denied for missing copy authority with no durable effects; unknown or malformed new actor-action payload with no effects; stale configured key; key, executable, or argument mismatch; changed key under a repeated launch operation ID; and an empty-key legacy launch control. The existing profile-mismatch assertion does not establish coverage of the other mismatches. Do not weaken or edit locked acceptance assertions to address these gaps.

No item above is marked passed by this draft. Existing unrelated focused evidence retains its original scope and source pin.

## Next review step

Review the four-file diff and lock-map comparison. Then select and run the focused counterexamples on the frozen combined candidate. Full M07 qualification remains pending on both required hosts.
