#!/usr/bin/env bash
set -u
cd "$(dirname "$0")" || exit 2
base=$PWD
mkdir -p evidence
sha256sum -c bundle.sha256 > evidence/bundle-check.log 2>&1 || exit 2
for profile in debug asan; do
    if [ "$profile" = debug ]; then server_profile=Debug; else server_profile=ASan-UBSan; fi
    server=/home/dflanag3/src/spacial_desktop-m04-gate-20261011/spatial-os-luna-handoff/local/m04-full-gate-01/$server_profile/server-launch/Xvfb
    test -x "$server" || exit 3
    mkdir -p "$base/evidence/$profile"
    sha256sum "$server" > "$base/evidence/$profile/server-wrapper.sha256"
    export PYTHONPATH="$base/$profile/python:$base/project"
    export SPATIAL_TEST_XSERVER="$server"
    export SPATIAL_SECURITY_EVIDENCE="$base/evidence/$profile"
    export ASAN_OPTIONS=detect_leaks=1:abort_on_error=1
    export UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
    python3 "$base/project/tools/with_resource_delegate.py" --native -- \
      python3 "$base/project/tools/with_adapter_resources.py" -- \
      python3 "$base/project/tests/native/test_host_actor_controller.py" "$base/project" \
      "$base/$profile/bin/spatial-authority-launch-probe" \
      "$base/$profile/bin/spatial-security-auth" \
      "$base/$profile/bin/identityd" \
      "$base/$profile/bin/spatial-issuer-client" > "$base/evidence/$profile/runtime.log" 2>&1
    printf '%s\n' "$?" > "$base/evidence/$profile/runtime.exit"
done
printf 'finished\n' > runner.done
