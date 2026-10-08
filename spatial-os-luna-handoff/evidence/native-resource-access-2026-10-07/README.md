# Native resource access evidence

Base commit: cc90884bfd50e4327127cf59e7db6a002e0af264.
This directory contains raw execution logs and source audits from the two authorized hosts.
It qualifies the recorded resource-access increment. It does not complete M04 or Spatial-M1.

## Final runs

| Host | Runtime build | Acceptance | Core | Harness |
|---|---|---:|---:|---:|
| sb16 | Debug | 151 | 570 | 51 |
| sb16-2 | ASan/UBSan | 151 | 570 | 51 |

Each suite passed without failures, errors, skips, or unrun cases.
The final cumulative runs are debug-02 and sanitize-02.
Both runs use the Debug project XLibre server with GLX enabled.
Each host also passed 36 focused resource checks and 22 identity checks on that server.
The final focused run is focused-05. The final Debug server build is server-05.

On sb16-2, server-sanitize-01 builds the native server with address and undefined-behavior instrumentation.
server-sanitize-focused-01 runs all 58 native resource and identity checks with GLX disabled.
It enables leak detection and aborts on sanitizer errors.
The upstream native server tests also passed during these server builds.
This core-X profile does not resolve the recorded graphics-provider GLX cleanup failure.

Full Debug on sb16-2, full runtime sanitizers on sb16, and native server sanitizers on sb16 were not repeated.
The final Spatial-M1 two-host qualification profile remains unchanged.
Do not count this development matrix as the complete final matrix.

## Commands and witnesses

The checked commands are recorded in each run.json, commands.json, or command.json.
The cumulative driver runs tools/qualify_core.py --through P07 with the explicit project server.
It then runs tools/gate.py --through P10 on the same runtime build and a disposable display.
The resource witness is tests/native/test_resource_isolation.py.
The identity witness is tests/native/test_security_identity.py, with the independent ordinary-Xlib probe.
The native witnesses use generated protobuf credentials and direct independent X11 requests.

The sb16 mutation-02 directory records three isolated source changes and their expected failed witnesses.
The mutations remove resource denial, root enumeration filtering, and parent-relative pixel authorization.
Each corresponding test detects its mutation. Mutation source files were restored after each run.
Mutation binaries remain only in ignored, isolated directories. They are not selected as runtime servers.

## Earlier evidence and limits

The baseline directory records 51 harness checks and all 151 P00-P10 cases before implementation.
missing-01 records the independent attacks against the previous native server.
focused-02 records the SendEvent denial discrepancy.
focused-04 records both parent-relative inheritance failures before their production fix.
Earlier passing runs remain available. Only the final source audits support the final counts above.

source-audit.json records every qualification source hash, build ID, selected server, and unrun matrix entry.
Both hosts' final source hashes match the committed source. Acceptance locks and assertions are unchanged.
No credentials, runtime databases, or compiled outputs are included.
The server ran only on disposable project displays. There are no host-system changes to reverse.

Remaining M04 work includes extension dispatch, global operations, atoms, selections, device authority,
resource aliases, asynchronous metadata, broker provisioning, and compositor integration.
Keep untrusted applications out of strict-mode sessions until that complete boundary is qualified.
