# Primitive-to-test matrix

141 named acceptance tests across 11 cumulative phases and 130 primitive tickets. Tests are not a claim of implemented or validated product behavior.

## P00 — Production host and strict protocol

Requests are correlated and validated; failure is never successful acknowledgment.

| Primitive | Exact test |
|---|---|
| `protocol.capabilities` | `tests.acceptance.test_00_protocol.ProtocolTests.test_capabilities_are_unique_and_explicit` |
| `protocol.concurrent_correlation` | `tests.acceptance.test_00_protocol.ProtocolTests.test_concurrent_calls_keep_their_responses` |
| `protocol.handshake` | `tests.acceptance.test_00_protocol.ProtocolTests.test_hello_has_version_and_build` |
| `protocol.required_parameters` | `tests.acceptance.test_00_protocol.ProtocolTests.test_missing_parameter_rejected` |
| `protocol.unknown_method` | `tests.acceptance.test_00_protocol.ProtocolTests.test_unknown_method_is_typed_error` |

## P01 — Geometry and exact six-axis motion

One right-handed convention and one body-twist integrator explain all isolated and combined motions.

| Primitive | Exact test |
|---|---|
| `geometry.half_open_pixels` | `tests.acceptance.test_01_geometry.GeometryTests.test_uv_is_half_open_and_never_clamped_into_target` |
| `geometry.identity` | `tests.acceptance.test_01_geometry.GeometryTests.test_identity` |
| `geometry.invalid_pose` | `tests.acceptance.test_01_geometry.GeometryTests.test_zero_and_nonunit_quaternion_rejected` |
| `geometry.inverse` | `tests.acceptance.test_01_geometry.GeometryTests.test_inverse_known_and_seeded_cases` |
| `geometry.quaternion_order` | `tests.acceptance.test_01_geometry.GeometryTests.test_yaw_turns_forward_to_left` |
| `geometry.quaternion_sign` | `tests.acceptance.test_01_geometry.GeometryTests.test_q_and_negative_q_equivalent` |
| `geometry.ray_center` | `tests.acceptance.test_01_geometry.GeometryTests.test_center_ray_returns_top_left_uv_convention` |
| `geometry.ray_miss` | `tests.acceptance.test_01_geometry.GeometryTests.test_parallel_behind_backface_and_outside_are_misses` |
| `geometry.ray_rotated` | `tests.acceptance.test_01_geometry.GeometryTests.test_rotated_panel_uses_its_local_frame` |
| `geometry.ray_validation` | `tests.acceptance.test_01_geometry.GeometryTests.test_zero_direction_and_invalid_extents` |
| `geometry.translation` | `tests.acceptance.test_01_geometry.GeometryTests.test_translation_not_direction` |
| `motion.invalid_time` | `tests.acceptance.test_01_geometry.GeometryTests.test_negative_time_rejected` |
| `motion.partition_invariance` | `tests.acceptance.test_01_geometry.GeometryTests.test_same_twist_same_result_at_different_tick_rates` |
| `motion.se3_constant_twist` | `tests.acceptance.test_01_geometry.GeometryTests.test_simultaneous_turn_and_forward_follow_arc` |
| `motion.six_signed_axes` | `tests.acceptance.test_01_geometry.GeometryTests.test_axis_r0_neg` |
| `motion.six_signed_axes` | `tests.acceptance.test_01_geometry.GeometryTests.test_axis_r0_pos` |
| `motion.six_signed_axes` | `tests.acceptance.test_01_geometry.GeometryTests.test_axis_r1_neg` |
| `motion.six_signed_axes` | `tests.acceptance.test_01_geometry.GeometryTests.test_axis_r1_pos` |
| `motion.six_signed_axes` | `tests.acceptance.test_01_geometry.GeometryTests.test_axis_r2_neg` |
| `motion.six_signed_axes` | `tests.acceptance.test_01_geometry.GeometryTests.test_axis_r2_pos` |
| `motion.six_signed_axes` | `tests.acceptance.test_01_geometry.GeometryTests.test_axis_t0_neg` |
| `motion.six_signed_axes` | `tests.acceptance.test_01_geometry.GeometryTests.test_axis_t0_pos` |
| `motion.six_signed_axes` | `tests.acceptance.test_01_geometry.GeometryTests.test_axis_t1_neg` |
| `motion.six_signed_axes` | `tests.acceptance.test_01_geometry.GeometryTests.test_axis_t1_pos` |
| `motion.six_signed_axes` | `tests.acceptance.test_01_geometry.GeometryTests.test_axis_t2_neg` |
| `motion.six_signed_axes` | `tests.acceptance.test_01_geometry.GeometryTests.test_axis_t2_pos` |
| `motion.zero_time` | `tests.acceptance.test_01_geometry.GeometryTests.test_zero_time_is_identity` |

