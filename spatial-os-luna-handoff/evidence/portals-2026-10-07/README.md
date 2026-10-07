# Portal qualification evidence

Both hosts passed Debug and ASan/UBSan runtime qualification.
Each mode passed 98 cumulative acceptance tests and eight focused P07 cases.
Each mode passed 114 core counterexamples and 39 harness self-tests.
The full P07 gate remains incomplete because object manipulation is pending.

The debug and sanitize directories contain command logs and source-pinned reports.
The focused directories contain one-case acceptance reports for the five portal and three earlier P07 requirements.
Native directories contain independent X-client event records.
Frame directories contain compositor captures for open apertures and front/back outlines.
Private RPC traces and runtime databases remain outside Git.

The sb16 baseline records the original failed crossing.
The first-debug directories retain the rejected room-label fixture setup.
The specific-second directories retain the corrected 17-case initial portal run.
Final runs include two additional cases, for 19 portal checks.

The sb16 mutation directory contains the isolated mutant, build logs, source hashes, and detected failure.
That mutant discards remaining movement after crossing.
The rotated-portal witness observes destination Z = 3 instead of Z = 1.
The main source retains correct movement.

All final runtime and focused reports contain the same 82 reviewed source hashes.
Debug build: sha256:e107ce481eeb8508c76e5b746cbf8a8dba8924fc7c353c7a159af2bf2e5833a3.
Sanitizer build: sha256:c719ff499f087bf0a2251187d769f4492025211506a31b8ac3ea5b046f7899cd.
The reports also identify each private XLibre binary.
Sanitizers instrument the runtime and compositor, not the XLibre server.
No operator desktop or system configuration changed.

Legacy totals are 106 passed, zero current failures, and 35 unrun.
Physical controls, native GPU timing, full workspace security, and the remaining Spatial-M1 milestones remain mandatory.
