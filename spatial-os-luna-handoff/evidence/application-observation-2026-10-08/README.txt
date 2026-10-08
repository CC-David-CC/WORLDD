Authenticated application observation evidence
Base commit: 180ee5537feaf80320b94958061aee8f08075bdf

Prerequisite: 51 harness checks and 151 P10 cases on both hosts.
Corrected baseline: eight expected observation failures on each old worldd binary.
Focused implementation: 16 checks passed on both hosts.
The full Debug core suite passed 787 checks across 41 suites on each host.
The full runtime sanitizer suite passed 771 checks and failed 16 confined-witness startup checks on each host.
ASan attempted to reserve 15392894357504 bytes inside a 4 GiB application address-space limit.
The dedicated spatial-confined-xprobe target builds the unchanged C witness without instrumentation.
Production runtime instrumentation and resource limits remain enabled.
Both profiles then passed the corrected 16-case suite, 51 harness checks, and 151 P10 cases on each host.
Qualification combines full-suite results and affected-suite reruns after a fixture-only correction.
All 787 core requirements have passing evidence per host/profile. None are skipped or unresolved.
The two audits show that no production C++ file changed during the fixture correction.
Binary inspection confirms the runtime remains instrumented.

Baseline-01 accidentally discovered imported test classes.
Its four extra errors came from missing fixtures for those unrelated suites.
Baseline-02 selects only ApplicationObservationTests and has eight production failures.
Focused-01 preserves a missing optional-header build failure.
Focused-02 passed eight initial cases after that include was fixed.
Focused-03 passed 14 cases with protocol faults and in-flight revocation.
Focused-04 passed all 16 cases, including legacy authenticated-session counterexamples.
Runtime-sanitize-01 preserves the failed instrumented-witness run.
Sanitize-reproduce-02 on sb16 retains the witness shadow-memory allocation failure.
Corrected-debug-01 and corrected-runtime-sanitize-01 contain the final affected-suite and cumulative gate results.

Evidence includes exact commands, return codes, binary hashes, and raw logs.
It excludes private credentials, runtime databases, generated bindings, and executable files.
No locked test, acceptance lock, schema, C witness, or XLibre server source was changed.
Native-server sanitizer GLX coverage, physical input, native GPU timing, and soak are unrun here.
Authenticated application input and pixel delivery remain pending.
These results do not complete M04, M09, M10, or Spatial-M1.
