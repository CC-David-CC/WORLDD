import hashlib,json,os,pathlib,socket,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
manifest=json.loads((root/'local/domain-production-tree-01.json').read_text())
def audit():
    for name,digest in manifest['files'].items(): assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest,name
audit()
binary=json.loads((root/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
for build,sanitize,label in [('build','OFF','debug'),('build-sanitize','ON','runtime-sanitize')]:
    out=root/('local/m01-domain-'+label+'-01');out.mkdir()
    env=dict(os.environ,SPATIAL_TEST_XSERVER=binary['path'],PYTHONPATH=str(root/build/'python')+':'+str(root),
        SPATIAL_SECURITY_EVIDENCE=str(out/'server'),SPATIAL_EVIDENCE_DIR=str(out/'cases'),
        SPATIAL_SUT=json.dumps([str(root/build/'spatial-test-host')]),SPATIAL_XPROBE=str(root/build/'spatial-xprobe'),
        ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    commands=[('configure',['cmake','-S','.','-B',build,'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_SANITIZERS='+sanitize,'-DSPATIAL_INPUT_TESTS=ON']),
        ('build',['cmake','--build',build,'--parallel','2']),
        ('harness',['python3','-m','unittest','discover','-s','tests/harness','-t','.','-q']),
        ('authority',['ctest','--test-dir',build,'-V','-R','^authority-(store|bootstrap)$']),
        ('core',['ctest','--test-dir',build,'-V','-E','^authority-(store|bootstrap)$']),
        ('P10',['python3','tools/with_test_display.py','--','python3','tools/gate.py','--through','P10','--report',str(out/'P10.json')])]
    results=[]
    for name,command in commands:
        with (out/(name+'.log')).open('wb') as log:
            result=subprocess.run(command,cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT)
        results.append({'name':name,'command':command,'exit_code':result.returncode})
        report={'host':host,'results':results,'source_files':len(manifest['files']),'base_commit':manifest['base_commit'],
                'binary':binary,'ASAN_OPTIONS':env['ASAN_OPTIONS'],'UBSAN_OPTIONS':env['UBSAN_OPTIONS']}
        (out/'run.json').write_text(json.dumps(report,indent=2)+'\n')
        print(label,name,result.returncode,flush=True)
        if result.returncode: print((out/(name+'.log')).read_text()[-5500:],flush=True);raise SystemExit(result.returncode)
    audit()
print('Both domain-store profiles completed. Authenticated domain delivery and native-server GLX qualification remain separate.',flush=True)
