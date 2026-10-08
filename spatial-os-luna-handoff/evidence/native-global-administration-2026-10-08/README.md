# Shared server administration evidence

This increment checks native server administration under immutable connection identities.
It does not complete global isolation, M04, or Spatial-M1.
The source pin is XLibre 25.1.9 at f844d7a86c10fd5cc89083e7daf97b862b8ca1fd.
Production changes are in server/xlibre/spatialpolicy.c and server/patches/0006-global-server-access.patch.
The independent witness is tests/native/test_global_isolation.py.
Credential setup and completion fences use Protocol Buffers.

## Executed results

Both pre-patch servers failed 14 new checks and passed five positive controls.
The first build failed on the nonexistent DixUngrabAccess constant.
The next build exposed three host-access failures under -ac.
The final patch uses DixGrabAccess and checks authority before the access-control shortcut.

Both patched Debug servers passed all 123 focused native checks.
Both patched ASan/UBSan servers passed the same checks with GLX disabled.
The suites contain 19 global, 14 selection, 32 extension, 36 resource, and 22 identity checks.
Both Debug hosts passed 151 locked P00-P10 cases and 51 harness checks.
Each host detected five isolated bypass mutations.
Each mutant failed its targeted native BadAccess assertion.
The mutations did not enter production.

## Commands and configuration

Run from spatial-os-luna-handoff.
Use fresh evidence directories when repeating these commands.

~~~sh
export PYTHONPATH="$PWD/build/python:$PWD"
python3 tools/build_xserver.py --evidence-dir local/m04-global-debug-build-03
python3 tools/build_xserver.py --sanitize --evidence-dir local/m04-global-sanitize-build-01
ctest --test-dir build -V -R '^native-(global-isolation|selection-isolation|extension-isolation|resource-isolation|security-identity)$'
python3 -m unittest discover -s tests/harness -t . -q
python3 tools/with_test_display.py -- python3 tools/gate.py --through P10 --report local/m04-global-debug-02/gate.json
~~~

Read SPATIAL_TEST_XSERVER from binaries.Xvfb.path in the matching build record.
Debug uses local/m04-global-debug-build-03/run.json.
Native sanitizers use local/m04-global-sanitize-build-01/run.json.
Each host's debug/commands.json records the executed commands and Debug binary.
Each host's native-sanitize/run.json records sanitizer settings and binary.

Set SPATIAL_SUT to the absolute spatial-test-host argv encoded as a JSON array.
Set SPATIAL_XPROBE to the absolute spatial-xprobe path.
Set SPATIAL_EVIDENCE_DIR to a host-local evidence directory.
Native sanitizer runs set SPATIAL_SECURITY_TEST_CORE_ONLY=1.
They use ASAN_OPTIONS=detect_leaks=1:abort_on_error=1.
They use UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1.

## Retained evidence

Each host folder contains prerequisite, baseline, failed build, intermediate failure, and final records.
Mutation records contain targeted test IDs, source diffs, build logs, and binary hashes.
The review folder preserves bounded local-model proposals and corrections.
source-sha256.json records source, test, schema, tool, and contract inputs.
Raw server logs and case directories remain under matching local/m04-global-* folders on each host.
No credentials, runtime databases, or build outputs belong in this evidence commit.
Generated logs retain their original formatting.

## Limits

Complete C++ core and runtime-sanitizer profiles were not repeated in this increment.
Focused native-server sanitizers do not qualify GLX provider cleanup or physical input.
GPU timing, surface lifecycle qualification, and the native session soak remain pending.
Device authority, focus/pointer queries, atom privacy, selection namespaces, and asynchronous metadata remain unqualified.
Broker provisioning and compositor authority integration remain incomplete.
