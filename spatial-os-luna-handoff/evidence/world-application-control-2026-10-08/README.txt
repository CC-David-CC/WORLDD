Authenticated native application control evidence
Base: cd651f5e76e01296b813335914990794e500efd8

This increment connects authenticated actor input to production broker reservations.
Native input cancellation must complete before the broker reservation is released.
The primary agent reviews this work without a local model.

Evidence layout

Each host directory contains preserved development failures and final qualification reports.
run.json records commands, exit codes, host identity, and relevant binary hashes.
test.log and core.log contain test names and assertions.
server logs contain independent native event witnesses.
summary.json records mechanically checked final counts.
binaries.json pins the final Debug and runtime-sanitized executables.

tested-tree.json pins 452 source-tree files before final documentation updates.
source-integrity.json records matching hashes on both hosts.
tested-inputs.json pins the nine changed production, build, and test inputs.
tested-inputs-before-review.json preserves the earlier 24-case draft.
production.patch records the production and build changes against the base commit.
review.txt records the primary agent's file-by-file review.

The runner scripts preserve the executed commands and environment.
They expect the original host checkout and recorded local prerequisites.
The supplemental test_control_cancel_close.py was executed from local/ before integration into the permanent test suite.
It is archived here as the original reproducer, not as another registered test suite.

Development results

The corrected baseline-04 passed two denial checks and failed ten authorized-input checks on both hosts.
Fixture setup failures before that baseline do not establish production defects.
The root M04_WORLD_APPLICATION_CONTROL_DEBUG.txt distinguishes those failures.

The initial implementation passed 12 focused checks.
Expanded tests exposed revocation delay and controller-loss delay during broker waits.
Both hosts reproduced these failures before the scheduler corrections.
The next 24-case suite passed on both hosts.
A separate reproducer then exposed incomplete device teardown after revocation during native cancel.
Both hosts retained 12 devices instead of the expected 8.
That reproducer is now the 25th control test.

The first full Debug run passed 44 of 45 suites on both hosts.
Five unchanged world-identity checks exposed overly broad denial of diagnostic interaction.
The correction preserves explicit diagnostic grants without bypassing authority for labeled applications.
The subsequent focused-06 run passed all 61 checks on both hosts:
25 application-control checks, 20 identity checks, and 16 observation checks.

Final qualification passed on both hosts.
Each Debug and runtime ASan/UBSan profile passed 891 core checks across 45 suites.
Each profile also passed 51 harness checks and all 151 P10 cases.
No final failed or skipped cases remain in these profiles.
The evidence collector independently checks these counts and the nine changed input hashes.

Scope and limits

The tests use real signed actors, issuer revocations, confined processes, the broker, worldd, spatialwm, and patched native XLibre.
The unchanged C witness observes real core X11 input.
A separate supervisor connection checks native actor-device identity and device removal.
No locked acceptance assertion, schema, independent C witness, or XLibre source changed.
No host system configuration changed.

Runtime ASan/UBSan uses the normal patched native server with GLX enabled.
Native-server ASan/UBSan GLX coverage remains unrun for this component.
The existing instrumented GLX cleanup issue remains separate.
The earlier GLX-disabled native-server test profile cannot qualify these renderer-dependent world tests.

Filtered pixels, physical input, native GPU timing, session restoration, and full M1 qualification remain incomplete.
Passing this increment does not complete M04, M09, M10, or Spatial-M1.