## P02 — Durable world objects

Each durable edit is validated and revision-checked before it commits; runtime X identity is not durable identity.

| Primitive | Exact test |
|---|---|
| `world.concurrent_writers` | `tests.acceptance.test_02_world.WorldTests.test_exactly_one_compare_and_swap_wins` |
| `world.crash_durability` | `tests.acceptance.test_02_world.WorldTests.test_sigkill_preserves_acknowledged_mutation` |
| `world.delete` | `tests.acceptance.test_02_world.WorldTests.test_delete_and_missing_get` |
| `world.external_labels_not_shell` | `tests.acceptance.test_02_world.WorldTests.test_labels_are_data_including_unicode` |
| `world.immutable_type_identity` | `tests.acceptance.test_02_world.WorldTests.test_identity_and_kind_cannot_be_patched` |
| `world.optimistic_concurrency` | `tests.acceptance.test_02_world.WorldTests.test_stale_revision_does_not_overwrite` |
| `world.pose_update` | `tests.acceptance.test_02_world.WorldTests.test_update_advances_revision_once` |
| `world.referential_integrity` | `tests.acceptance.test_02_world.WorldTests.test_object_cannot_point_at_missing_room` |
| `world.restart_durability` | `tests.acceptance.test_02_world.WorldTests.test_real_process_restart_preserves_objects` |
| `world.room_create` | `tests.acceptance.test_02_world.WorldTests.test_create_get_list` |
| `world.room_delete_referenced` | `tests.acceptance.test_02_world.WorldTests.test_nonempty_room_delete_rejected` |
| `world.snapshot_consistency` | `tests.acceptance.test_02_world.WorldTests.test_room_move_is_one_reference_change` |
| `world.unique_identity` | `tests.acceptance.test_02_world.WorldTests.test_duplicate_id_rejected_without_mutation` |
| `world.validation_is_atomic` | `tests.acceptance.test_02_world.WorldTests.test_invalid_pose_does_not_partially_update_label` |

## P03 — Actors, input lifetimes and grants

Each actor has independent state; stale or unauthorized actions cannot move or control it.

| Primitive | Exact test |
|---|---|
| `actor.checkpoint` | `tests.acceptance.test_03_actors.ActorTests.test_checkpoint_restores_pose_but_not_held_input_or_token` |
| `actor.deadman` | `tests.acceptance.test_03_actors.ActorTests.test_stale_input_stops_at_exact_timeout_boundary` |
| `actor.deadman_refresh` | `tests.acceptance.test_03_actors.ActorTests.test_new_sample_refreshes_deadman_not_unrelated_commands` |
| `actor.deadzone` | `tests.acceptance.test_03_actors.ActorTests.test_deadzone_is_rescaled_not_subtracted_every_tick` |
| `actor.disconnect` | `tests.acceptance.test_03_actors.ActorTests.test_disconnect_revokes_token_and_stops_motion` |
| `actor.frame_partition` | `tests.acceptance.test_03_actors.ActorTests.test_motion_matches_with_different_clock_partitions` |
| `actor.identity` | `tests.acceptance.test_03_actors.ActorTests.test_human_and_agent_have_independent_state` |
| `actor.input_validation` | `tests.acceptance.test_03_actors.ActorTests.test_axis_count_and_range_checked` |
| `actor.local_frame` | `tests.acceptance.test_03_actors.ActorTests.test_forward_uses_camera_orientation` |
| `actor.permissions` | `tests.acceptance.test_03_actors.ActorTests.test_missing_navigation_grant_blocks_motion` |
| `actor.simultaneous_axes` | `tests.acceptance.test_03_actors.ActorTests.test_translation_axes_not_normalized_to_a_single_axis` |
| `actor.stale_sequence` | `tests.acceptance.test_03_actors.ActorTests.test_replayed_sequence_not_applied_twice` |
| `actor.stop` | `tests.acceptance.test_03_actors.ActorTests.test_stop_is_immediate_and_idempotent` |
| `actor.token_binding` | `tests.acceptance.test_03_actors.ActorTests.test_payload_cannot_impersonate_another_actor` |

## P04 — Real compositor rendering

