# M08 gateway evidence case isolation

The gateway fixture assigns each method a retained 0700 child under the wrapper-provided evidence root before inherited setup launches native children. It restores the prior environment during unittest cleanup, including setup failures. The bounded inventory still enforces 64 files, 2,048 entries, and depth 8; caller checks retain 4 MiB per-file and 16 MiB aggregate limits.

Selected host-independent checks before execution:

- `test_repeated_cases_get_distinct_retained_private_leaves`: unique leaves, exact mode, retention, and environment restoration.
- `test_child_process_inherits_the_case_leaf`: child processes inherit the same case leaf.
- `test_cleanup_callback_restores_environment_after_setup_failure`: unittest cleanup after failed setup while preserving its evidence leaf.
- `test_bounded_scan_still_rejects_more_than_64_files`: checks the exact file-cap failure.
- `test_bounded_scan_still_rejects_excess_entries_and_depth`: checks exact entry-cap and depth-cap failures.
- Namespaced helper and gateway-caller import smokes: use source `harness` and existing generated protobuf bindings under the declared `PYTHONPATH`.
- Python syntax compilation: covers the gateway fixture, source helper, and tooling regression.

Primary review found that the first draft imported a helper through the wrong package path and that its entry-limit test hit the file cap first. The original attempt-01 and attempt-02 checks are retained but superseded.

The corrected tooling suite passed all five tests under the source `harness` package path in attempt-03. It verifies exact file, entry, and depth cap failures. The gateway caller and helper import smoke passed with generated bindings from the existing `m08-gateway-envfix-20261017/build-debug/python` tree and the declared source/test `PYTHONPATH` in attempt-05. The first import-smoke invocation failed before module execution because fixture modules consume three path arguments at import time; attempt-04 preserves that invocation error. Python syntax compilation passed in attempt-06. After the final newline repair, the five tooling tests passed again in attempt-07, the caller import smoke passed again in attempt-08, and syntax compilation passed again in attempt-09. All receipts and logs are under `/home/dflanag3/spatial-artifacts/m08-gateway-evidence-case-isolation-20261018`.

These were source and tooling checks on middle-child. No gateway runtime ran there. Native gateway runtime remains for the primary's unchanged 23-case overlay on sb16 and sb16-2.
