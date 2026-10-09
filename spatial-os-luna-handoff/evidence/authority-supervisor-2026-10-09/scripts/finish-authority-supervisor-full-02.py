import hashlib,json,os,pathlib,socket,subprocess,tarfile
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
source=r/'local/m02-authority-supervisor-02/source'
manifest=json.loads((r/'local/authority-supervisor-candidate-02.json').read_text())['files'];locked=json.loads((source/'acceptance.lock.json').read_text())['files']
def audit():
    for files in [manifest,locked]:
        for n,h in files.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
audit()
server=json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb'];assert hashlib.sha256(pathlib.Path(server['path']).read_bytes()).hexdigest()==server['sha256']
for profile in ['debug','runtime-sanitize']:
    run=json.loads((source.parent/profile/'run.json').read_text())
    assert len(run['results'])==6 and all(x['exit_code']==0 for x in run['results'])
for profile,sanitize in [('debug','OFF'),('runtime-sanitize','ON')]:
    out=source.parent/profile;build=out/'build'
    prior=json.loads((out/'run.json').read_text())
    assert [x['name'] for x in prior['results']]==['configure','build','harness','ownership','supervisor','focused'] and all(x['exit_code']==0 for x in prior['results'])
    assert not (out/'run-full.json').exists() and not (out/'core.log').exists()
    env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),SPATIAL_TEST_XSERVER=server['path'],SPATIAL_XPROBE=str(build/'spatial-xprobe'),SPATIAL_SUT=json.dumps([str(build/'spatial-test-host')]),SPATIAL_SECURITY_EVIDENCE=str(out/'server'),SPATIAL_EVIDENCE_DIR=str(out/'cases'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    commands=[('core',['ctest','--test-dir',str(build),'-V','-E','^(identity-(launch-authorization|supervisor-registry|keys|issuer|enrollment|bootstrap|revocation|participants|participant-(client|incarnation|channel|runtime|barrier|barrier-client|session|session-client))|issuer-client|authority-(native-config|supervisor|participant-relay|owned-participant|launch-coordinator|prepared-launch|process-scope|native-scope))$']),('P10',['python3','tools/with_test_display.py','--','python3','tools/gate.py','--through','P10','--report',str(out/'P10.json')])]
    results=list(prior['results'])
    for name,command in commands:
        with (out/(name+'.log')).open('wb') as log:run=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
        results.append(dict(name=name,command=command,exit_code=run.returncode));(out/'run-full.json').write_text(json.dumps(dict(host=host,results=results,server=server,source_files=len(manifest)),indent=2)+'\n');print(host,profile,name,run.returncode,flush=True)
        if run.returncode:print((out/(name+'.log')).read_text()[-20000:],flush=True);raise SystemExit(run.returncode)
    audit()
print(host,'full authority supervisor qualification complete',flush=True)
