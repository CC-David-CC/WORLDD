#!/bin/bash
set -u
cd "$(dirname "$0")"
base="$PWD"
sha256sum -c archives.sha256 > archive-check.log 2>&1 || exit 2
for profile in debug asan; do
 tar -xzf "$profile-bundle.tar.gz" || exit 3
 (cd "$profile" && sha256sum -c SHA256SUMS) > "$profile-file-check.log" 2>&1 || exit 4
 if [ "$profile" = debug ]; then server_profile=Debug; else server_profile=ASan-UBSan; fi
 export PYTHONPATH="$base/$profile/bin/python:$base/$profile/source"
 export SPATIAL_TEST_XSERVER="/home/dflanag3/src/spacial_desktop-m04-gate-20261011/spatial-os-luna-handoff/local/m04-full-gate-01/$server_profile/server-launch/Xvfb"
 export ASAN_OPTIONS=detect_leaks=1:abort_on_error=1
 export UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
 test -x "$SPATIAL_TEST_XSERVER" || exit 5
 for bin in "$base/$profile/bin/"*; do
  test -x "$bin" && test -f "$bin" || continue
  ldd "$bin" >> "$profile-ldd.log" 2>&1
 done
 if grep -q 'not found' "$profile-ldd.log"; then exit 6; fi
 for suite in realworld recovery host; do
  export SPATIAL_SECURITY_EVIDENCE="$base/evidence/$profile/$suite"
  export SPATIAL_SCOPE_EVIDENCE="$SPATIAL_SECURITY_EVIDENCE/scopes"
  mkdir -p "$SPATIAL_SECURITY_EVIDENCE"
  if [ "$suite" = realworld ]; then
   command=(python3 "$base/$profile/source/tests/core/test_actor_transition_integration.py" "$base/$profile/bin/identityd" "$base/$profile/bin/spatial-issuer-client" "$base/$profile/bin/worldd" "$base/$profile/bin/spatial-actor-transition-integration-driver")
  else
   if [ "$suite" = recovery ]; then test_file=test_actor_transition_recovery.py; else test_file=test_host_actor_controller.py; fi
   command=(python3 "$base/$profile/source/tests/native/$test_file" "$base/$profile/source" "$base/$profile/bin/spatial-authority-launch-probe" "$base/$profile/bin/spatial-security-auth" "$base/$profile/bin/identityd" "$base/$profile/bin/spatial-issuer-client")
  fi
  python3 "$base/$profile/source/tools/with_resource_delegate.py" --native -- python3 "$base/$profile/source/tools/with_adapter_resources.py" -- "${command[@]}" > "$profile-$suite-runtime.log" 2>&1
  printf '%s\n' "$?" > "$profile-$suite-runtime.exit"
 done
done
printf 'finished\n' > runner.done