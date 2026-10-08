Broker application control lease evidence
Base commit: 04e0cbf2aa1879248e77208f3760b0dbfc6774db

Both hosts passed prerequisite harness and P10 checks.
Both old broker binaries failed all 16 baseline cases because the new protobuf operations were absent.
Both implementations passed the initial 16 cases and the corrected expanded 25-case suite.
Both schema audits preserve 18 messages, 70 fields, and all 151 locked acceptance identifiers.

Sb16-2 focused-02 retained a fixture failure and its teardown failures.
The issuer request omitted expected_security_revision.
The fixture now includes that field and releases the test process even when assertions fail.
Sb16 focused-02 did not run after an SSH connection failure.
No missing run is counted as passed.

Full qualification passed on both hosts.
The native-server profile disables GLX because these lease checks do not render.
Commands, exit codes, source hashes, binary hashes, and raw logs are retained.
Private credentials, runtime databases, generated bindings, and executables are excluded.

These tests qualify the broker reservation and notification protocol.
Worldd subscription handling and native input cancellation remain pending.
M04, M09, and Spatial-M1 remain incomplete.

Final checked results, per host:
- Debug: 812 core checks / 42 suites, 51 harness checks, 151 P10 cases.
- Runtime ASan/UBSan: 812 core checks / 42 suites, 51 harness checks, 151 P10 cases.
- Native-server ASan/UBSan: 25 lease checks; GLX disabled; leak detection enabled.
- No final profile skipped a case.
Sb16 also reran the corrected 19-case authority fixture in both profiles.
Sb16-2 passed full suffix-02 qualification after the ESRCH helper correction.
All 11 reviewed production/build/test inputs match the collected host hashes.
Native input cancellation, authenticated pixels, physical devices, GPU timing, and full M1 remain pending.

The initial sb16-2 debug-01 run failed in the process-exit helper.
A process exited between opening and reading /proc/PID/stat, which produced ESRCH.
The helper accepts ProcessLookupError as process absence, like FileNotFoundError.
No live-process, timeout, revocation, or side-effect assertion changed.
The failed run remains separate from final suffix-02 qualification.
An initial retry script exited before tests because it renamed a prerequisite evidence path.
qualification_retry.py contains the corrected command.