Captured/presented pixels are generated by the same renderer and geometry used for selection.

| Primitive | Exact test |
|---|---|
| `render.actor_view` | `tests.acceptance.test_04_render.RenderTests.test_independent_cameras_produce_different_frames` |
| `render.backface` | `tests.acceptance.test_04_render.RenderTests.test_back_facing_panel_not_visible_or_selectable` |
| `render.damage_of_scene` | `tests.acceptance.test_04_render.RenderTests.test_scene_edit_appears_in_next_fenced_frame` |
| `render.depth` | `tests.acceptance.test_04_render.RenderTests.test_nearer_surface_occludes_farther_independent_of_creation_order` |
| `render.pick_depth_agreement` | `tests.acceptance.test_04_render.RenderTests.test_pick_returns_visible_nearest_surface` |
| `render.projection` | `tests.acceptance.test_04_render.RenderTests.test_fixed_fov_projects_known_quad_bounds` |
| `render.real_frame` | `tests.acceptance.test_04_render.RenderTests.test_frame_is_readable_and_has_monotonic_identity` |
| `render.removal` | `tests.acceptance.test_04_render.RenderTests.test_deleted_surface_disappears_from_pixels_and_pick` |
| `render.resize_output` | `tests.acceptance.test_04_render.RenderTests.test_output_aspect_does_not_stretch_square` |
| `render.room_visibility` | `tests.acceptance.test_04_render.RenderTests.test_other_room_is_not_visible_even_at_same_coordinates` |

## P05 — Real X surface lifecycle

Live client pixels follow actual map, damage, resize and destruction without losing stable world placement.

| Primitive | Exact test |
|---|---|
| `x11.damage_updates` | `tests.acceptance.test_05_xsurfaces.XSurfaceTests.test_random_client_paint_appears_in_compositor` |
| `x11.destroy` | `tests.acceptance.test_05_xsurfaces.XSurfaceTests.test_destroy_drops_resource_and_pick` |
| `x11.output_independent_readback` | `tests.acceptance.test_05_xsurfaces.XSurfaceTests.test_native_readback_sees_the_compositor_output` |
| `x11.popup_parent_move` | `tests.acceptance.test_05_xsurfaces.XSurfaceTests.test_child_follows_parent_room_and_transform` |
| `x11.popup_relationship` | `tests.acceptance.test_05_xsurfaces.XSurfaceTests.test_transient_is_attached_not_orphaned` |
| `x11.real_surface_import` | `tests.acceptance.test_05_xsurfaces.XSurfaceTests.test_independent_x_window_becomes_surface` |
| `x11.resize_reacquire` | `tests.acceptance.test_05_xsurfaces.XSurfaceTests.test_resize_changes_pixmap_generation_not_world_identity` |
| `x11.resource_lifecycle` | `tests.acceptance.test_05_xsurfaces.XSurfaceTests.test_resize_storm_has_no_outstanding_retired_resources` |
| `x11.texture_orientation` | `tests.acceptance.test_05_xsurfaces.XSurfaceTests.test_all_four_quadrants_reach_correct_frame_locations` |
| `x11.unmap_remap` | `tests.acceptance.test_05_xsurfaces.XSurfaceTests.test_unmapped_not_visible_or_interactive_then_returns` |

## P06 — Independent native actor input

Actual XI2 events reach the selected live surface in local coordinates without a root-coordinate atlas or cross-actor leakage.

