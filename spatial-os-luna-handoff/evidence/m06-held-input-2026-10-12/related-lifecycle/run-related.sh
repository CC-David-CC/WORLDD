#!/usr/bin/env bash
set -u
base=/home/dflanag3/spatial-test-artifacts/m06-held-input-20261012-05
run=$base/evidence/related-lifecycle-01
mkdir "$run" || exit 2
cd "$base" || exit 2
sha256sum -c bundle.sha256 > "$run/bundle-check.log" 2>&1 || exit 2
sha256sum debug/bin/spatial-session-application-probe asan/bin/spatial-session-application-probe > "$run/helper.sha256"
result=0
for profile in debug asan; do
    if [ "$profile" = debug ]; then server_profile=Debug; else server_profile=ASan-UBSan; fi
    export PYTHONPATH="$base/$profile/python:$base/project"
    export SPATIAL_TEST_XSERVER=/home/dflanag3/src/spacial_desktop-m06-20261012/spatial-os-luna-handoff/local/m06-scoped-input-05/$server_profile/server-launch/Xvfb
    export ASAN_OPTIONS=detect_leaks=1:abort_on_error=1
    export UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
    for suite in session_application session_replacement; do
        out=$run/$profile/$suite
        mkdir -p "$out"
        export SPATIAL_SECURITY_EVIDENCE="$out"
        if [ "$suite" = session_application ]; then limit=180; else limit=240; fi
        timeout --signal=TERM --kill-after=30s "$limit" \
          python3 "$base/project/tools/with_resource_delegate.py" --native -- \
          python3 "$base/project/tools/with_adapter_resources.py" -- \
          python3 "$base/project/tests/native/test_$suite.py" "$base/project" \
          "$base/$profile/bin/spatial-authority-launch-probe" \
          "$base/$profile/bin/spatial-security-auth" \
          "$base/$profile/bin/identityd" \
          "$base/$profile/bin/spatial-issuer-client" > "$out/runtime.log" 2>&1
        code=$?
        printf '%s\n' "$code" > "$out/runtime.exit"
        if [ "$code" -ne 0 ]; then result=1; fi
    done
done
printf '%s\n' "$result" > "$run/runner.exit"
exit "$result"
