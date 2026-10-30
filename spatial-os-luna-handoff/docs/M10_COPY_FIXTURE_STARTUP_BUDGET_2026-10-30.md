# M10 copy fixture startup budget

Status: test repair in progress. M10 remains unqualified.

## Prior candidate result

Candidate `a2a6adeb020c7648d8223d1902248051099c3cae` did not complete qualification.
sb16 stopped with an incomplete gate and no recorded Debug failure.
sb16-2 failed `workspace-copy-absolute-deadline` before accepting a copy request.
One of fourteen sequential app writers missed the test's ten-second readiness window.
The assertion fired 238 microseconds after that window.
The retained failure does not identify the app or prove whether it later wrote state.
See the external stop receipts and the sb16-2 failure manifest under `spatial-artifacts/m10-revocation-denial-reply-20261030/runs/`.

## Repair

The test grants each writer sixty seconds to update its declared state.
It records each writer key and readiness time.
Timeout errors report pending keys, data/config domains, and observed counter values.
Only `workspace-copy-absolute-deadline` receives an extended 1,800-second test service and CTest budget.
Its runner budget is 1,740 seconds.
Other copy fault tests keep their existing budgets.
The tested copy operation still has its original 300-second absolute deadline.
The completion, no-publication, and status-observation assertions remain unchanged.
This repair changes test setup tolerance, not production behavior or acceptance semantics.

## Checks recorded before execution

1. Inspect the source diff and validate Python syntax and CMake timeout registration.
2. Run the delegate runtime-bound tests on sb16 and sb16-2.
3. Run `workspace-copy-absolute-deadline` in Debug on both hosts.
4. Preserve the prior sb16-2 failure and compare each app's measured readiness time.
5. Freeze the repaired candidate and run full Debug and ASan/UBSan gates on both hosts.
6. Run the required cross-host M06 semantic/native geometry gate after both local gates pass.

The first-pointer denial race remains unmodified and visible in its test results.
David approved documenting that race as a known exception only if all other checks pass.
Report its result and this exception explicitly; do not convert the test to a pass.
