#!/usr/bin/env bash
# Build and freeze a complete qualification package for one reviewed profile.
# Usage: package_debug_qualification.sh SOURCE_CHECKOUT PROFILE PACKAGE_DIR ARCHIVE CTEST_STAGE
set -Eeuo pipefail
if (( $# != 5 )); then
  echo "usage: $0 SOURCE_CHECKOUT PROFILE PACKAGE_DIR ARCHIVE CTEST_STAGE" >&2
  exit 64
fi
PROFILE=$2
case "$PROFILE" in
  Debug)
    BUILD_DIR_NAME=debug
    SANITIZERS=OFF
    ;;
  ASan-UBSan)
    BUILD_DIR_NAME=asan-ubsan
    SANITIZERS=ON
    ;;
  *)
    echo "unsupported profile: $PROFILE (expected Debug or ASan-UBSan)" >&2
    exit 64
    ;;
esac
SRC=$(realpath "$1")
FINAL=$(realpath -m "$3")
ARCHIVE=$(realpath -m "$4")
CTEST_STAGE=$(realpath -m "$5")
[[ -d "$SRC/.git" || -f "$SRC/.git" ]] || { echo "source is not a Git checkout" >&2; exit 66; }
for path in "$FINAL" "$ARCHIVE" "$CTEST_STAGE"; do
  [[ "$path" != "$SRC" && "$path" != "$SRC/"* ]] || { echo "output must be outside source checkout" >&2; exit 65; }
done
[[ "$CTEST_STAGE" != "$FINAL" && "$CTEST_STAGE" != "$FINAL/"* ]] || { echo "CTest stage must be outside package" >&2; exit 65; }
[[ ! -e "$FINAL" && ! -L "$FINAL" && ! -e "$ARCHIVE" && ! -e "$ARCHIVE.sha256" && ! -e "$CTEST_STAGE" ]] || { echo "refusing existing output path" >&2; exit 73; }
COMMIT=$(git -C "$SRC" rev-parse HEAD)
[[ -z "$(git -C "$SRC" status --porcelain)" ]] || { echo "source checkout is not clean" >&2; exit 65; }
mkdir -p "$(dirname "$FINAL")" "$(dirname "$ARCHIVE")" "$(dirname "$CTEST_STAGE")"
FREE_KB=$(df -Pk "$(dirname "$FINAL")" | awk 'NR==2 {print $4}')
(( FREE_KB > 10 * 1024 * 1024 )) || { echo "less than 10 GiB free before build" >&2; exit 75; }
mkdir -p "$FINAL/source" "$FINAL/builds"
git -C "$SRC" archive "$COMMIT" | tar -x -C "$FINAL/source"
PROJECT="$FINAL/source/spatial-os-luna-handoff"
BUILD="$FINAL/builds/$BUILD_DIR_NAME"
[[ -f "$PROJECT/CMakeLists.txt" ]] || { echo "packaged project CMakeLists.txt missing" >&2; exit 66; }
printf 'source_commit=%s\nbuild_profile=%s\nspatial_sanitizers=%s\ngenerator=Ninja\npackage_path=%s\nbuild_path=%s\nctest_stage=%s\n' \
  "$COMMIT" "$PROFILE" "$SANITIZERS" "$FINAL" "$BUILD" "$CTEST_STAGE" | tee "$FINAL/SOURCE_PIN.txt"
cmake -G Ninja -S "$PROJECT" -B "$BUILD" -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON \
  -DSPATIAL_INPUT_TESTS=ON -DSPATIAL_SANITIZERS="$SANITIZERS"
cmake --build "$BUILD" --parallel 2
# Copy CTest registration files into an external writable stage.
mkdir -p "$CTEST_STAGE"
python3 - "$BUILD" "$CTEST_STAGE" "$BUILD" "$PROJECT/tools" <<'PY'
import pathlib,re,shutil,sys
build,stage,workdir=map(pathlib.Path,sys.argv[1:4])
sys.path.insert(0,sys.argv[4])
from package_qualification_inventory import registration_names_from_text
files=list(build.rglob('CTestTestfile.cmake'))
assert files, 'no CTestTestfile.cmake found'
registration_names=[]
for src in files:
 rel=src.relative_to(build); dst=stage/rel; dst.parent.mkdir(parents=True,exist_ok=True)
 text=src.read_text(); names=registration_names_from_text(text, str(src))
 if names:
  text += '\n# External stage keeps CTest logs outside the frozen package.\n'
  for name in names:
   text += f'set_tests_properties([=[{name}]=] PROPERTIES WORKING_DIRECTORY [=[{workdir}]=])\n'
  registration_names.extend(names)
 dst.write_text(text)
 print(f'{rel}: registrations={len(names)}')
