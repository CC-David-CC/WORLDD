Native surface attribution qualification: 2026-10-08

Base commit: 972b61f9a3275647ca264c447dd81f27037d5d0e.
Schema migration commit: 4649da1 (protocol: version native surface attribution schema).
Both hosts passed final qualification for this component.
David waived local-model review. Primary-agent review is in review.txt.

Final results on EACH host

Debug: 729 checks across 38 CTest suites, 51 harness checks, and 151 P10 cases.
Runtime C++ ASan/UBSan: the same 729, 51, and 151 checks.
Native-server ASan/UBSan: 210 focused native checks, including all 15 new native attribution checks.
All seven real compositor integration checks passed in both runtime profiles.
The focused checks are included in the core count; do not add them again.
One additional older-server rejection passed separately in Debug and runtime ASan/UBSan.
One additional independently compiled C window witness passed separately in both runtime profiles on both hosts.
All final commands exited zero. No final executed profile skipped a case.
Native sanitizer settings: detect_leaks=1:abort_on_error=1 and halt_on_error=1:print_stacktrace=1.

Evidence directories

prerequisite-01: preceding harness and P10 checks on the base commit.
baseline-01: thirteen missing-attribution failures among fifteen raw native checks.
baseline-02: added compositor fixture omitted its state directory; preserve this fixture failure.
baseline-03: eighteen failures among twenty-two checks against the previous implementation.
debug-01: all fifteen native and seven compositor checks passed.
build-01 and sanitize-build-01: pinned native builds, unit checks, source hashes, and binary hashes.
runtime-build-01: initial C++ configure and build logs.
debug-02: integrity gate rejected the unrecorded locked core-schema change before core or P10 ran.
schema-audit-01: 481 existing fields, 71 enum values, 135 existing messages, and 151 acceptance IDs preserved.
legacy-rejection-01: actual new compositor rejects the previous server before reporting readiness.
legacy-rejection-sanitize-01: the same rejection passes with runtime ASan/UBSan and explicit abort/halt settings.
c-witness-debug-01 and c-witness-runtime-sanitize-01: the independent C window carries the expected identity in a real compositor snapshot.
debug-03 and runtime-sanitize-03: final cumulative profiles after explicit migration 0.15.0.
native-sanitize-01: focused native-server ASan/UBSan profile with GLX disabled.

The baseline remap integration case initially compared two absent identities.
Its final version requires a present identity before testing unmap and remap.
The baseline's eighteen failures remain valid evidence; the earlier remap result does not prove attribution.

Reproduction

Use sb16 or sb16-2 and the pinned native source builder.
qualification.py records the final commands and required build paths.
schema_audit.py checks predecessor descriptors against the new schemas.
Use fresh output directories for new runs.
The original host-local logs remain under local/m04-surface-identity-*.
The archive excludes application credentials, runtime databases, and binary outputs.

Limits

The native-server sanitizer profile disables GLX and enables leak detection.
Runtime compositor tests use software GLX with the Debug server.
Physical keyboard, mouse, SpaceMouse, GPU scanout, native timing, instrumented GLX lifecycle, and soak remain unrun here.
No system login or display configuration changed. SSH recovery remains available.
Worldd application grants, application leases, actor metadata filtering, and automatic session provisioning remain required.
M04, M09, Spatial-M1, and the full project remain incomplete.
