#!/bin/bash
set -u
cd "$(dirname "$0")"
base="$PWD"
echo 'a2a601dfb47505bfd1f136036effd1d4cf6eb2f509419ed6a0dcfa7a804e9fd9  attempt02-bundle.tar.gz' | sha256sum -c > archive-check.log 2>&1 || exit 2
tar -xzf attempt02-bundle.tar.gz || exit 3
sha256sum -c bundle.sha256 > file-check.log 2>&1 || exit 4
echo 'd3dcc8abee97528002f7facc562c1c0c68c11984f81a72777461e87a71e4d7a2  runtime.textproto' | sha256sum -c > runtime-check.log 2>&1 || exit 5
cp runtime.textproto debug/bin/runtime.textproto
cp runtime.textproto asan/bin/runtime.textproto
echo 'd2ff002f358272f43a733076305a139bc7d9f9780fe144fbe22f23d61a32c876  test_session_world.py' | sha256sum -c >> overlay-check.log 2>&1 || exit 6
cp test_session_world.py project/tests/native/test_session_world.py
echo '4f32d516fb29af6d0a20be302f6600b8362952ada6cbabc6d56bc7aae711e527  test_host_actor_controller.py' | sha256sum -c >> overlay-check.log 2>&1 || exit 6
cp test_host_actor_controller.py project/tests/native/test_host_actor_controller.py
while tmux has-session -t spatial-m06-realworld01 2>/dev/null; do sleep 5; done
for profile in debug asan; do
 if [ "$profile" = debug ]; then server_profile=Debug; else server_profile=ASan-UBSan; fi
 mkdir -p "$base/evidence/$profile"
 server="/home/dflanag3/src/spacial_desktop-m04-gate-20261011/spatial-os-luna-handoff/local/m04-full-gate-01/$server_profile/server-launch/Xvfb"
 systemd-run --user --wait --pipe --collect --unit="spatial-m06-host-native-$profile-06" --property=Delegate=yes --property=RuntimeMaxSec=300   "--setenv=PYTHONPATH=$base/$profile/python:$base/project"   "--setenv=SPATIAL_TEST_XSERVER=$server" "--setenv=SPATIAL_SECURITY_EVIDENCE=$base/evidence/$profile"   --setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1 --setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1   python3 "$base/project/tests/native/test_host_actor_controller.py" "$base/project"   "$base/$profile/bin/spatial-authority-launch-probe" "$base/$profile/bin/spatial-security-auth"   "$base/$profile/bin/identityd" "$base/$profile/bin/spatial-issuer-client" > "$profile-native.log" 2>&1
 result=$?
 printf '%s
' "$result" > "$profile-native.exit"
 mkdir -p "$base/evidence/regression-$profile"
 systemd-run --user --wait --pipe --collect --unit="spatial-m06-session-regression-$profile-06" --property=Delegate=yes --property=RuntimeMaxSec=600 "--setenv=PYTHONPATH=$base/$profile/python:$base/project" "--setenv=SPATIAL_TEST_XSERVER=$server" "--setenv=SPATIAL_SECURITY_EVIDENCE=$base/evidence/regression-$profile" --setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1 --setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 python3 "$base/project/tests/native/test_session_world.py" "$base/project" "$base/$profile/bin/spatial-authority-launch-probe" "$base/$profile/bin/spatial-security-auth" "$base/$profile/bin/identityd" "$base/$profile/bin/spatial-issuer-client" > "$profile-regression.log" 2>&1
 printf '%s\n' "$?" > "$profile-regression.exit"
done
printf 'finished
' > runner.done