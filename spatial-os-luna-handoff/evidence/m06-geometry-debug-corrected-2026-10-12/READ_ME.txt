Corrected geometry Debug evidence

Status: focused Debug PASS across both hosts. The M06 milestone gate remains pending.
ASan results are separate. This task did not inspect or change ASan processes.

Suite                                     Credited cases   Passing attempt per host
world-authority                           27               Debug02
actor-geometry-world-headless               9               Debug02
actor-geometry-client-hostile-peer         19 named         Debug02
actor-session-client                      51 named         Debug02
actor-session-client-tls                  31 named         Debug02
actor-transition-coordinator               35 named         Debug02
actor-transition-real-world               15               retry3
session-application                        3               retry3
world-identity                            27               retry3

All nine selected suites pass across the two attempts on each host.
Credited named checks and unittest cases: 217 per host, 434 total.
This count is not the complete project acceptance count.

Debug02 passed six suites, then ran 15 transition cases: 12 passed and three errored.
Each error is FileNotFoundError for bin/spatial-process-scope-probe.
Its command, wrapper, and outer runner exited 1. The final two suites were UNRUN.
Retry3 added the hash-verified missing helper and ran only the remaining three suites.
All 45 cases passed. Every command and wrapper exited 0. suite.complete says completed 3 of 9.
The first six suites were UNRUN in retry3. Their Debug02 evidence supplies their passes.
Preserved attempts contain 464 named/unittest executions: 458 passes and six errors.
Repeated transition cases do not add unique coverage.

The full client probe prints 51 names and reports 53 cases.
Exact source calls two throwing prechecks before initializing count=2.
The geometry-only mode also calls those two checks before printing its 19 names.
These are eight repeated precheck executions across both modes and hosts, not eight new unique cases.
They remain separate from the conservative credited count.

Original frozen source: 8bdda7631ae51d0ae1e94b913031d0fd8dffb877.
Fixture overlay and helper source: d0194a7e30350f155e1b74e1e9d5c9b775e8adc0.
Packager-only correction: db35e58471d10cc943f6be574a6f917bdd9977b5.
The fixture commit changes two tests and documentation, with no production source change.
Both attempts use the same base Debug archive and explicit two-test overlay.
All 18 declared source hashes were rechecked in all four deployed bundles.
Debug02 has no helper. Retry3 has the exact supplement helper with mode 0755.
The supplement adds no libraries; its dependencies match the base bundle hashes.
source-linkage.json and helper-build-provenance.json preserve the immutable source and compile checks.

The archives preserve exact logs, exit records, argv, preflight/selection JSON, manifests, and diagnostics.
The receipts list original paths and hashes for all 508 files per host.
Copied bytes and archive contents match every original hash and size.
Selected credential scans are clear. No package binaries, databases, credentials, or private keys are included.
Expected malformed-config TLS stderr and the failed wrapper's already-absent transient-unit diagnostic remain preserved.
The exact four Debug tmux sessions were absent. No process matched their Debug run roots.
These are current cleanup observations, not a historical lifetime audit.
No remote files, runtime processes, source, or root status were changed.

Verify from the project root:
python spatial-os-luna-handoff/evidence/m06-geometry-debug-corrected-2026-10-12/verify_evidence.py

The verifier reads archives without extraction, network access, or runtime execution.
The primary agent owns review, commit, integration, and milestone qualification.
