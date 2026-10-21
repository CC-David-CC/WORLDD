M08 issuer-progress fresh-control BUSY diagnostic
UNQUALIFIED - diagnostic fixture only - runtime UNRUN

Source pin and ownership
------------------------
Base commit: 326450da702525d73713e7b779fe66e76917c163.
Worktree: /home/dflanag3/src/m08-issuer-progress-diagnostic-20261021.
Branch: m08/issuer-progress-diagnostic-20261021.
Owned source: tests/core/test_broker_issuer_progress.py.
This note is the only other owned file. No production source, protobuf, locked
acceptance contract, selected-test inventory, or gate profile changes.

Observed failure
----------------
The sb16 Debug full-gate core prefix identifies registration 185,
`broker-issuer-progress`, and
`BrokerIssuerProgressTests.test_broker_actual_issuer_exit_fails_closed_while_info_is_pending`
at tests/core/test_broker_issuer_progress.py:197. The fresh control acquire
returned `core.BUSY` (18). The fixture allows only `core.UNAVAILABLE` (26) or
`core.UNAUTHORIZED` (12). The response message was not captured in that prefix.

Earlier assertions passed: issuer daemons exited with code zero; the revocation
event arrived within 1.5 seconds while delayed INFO remained pending; and the
control-release acknowledgement succeeded. The fresh acquire returned an error,
so this evidence shows a safe denial and a reason-classification failure. It does
not show a new lease or an authority grant. Subsequent bookkeeping and broker-close assertions at lines 207-209 were
not reached. Other selected methods after this failure remain unrun.

The primary capture remains external:
`PK/fullgate-issuer-progress-failure-01/sb16-Debug-core-prefix-01.log`
and `capture-manifest-01.json`. The manifest records the prefix hash. The original gates retain test-host ownership.
This task made no host probes or mutations and copied no generated evidence into Git.

Static source map
-----------------
`src/workspace/main.cpp:75-109` returns BUSY with message
`Execution durability is pending` before handler dispatch whenever
`store.authority_durability_pending()` is true. Its allowlist includes
`control_release` at lines 76-78, but excludes `control_acquire`. The later
handler calls `controls.acquire()` at line 147. This is one plausible source;
without the actual message, it is not attributed.

`src/workspace/control.cpp:29-59` checks authority, ticks revocation state, and
returns BUSY with `Application control reservation requires release` when an
application group still has a reservation. `control.cpp:61-70` removes that
group after a valid release. The test's release acknowledgement passed, but no
state snapshot proves which internal path produced the later reply. Store loan
and durability guards are additional possible BUSY sources.

`main.cpp:175` and its `revocation_result()` path preserve preexisting error
replies. They do not replace an earlier BUSY classification after issuer
cancellation. `authority.cpp:582-588` provides
`WorkspaceAuthority::requester_state(actor)`, a cached, no-I/O classifier for
active, revoked, or unavailable identities. It may support a later narrow
precedence fix if the saved reply message confirms the durability guard. Do not
add issuer or SQLite recursion to the request path.

Contract assessment and uncertainty
-----------------------------------
`docs/WORKSPACE_AUTHORITY.md:94-98` says committed grant changes invalidate
cached decisions, the broker reconciles processes before replying, consumes
issuer events during waits, and issuer failure disables persistent authority
and cancels affected execution. `docs/APPLICATION_CONTROL_LEASES.md:19-27`
allows BUSY while another reservation exists and requires release. Neither
document defines error precedence when issuer failure and authority durability
are pending at once.

A BUSY reply is fail-closed. The captured result therefore does not prove an
authority grant or a safety-contract violation. It does fail this fixture's
explicit error taxonomy. If the saved message is `Execution durability is
pending`, the guard may be masking the issuer-disabled state with an internal
progress status. That would be a status-precedence mismatch, but the normative
docs do not currently require UNAVAILABLE over BUSY in this overlap. Keep the
allowed codes unchanged until primary reviews the actual diagnostic and any
contract decision.

Diagnostic-only source change
-----------------------------
Immediately after the fresh `control_acquire` reply, the fixture now serializes
the exact `Reply` protobuf into hex, records the error code and message, and calls
its existing `retain_progress()` helper before either rejection assertion. That
helper writes under external `SPATIAL_SECURITY_EVIDENCE`. Both assertions now
include the formatted reply as failure context. The existing allowed-code tuple,
reply handling, delays, release, and all other semantics remain unchanged. The
existing `load_tests()` ten-method inventory remains unchanged.

Next controlled reproduction
----------------------------
After root releases exact sb16 ownership, rerun only the existing registered
Debug `broker-issuer-progress` case with a fresh external
`SPATIAL_SECURITY_EVIDENCE` directory and preserve its canonical runner
receipts. Read the emitted fresh-control reply before proposing a production
change. Compare its message with the source candidates above. If it confirms the
durability guard, review whether cached `requester_state()` can classify the
request before BUSY without SQLite, issuer I/O, or recursion. If it names a
reservation or store-lent guard, diagnose that path instead. Preserve the
existing <1.5-second cancellation and delayed-INFO assertions. Keep the allowed
codes and selected method inventory unchanged absent a separately reviewed
contract change.

Checks for this checkpoint
--------------------------
Static AST, selected-inventory, 42-locked-hash, whitespace, source-guard, and
checked Git procedures only. Runtime, build, CTest, test-host probes, and
reproduction are UNRUN by assignment. The persistent tmux run is
`/home/dflanag3/spatial-artifacts/m08-issuer-progress-diagnostic-20261021/run-01`;
command and wrapper exits are both zero.
