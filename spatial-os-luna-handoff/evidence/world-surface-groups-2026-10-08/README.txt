Worldd application relationships and group leases

Base commit: 644e680ff93b601fb1e1474f5ba5bfb36c8186f2
Primary agent reviewed the change without local-model review.

Both hosts passed the prerequisite harness and P10 gate.
The corrected baseline ran ten native integration cases on each original worldd.
Six cases failed. Four cases passed.
The first three baseline logs contain fixture mistakes, described in the root debug note.
baseline-test.py preserves the corrected ten-case baseline.

The first corrected production run passed those ten cases on each host.
The expanded focused run passed fourteen cases on each host.
The new cases include real C key-event checks, nested relationships, cycles, and parent destruction.
Actors use management fixtures. Applications use real native credentials.

Both hosts passed Debug: 39 suites, 743 core checks, 51 harness checks, and 151 P10 cases.
Both hosts passed runtime ASan/UBSan with the same 743 core, 51 harness, and 151 P10 checks.
No final profile failed or skipped a case.

Each host directory contains commands, logs, server diagnostics, and binary hashes.
production.patch records the small production change against the base commit.
tested-inputs.json records the five build/test inputs checked on both hosts.
source-sha256.json records normalized source and contract inputs after final review.
review.txt describes the primary review.
The collector verifies counts, process exits, and final acceptance results before export.

The source and acceptance schemas remain unchanged.
No locked assertion, witness, oracle, codec, gate, or acceptance lock changes are required.
No upstream XLibre patch or installed session change is included.
Native-server-only sanitizers were not repeated because native server source did not change.
The prior native-server qualification and its GLX limitation remain separately recorded.

Workspace application grants, filtered actor metadata, and automatic session provisioning remain pending.
Physical input, native GPU performance, full instrumented GLX lifecycle, and native soak remain unqualified.
M04, M09, and Spatial-M1 remain incomplete.
