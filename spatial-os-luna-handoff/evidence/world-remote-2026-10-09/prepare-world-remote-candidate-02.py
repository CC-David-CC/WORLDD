import hashlib,io,json,pathlib,tarfile
root=pathlib.Path(__file__).resolve().parents[1]
base=json.loads((root/'local/world-remote-source-01.json').read_text())
with tarfile.open(root/'local/world-remote-source-01.tar') as archive:
    files={name:archive.extractfile(name).read() for name in base['files']}
for name,digest in base['files'].items():assert hashlib.sha256(files[name]).hexdigest()==digest,name
def replace(name,old,new):
    text=files[name].decode();assert text.count(old)==1,(name,old,text.count(old));files[name]=text.replace(old,new).encode()
name='src/world/actor_tls.cpp'
replace(name,'#include <poll.h>','#include <poll.h>\n#include <optional>')
replace(name,'Clock::time_point started = Clock::now(), activity = started;',
    'Clock::time_point started = Clock::now(), activity = started;\n    std::optional<Clock::time_point> read_retry_started;')
replace(name,'        !authenticated && Clock::now()-impl_->activity >= impl_->idle;',
    '        (impl_->read_retry_started && Clock::now()-*impl_->read_retry_started >= impl_->timeout) ||\n        (!authenticated && Clock::now()-impl_->activity >= impl_->idle);')
replace(name,'    if (p.read_wanted != POLLOUT) p.read_size =', '    if (!p.read_retry_started) p.read_size =')
replace(name,'    if (result != 1) return p.retry(result, p.read_wanted);\n    p.read_wanted = POLLIN;',
    '''    if (result != 1) {
        const auto status = p.retry(result, p.read_wanted);
        if (!status && p.read_wanted == POLLOUT && !p.read_retry_started) p.read_retry_started = Clock::now();
        return status;
    }
    p.read_retry_started.reset(); p.read_wanted = POLLIN;''')
name='src/world/identity_sessions.cpp'
timeout='''            const auto timeout = channel.tls ? channel.tls->timeout() : std::chrono::milliseconds(10000);
            if ((!channel.input.empty() && now-channel.input_started > timeout) ||
                    (!channel.output.empty() && now-channel.output_started > timeout)) { drop(); continue; }
'''
replace(name,timeout,'')
replace(name,'            const auto now = Clock::now();\n            if (channel.tls) {',
    '            const auto now = Clock::now();\n'+timeout+'            if (channel.tls) {')
for test in ('security','lifecycle','native','races','config'):
    files['tests/core/test_world_remote_'+test+'.py']=(root/'local'/('test_world_remote_'+test+'.py')).read_bytes().replace(b'\r\n',b'\n')
replace('tests/core/test_world_remote.py','config=issuer.IssuerTlsServerConfig(', 'config=session.WorldActorTlsConfig(')
replace('tests/core/test_world_remote.py','        # Draft WorldActorTlsConfig uses these common TLS-material and limit fields.\n','')
files['src/testing/actor_tls_retry_probe.cpp']=(root/'local/actor_tls_retry_probe.cpp').read_bytes().replace(b'\r\n',b'\n')
files['tests/core/test_actor_tls_retries.py']=(root/'local/test_actor_tls_retries.py').read_bytes().replace(b'\r\n',b'\n')
# The probe is an explicit fault witness. It reuses the actual production adapter and OpenSSL.
registration='''    add_executable(spatial-actor-tls-retry-probe src/testing/actor_tls_retry_probe.cpp src/world/actor_tls.cpp)
    target_link_libraries(spatial-actor-tls-retry-probe PRIVATE spatial-identity-tls)
    target_link_options(spatial-actor-tls-retry-probe PRIVATE -Wl,--wrap=SSL_read_ex -Wl,--wrap=SSL_get_error)
    add_test(NAME world-remote-retries COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_actor_tls_retries.py" $<TARGET_FILE:spatial-actor-tls-retry-probe>)
    set_tests_properties(world-remote-retries PROPERTIES TIMEOUT 30)
'''
for test in ('security','lifecycle','native','races','config'):
    extra=' $<TARGET_FILE:spatial-security-auth>' if test=='native' else ''
    registration+='''    add_test(NAME world-remote-TEST COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_world_remote_TEST.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>EXTRA)
    set_tests_properties(world-remote-TEST PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
'''.replace('TEST',test).replace('EXTRA',extra)
replace('CMakeLists.txt','    find_package(Python3 3.11 REQUIRED COMPONENTS Interpreter)\n',
    '    find_package(Python3 3.11 REQUIRED COMPONENTS Interpreter)\n'+registration)
hashes={name:hashlib.sha256(data).hexdigest() for name,data in files.items()}
record={'base_commit':'d4b025e3705fabad151c97e60de010187a85ed7e','previous_candidate':1,'files':hashes,
    'changed_from_draft01':[name for name,digest in hashes.items() if base['files'].get(name)!=digest]}
dest=root/'local/world-remote-candidate-02';dest.mkdir(exist_ok=True)
for name,data in files.items():
    path=dest/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
(root/'local/world-remote-source-02.json').write_text(json.dumps(record,indent=2)+'\n',newline='\n')
with tarfile.open(root/'local/world-remote-source-02.tar','w') as archive:
    for name,data in files.items():
        entry=tarfile.TarInfo(name);entry.size=len(data);entry.mode=0o644;archive.addfile(entry,io.BytesIO(data))
print('Prepared candidate02:',len(files),'files; changes from01:',record['changed_from_draft01'])