assert registration_names, 'no CTest registrations found in metadata'
assert len(registration_names)==len(set(registration_names)), 'duplicate CTest names in metadata'
(stage/'CTEST_REGISTERED_NAMES.txt').write_text('\n'.join(sorted(registration_names))+'\n')
shutil.copy2(build/'DartConfiguration.tcl',stage/'DartConfiguration.tcl')
dart=(stage/'DartConfiguration.tcl').read_text()
dart=re.sub(r'^BuildDirectory: .*$',f'BuildDirectory: {stage}',dart,flags=re.M)
dart=re.sub(r'^SourceDirectory: .*$',f'SourceDirectory: {build.parent.parent}/source/spatial-os-luna-handoff',dart,flags=re.M)
(stage/'DartConfiguration.tcl').write_text(dart)
PY
python3 - "$FINAL" "$CTEST_STAGE" "$BUILD_DIR_NAME" "$PROJECT/tools" <<'PY'
import json,pathlib,re,subprocess,sys
root,stage=map(pathlib.Path,sys.argv[1:3]); build=root/'builds'/sys.argv[3]
sys.path.insert(0,sys.argv[4])
from package_qualification_inventory import validate_inventory
r=subprocess.run(['ctest','--test-dir',str(stage),'--show-only=json-v1'],text=True,capture_output=True,check=True)
j=json.loads(r.stdout); tests=j.get('tests',[]); names=[t['name'] for t in tests]
registered=(stage/'CTEST_REGISTERED_NAMES.txt').read_text().splitlines()
cmake=(root/'source/spatial-os-luna-handoff/CMakeLists.txt').read_text()
inventory=validate_inventory(registered, j, cmake, build)
previously_missing=['broker-file-action-client-test','file-action-effect-wait-test','presentation-metadata-test','queued-copy-status-replacement-test','spatial-actor-session-client-probe','spatial-actor-session-client-tls-probe','spatial-actor-transition-coordinator-probe','spatial-host-actor-controller-probe','spatial-snapshot-size-regression','workspace-copy-journal-test','workspace-file-engine-test','workspace-file-operation-journal-test','workspace-presentation-projection-test','workspace-presentation-store-test','world-diagnostic-timing-test']
for name in previously_missing:
 exe=build/name
 assert exe.is_file() and exe.stat().st_mode & 0o111, f'missing previously absent executable {name}'
(stage/'CTEST_REGISTRATION.json').write_text(json.dumps(j,indent=2,sort_keys=True)+'\n')
r=subprocess.run(['ctest','--test-dir',str(stage),'-N','-C','Debug'],text=True,capture_output=True)
(stage/'CTEST_LIST_ONLY.txt').write_text(r.stdout+r.stderr)
assert r.returncode==0, f'ctest -N exit {r.returncode}'
assert 'Could not find executable' not in r.stdout+r.stderr, 'missing registered executable'
assert f'Test project {stage}' in r.stdout, 'CTest bookkeeping is not external'
assert f'Total Tests: {len(names)}' in r.stdout, 'CTest registration count mismatch'
print(f'external CTest stage registrations={inventory["registered_count"]}; SPATIAL_INPUT_TESTS registrations={inventory["input_count"]} present; prior missing executables=15 present; explicit package workdirs={inventory["working_directory_count"]}; list-only is warning-free')
PY
: > "$FINAL/ELF_DEPENDENCIES.txt"
while IFS= read -r -d '' f; do
  if file -b "$f" | grep -q 'ELF'; then
    printf '## %s\n' "${f#"$FINAL"/}" >> "$FINAL/ELF_DEPENDENCIES.txt"
    ldd "$f" >> "$FINAL/ELF_DEPENDENCIES.txt" 2>&1 || { echo "ldd failed: $f" >&2; exit 31; }
  fi
