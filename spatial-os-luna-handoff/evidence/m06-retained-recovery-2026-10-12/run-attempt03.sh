#!/bin/bash
set -u
cd "$(dirname "$0")"
base="$PWD"
echo '024b8b01ee6c6845b7a103d04ac839936847091892c951444b7089325c39b98c  test_actor_transition_recovery.py' | sha256sum -c > recovery-overlay-check.log 2>&1 || exit 8
echo '8b0243eedbda925141457374128873ef1df061ca90b3f824699e2d3e60aac7aa  test_session_world.py' | sha256sum -c > overlay-check.log 2>&1 || exit 7
sha256sum -c run02-bundles.sha256 > archive-check.log 2>&1 || exit 2
for profile in debug asan; do
 tar -xzf "run02-$profile.tar.gz" || exit 3
 (cd "run02-$profile" && sha256sum -c SHA256SUMS) > "$profile-file-check.log" 2>&1 || exit 4
 cp test_session_world.py "run02-$profile/source/tests/native/test_session_world.py"
 cp test_actor_transition_recovery.py "run02-$profile/source/tests/native/test_actor_transition_recovery.py"
 if [ "$profile" = debug ]; then server_profile=Debug; else server_profile=ASan-UBSan; fi
 export SPATIAL_TEST_XSERVER="/home/dflanag3/src/spacial_desktop-m04-gate-20261011/spatial-os-luna-handoff/local/m04-full-gate-01/$server_profile/server-launch/Xvfb"
 export SPATIAL_SECURITY_EVIDENCE="$base/evidence/$profile"
 export SPATIAL_SCOPE_EVIDENCE="$base/evidence/$profile/scopes"
 mkdir -p "$SPATIAL_SECURITY_EVIDENCE"
 test -x "$SPATIAL_TEST_XSERVER" || exit 5
 for bin in "$base/run02-$profile/bin/"*; do
  test -x "$bin" && test -f "$bin" || continue
  ldd "$bin" >> "$profile-ldd.log" 2>&1
 done
 if grep -q 'not found' "$profile-ldd.log"; then exit 6; fi
 (cd "run02-$profile" && ./run-tests.sh) > "$profile-runtime.log" 2>&1
 printf '%s\n' "$?" > "$profile-runtime.exit"
done
printf 'finished\n' > runner.done