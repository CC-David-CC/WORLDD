#!/usr/bin/env bash
set -eu
expected=2954de07c8646da6a0dfc7ffbcf9fdd8d59846fe
test "$(git rev-parse HEAD)" = "$expected"
test -z "$(git status --porcelain --untracked-files=no)"
run="$PWD/local/integrated-build01"
mkdir "$run/steps"
printf '%s\n' 'Selected: Debug and ASan ON/OFF host-session builds; ON crosshost/integration/native-driver dependencies and CTest inventories. OFF generated Python import closure checked statically. Runtime UNRUN; purpose is integrated CMake ownership and dependency verification.' > "$run/selected-checks.txt"
step() {
    name=$1; shift
    printf 'START %s\n' "$name"
    set +e
    "$@" > "$run/steps/$name.log" 2>&1
    result=$?
    set -e
    printf '%s\n' "$result" > "$run/steps/$name.exit"
    printf 'END %s %s\n' "$name" "$result"
    return "$result"
}
for profile in debug asan; do
    sanitizer=OFF
    if [ "$profile" = asan ]; then sanitizer=ON; fi
    for mode in on off; do
        testing=OFF
        if [ "$mode" = on ]; then testing=ON; fi
        build="$run/$profile-$mode"
        step "$profile-$mode-configure" flock "$run/build.lock" cmake -S spatial-os-luna-handoff -B "$build" -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING="$testing" -DSPATIAL_SANITIZERS="$sanitizer" -DSPATIAL_ACTOR_TRANSITION_PLAN=/tmp/registration-only-host-session-plan.pb
        targets=(spatial-host-session)
        if [ "$mode" = on ]; then
            targets+=(spatial-actor-transition-crosshost-probe spatial-actor-transition-integration-driver spatial-host-actor-native-runner)
        fi
        step "$profile-$mode-build" flock "$run/build.lock" cmake --build "$build" --target "${targets[@]}" -j2
        if [ "$mode" = on ]; then
            step "$profile-inventory" ctest --test-dir "$build" --show-only=json-v1
        fi
        step "$profile-$mode-bindings" python3 - "$build/python" <<'PY'
import ast
import sys
from pathlib import Path
root = Path(sys.argv[1])
for name in ('host_session', 'host_actor_controller', 'actor_transition', 'actor_session_client'):
    assert (root / (name + '_pb2.py')).is_file(), name
for path in root.glob('*_pb2.py'):
    for node in ast.walk(ast.parse(path.read_text())):
        names = ([x.name for x in node.names] if isinstance(node, ast.Import)
                 else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
        for name in names:
            if name.endswith('_pb2'):
                assert (root / (name + '.py')).is_file(), (path.name, name)
print('Static generated import closure verified; no runtime imports or probes executed.')
PY
    done
done
python3 - "$run" <<'PY'
import json
import sys
from pathlib import Path
root = Path(sys.argv[1])
required = {'actor-transition-real-world', 'actor-transition-crosshost',
            'host-actor-controller-native', 'host-session-startup', 'host-session-production-native'}
for profile in ('debug', 'asan'):
    tests = json.loads((root / 'steps' / (profile + '-inventory.log')).read_text())['tests']
    selected = {row['name']: row for row in tests if row['name'] in required}
    assert set(selected) == required
    assert all(row.get('command') and row['command'][0] for row in selected.values())
    assert any('with_resource_delegate.py' in arg for arg in selected['host-actor-controller-native']['command'])
    print(profile, sorted(selected), 'registration only; runtime UNRUN')
PY
