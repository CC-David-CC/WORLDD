#!/bin/bash
set -u
cd "$(dirname "$0")"
base="$PWD"
sha256sum -c run02-bundles.sha256 > archive-check.log 2>&1 || exit 2
for profile in debug asan; do
 tar -xzf "run02-$profile.tar.gz" || exit 3
 (cd "run02-$profile" && sha256sum -c SHA256SUMS) > "$profile-file-check.log" 2>&1 || exit 4
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