| Primitive | Exact test |
|---|---|
| `input.background_no_clickthrough` | `tests.acceptance.test_06_input.InputTests.test_miss_does_not_fall_through_to_x_root_geometry` |
| `input.cancellation_releases` | `tests.acceptance.test_06_input.InputTests.test_stop_releases_keys_and_buttons_in_real_client` |
| `input.destroy_during_drag` | `tests.acceptance.test_06_input.InputTests.test_destroy_during_drag_clears_held_state_without_retargeting` |
| `input.disconnect_releases` | `tests.acceptance.test_06_input.InputTests.test_disconnect_releases_in_native_client` |
| `input.double_click` | `tests.acceptance.test_06_input.InputTests.test_double_click_is_two_real_balanced_clicks` |
| `input.drag_capture` | `tests.acceptance.test_06_input.InputTests.test_drag_release_goes_to_original_target` |
| `input.focus_keyboard` | `tests.acceptance.test_06_input.InputTests.test_keyboard_is_received_by_focused_client` |
| `input.independent_seats` | `tests.acceptance.test_06_input.InputTests.test_two_agents_type_concurrently_without_stealing_focus` |
| `input.modifiers` | `tests.acceptance.test_06_input.InputTests.test_modifier_state_affects_real_client_text` |
| `input.no_focus` | `tests.acceptance.test_06_input.InputTests.test_typing_without_focus_is_rejected` |
| `input.no_hidden_atlas` | `tests.acceptance.test_06_input.InputTests.test_interaction_does_not_move_native_window_to_private_slot` |
| `input.occlusion` | `tests.acceptance.test_06_input.InputTests.test_hidden_client_does_not_receive_click` |
| `input.one_surface_lease` | `tests.acceptance.test_06_input.InputTests.test_two_actors_cannot_mutate_one_surface_at_once` |
| `input.permission` | `tests.acceptance.test_06_input.InputTests.test_actor_without_interaction_grant_cannot_type` |
| `input.ray_to_client_pixel` | `tests.acceptance.test_06_input.InputTests.test_click_reaches_real_client_at_expected_pixel` |
| `input.right_middle_buttons` | `tests.acceptance.test_06_input.InputTests.test_each_mouse_button_preserves_identity` |
| `input.scroll` | `tests.acceptance.test_06_input.InputTests.test_vertical_scroll_generates_balanced_client_scroll_events` |
| `input.stale_surface_generation` | `tests.acceptance.test_06_input.InputTests.test_resize_invalidates_previously_aimed_target` |

## P07 — Rooms, portals and manipulation

Movement, visibility, reach and object ownership describe one world; cancellation never commits a preview.

| Primitive | Exact test |
|---|---|
| `actor.observation` | `tests.acceptance.test_07_rooms_objects.RoomsObjectsTests.test_observation_contains_own_pose_and_only_visible_room_entities` |
| `collision.slide` | `tests.acceptance.test_07_rooms_objects.RoomsObjectsTests.test_motion_slides_along_wall` |
| `collision.swept_sphere` | `tests.acceptance.test_07_rooms_objects.RoomsObjectsTests.test_thin_wall_cannot_be_tunnelled_through_in_one_tick` |
| `object.cross_room_denial` | `tests.acceptance.test_07_rooms_objects.RoomsObjectsTests.test_object_in_other_room_cannot_be_grabbed` |
| `object.grab_cancel` | `tests.acceptance.test_07_rooms_objects.RoomsObjectsTests.test_cancel_restores_original_pose` |
| `object.grab_commit` | `tests.acceptance.test_07_rooms_objects.RoomsObjectsTests.test_grab_move_commit_persists_pose` |
| `object.grab_disconnect` | `tests.acceptance.test_07_rooms_objects.RoomsObjectsTests.test_disconnect_rolls_back_uncommitted_drag_and_releases_lease` |
| `object.grab_mutual_exclusion` | `tests.acceptance.test_07_rooms_objects.RoomsObjectsTests.test_exactly_one_of_two_agents_can_grab` |
| `object.permission` | `tests.acceptance.test_07_rooms_objects.RoomsObjectsTests.test_navigation_only_actor_cannot_change_world` |
| `object.reach` | `tests.acceptance.test_07_rooms_objects.RoomsObjectsTests.test_out_of_reach_objects_cannot_be_grabbed` |
| `room.portal_aperture` | `tests.acceptance.test_07_rooms_objects.RoomsObjectsTests.test_movement_outside_portal_rectangle_does_not_switch_rooms` |
| `room.portal_backface` | `tests.acceptance.test_07_rooms_objects.RoomsObjectsTests.test_back_to_front_crossing_does_not_activate_one_way_portal` |
| `room.portal_crossing` | `tests.acceptance.test_07_rooms_objects.RoomsObjectsTests.test_walking_through_portal_preserves_remaining_displacement` |
| `room.portal_references` | `tests.acceptance.test_07_rooms_objects.RoomsObjectsTests.test_referenced_destination_cannot_be_deleted` |
| `room.portal_roundtrip` | `tests.acceptance.test_07_rooms_objects.RoomsObjectsTests.test_walk_to_other_room_turn_and_walk_back` |

## P08 — Real Git worktrees and process context

Git owns refs and worktree state; the room determines cwd and argv is never implicit shell syntax.

