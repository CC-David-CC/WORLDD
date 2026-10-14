# sb16 issuer diagnostic handoff (2026-10-12)

Status: diagnostic-only, unqualified. This handoff preserves one passing real-TLS issuer regression and two failed count-limit reproductions. It does not establish the issuer failure cause or qualify the geometry path.

The candidate artifact was a mixed-artifact diagnostic experiment. It overlaid reviewed ASan/UBSan `worldd`, `spatial-issuer-client`, and `spatial-issuer-failure-diagnostic` binaries plus `tests/core/test_issuer_client.py` onto the frozen baseline ASan bundle. The baseline bundle hash is `82a326fb3f2982b7b78b9555c4ecf8b8d27f0c6a5ddcb13ffdd29d46b9aac336`. The candidate source commit is `5cbec99d1ad2844da1b8fd4b709c8e0444beb414`; the corrected supplement archive hash is recorded in `artifact-commit-binding02.json`. This was a diagnostic experiment, not a qualification bundle.

The real-TLS regression `ClientTests.test_callback_failure_diagnostic_is_bounded_and_fail_closed` passed: one test, exit 0. Count attempt 01 failed with `EOFError: worldd closed the channel`; its run omitted `SPATIAL_SECURITY_EVIDENCE`, so fixture service logs were not retained. Count attempt 02 used an explicit persistent evidence directory and failed with the same test-level EOF. Its retained world-1 log reports `issuer_eof` while reading the reply header, with 0 of 4 bytes received and 0 ms elapsed, followed by identity service disablement at `identity_sessions.cpp:295`. This identifies the observed protocol stage only. The underlying cause remains unknown.

The source review identified a possible timing interaction: geometry preparation can perform large snapshot work before the count guard, while issuer keepalive occurs before a queued rejection reply. This remains a hypothesis; the runtime logs do not prove it. No peer-host repeat was run. No geometry acceptance or native qualification claim is made.

Attempt 01's missing retention environment is preserved as a process defect. The v2 driver now requires an absolute, existing, writable `SPATIAL_SECURITY_EVIDENCE` directory before importing fixtures. Its isolated guard checks passed. Cleanup receipt reports no remaining attempt processes and no tmux server. The raw attempt receipts and logs are preserved by attempt.

The corrected count attempt's authoritative sb16 world-1 log originated at `/home/dflanag3/spatial-qualification/m06-issuer-diagnostic-sb16-20261012-02/runs/geometry-count-02-security-evidence/GeometryViewTests-test_geometry_count_limit_is_controlled_and_other_actor_survives-world-1.log`.

`SHA256SUMS` covers every handoff file except itself. No runtime database, credential, or private key is included.

Primary review: baseline_task_base_commit identifies the original task base, not the complete bundle source. The preserved preflight base_commit field has the same limited meaning. Archive hashes and per-member preflight checks identify the actual mixed runtime input. All 55 original handoff payload hashes were independently verified before copying. The primary also inspected terminal receipts, the retained world log, and all eight guard-check results.
