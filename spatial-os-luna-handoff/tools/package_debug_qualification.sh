#!/usr/bin/env bash
# Build and freeze a complete, relocatable Debug qualification package.
# Usage: package_debug_qualification.sh SOURCE_CHECKOUT PACKAGE_DIR ARCHIVE
set -Eeuo pipefail
if (( $# != 3 )); then
  echo "usage: $0 SOURCE_CHECKOUT PACKAGE_DIR ARCHIVE" >&2
  exit 64
fi
SRC=$(realpath "$1")
FINAL=$(realpath -m "$2")
ARCHIVE=$(realpath -m "$3")
[[ -d "$SRC/.git" || -f "$SRC/.git" ]] || { echo "source is not a Git checkout" >&2; exit 66; }
[[ "$FINAL" != "$SRC" && "$FINAL" != "$SRC/"* ]] || { echo "package must be outside source checkout" >&2; exit 65; }
[[ "$ARCHIVE" != "$SRC" && "$ARCHIVE" != "$SRC/"* ]] || { echo "archive must be outside source checkout" >&2; exit 65; }
[[ ! -e "$FINAL" && ! -L "$FINAL" ]] || { echo "refusing existing package path: $FINAL" >&2; exit 73; }
[[ ! -e "$ARCHIVE" && ! -e "$ARCHIVE.sha256" ]] || { echo "refusing existing archive path" >&2; exit 73; }
COMMIT=$(git -C "$SRC" rev-parse HEAD)
[[ -z "$(git -C "$SRC" status --porcelain)" ]] || { echo "source checkout is not clean" >&2; exit 65; }
mkdir -p "$(dirname "$FINAL")" "$(dirname "$ARCHIVE")"
FREE_KB=$(df -Pk "$(dirname "$FINAL")" | awk 'NR==2 {print $4}')
(( FREE_KB > 10 * 1024 * 1024 )) || { echo "less than 10 GiB free before build" >&2; exit 75; }
mkdir -p "$FINAL/source" "$FINAL/builds"
git -C "$SRC" archive "$COMMIT" | tar -x -C "$FINAL/source"
PROJECT="$FINAL/source/spatial-os-luna-handoff"
BUILD="$FINAL/builds/debug"
[[ -f "$PROJECT/CMakeLists.txt" ]] || { echo "packaged project CMakeLists.txt missing" >&2; exit 66; }
printf 'source_commit=%s\npackage_path=%s\nbuild_path=%s\n' "$COMMIT" "$FINAL" "$BUILD" | tee "$FINAL/SOURCE_PIN.txt"
cmake -S "$PROJECT" -B "$BUILD" -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON -DSPATIAL_SANITIZERS=OFF
cmake --build "$BUILD" --parallel 2
python3 - "$FINAL" <<'PY'
import json, pathlib, subprocess, sys
root=pathlib.Path(sys.argv[1]); build=root/'builds/debug'
j=json.loads(subprocess.check_output(['ctest','--test-dir',str(build),'--show-only=json-v1'],text=True))
tests=j.get('tests',[]); names=[t['name'] for t in tests]
assert len(tests)==167, f'expected 167 CTest registrations, got {len(tests)}'
assert len(set(names))==167, 'duplicate CTest registration names'
(root/'CTEST_REGISTRATION.json').write_text(json.dumps(j,indent=2,sort_keys=True)+'\n')
r=subprocess.run(['ctest','--test-dir',str(build),'-N','-C','Debug'],text=True,capture_output=True)
(root/'CTEST_LIST_ONLY.txt').write_text(r.stdout+r.stderr)
assert r.returncode==0, f'ctest -N failed with {r.returncode}'
assert 'Could not find executable' not in r.stdout+r.stderr, 'CTest reports missing registered executable'
assert f'Test project {build}' in r.stdout, 'CTest project path is not the package build directory'
assert 'Total Tests: 167' in r.stdout, 'CTest list did not report all 167 tests'
dart=(build/'DartConfiguration.tcl').read_text()
assert f'BuildDirectory: {build}' in dart, 'DartConfiguration has noncanonical build directory'
assert f'SourceDirectory: {root}/source/spatial-os-luna-handoff' in dart, 'DartConfiguration has noncanonical source directory'
print(f'CTest registrations={len(tests)}; unique={len(set(names))}; CTest -N warning-free; build={build}')
PY
: > "$FINAL/ELF_DEPENDENCIES.txt"
while IFS= read -r -d '' f; do
  if file -b "$f" | grep -q 'ELF'; then
    printf '## %s\n' "${f#"$FINAL"/}" >> "$FINAL/ELF_DEPENDENCIES.txt"
    ldd "$f" >> "$FINAL/ELF_DEPENDENCIES.txt" 2>&1 || { echo "ldd failed: $f" >&2; exit 31; }
  fi
done < <(find "$BUILD" -type f -perm /111 -print0)
if grep -q 'not found' "$FINAL/ELF_DEPENDENCIES.txt"; then
  echo "unresolved ELF dependency found" >&2
  exit 32
fi
( cd "$FINAL/source" && find . -type f -print0 | sort -z | xargs -0 sha256sum ) > "$FINAL/SOURCE_MANIFEST.sha256"
( cd "$BUILD" && find . -type f -print0 | sort -z | xargs -0 sha256sum ) > "$FINAL/BUILD_MANIFEST.sha256"
( cd "$FINAL" && find . -type f ! -name PACKAGE_MANIFEST.sha256 -print0 | sort -z | xargs -0 sha256sum ) > "$FINAL/PACKAGE_MANIFEST.sha256"
python3 - "$FINAL" <<'PY'
import hashlib,pathlib,sys
p=pathlib.Path(sys.argv[1])
for name,base in [('SOURCE_MANIFEST.sha256',p/'source'),('BUILD_MANIFEST.sha256',p/'builds/debug'),('PACKAGE_MANIFEST.sha256',p)]:
 for line in (p/name).read_text().splitlines():
  if not line: continue
  digest,rel=line.split('  ',1); target=base/rel.removeprefix('./')
  assert target.is_file(), f'{name}: missing {rel}'
  assert hashlib.sha256(target.read_bytes()).hexdigest()==digest, f'{name}: hash mismatch {rel}'
print('all three manifests verified')
PY
find "$FINAL" -type f -exec chmod a-w {} +
find "$FINAL" -type d -exec chmod a-w {} +
tar -czf "$ARCHIVE" -C "$(dirname "$FINAL")" "$(basename "$FINAL")"
sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"
sha256sum -c "$ARCHIVE.sha256"
printf 'PACKAGE=%s\nARCHIVE=%s\n' "$FINAL" "$ARCHIVE"
du -sh "$FINAL" "$ARCHIVE"