| Primitive | Exact test |
|---|---|
| `git.actual_status` | `tests.acceptance.test_08_git_processes.GitTests.test_untracked_and_modified_are_distinct` |
| `git.branch_not_directory` | `tests.acceptance.test_08_git_processes.GitTests.test_repository_branch_commit_and_worktree_are_distinct` |
| `git.checkout_isolation` | `tests.acceptance.test_08_git_processes.GitTests.test_two_agents_do_not_share_working_files` |
| `git.clean_removal` | `tests.acceptance.test_08_git_processes.GitTests.test_clean_removal_leaves_branch_intact` |
| `git.dirty_removal` | `tests.acceptance.test_08_git_processes.GitTests.test_dirty_worktree_is_never_silently_discarded` |
| `git.duplicate_branch` | `tests.acceptance.test_08_git_processes.GitTests.test_same_branch_cannot_be_force_checked_out_twice` |
| `git.external_changes` | `tests.acceptance.test_08_git_processes.GitTests.test_direct_git_commit_updates_world_view` |
| `git.idempotency` | `tests.acceptance.test_08_git_processes.GitTests.test_retry_same_operation_does_not_duplicate_worktree` |
| `git.idempotency_payload_mismatch` | `tests.acceptance.test_08_git_processes.GitTests.test_reused_operation_id_with_different_payload_rejected` |
| `git.invalid_ref` | `tests.acceptance.test_08_git_processes.GitTests.test_invalid_branch_name_fails_without_side_effect` |
| `git.permission` | `tests.acceptance.test_08_git_processes.GitTests.test_actor_without_git_grant_cannot_create_worktree` |
| `git.real_worktree` | `tests.acceptance.test_08_git_processes.GitTests.test_create_is_a_real_git_worktree` |
| `git.restart_registry` | `tests.acceptance.test_08_git_processes.GitTests.test_restart_reconciles_real_worktrees` |
| `process.exact_launch_identity` | `tests.acceptance.test_08_git_processes.GitTests.test_process_launch_retry_does_not_spawn_twice` |
| `process.room_worktree_cwd` | `tests.acceptance.test_08_git_processes.GitTests.test_launched_process_runs_in_rooms_actual_worktree` |

## P09 — Actual crashes, reconciliation and durable slots

Acknowledged durable edits survive while ephemeral input and live X identities do not; external effects are reconciled.

| Primitive | Exact test |
|---|---|
| `recovery.after_commit_before_reply` | `tests.acceptance.test_09_recovery.RecoveryTests.test_commit_survives_lost_reply` |
| `recovery.before_commit` | `tests.acceptance.test_09_recovery.RecoveryTests.test_crash_before_commit_keeps_old_state` |
| `recovery.git_side_effect_boundary` | `tests.acceptance.test_09_recovery.RecoveryTests.test_git_effect_completed_before_db_ack_is_reconciled_once` |
| `recovery.kill_repeatedly` | `tests.acceptance.test_09_recovery.RecoveryTests.test_repeated_sigkills_preserve_last_acknowledged_revision` |
| `recovery.process_identity` | `tests.acceptance.test_09_recovery.RecoveryTests.test_worker_identities_refer_to_real_owned_processes` |
| `recovery.real_worker_death` | `tests.acceptance.test_09_recovery.RecoveryTests.test_worker_death_invalidates_session_then_restart_recovers` |
| `recovery.storage_failure` | `tests.acceptance.test_09_recovery.RecoveryTests.test_injected_write_failure_does_not_ack_or_partially_update` |
| `recovery.uncommitted_drag` | `tests.acceptance.test_09_recovery.RecoveryTests.test_crash_does_not_commit_preview_transform` |
| `surface.durable_slot` | `tests.acceptance.test_09_recovery.SlotRecoveryTests.test_replacement_x_window_attaches_to_durable_slot_not_old_xid` |
| `surface.stale_handle` | `tests.acceptance.test_09_recovery.SlotRecoveryTests.test_old_surface_handle_cannot_control_new_window` |

## P10 — Whole-world walkthroughs

A real actor walks into a branch room, interacts with a real X client, works in the real worktree and returns.

| Primitive | Exact test |
|---|---|
| `story.cancel_without_harming_other_actor` | `tests.acceptance.test_10_walkthrough.WalkthroughTests.test_cancelled_agent_leaves_human_usable` |
| `story.observe_act_verify` | `tests.acceptance.test_10_walkthrough.WalkthroughTests.test_agent_observation_and_received_input_agree_on_target` |
| `story.walk_work_return` | `tests.acceptance.test_10_walkthrough.WalkthroughTests.test_walk_into_branch_room_use_real_window_launch_work_and_return` |

