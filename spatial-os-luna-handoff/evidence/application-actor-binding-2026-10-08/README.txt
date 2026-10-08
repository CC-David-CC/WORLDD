Application actor binding qualification: 2026-10-08

Base commit: 33dded03ab96399647d72d3634eb50f82ea3266c.
Production and test source hashes are in source-sha256.json.
Each build run.json records the pinned server, overlay inputs, commands, and binary hashes.
The source-versions folders preserve the first implementation and both lifecycle corrections.
The reviewed-source folder contains the final C implementation, witness, and CMake registration.
review.txt records primary-agent review under David's local-model waiver.

Final results on EACH host: sb16 and sb16-2

Debug: 688 checks across 35 CTest suites, 51 harness checks, 151 P10 cases.
Runtime C++ ASan/UBSan: the same 688, 51, and 151 checks.
Native-server ASan/UBSan: 176 focused native checks, including all 25 new actor-binding checks.
Native sanitizer settings: detect_leaks=1:abort_on_error=1 and halt_on_error=1:print_stacktrace=1.
The native sanitizer profile disables GLX. Runtime sanitizer profiles use the Debug Xvfb server.
All final commands exited zero. No final executed profile skipped a case.
Each host's summary.json and final run records substantiate these counts.

Evidence directory guide

prerequisite-01: preceding harness and cumulative P10 checks.
baseline-01 and baseline-02: earlier witness drafts on the previous server.
baseline-03: final 25-test witness on the previous server; 24 failures and one passing control.
debug-01: the first 20-test draft; it incorrectly expected no fresh history after a new click.
debug-02: the expanded 25-test draft, before final positive preconditions were added.
debug-03: final complete Debug qualification.
runtime-sanitize-03: final complete runtime sanitizer qualification.
native-sanitize-01: original paired-device hierarchy use-after-free.
native-sanitize-02: remaining stale pointer-window use-after-free.
native-sanitize-03: all 176 focused native checks passing after both corrections.
build-* and sanitize-build-*: original build logs, input pins, and binary hashes.
qualification.py: the final qualification driver, as executed through SSH on both hosts.
Earlier failure records are diagnostic evidence, not passing acceptance results.

Reproduction

Use sb16 or sb16-2 and the committed project source.
Use tools/build_xserver.py to build the pinned server in Debug and with --sanitize.
Select fresh evidence directories; the builder rejects existing output directories.
Use the returned Xvfb path as SPATIAL_TEST_XSERVER.
qualification.py records the exact configure, build, native-test, CTest, harness, and P10 commands.
Its existing output directory names must change for a new run.
The original host-local per-server logs remain under local/m04-actor-binding-*.
This archive retains the command logs, aggregate test logs, reports, and source snapshots.
It excludes credentials, databases, binaries, and application state.

Limits and recovery

All displays were disposable project displays. No system display or login configuration changed.
The normal desktop and SSH recovery path remain available.
Physical keyboard, mouse, SpaceMouse, GPU-session timing, and native soak were not run.
Full instrumented GLX lifecycle qualification remains pending.
Broker credential delivery and compositor/worldd application authority remain required.
M04, M09, Spatial-M1, and the full project are incomplete.
