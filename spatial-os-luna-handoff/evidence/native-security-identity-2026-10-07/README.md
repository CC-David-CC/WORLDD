# Native security identity evidence

Base project commit: `2b5b6f073a4561e80ad9358c56f581c62bfa8b44`.
XLibre source pin: `f844d7a86c10fd5cc89083e7daf97b862b8ca1fd`.
Both hosts match all 168 recorded final source hashes.

Debug and runtime sanitizer runs each passed 151 acceptance cases, 534 core checks, and 51 harness checks.
Those cumulative runs use the project Debug XLibre server with GLX enabled.
Each native sanitizer run passed all 22 identity assertions with GLX explicitly disabled.
No identity assertion was removed. No leak or address sanitizer suppression was installed.
The two isolated mutation witnesses detect authentication bypass and missing disconnect revocation.

Full instrumented GLX shutdown remains failed due to graphics-provider allocations.
The open defect is `tasks/m1/server.graphics_cleanup.json`.
This evidence does not complete M04, Spatial-M1, native GPU, or physical-device qualification.

Each host's source-audit.json records scope, results, source pins, packages, and binary paths.
The debug and sanitize folders contain cumulative reports and exact command records.
server-09 and server-sanitize-03 contain final native build commands and upstream unit results.
server-sanitize-focused-10 contains the passing instrumented core-X identity run and native server logs.
Earlier numbered folders retain failed builds, the swapped-client fixture correction, and graphics cleanup diagnostics.
baseline and contract preserve the preceding gate and the old-server missing-feature failure.
The sb16 mutation folder records separate defective binaries and independent witness failures.

Build servers only on sb16 or sb16-2, with tools/build_xserver.py and a new evidence directory.
Add --sanitize for the separate native sanitizer build.
Pass the recorded Xvfb path to tools/qualify_core.py --through P07 --xserver PATH.
Run tools/gate.py --through P10 through tools/with_test_display.py for the complete baseline.
The focused native command is tests/native/test_security_identity.py with the built spatial-security-auth witness.
Its sanitizer profile uses SPATIAL_SECURITY_TEST_CORE_ONLY=1, ASAN_OPTIONS=detect_leaks=1:abort_on_error=1,
and UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1.
Set PYTHONPATH to the generated build/python directory and the project root.
All exact executed paths and argument lists remain in the reports.

All displays are disposable project servers. Stop only their recorded processes during recovery.
Normal login, GNOME sessions, host services, and SSH remain unchanged.
