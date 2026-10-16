# M07 acknowledgement phase timeline diagnostic

This change adds test-only monotonic timing to the third native Git-import contention scenario. It does not change production code, assertions, or the six-second final barrier deadline.

The timeline records:

- Git-import request write and reply receipt.
- Initial Git discovery and bounded later presence/absence samples.
- Actor-revocation execute request start and reply receipt.
- Application-exit witness start, duration, process counts, Git count, and pending-reply state.
- The final barrier wait start and each barrier execute request start/end.
- Decoded barrier revision, participant count, pending count, completion, and applied-revision counts.
- Final barrier completion or final barrier exception type.

Records omit raw participant IDs, reply bytes, credentials, and payloads. The timeline holds at most 256 records and reserves 16 slots for essential events. Git process samples are separately capped at 64. Excess records set `ack_timeline_truncated` and increment `ack_timeline_dropped_count`. Essential events can replace a retained noisy sample when the record cap is reached.

The fixture stores this data in its existing `contention.json` measurement object and final cleanup path. Existing `authority_diag` capture remains unchanged. The deterministic recorder checks verify the cap, truncation marker, preservation of essential events, and late first Git discovery.

## Selected test plan

- `AckTimelineRecorderTests.test_ack_timeline_stays_bounded_and_marks_truncation`: verify the hard record bound and explicit truncation.
- `AckTimelineRecorderTests.test_essential_ack_event_survives_a_full_sample_buffer`: verify essential event retention under a full buffer.
- `AckTimelineRecorderTests.test_late_first_git_discovery_survives_sample_truncation`: verify first Git discovery remains recorded after ordinary samples truncate.
- `StorageContentionTests.test_native_storage_actor_revocation_during_bounded_git_import`: later authorized full-registration run should anchor world/workspace diagnostics to Git reply and final barrier timings.
- Preserve the existing CTest registration and its method order. Keep the existing six-second final barrier unchanged.

This assignment permits syntax, static, diff, data-guard, and authored-text control-byte checks only. All recorder unit tests and all runtime tests remain UNRUN here.
