First geometry Debug qualification failure

Status: UNQUALIFIED. The M06 milestone gate remains pending.
Source: 8bdda7631ae51d0ae1e94b913031d0fd8dffb877.
Task branch: m06/actor-geometry-view.
Base: 5368e7d280c1ad7f366c37a6b662bce64fb95bcd.
All 18 frozen source-overlay hashes match this commit.
Current task-checkout HEAD had advanced to d0194a7e30350f155e1b74e1e9d5c9b775e8adc0 during collection.
The source audit uses git show at 8bdda, not changed working files.

Observed results

Both hosts produced the same outcomes.
The hostile-peer probe passed all 19 named checks. Its command and wrapper exited 0.
The headless geometry suite ran 9 cases. Six passed and three failed. No case was skipped.
The headless command, its wrapper, and the outer runner exited 1.
The sb16 headless suite took 256.053 seconds. The sb16-2 suite took 215.682 seconds.
Total executed cases: 56. Passed: 50. Failed: 6.

The other seven selected Debug suites are UNRUN on each host:
actor-session-client, actor-session-client-tls, actor-transition-coordinator,
actor-transition-real-world, session-application, world-authority, world-identity.
All nine ASan-UBSan suites are UNRUN on both hosts.
Prepared commands in preflight.json do not prove execution.
The current runtime-directory observation contains only the first two Debug suite directories.

Observed failure 1: revocation and preparation

Frozen test_geometry_view.py:78 expects UNAUTHORIZED (12) after grant revocation.
Both raw replies report FORBIDDEN (13): Actor lacks active world observation authority.
Frozen identity_sessions.cpp:540-548 distinguishes inactive channels from lost observation authority.
An inactive/prepared channel returns UNAUTHORIZED. An active channel without observation authority returns FORBIDDEN.
The earlier prepared-channel assertion at test line 73 did not fail.
This is an observed test-expectation/production-code discrepancy. It is not evidence of successful unauthorized observation.
The failing assertion did not check absence of a geometry payload or continued session availability afterward.

Observed failure 2: real edit and second actor motion

Frozen test_geometry_view.py:98 asserts that object_grab returns an action payload.
Both logs report only AssertionError: False is not true.
The assertion supplied no response body. The actual error code and message were not recorded.
Earlier motion and stop assertions completed before the grab assertion failed.
The edit, release, and post-edit revision/pose assertions were not reached.

Separate source-level inference:
The test uses scoped_world(), whose native argument defaults to False at test_world_authority.py:108.
Frozen objects.cpp:49 requires compositor observation before acquiring an object.
Frozen actors.cpp:597 throws UNIMPLEMENTED when no renderer exists.
A headless object grab therefore appears incompatible with the existing visibility requirement.
UNIMPLEMENTED was not observed in this run's retained response. Do not report it as the actual runtime error.
Do not remove object visibility or authority checks to make this test pass.

Observed failure 3: encoded snapshot size

Frozen test_geometry_view.py:188 expects INVALID_ARGUMENT (1).
Both raw replies report RESOURCE_EXHAUSTED (11): Snapshot exceeds frame limit; no partial snapshot returned.
Frozen store.cpp:324-332 emits this exact resource error when the complete snapshot exceeds its frame budget.
The separate geometry count-limit case passed.
The assertion after the size-error check was not reached, so this case does not prove continued session availability.
The error message states no partial snapshot. This failed test did not independently assert payload absence.

Retained diagnostics

The issuer reported delayed work during the revocation case.
sb16: work_ms=1541, poll_ms=10, monotonic_us=115225260596.
sb16-2: work_ms=884 and 983, poll_ms=10, monotonic_us=115143367484 and 115144842422.
The duplicate issuer/relay logs preserve the same events.
These lines do not establish a cause for the three assertion failures.

Frozen artifact facts

qualification-input.json SHA256:
46ee59d020ba998c751798ed9f98e142b20b7b2f7b54bb0cee454d6d5cf526e0
Debug archive SHA256:
0a8f377f289f271529ca60338003db965a16ef57b0b138448b4880cbaf874985
Debug inventory SHA256:
bdeb59ff2dd13b22a9d97416001a31cef3451f67712af73724b2bd44ed13ff63
ASan archive SHA256 (staged; runtime UNRUN):
82a326fb3f2982b7b78b9555c4ecf8b8d27f0c6a5ddcb13ffdd29d46b9aac336
ASan inventory SHA256:
6777eb6be82246ae5799d457b5db8178c343a91f93d90abd91c4edc4b314c890
Both executed bundle test_geometry_view.py files SHA256:
b736b882014ed21befd2f963517e972b440aa6d77f0dc3b11da1b1bbf3aa391d

Each raw archive retains exact first-two-suite and outer stdout/stderr/exit bytes.
It also retains command receipts, argv, preflight.json, selection.json, and witness logs.
Command-receipt Git fields report unknown because the deployed bin directory has no Git checkout.
Those fields are not used as source provenance. The frozen overlay audit and artifact receipts supply provenance.
The two collection receipts list original paths, byte lengths, hashes, and credential-pattern scan results.
All original file hashes were verified again inside each local raw archive.
No package binaries, runtime databases, credentials, or private keys were included.

Current cleanup observation

At 2026-10-12T14:53:38Z on sb16 and 14:53:40Z on sb16-2:
The exact runtime tmux sessions were absent.
No process executable or cwd matched the run bundle, run directory, or exact runner directory.
All nine issuer PIDs named in each host's witness logs were absent from /proc.
The collection receipts preserve the exact paths, PIDs, timestamps, and tmux error text.
This is a current process observation. It is not a complete historical process-lifetime audit.
The collector did not kill processes, change runtime files, edit source, or rerun tests.

Next review

Review the two error-code expectations against the production authority and frame-limit contracts.
Preserve the prepared/inactive UNAUTHORIZED case separately from active observation revocation.
Capture full object-action replies in the corrected native edit regression.
Keep genuine visible/reachable-target checks and authority checks in that regression.
Freeze corrected source and artifacts before rerunning affected tests.
This failure evidence does not qualify later task-branch corrections or complete M06.
