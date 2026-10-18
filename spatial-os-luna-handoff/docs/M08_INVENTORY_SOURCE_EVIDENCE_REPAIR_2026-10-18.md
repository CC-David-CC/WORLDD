# M08 inventory source and evidence repair

Status: UNQUALIFIED. Primary review and integration remain pending.
Base: `03ed27a85bd596dccf5647294ac291cfb53d0fc4`.
Branch: `m08/inventory-source-evidence-repair-20261018`.
Host: middle-child.
Checkout: `/home/dflanag3/src/m08-inventory-source-evidence-repair-20261018`.

The M08 runner referenced undefined `M07_REQUIRED` after inventory preflight.
It now forwards `m07.M07_REQUIRED | M08_REQUIRED` to the inherited gate.
M06 adds its own `REQUIRED` set through `required_test_names`.
The harness checks that composition against the full M08 effective required set.
No inventory requirement, inherited gate, or locked acceptance contract changes.

Selected checks were recorded before execution in the external `SELECTED_CHECKS.txt`.
The focused harness passed 11 tests in `runs/tooling-01`.
Cases cover inherited and M08 missing registrations, disabled registrations, duplicates, and malformed inventories.
Both deployed profiles must pass preflight, even when only Debug is selected for execution.
The CLI tests execute the actual entrypoint and replace only the inherited launch function.
They verify success reaches that function and invalid inventory refuses before launch.
The success path checks propagated requirements and selected-profile options.
Syntax checks also passed for the three gate modules and the harness.
Source SHA256 values matched before and after the suite.

Evidence root: `/home/dflanag3/spatial-artifacts/m08-inventory-source-evidence-repair-20261018`.
The unchanged external `suite.sh` supplies the exact replay command.
It runs `python3 -B -m unittest tests.harness.test_m08_deployed_gate -v`.
It uses a new empty `PYTHONPYCACHEPREFIX` and `PYTHONDONTWRITEBYTECODE=1`.
It records source hashes before and after execution and checks they match.
The committed-tree replay is retained separately in `runs/committed-replay-01`.
Its actual outcome belongs to the external handoff and receipts.

## Earlier evidence discrepancy

Earlier evidence remains untouched at:
`/home/dflanag3/spatial-artifacts/m08-qualification-coverage-audit-20261018`.
The authoritative `runs/primary-review-001` failed with five passes and one NameError.
It used the committed tree and a separate bytecode prefix.
Earlier `runs/tooling-check-02` reported the old required-set assertion failure.
Earlier `runs/tooling-check-03` reported six passes using `python3 -B`.
Both earlier receipts identify base `0b6a194e81134e93adc1792a4177b91995b8a302` and untracked M08 files.
The checkpoint committed those files at 09:18:30 UTC, after the 09:16:16 tooling run.
The checkpoint receipt proves a clean committed tree, but does not pin the earlier tested source hashes.
The committed runner has the undefined name and the committed harness has the old assertion.
No M08 bytecode files remained in the original audit worktree during this inspection.

The exact source/evidence divergence cause is unknown.
The retained logs cannot distinguish different tested source from previously loaded bytecode.
`-B` alone prevents bytecode writes; it does not exclude existing bytecode reads.
No skill procedure changes were made because that cause remains unverified.
The separate-prefix replay and hash checks here directly verify this candidate's source.

## Remaining checks

Native runs, deployments, builds, sanitizers, product acceptance, and physical-input checks are UNRUN.
Cross-host behavior, runtime recovery, cabinet pixel privacy, and performance remain UNRUN here.
This tooling repair supplies no M08 or Spatial-M1 qualification claim.
The production coverage gaps in `M08_PRODUCTION_COVERAGE_GAPS_2026-10-18.md` remain pending.
