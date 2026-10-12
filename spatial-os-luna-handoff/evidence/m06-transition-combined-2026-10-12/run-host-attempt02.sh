#!/bin/bash
set -u
cd "$(dirname "$0")"
evidence_base="$PWD"
base=/home/dflanag3/spatial-test-artifacts/m06-transition-combined-20261012-01
while tmux has-session -t spatial-m06-transition-combined01 2>/dev/null; do sleep 5; done
test -f "$base/runner.done" || exit 3
for profile in debug asan; do
 (cd "$base/$profile" && sha256sum -c SHA256SUMS) > "$profile-file-check.log" 2>&1 || exit 2
 if [ "$profile" = debug ]; then server_profile=Debug; else server_profile=ASan-UBSan; fi
 server="/home/dflanag3/src/spacial_desktop-m04-gate-20261011/spatial-os-luna-handoff/local/m04-full-gate-01/$server_profile/server-launch/Xvfb"
 mkdir -p "$evidence_base/evidence/$profile"
 systemd-run --user --wait --pipe --collect --unit="spatial-m06-combined-host-$profile-02" --property=Delegate=yes --property=RuntimeMaxSec=300 "--setenv=PYTHONPATH=$base/$profile/bin/python:$base/$profile/source" "--setenv=SPATIAL_TEST_XSERVER=$server" "--setenv=SPATIAL_SECURITY_EVIDENCE=$evidence_base/evidence/$profile" --setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1 --setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 python3 "$base/$profile/source/tests/native/test_host_actor_controller.py" "$base/$profile/source" "$base/$profile/bin/spatial-authority-launch-probe" "$base/$profile/bin/spatial-security-auth" "$base/$profile/bin/identityd" "$base/$profile/bin/spatial-issuer-client" > "$profile-host-runtime.log" 2>&1
 printf '%s\n' "$?" > "$profile-host-runtime.exit"
done
printf 'finished\n' > runner.done