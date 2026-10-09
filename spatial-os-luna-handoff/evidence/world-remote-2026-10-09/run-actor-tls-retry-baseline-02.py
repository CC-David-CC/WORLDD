import hashlib,json,pathlib,socket,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
out=root/'local/m01-remote-retry-baseline-02';out.mkdir();secrets=out/'secrets';secrets.mkdir(mode=0o700)
source=root/'local/m01-world-remote-draft-01/source';build=root/'local/m01-world-remote-draft-01/build'
probe=out/'actor_tls_retry_probe.cpp';probe.write_bytes((root/'local'/probe.name).read_bytes())
for name in ('server','client'):
    subprocess.run(['openssl','req','-x509','-newkey','ed25519','-nodes','-keyout',str(secrets/(name+'.key')),
        '-out',str(secrets/(name+'.crt')),'-days','2','-subj','/CN='+name+'.test','-addext','subjectAltName=DNS:'+name+'.test'],check=True,capture_output=True)
    (secrets/(name+'.key')).chmod(0o600)
    (secrets/(name+'.crt')).chmod(0o644)
command=['c++','-std=c++20','-g','-I'+str(source/'src'),'-I'+str(build),str(probe),str(source/'src/world/actor_tls.cpp'),
    '-Wl,--wrap=SSL_read_ex','-Wl,--wrap=SSL_get_error',str(build/'libspatial-identity-tls.a'),str(build/'libspatial-scene.a'),
    str(build/'libspatial-protobuf.a'),'-lprotobuf','-lssl','-lcrypto','-pthread','-o',str(out/'probe')]
compiled=subprocess.run(command,capture_output=True,text=True);(out/'build.log').write_text(compiled.stdout+compiled.stderr)
assert compiled.returncode==0,compiled.stderr
results=[]
for case in ('retry-stalls','retry-wants-read','retry-progress','retry-recovers','authenticated-idle'):
    result=subprocess.run([str(out/'probe'),str(secrets),case],capture_output=True,text=True,timeout=5)
    (out/(case+'.log')).write_text(result.stdout+result.stderr)
    results.append({'case':case,'exit_code':result.returncode});print(case,result.returncode,flush=True)
record={'host':'sb16','build_command':command,'results':results,
    'actor_tls_sha256':hashlib.sha256((source/'src/world/actor_tls.cpp').read_bytes()).hexdigest(),
    'probe_sha256':hashlib.sha256(probe.read_bytes()).hexdigest(),
    'scope':'Controlled OpenSSL retry fault injection after real mutual TLS. Not a reproduced network attack.'}
(out/'run.json').write_text(json.dumps(record,indent=2)+'\n')
assert [r['exit_code'] for r in results]==[1,1,1,0,0],results
print('Confirmed three retry-deadline failures; recovery and authenticated idle controls pass',flush=True)
