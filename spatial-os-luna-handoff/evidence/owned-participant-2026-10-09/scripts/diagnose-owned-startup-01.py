import hashlib,json,os,pathlib,socket,subprocess,time
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
source=r/'local/m02-owned-participant-02/source';build=source.parent/'runtime-sanitize/build'
manifest=json.loads((r/'local/owned-participant-candidate-01.json').read_text())['files']
for n,h in manifest.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
assert (r/('local/owned-participant-full-failure-'+host+'-02.tar.gz')).is_file()
out=r/'local/owned-startup-diagnostic-01';out.mkdir();results=[]
cases=[('test_world_bootstrap.py','WorldBootstrapTests.test_challenge_and_failed_proof_do_not_materialize_grants'),
       ('test_workspace_authority.py','WorkspaceAuthorityTests.test_grants_do_not_make_workspace_available_implicitly'),
       ('test_world_authority.py','WorldAuthorityTests.test_grant_cannot_cross_world_identity')]
for repeat in range(5):
    for filename,test in cases:
        name=str(repeat)+'-'+test;folder=out/name;folder.mkdir()
        command=['python3',str(source/'tests/core'/filename),str(build/'identityd'),str(build/'spatial-issuer-client'),str(build/'worldd'),test,'-v']
        env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),SPATIAL_SECURITY_EVIDENCE=str(folder/'server'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
        begin=time.monotonic()
        with (folder/'test.log').open('wb') as log:run=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
        result=dict(name=name,command=command,exit_code=run.returncode,seconds=time.monotonic()-begin)
        results.append(result);(out/'run.json').write_text(json.dumps(dict(host=host,results=results,source_files=len(manifest),production_or_tests_changed=False),indent=2)+'\n')
        print(host,name,run.returncode,flush=True)
print(host,'diagnostic complete; failures',sum(x['exit_code']!=0 for x in results),'of',len(results),flush=True)
