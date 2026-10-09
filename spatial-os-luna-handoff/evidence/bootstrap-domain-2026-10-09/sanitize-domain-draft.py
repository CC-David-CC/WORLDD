import hashlib,json,os,pathlib,shutil,socket,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
previous=root/'local/m01-bootstrap-domain-build-01'
run=root/'local/m01-bootstrap-domain-sanitize-01';run.mkdir()
source=run/'source';shutil.copytree(previous/'source',source,ignore=shutil.ignore_patterns('__pycache__'))
shutil.copyfile(root/'local/m01-bootstrap-domain-focused-03/test_authority_bootstrap.py',source/'tests/core/test_authority_bootstrap.py')
manifest=json.loads((source/'source-manifest.json').read_text())
manifest['tests/core/test_authority_bootstrap.py']=hashlib.sha256((source/'tests/core/test_authority_bootstrap.py').read_bytes()).hexdigest()
(source/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
for name,digest in manifest.items(): assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
build=run/'build'
env=dict(os.environ,ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
report={'scope':'isolated candidate store and production migration CLI; no world/workspace integration',
        'ASAN_OPTIONS':env['ASAN_OPTIONS'],'UBSAN_OPTIONS':env['UBSAN_OPTIONS'],'results':[]}
commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_SANITIZERS=ON']),
          ('build',['cmake','--build',str(build),'--parallel','2','--target','spatial-authority-probe',
            'spatial-authority-migrate','python-protobuf','security-python-protobuf','grants-python-protobuf','grants-probe-python']),
          ('authority',['ctest','--test-dir',str(build),'-V','-R','^authority-(store|bootstrap)$'])]
for name,command in commands:
    with (run/(name+'.log')).open('wb') as output:
        result=subprocess.run(command,cwd=source,env=env,stdout=output,stderr=subprocess.STDOUT)
    report['results'].append({'name':name,'command':command,'exit_code':result.returncode})
    (run/'run.json').write_text(json.dumps(report,indent=2)+'\n')
    print(name,result.returncode,flush=True)
    if result.returncode: raise SystemExit(result.returncode)
for name,digest in manifest.items(): assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