done < <(find "$BUILD" -type f -perm /111 -print0)
if grep -q 'not found' "$FINAL/ELF_DEPENDENCIES.txt"; then echo 'unresolved ELF dependency found' >&2; exit 32; fi
python3 "$PROJECT/tools/clean_package_ctest_bookkeeping.py" "$FINAL" "$BUILD"
( cd "$FINAL/source" && find . -type f -print0 | sort -z | xargs -0 sha256sum ) > "$FINAL/SOURCE_MANIFEST.sha256"
( cd "$BUILD" && find . -type f -print0 | sort -z | xargs -0 sha256sum ) > "$FINAL/BUILD_MANIFEST.sha256"
( cd "$FINAL" && find . -type f ! -name PACKAGE_MANIFEST.sha256 -print0 | sort -z | xargs -0 sha256sum ) > "$FINAL/PACKAGE_MANIFEST.sha256"
python3 - "$FINAL" "$BUILD_DIR_NAME" <<'PY'
import hashlib,pathlib,sys
p=pathlib.Path(sys.argv[1]); build_dir=sys.argv[2]
for name,base in [('SOURCE_MANIFEST.sha256',p/'source'),('BUILD_MANIFEST.sha256',p/'builds'/build_dir),('PACKAGE_MANIFEST.sha256',p)]:
 for line in (p/name).read_text().splitlines():
  if not line: continue
  d,rel=line.split('  ',1); f=base/rel.removeprefix('./')
  assert f.is_file() and hashlib.sha256(f.read_bytes()).hexdigest()==d, f'{name}: {rel}'
print('all three manifests verified')
PY
find "$FINAL" -type f -exec chmod a-w {} +
find "$FINAL" -type d -exec chmod a-w {} +
tar -czf "$ARCHIVE" -C "$(dirname "$FINAL")" "$(basename "$FINAL")"
sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"
sha256sum -c "$ARCHIVE.sha256"
# Re-extract at the same canonical package path, then inspect CTest only from the external stage.
BACKUP="$FINAL.pre-extraction-verify"
[[ ! -e "$BACKUP" && ! -L "$BACKUP" ]] || { echo "refusing existing verification backup" >&2; exit 73; }
mv -- "$FINAL" "$BACKUP"
tar -xzf "$ARCHIVE" -C "$(dirname "$FINAL")"
sha256sum -c "$ARCHIVE.sha256"
python3 - "$FINAL" "$BUILD_DIR_NAME" "$CTEST_STAGE" "$PROJECT/tools" <<'PY'
import hashlib,json,pathlib,sys
p=pathlib.Path(sys.argv[1]); build_dir=sys.argv[2]; stage=pathlib.Path(sys.argv[3])
sys.path.insert(0,sys.argv[4])
from package_qualification_inventory import validate_inventory
for name,base in [('SOURCE_MANIFEST.sha256',p/'source'),('BUILD_MANIFEST.sha256',p/'builds'/build_dir),('PACKAGE_MANIFEST.sha256',p)]:
 for line in (p/name).read_text().splitlines():
  if not line: continue
  d,rel=line.split('  ',1); f=base/rel.removeprefix('./')
  assert f.is_file() and hashlib.sha256(f.read_bytes()).hexdigest()==d, f'{name}: {rel}'
assert not any(x.name=='Testing' for x in p.rglob('*')), 'CTest bookkeeping entered extracted package'
registered=(stage/'CTEST_REGISTERED_NAMES.txt').read_text().splitlines()
registration=json.loads((stage/'CTEST_REGISTRATION.json').read_text())
cmake=(p/'source/spatial-os-luna-handoff/CMakeLists.txt').read_text()
inventory=validate_inventory(registered, registration, cmake, p/'builds'/build_dir)
print(f'extracted package inventory registrations={inventory["registered_count"]}; input registrations={inventory["input_count"]}; workdirs={inventory["working_directory_count"]}')
PY
ctest --test-dir "$CTEST_STAGE" -N -C Debug > "$CTEST_STAGE/CTEST_LIST_ONLY_AFTER_EXTRACT.txt" 2>&1
! grep -q 'Could not find executable' "$CTEST_STAGE/CTEST_LIST_ONLY_AFTER_EXTRACT.txt"
grep -F "Test project $CTEST_STAGE" "$CTEST_STAGE/CTEST_LIST_ONLY_AFTER_EXTRACT.txt"
registered_count=$(wc -l < "$CTEST_STAGE/CTEST_REGISTERED_NAMES.txt")
grep -F "Total Tests: $registered_count" "$CTEST_STAGE/CTEST_LIST_ONLY_AFTER_EXTRACT.txt"
[[ "$(realpath "$BACKUP")" == "$BACKUP" && -d "$BACKUP" && ! -L "$BACKUP" ]]
removed_bytes=$(du -sb "$BACKUP" | awk '{print $1}')
find "$BACKUP" -type d -exec chmod u+w {} +
rm -rf -- "$BACKUP"
printf 'removed_verified_duplicate_bytes=%s path=%s\n' "$removed_bytes" "$BACKUP"
printf 'PACKAGE=%s\nARCHIVE=%s\nCTEST_STAGE=%s\n' "$FINAL" "$ARCHIVE" "$CTEST_STAGE"
du -sh "$FINAL" "$ARCHIVE" "$CTEST_STAGE"
