import hashlib,json,os,pathlib,socket,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
source=root/'local/m01-world-remote-draft-01/source'
out=root/'local/m01-world-remote-runtime-sanitize-01';out.mkdir();build=out/'build'
manifest=json.loads((root/'local/world-remote-source-01.json').read_text())
def audit():
    for name,digest in manifest['files'].items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
audit()
for name,folder in [('test_world_remote_security.py','security'),('test_world_remote_lifecycle.py','lifecycle'),('test_world_remote_native.py','native')]:
    (out/name).write_bytes((root/('local/m01-world-remote-'+folder+'-01')/name).read_bytes())
server=json.loads((root/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
env=dict(os.environ,PYTHONPATH=':'.join(map(str,[build/'python',source,source/'tests/core'])),
    SPATIAL_TEST_XSERVER=server['path'],SPATIAL_SECURITY_EVIDENCE=str(out/'server'),
    SPATIAL_XPROBE=str(build/'spatial-xprobe'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',
    UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
executables=[str(build/n) for n in ('identityd','spatial-issuer-client','worldd')]
commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_INPUT_TESTS=ON','-DSPATIAL_SANITIZERS=ON']),
    ('build',['cmake','--build',str(build),'--parallel','2']),
    ('focused',['ctest','--test-dir',str(build),'-V','-R','^(world-remote|world-identity|world-authority|world-bootstrap|world-bootstrap-races|actor-pixels)$'])]
for name in ('security','lifecycle','native'):
    command=['python3',str(out/('test_world_remote_'+name+'.py')),*executables]
    if name=='native':command.append(str(build/'spatial-security-auth'))
    commands.append((name,command))
results=[]
for name,command in commands:
    with (out/(name+'.log')).open('wb') as log:
        result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
    results.append({'name':name,'command':command,'exit_code':result.returncode})
    (out/'run.json').write_text(json.dumps({'host':'sb16','results':results,'source_files':len(manifest['files']),
        'server':server,'ASAN_OPTIONS':env['ASAN_OPTIONS'],'UBSAN_OPTIONS':env['UBSAN_OPTIONS']},indent=2)+'\n')
    print(name,result.returncode,flush=True)
    if result.returncode:print((out/(name+'.log')).read_text()[-6500:],flush=True);raise SystemExit(result.returncode)
audit();print('Remote runtime sanitizer focused qualification complete',flush=True)
