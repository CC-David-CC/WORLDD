import hashlib,json,os,pathlib,socket,subprocess,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
source=root/'local/m02-participant-source-02';source.mkdir()
with tarfile.open(root/'local/participant-candidate-02.tar') as archive:
    for member in archive.getmembers():
        name=pathlib.PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
    archive.extractall(source,filter='data')
manifest=json.loads((root/'local/participant-candidate-02.json').read_text())
def audit():
    for name,digest in manifest['files'].items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
audit()
subprocess.run(['python3',str(root/'local/audit-participants-02.py')],check=True)
server=json.loads((root/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
assert hashlib.sha256(pathlib.Path(server['path']).read_bytes()).hexdigest()==server['sha256']
pattern='^identity-participant(s|-client)$'
for label,sanitize in [('debug','OFF'),('runtime-sanitize','ON')]:
    out=root/('local/m02-participant-'+label+'-02');out.mkdir();build=out/'build'
    env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),SPATIAL_TEST_XSERVER=server['path'],
        SPATIAL_XPROBE=str(build/'spatial-xprobe'),SPATIAL_SUT=json.dumps([str(build/'spatial-test-host')]),
        SPATIAL_SECURITY_EVIDENCE=str(out/'server'),SPATIAL_EVIDENCE_DIR=str(out/'cases'),
        ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug',
        '-DSPATIAL_INPUT_TESTS=ON','-DSPATIAL_SANITIZERS='+sanitize]),
        ('build',['cmake','--build',str(build),'--parallel','2']),
        ('harness',['python3','-m','unittest','discover','-s','tests/harness','-t','.','-q']),
        ('participants',['ctest','--test-dir',str(build),'-V','-R',pattern]),
        ('core',['ctest','--test-dir',str(build),'-V','-E',pattern]),
        ('P10',['python3','tools/with_test_display.py','--','python3','tools/gate.py','--through','P10','--report',str(out/'P10.json')])]
    results=[]
    for name,command in commands:
        with (out/(name+'.log')).open('wb') as log:
            result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
        results.append({'name':name,'command':command,'exit_code':result.returncode})
        report={'host':host,'results':results,'source_files':len(manifest['files']),'base_commit':manifest['base_commit'],
            'server':server,'ASAN_OPTIONS':env['ASAN_OPTIONS'],'UBSAN_OPTIONS':env['UBSAN_OPTIONS']}
        (out/'run.json').write_text(json.dumps(report,indent=2)+'\n');print(host,label,name,result.returncode,flush=True)
        if result.returncode:
            print((out/(name+'.log')).read_text()[-7500:],flush=True);raise SystemExit(result.returncode)
    audit()
print(host,'both participant candidate02 profiles completed',flush=True)
