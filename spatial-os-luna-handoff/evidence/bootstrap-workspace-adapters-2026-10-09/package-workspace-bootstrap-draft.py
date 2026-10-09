import hashlib,io,json,pathlib,subprocess,tarfile
root=pathlib.Path(__file__).resolve().parents[1];draft=root/'local/bootstrap-adapter-draft'
manifest=json.loads((root/'local/domain-production-tree-01.json').read_text())
files={name:(root/name).read_bytes().replace(b'\r\n',b'\n') for name in manifest['files']}
for name,digest in manifest['files'].items():assert hashlib.sha256(files[name]).hexdigest()==digest,name
locked=json.loads((root/'acceptance.lock.json').read_text())
for name,digest in locked['files'].items():
    data=(root/name).read_bytes().replace(b'\r\n',b'\n')
    assert hashlib.sha256(data).hexdigest()==digest,name
    if name not in files:files[name]=data
overlays={'grants.proto':'proto/spatial/authority/v1/grants.proto','world_session.proto':'proto/spatial/identity/v1/world_session.proto',
    'broker.proto':'proto/spatial/workspace/v1/broker.proto','world_authority.cpp':'src/world/authority.cpp',
    'world_authority.hpp':'src/world/authority.hpp','identity_sessions.cpp':'src/world/identity_sessions.cpp',
    'workspace_authority.cpp':'src/workspace/authority.cpp','workspace_authority.hpp':'src/workspace/authority.hpp',
    'workspace_main.cpp':'src/workspace/main.cpp','broker_client.cpp':'src/platform/broker.cpp','broker_client.hpp':'src/platform/broker.hpp',
    'broker_control_client_probe.cpp':'src/testing/broker_control_client_probe.cpp'}
for name in ('test_world_bootstrap.py','test_workspace_bootstrap.py','test_world_bootstrap_races.py','test_broker_bootstrap_client.py','test_workspace_bootstrap_races.py'):
    overlays[name]='tests/core/'+name
for name,target in overlays.items():files[target]=(draft/name).read_bytes().replace(b'\r\n',b'\n')
cmake=(draft/'CMakeLists.txt').read_text()
old='target_link_libraries(spatial-core PUBLIC spatial-scene SQLite::SQLite3)';assert cmake.count(old)==1
cmake=cmake.replace(old,'target_link_libraries(spatial-core PUBLIC spatial-scene SQLite::SQLite3 spatial-authority)')
marker='    find_package(Python3 3.11 REQUIRED COMPONENTS Interpreter)\n';assert cmake.count(marker)==1
cmake=cmake.replace(marker,marker+'''    add_test(NAME workspace-bootstrap-races COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_bootstrap_races.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(workspace-bootstrap-races PROPERTIES TIMEOUT 120
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME workspace-bootstrap COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_bootstrap.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(workspace-bootstrap PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME world-bootstrap-races COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_world_bootstrap_races.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(world-bootstrap-races PROPERTIES TIMEOUT 120
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME broker-bootstrap-client COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_broker_bootstrap_client.py" $<TARGET_FILE:spatial-broker-control-client-probe>)
    set_tests_properties(broker-bootstrap-client PROPERTIES TIMEOUT 120
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
''')
files['CMakeLists.txt']=cmake.encode()
(draft/'CMakeLists.workspace.txt').write_bytes(files['CMakeLists.txt'])
hashes={name:hashlib.sha256(data).hexdigest() for name,data in files.items()}
record={'base_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root).decode().strip(),
    'normalization':'CRLF to LF','files':hashes}
(root/'local/adapters-tested-tree-03.json').write_text(json.dumps(record,indent=2)+'\n',newline='\n')
files['source-manifest.json']=(json.dumps(hashes,indent=2)+'\n').encode()
with tarfile.open(root/'local/bootstrap-adapters-candidate-03.tar','w') as archive:
    for name,data in files.items():
        member=tarfile.TarInfo(name);member.size=len(data);member.mode=0o644;archive.addfile(member,io.BytesIO(data))
print('Packaged',len(hashes),'isolated adapter sources; production source remains unchanged')
