# Shared device administration evidence

This increment checks shared keyboard and pointer configuration authority.
It does not complete device authority, input privacy, M04, or Spatial-M1.
The source pin is XLibre 25.1.9 at f844d7a86c10fd5cc89083e7daf97b862b8ca1fd.
Production changes are in server/xlibre/spatialpolicy.c.
The independent witness is tests/native/test_device_administration.py.
Credential setup and completion fences use Protocol Buffers.

## Executed results

Both pre-patch servers failed nine new checks and passed four positive controls.
Both patched Debug servers passed all 136 focused native checks.
Both instrumented Xvfb servers passed the same checks with GLX disabled.
The suites contain 13 device, 19 global, 14 selection, 32 extension, 36 resource, and 22 identity checks.
Both Debug hosts passed 151 locked P00-P10 cases and 51 harness checks.
Each host detected six isolated bypass mutations.
Each mutation removes one protected configuration opcode.
Its targeted witness fails the native denial assertion or observes the resulting MappingNotify.
No mutation entered production.

## Commands

Run from spatial-os-luna-handoff.
Use fresh evidence directories when repeating these commands.

~~~sh
export PYTHONPATH="$PWD/build/python:$PWD"
python3 tools/build_xserver.py --evidence-dir local/m04-device-debug-build-01
python3 tools/build_xserver.py --sanitize --evidence-dir local/m04-device-sanitize-build-01
ctest --test-dir build -V -R '^native-(device-administration|global-isolation|selection-isolation|extension-isolation|resource-isolation|security-identity)$'
python3 -m unittest discover -s tests/harness -t . -q
python3 tools/with_test_display.py -- python3 tools/gate.py --through P10 --report local/m04-device-debug-01/gate.json
~~~

Read SPATIAL_TEST_XSERVER from binaries.Xvfb.path in the matching build record.
Debug uses local/m04-device-debug-build-01/run.json.
Native sanitizers use local/m04-device-sanitize-build-01/run.json.
Each host's debug/commands.json records the executed commands and Debug binary.
Each host's native-sanitize/run.json records sanitizer settings and binary.
Set SPATIAL_SUT to the absolute spatial-test-host argv encoded as a JSON array.
Set SPATIAL_XPROBE to the absolute spatial-xprobe path.
Set SPATIAL_EVIDENCE_DIR to a host-local evidence directory.
Native sanitizer runs set SPATIAL_SECURITY_TEST_CORE_ONLY=1.
They use ASAN_OPTIONS=detect_leaks=1:abort_on_error=1.
They use UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1.

## Inference service state

The llm-79 fleet-iq3 service was repeatedly stopped during documentation review.
Both recorded inference addresses refused connections.
The known stop override was absent. No unit files changed.
David's existing restart authorization covered starting this service.
sudo -n systemctl start fleet-iq3.service succeeded.
The first start became active with MainPID 104698.
After another successful service stop, a second start became active with MainPID 106252.
That startup was stopped cleanly after 51 seconds, before port 8080 opened.
Implementation and contract reviews completed before this second outage.
The service was later masked. An authorized start failed, and the mask was left intact.
David then authorized primary-agent review without the local model.
The primary agent reviewed the callback, registration/reset, native tests, contract, and recorded outcomes.
The callback preserves earlier denials and gates opcode checks on management or bell access intent.
The review found no further defect within this component's stated scope.
Historical pending-review records describe the earlier outage, not the final review status.

To restore the prior inactive service state:

~~~sh
ssh llm-79 sudo -n systemctl stop fleet-iq3.service
~~~

## Evidence and limits

Each host folder contains prerequisite, baseline, Debug, native sanitizer, and build records.
Mutation records contain targeted test IDs, source diffs, build logs, and binary hashes.
The review folder preserves bounded local-model proposals and corrections.
source-sha256.json records source, test, schema, tool, and contract inputs.
Raw server logs and case directories remain under local/m04-device-* on each host.
No credentials, runtime databases, or build outputs belong in this evidence commit.
Generated logs retain their original formatting.

Both hosts passed complete Debug and runtime ASan/UBSan core profiles.
Each profile passed 648 checks across 33 CTest suites, 51 harness checks, and 151 P10 cases.
The complete profile records are in each host's full-debug and full-runtime-sanitize folders.
No executed profile reported skipped or unrun cases.
Physical input, GPU timing, lifecycle qualification, and native session soak remain pending.
Focus, pointer, held-key observation, actor-seat reads, and application input binding remain unqualified.
The access-intent guard is source-verified; no dedicated stale-opcode event-receive mutation was run.
Broker/compositor integration and GLX provider cleanup remain incomplete.
