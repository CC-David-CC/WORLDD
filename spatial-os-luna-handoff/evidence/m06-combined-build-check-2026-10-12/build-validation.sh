#!/usr/bin/env bash
set -u
ROOT=/home/dflanag3/src/spacial-m06-combined-build-check-20261012
REL=local/m06-combined-build-check-20261012
RUN="$ROOT/$REL"
LOGS="$RUN/logs"
PLAN=/tmp/m06-combined-build-check-20261012/registration-only-plan.pb
TARGETS=(spatial-actor-transition-crosshost-probe spatial-actor-transition-integration-driver spatial-host-actor-native-runner)
mkdir -p "$LOGS"
run_step() {
    name="$1"
    shift
    printf 'RUN %s\n' "$name"
    (cd "$ROOT" && "$@") >"$LOGS/$name.log" 2>&1
    rc=$?
    printf '%s\n' "$rc" >"$LOGS/$name.exit"
    printf 'EXIT %s %s\n' "$name" "$rc"
    return "$rc"
}
failed=0
if run_step debug-configure cmake -S spatial-os-luna-handoff -B "$REL/debug" -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON -DSPATIAL_SANITIZERS=OFF "-DSPATIAL_ACTOR_TRANSITION_PLAN=$PLAN"; then
    if run_step debug-build flock "$RUN/build.lock" cmake --build "$REL/debug" --target "${TARGETS[@]}" -j2; then
        run_step debug-ctest-registration ctest --test-dir "$REL/debug" -N -R 'actor-transition-real-world|actor-transition-crosshost|host-actor-controller-native' || failed=1
    else
        failed=1
        printf 'SKIPPED debug-ctest-registration: debug build failed\n' >"$LOGS/debug-ctest-registration.skipped"
    fi
else
    failed=1
    printf 'SKIPPED debug-build and debug-ctest-registration: debug configure failed\n' >"$LOGS/debug-downstream.skipped"
fi
if run_step asan-configure cmake -S spatial-os-luna-handoff -B "$REL/asan" -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON -DSPATIAL_SANITIZERS=ON "-DSPATIAL_ACTOR_TRANSITION_PLAN=$PLAN"; then
    if run_step asan-build flock "$RUN/build.lock" cmake --build "$REL/asan" --target "${TARGETS[@]}" -j2; then
        run_step asan-ctest-registration ctest --test-dir "$REL/asan" -N -R 'actor-transition-real-world|actor-transition-crosshost|host-actor-controller-native' || failed=1
    else
        failed=1
        printf 'SKIPPED asan-ctest-registration: asan build failed\n' >"$LOGS/asan-ctest-registration.skipped"
    fi
else
    failed=1
    printf 'SKIPPED asan-build and asan-ctest-registration: asan configure failed\n' >"$LOGS/asan-downstream.skipped"
fi
printf 'FINAL failures=%s\n' "$failed"
exit "$failed"