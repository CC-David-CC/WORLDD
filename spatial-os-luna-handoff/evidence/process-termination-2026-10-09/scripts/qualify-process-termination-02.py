import hashlib,json,os,pathlib,socket,subprocess,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
for path in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
    try:arguments=path.read_bytes().split(b'\0')
    except (FileNotFoundError,ProcessLookupError,PermissionError):continue
    assert not any(a.endswith(b'/test-process-termination-01.py') for a in arguments),'Focused qualification is still live'
source=root/'local/m02-process-termination-source-02';source.mkdir()
with tarfile.open(root/'local/process-termination-candidate-02.tar') as a:
    for m in a.getmembers():assert m.isfile() and not pathlib.PurePosixPath(m.name).is_absolute() and '..' not in pathlib.PurePosixPath(m.name).parts
    a.extractall(source,filter='data')
manifest=json.loads((root/'local/process-termination-candidate-02.json').read_text());base=json.loads((root/'evidence/participant-barrier-2026-10-09/integration-applied-05.json').read_text())['files']
assert set(manifest['files'])-set(base)=={'src/testing/process_termination_probe.cpp','tests/core/test_process_termination.py'}
assert {n for n,h in base.items() if manifest['files'][n]!=h}=={'CMakeLists.txt','src/workspace/process.cpp','src/workspace/process.hpp'}
locked=json.loads((source/'acceptance.lock.json').read_text())['files']
def audit():
    for files in [manifest['files'],locked]:
        for n,h in files.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
audit()
server=json.loads((root/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb'];assert hashlib.sha256(pathlib.Path(server['path']).read_bytes()).hexdigest()==server['sha256']
for label,sanitize in [('debug','OFF'),('runtime-sanitize','ON')]:
    out=root/('local/m02-process-termination-'+label+'-02');out.mkdir();build=out/'build'
    env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),SPATIAL_TEST_XSERVER=server['path'],SPATIAL_XPROBE=str(build/'spatial-xprobe'),SPATIAL_SUT=json.dumps([str(build/'spatial-test-host')]),SPATIAL_SECURITY_EVIDENCE=str(out/'server'),SPATIAL_EVIDENCE_DIR=str(out/'cases'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_INPUT_TESTS=ON','-DSPATIAL_SANITIZERS='+sanitize]),('build',['cmake','--build',str(build),'--parallel','2']),('harness',['python3','-m','unittest','discover','-s','tests/harness','-t','.','-q']),('termination',['ctest','--test-dir',str(build),'-V','-R','^process-termination$']),('core',['ctest','--test-dir',str(build),'-V','-E','^process-termination$']),('P10',['python3','tools/with_test_display.py','--','python3','tools/gate.py','--through','P10','--report',str(out/'P10.json')])]
    results=[]
    for name,command in commands:
        with (out/(name+'.log')).open('wb') as log:r=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
        results.append(dict(name=name,command=command,exit_code=r.returncode));(out/'run.json').write_text(json.dumps(dict(host=host,results=results,source_files=len(manifest['files']),locked_files=len(locked),server=server,ASAN_OPTIONS=env['ASAN_OPTIONS'],UBSAN_OPTIONS=env['UBSAN_OPTIONS']),indent=2)+chr(10));print(host,label,name,r.returncode,flush=True)
        if r.returncode:print((out/(name+'.log')).read_text()[-10000:],flush=True);raise SystemExit(r.returncode)
    audit()
print(host,'full process termination qualification complete',flush=True)
