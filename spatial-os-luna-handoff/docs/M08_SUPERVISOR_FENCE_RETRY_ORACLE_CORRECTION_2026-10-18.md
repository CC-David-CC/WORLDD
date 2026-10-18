# M08 supervisor fence retry oracle correction — 2026-10-18

The old native-cleanup and failed-local-commit cases expected the supervisor to exit
immediately after a failed cleanup reply. That contradicted the existing supervisor
contract in src/supervisor/main.cpp: cleanup_once failure sends a correlated error,
keeps the owner alive with cleanup pending, and accepts only an explicit stop retry.
An inspect request returns an error. SIGTERM exits with status 2 and preserves pending
evidence without a checked cleanup receipt.

The fixture now checks the original fence, journal, native-scope, process, and issuer
invariants. It also checks that the first reply has no cleanup receipt, correlated
inspection is refused, a correlated explicit stop retry is accepted but still reports
the injected failure, and SIGTERM exits 2 with the original state preserved. This
changes lifecycle expectations only. It grants no authority and reports no cleanup
success.

The two changed runtime selectors are
SupervisorFenceFailures.test_native_cleanup_uncertainty_preserves_committed_fence
and SupervisorFenceFailures.test_failed_local_commit_never_reports_success.
Both remain UNRUN after this source change on sb16 and sb16-2. Debug, sanitizer, and
the full 12-method registration remain UNRUN.