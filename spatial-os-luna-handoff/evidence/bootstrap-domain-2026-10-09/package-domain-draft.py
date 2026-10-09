import hashlib, io, json, pathlib, tarfile
root = pathlib.Path(__file__).resolve().parents[1]
draft = root/'local/bootstrap-domain-draft'
files = {name: (root/name).read_bytes().replace(b'\r\n',b'\n')
         for name in json.loads((root/'local/bootstrap-tested-tree-02.json').read_text())['files']}
for source, target in {
    'store.cpp': 'src/authority/store.cpp', 'store.hpp': 'src/authority/store.hpp',
    'authority_migrate.cpp': 'src/authority/migrate_main.cpp',
    'authority_probe.cpp': 'src/testing/authority_probe.cpp',
    'grants.proto': 'proto/spatial/authority/v1/grants.proto',
    'grants_probe.proto': 'proto/spatial/authority/v1/grants_probe.proto',
    'test_authority.py': 'tests/core/test_authority.py',
    'test_authority_bootstrap.py': 'tests/core/test_authority_bootstrap.py',
}.items():
    files[target] = (draft/source).read_bytes().replace(b'\r\n',b'\n')
cmake = files['CMakeLists.txt'].decode()
anchor = 'target_link_libraries(spatial-authority PUBLIC spatial-scene SQLite::SQLite3 OpenSSL::Crypto)\n'
assert cmake.count(anchor) == 1
cmake = cmake.replace(anchor, anchor+'add_executable(spatial-authority-migrate src/authority/migrate_main.cpp)\n'
    'target_link_libraries(spatial-authority-migrate PRIVATE spatial-authority)\n')
anchor = '    add_test(NAME workspace-authority COMMAND'
assert cmake.count(anchor) == 1
cmake = cmake.replace(anchor, '    add_test(NAME authority-bootstrap COMMAND "${Python3_EXECUTABLE}"\n'
    '        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_authority_bootstrap.py"\n'
    '        $<TARGET_FILE:spatial-authority-probe> $<TARGET_FILE:spatial-authority-migrate>)\n'
    '    set_tests_properties(authority-bootstrap PROPERTIES TIMEOUT 90\n'
    '        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")\n'+anchor)
files['CMakeLists.txt'] = cmake.encode()
(draft/'CMakeLists.txt').write_bytes(files['CMakeLists.txt'])
manifest = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
files['source-manifest.json'] = (json.dumps(manifest,indent=2)+'\n').encode()
with tarfile.open(root/'local/bootstrap-domain-build-01.tar','w') as archive:
    for name,data in files.items():
        entry = tarfile.TarInfo(name); entry.size=len(data); entry.mode=0o644
        archive.addfile(entry,io.BytesIO(data))
print(len(manifest),'isolated source files')
