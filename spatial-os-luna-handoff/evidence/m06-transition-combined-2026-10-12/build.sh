#!/bin/bash
set -u
root=/home/dflanag3/src/spacial-m06-retained-recovery-20261012
out=$root/local/m06-transition-combined
mkdir -p "$out"
git -C "$root" rev-parse HEAD > "$out/source-commit.txt"
for profile in debug asan; do
 sanitizer=OFF
 if [ "$profile" = asan ]; then sanitizer=ON; fi
 flock "$out/$profile.lock" bash -c 'cmake -S "$1/spatial-os-luna-handoff" -B "$2" -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON -DSPATIAL_SANITIZERS="$3" && cmake --build "$2" --target spatial-actor-transition-integration-driver spatial-actor-transition-recovery-probe spatial-host-actor-native-runner -j2' _ "$root" "$out/build-$profile" "$sanitizer" > "$out/$profile-build.log" 2>&1
 result=$?
 printf '%s\n' "$result" > "$out/$profile-build.exit"
 if [ "$result" -ne 0 ]; then exit "$result"; fi
done
printf 'finished\n' > "$out/build.done"