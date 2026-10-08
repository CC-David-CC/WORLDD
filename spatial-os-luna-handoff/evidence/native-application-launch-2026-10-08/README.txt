Native application launch qualification: 2026-10-08

Base commit: 85283c0e1f6f02164918214ee2574523d52d9c9f.
Primary-agent review follows David's local-model review waiver.
No local-model proposal or approval is claimed.

Final results on EACH host: sb16 and sb16-2

Debug: 707 checks across 36 CTest suites, 51 harness checks, and 151 P10 cases.
Runtime C++ ASan/UBSan: the same 707, 51, and 151 checks.
Native-server ASan/UBSan: 195 focused checks, including all 19 new launch checks.
Native settings: detect_leaks=1:abort_on_error=1 and halt_on_error=1:print_stacktrace=1.
All final commands exited zero. No final executed profile skipped a case.
The focused native checks are also included in the core matrix; do not count them as additional core checks.

Evidence directory guide

prerequisite-01: preceding harness and cumulative P10 checks.
baseline-01: first witness draft against the previous broker.
baseline-02: six functional failures before implementation; four configuration-denial cases passed.
debug-01: initial ten-check implementation.
debug-02: expanded fifteen-check suite exposed idle credential cleanup failure.
debug-03: all fifteen checks passed after automatic process reaping.
debug-04: expanded seventeen-check suite passed.
native-sanitize-01: 193 focused native checks passed before the checkpoint review correction.
debug-05 and runtime-sanitize-05: earlier complete regression matrix with seventeen native-launch checks.
checkpoint-review-01: both authority-denial faults fail before the correction and pass afterward.
final-focused-01: final nineteen-check suite and build commands.
native-sanitize-02: final focused native-server sanitizer qualification.
debug-06 and runtime-sanitize-06: final cumulative qualification.
build-01 and build-02: earlier build commands and logs.
summary.json: per-host counts and final broker/checkpoint-probe hashes.
reviewed-source: final native client, header, checkpoint driver, and focused witness.
source-versions: first implementation and idle-reaping correction, before the final checkpoint correction.
source-sha256.json: normalized source input hashes, verified on both hosts.
qualification.py: final qualification commands executed through SSH on both hosts.
review.txt: primary-agent review, defects, corrections, and remaining limits.

Reproduction

Run on sb16 or sb16-2 with the committed source.
Use tools/build_xserver.py for the pinned server and its sanitizer profile.
The existing actor-binding server builds were reused because this increment changes no server source.
Each native run.json records the selected server path and hash.
qualification.py records the configure, build, native, CTest, harness, and P10 commands.
Use fresh output directories for a new run.
The qualification driver expects the explicit native-launch-final-source.tar source package used during development.
For a checkout reproduction, omit its source-extraction step and use the checked-out source directly.
The original logs remain in host-local directories under local/m04-native-launch-*.

Evidence boundaries

The archive includes command logs, aggregate reports, selected server logs, and source snapshots.
It excludes application records, credentials, runtime databases, and binary outputs.
Earlier failures remain diagnostic evidence. They are not passing acceptance results.
The two checkpoint tests inject denial at the real production client's authorization callback.
They independently authenticate an unrelated credential through the native server afterward.
The application fixtures use real process confinement, Xlib, and native windows.

Limits and recovery

All displays are disposable project displays. No system login or display configuration changed.
Normal desktop operation and SSH recovery remain available.
The native-server sanitizer profile disables GLX and enables leak detection.
Runtime C++ sanitizer profiles use the Debug native server.
Physical keyboard, mouse, SpaceMouse, GPU timing, instrumented GLX lifecycle, and native soak remain unrun here.
Automatic session provisioning, authenticated surface identity, and application-control grants remain required.
M04, M09, Spatial-M1, and the full project remain incomplete.
