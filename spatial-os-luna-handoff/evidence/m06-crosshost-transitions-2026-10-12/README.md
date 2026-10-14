# M06 cross-host transition fixture evidence — 2026-10-12

This folder records the bounded headless cross-host actor transition fixture. It does not establish native presentation or Spatial-M1 qualification.

## Source and bundle

- Worker branch: m06/crosshost-actor-transitions
- Base: 4d0c788e96b645d33e57268bd7c40d2a2b5a9f9b
- Commit: c93e02b9695fe6716bf8c32054d06cc7caf084e9
- Qualification label: FOCUSED-PASS
- Corrected archive SHA256: 2fbd500c40eb7e2832c835eaa8bd11b389470532987988fa0887e6ed845dec7c
- Bundle manifest SHA256: 158843aa829eb7fae2f2fba0a659809bb4f14a9a5390fe20144a596e1ef77b29
- Bundle payload: 215 files plus SHA256SUMS; all entries passed on sb16 and sb16-2.
- The corrected archive hash was independently checked on middle-child, sb16, and sb16-2.
- Source artifact manifest: 392 entries, verified on middle-child.
- Worker report and review are in source/.

## Results

| Check | Result | Evidence |
|---|---:|---|
| Initial Debug semantic cases | 6/6 passed | First runner failed overall during cleanup with AF_UNIX EAGAIN; preserve as FAIL_CLEANUP. |
| Initial guarded recovery | stop, snapshot, remove all returned 0 | Raw snapshot archive remains on sb16; only its hash receipt is copied here. |
| Corrected Debug coordinator cases | 6/6 passed | Runner and harness exit 0; cleanup complete; both world roots absent. |
| Corrected Debug slow-owner regression | 1/1 passed | Exactly one stop byte while the retained owner was polled. |
| ASan/UBSan coordinator cases | 6/6 passed | Runner and harness exit 0; cleanup complete; no sanitizer diagnostics. |
| ASan/UBSan slow-owner regression | 1/1 passed | Exactly one stop byte while the retained owner was polled. |
| Issuer record failure after spawn | 1/1 passed | Deliberate fixture exit 1; issuer exit 0; PID absent; cleanup complete; owned root absent. |

The corrected profiles therefore passed 12/12 coordinator cases and 2/2 slow-owner checks. The partial-startup recovery regression passed 1/1.

## Failures and unrun checks

The first Debug run passed all six semantic assertions but failed cleanup. Repeated nonblocking stop connections filled a backlog of one and raised EAGAIN. The exact retained world owner and worldd were absent during recovery. Guarded stop, snapshot, and removal succeeded. The corrected helper sends at most one bounded stop request and passed its slow-owner regression in both profiles.

An initial overlay packager was found at zero bytes and produced no new bundle. A later archive had a malformed SHA256SUMS newline. Both are preserved as packaging-failure receipts. The invalid archive is not the candidate artifact.

No CTest test was executed. ctest -N only recorded test registration. Native presentation, spatial geometry, compositor continuity, held-input retirement, physical input, native-session recovery, and full M06/M1 qualification remain unrun or pending.

## SSH setup record

ssh/fixture-setup-recovery.txt records only public host pins, strict-host-checking policy, backup filenames, and recovery commands. No key bytes, private material, runtime database, or credential is included.

The evidence copies omit all private plans, TLS configs, seed replies, identity databases, issuer rows, and the raw recovery snapshot archive. The raw snapshot stays on sb16 and is identified by its SHA256 receipt.

## Preserved raw evidence whitespace

These captured files retain trailing spaces from their original command or tool output. They remain byte-for-byte unchanged: `source/run03-build-commands.txt`, `source/run03-debug-configure.log`, `source/run03-asan-configure.log`, and `source/run03-report.txt`. The two host hash receipts retain their captured CRLF line endings. These raw-evidence exceptions explain the scoped `git diff --check` warnings. Authored summaries and the evidence manifest use LF.
