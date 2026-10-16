# Owned application stop ordering (UNQUALIFIED)

`ApplicationProcess::stop()` starts one absolute two-second deadline, issues owned scope cleanup and namespace/direct-child kill requests, then confirms namespace exit, cgroup emptiness, and direct-child reap against that deadline. Signals alone never confirm stop. The default `ProcessScope::stop()` retains its two-second contract for other callers.

The focused `process-termination` regressions add `test_owned_cgroup_kill_precedes_namespace_wait_failure` and `test_reaped_application_exit_is_a_confirmed_stop`. The first places an independent sidecar inside the real authority cgroup, injects `poll/ppoll(nfds=1)` failure after launch, expects fail-closed status 2, and requires the sidecar pidfd to become readable. Both are source-ready only and unrun. The shared-deadline path has no dedicated timing regression yet.

The change does not establish that the historical M07 failure is fixed. Qualification and runtime evidence remain pending.
