Authenticated actor pixel evidence
Schema base: 71386ba45d9f55368e5b1bb53913bbf30bcdce27

The separate schema migration passed on both hosts.
See evidence/actor-pixels-schema-2026-10-08 for its descriptor audit and compatibility checks.

The production baseline passed three of fourteen cases on each host.
Eleven cases failed because requested images and pixel-specific bounds were absent.
The first implementation passed thirteen native cases on each host.
The remaining case failed setup because its occluding panel omitted a required label.
The original test file and source-input manifest are preserved here.
The fixture correction changes no pixel or authority assertion.

The corrected focused run passed all 45 checks on both hosts:
- 14 real native pixel checks.
- 9 adversarial compositor-client checks.
- 13 existing rendering checks.
- 9 existing observation checks.

tested-inputs.json records the eleven changed build, production, and test inputs.
tested-tree.json records 460 source-tree files before final documentation updates.
Both host audits matched these files to the controller.
review.txt contains the primary agent's file-by-file review.
The runner scripts preserve the executed commands and original checkout prerequisites.
test_actor_pixels_before_label.py is the preserved earlier test source.
It is not an additional registered suite.

Both hosts passed complete Debug and runtime ASan/UBSan qualification.
Each profile passed 914 core checks across 47 suites, 51 harness checks, and all 151 P10 cases.
No final profile failed or skipped a case.
Each host directory preserves its original failures, corrected focused run, final logs, source hashes, and binary hashes.
source-sha256.json records the final source and documentation state for integration.

Native tests use signed actors, real grants, confined C clients, worldd, spatialwm, and the patched native server.
The transport peer supplies malformed replies only for checked-client tests.
It does not substitute for native rendering evidence.
Revocation is checked before export and during a partially written image reply.

No locked acceptance assertion, oracle, or independent native witness changed.
No XLibre source or host system configuration changed in this increment.
Native-server GLX sanitizer coverage remains unrun for this component.
Physical input, native timing, provider export, real-model tasks, and full M1 qualification remain incomplete.
