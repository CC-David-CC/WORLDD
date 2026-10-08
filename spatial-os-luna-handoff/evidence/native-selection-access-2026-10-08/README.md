# Owned selection access evidence

This increment checks native selection-owner access under full application identities.
It does not complete M04 or Spatial-M1.
The upstream source pin is XLibre 25.1.9 at f844d7a86c10fd5cc89083e7daf97b862b8ca1fd.
Production changes are in server/xlibre/spatialpolicy.c.
The independent witness is tests/native/test_selection_isolation.py.
Credential requests and completion fences use generated Protocol Buffers.

## Results

Both hosts' rebuilt pre-patch servers failed nine new checks and passed five positive controls.
An earlier cached server had a different policy hash and is not the current-source baseline.
Both patched Debug servers passed all 104 focused native checks.
Both patched ASan/UBSan servers passed the same checks with GLX disabled.
The four suites contain 14 selection, 32 extension, 36 resource, and 22 identity checks.
Both Debug hosts passed 151 locked P00-P10 cases and 51 harness checks.
The isolated sb16 selection-policy bypass mutation failed nine of the 14 checks.
The mutation did not enter production.

## Commands

Run from the spatial-os-luna-handoff directory.
Use fresh evidence directories when repeating these commands.

~~~sh
export PYTHONPATH="$PWD/build/python:$PWD"
python3 tools/build_xserver.py --evidence-dir local/m04-selection-debug-build-01
python3 tools/build_xserver.py --sanitize --evidence-dir local/m04-selection-sanitize-build-01
ctest --test-dir build -V -R '^native-(selection-isolation|extension-isolation|resource-isolation|security-identity)$'
PYTHONPATH=build/python:. python3 -m unittest discover -s tests/harness -t . -q
python3 tools/with_test_display.py -- python3 tools/gate.py --through P10 --report local/m04-selection-debug-01/gate.json
~~~

Read SPATIAL_TEST_XSERVER from binaries.Xvfb.path in the matching build record.
Debug uses local/m04-selection-debug-build-01/run.json.
Native sanitizers use local/m04-selection-sanitize-build-01/run.json.
Each host's debug/commands.json records Debug commands and binaries.
Each host's native-sanitize/run.json records sanitizer settings and binaries.
Set SPATIAL_SUT to the absolute spatial-test-host argv encoded as a JSON array.
Set SPATIAL_XPROBE to the absolute spatial-xprobe path.
Set SPATIAL_EVIDENCE_DIR to a host-local evidence directory.
The recorded commands and build records identify the actual binaries.
Native sanitizer runs set SPATIAL_SECURITY_TEST_CORE_ONLY=1.
They use ASAN_OPTIONS=detect_leaks=1:abort_on_error=1.
They use UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1.

## Evidence layout and limits

Each host folder contains rebuilt baseline, Debug, native sanitizer, and build records.
The sb16 mutation folder preserves its configuration, source hashes, and failing witness log.
The sb16 review folder preserves bounded local-model proposals and corrections.
source-sha256.json records the implementation, tests, schemas, tools, and contract inputs.
Raw server logs and case directories remain under matching local/m04-selection-* folders on each host.
No runtime databases, credentials, or build outputs belong in this evidence commit.

This increment does not repeat the complete C++ core or runtime-sanitizer profiles.
It does not qualify GLX provider cleanup, physical input, native GPU timing, or session soak.
Atom privacy, independent selection namespaces, global requests, devices, and asynchronous metadata remain unqualified.
Broker provisioning and compositor authority integration remain incomplete.
