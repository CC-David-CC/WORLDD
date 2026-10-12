# ASan geometry failure evidence — 2026-10-12

This directory records a failed run04 ASan/UBSan qualification attempt on `sb16` and `sb16-2`. Both hosts used the same frozen archive, test overlay, runner, and ASan process-scope helper. The exact pins appear in `source-attempt-pins.json`.

## Observed results

`world-authority` passed 27 of 27 tests on each host. It took 73.991 seconds on `sb16` and 91.289 seconds on `sb16-2`.

`actor-geometry-world-headless` ran 9 tests on each host. Eight passed, including oversized-snapshot and authenticated-TLS cases. One errored: `test_geometry_count_limit_is_controlled_and_other_actor_survives`. The complete geometry suite took 269.835 seconds on `sb16` and 240.645 seconds on `sb16-2`.

The error occurred at the peer's geometry-view request after seeding 4,097 entities. The test expected an `INVALID_ARGUMENT` denial. Instead, the client received `EOFError: worldd closed the channel`. The world-1 logs report `worldd: identity service disabled at identity_sessions.cpp:290`; world-0 and issuer logs are empty. Source line 290 catches an exception from the issuer client call/exchange and marks the participant service failed. The logs do not retain the underlying exception. This establishes that the world identity service failed before the actor-channel EOF, but does not establish why.

Both geometry command and wrapper exits are 1. Their terminal records say `COMPLETE`; they do not indicate success. No `suite.complete` exists. The runner stopped after geometry, so seven selected suites did not run. `terminal-cleanup.json` records a later read-only check: no tmux server, Python test process, worldd, identityd, or scope-probe process remained on either host.

## What remains unknown

The fixture issuer config uses a 3,000 ms idle timeout. The suite lasted about four minutes, but no per-case timing or issuer connection trace was retained. Timeout or keepalive starvation is only a hypothesis. The empty issuer logs do not confirm it.

A scan of the compact copied raw text files found no ASan or UBSan diagnostic markers and no private-key or bearer-token patterns. This scan covers only the files retained here. Later selected suites did not run and have no sanitizer evidence.

## Raw files

Each host folder retains the world-authority pass output and argv, plus the failed geometry stderr, argv, command/wrapper exits, command receipts, terminal markers, and the two world logs plus issuer log for the failing case. `SHA256SUMS.txt` covers every file in this evidence directory except itself.