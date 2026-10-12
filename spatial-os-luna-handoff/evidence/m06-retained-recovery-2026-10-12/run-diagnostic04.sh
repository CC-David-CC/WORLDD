#!/bin/bash
set -u
cd "$(dirname "$0")"
base="$PWD"
echo '43e4d8ae512a7ce3c058034fec332725c816929ae7d4b8056549828cb7e95174  run02-asan.tar.gz' | sha256sum -c > archive-check.log 2>&1 || exit 2
tar -xzf run02-asan.tar.gz || exit 3
(cd run02-asan && sha256sum -c SHA256SUMS) > file-check.log 2>&1 || exit 4
sha256sum -c overlay.sha256 > overlay-check.log 2>&1 || exit 5
cp test_session_world.py test_actor_transition_recovery.py healthy_issuer_setup.py run02-asan/source/tests/native/
export PYTHONPATH="$base/run02-asan/bin/python:$base/run02-asan/source"
export SPATIAL_TEST_XSERVER=/home/dflanag3/src/spacial_desktop-m04-gate-20261011/spatial-os-luna-handoff/local/m04-full-gate-01/ASan-UBSan/server-launch/Xvfb
export SPATIAL_SECURITY_EVIDENCE="$base/evidence"
export SPATIAL_SCOPE_EVIDENCE="$base/evidence/scopes"
export ASAN_OPTIONS=detect_leaks=1:abort_on_error=1
export UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
mkdir -p "$SPATIAL_SECURITY_EVIDENCE"
python3 "$base/run02-asan/source/tools/with_resource_delegate.py" --native -- python3 "$base/run02-asan/source/tools/with_adapter_resources.py" -- python3 "$base/run02-asan/source/tests/native/test_actor_transition_recovery.py" "$base/run02-asan/source" "$base/run02-asan/bin/spatial-authority-launch-probe" "$base/run02-asan/bin/spatial-security-auth" "$base/run02-asan/bin/identityd" "$base/run02-asan/bin/spatial-issuer-client" ActorTransitionRecoveryTests.test_departure_before_durable_commit ActorTransitionRecoveryTests.test_departure_after_durable_commit > runtime.log 2>&1
printf '%s\n' "$?" > runtime.exit
printf 'finished\n' > runner.done