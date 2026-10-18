# M08 native management-loss cleanup

At base `dc629027e19d3e8c8d4ba7b9ffe198afc89d9413`, the reproduced disconnect path attempted failure fencing and returned. It did not invoke explicit checked cleanup. The observed journal stayed at `START_ATTEMPTED`, so `checked_cleanup` failed. The supervisor log also reported the private participant channel had closed.

On a fresh management disconnect, the supervisor now runs bounded local scope cleanup and exits with failure. It does not fence, acknowledge, or release issuer activation. If cleanup was already pending, it preserves that unresolved record and does not claim terminal cleanup.

Selected checks and purpose:

- `test_management_loss_stops_owned_child_and_records_cleanup`: require checked scope cleanup and unchanged activation rows.
- `test_explicit_stop_preserves_unresolved_issuer_generation`: retain issuer uncertainty during explicit stop.
- `test_unconsumed_authorization_cannot_be_fenced`: reject strict fencing before activation is consumed.
- `test_native_cleanup_uncertainty_preserves_committed_fence`: retain a committed fence when native cleanup fails.
- `test_failed_local_commit_never_reports_success`: retain failure when the fence journal commit fails.

All selected tests remain UNRUN on this branch. No acceptance contract or locked test changed.