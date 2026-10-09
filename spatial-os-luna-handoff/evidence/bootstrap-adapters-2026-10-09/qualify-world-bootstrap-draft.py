import hashlib,json,os,pathlib,socket,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
source=root/'local/m01-world-bootstrap-draft-01/source'
manifest=json.loads((source/'source-manifest.json').read_text())
def audit():
    for name,digest in manifest.items(): assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
audit()
test=root/'local/m01-world-bootstrap-races-02/test_world_bootstrap_races.py'
server=json.loads((root/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
for label,sanitize in [('debug','OFF'),('runtime-sanitize','ON')]:
    out=root/('local/m01-world-bootstrap-focused-'+label+'-01');out.mkdir();build=out/'build'
    saved=out/test.name;saved.write_bytes(test.read_bytes())
    env=dict(os.environ,SPATIAL_TEST_XSERVER=server['path'],SPATIAL_XPROBE=str(build/'spatial-xprobe'),
        PYTHONPATH=str(build/'python')+':'+str(source)+':'+str(source/'tests/core'),
        ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug',
        '-DSPATIAL_INPUT_TESTS=ON','-DSPATIAL_SANITIZERS='+sanitize]),
        ('build',['cmake','--build',str(build),'--parallel','2']),
        ('regressions',['ctest','--test-dir',str(build),'-V','-R','^(world-bootstrap|world-identity|world-authority|workspace-authority)$']),
        ('races',['python3',str(saved),str(build/'identityd'),str(build/'spatial-issuer-client'),str(build/'worldd')])]
    results=[]
    for name,command in commands:
        with (out/(name+'.log')).open('wb') as log:
            result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
        results.append({'name':name,'command':command,'exit_code':result.returncode})
        (out/'run.json').write_text(json.dumps({'scope':'isolated world adapter focused regressions',
            'results':results,'server':server,'source_files':len(manifest),
            'ASAN_OPTIONS':env['ASAN_OPTIONS'],'UBSAN_OPTIONS':env['UBSAN_OPTIONS']},indent=2)+'\n')
        print(label,name,result.returncode,flush=True)
        if result.returncode:
            print((out/(name+'.log')).read_text()[-7500:],flush=True);raise SystemExit(result.returncode)
    audit()
