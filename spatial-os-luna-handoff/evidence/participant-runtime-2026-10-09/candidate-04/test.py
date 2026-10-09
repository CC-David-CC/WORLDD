import hashlib,json,os,pathlib,socket,subprocess,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
out=root/'local/m02-participant-runtime-draft-04';out.mkdir();source=out/'source';source.mkdir()
manifest=json.loads((root/'local/participant-runtime-candidate-04.json').read_text())
with tarfile.open(root/'local/participant-runtime-candidate-04.tar') as archive:
    for m in archive.getmembers():assert m.isfile() and not pathlib.PurePosixPath(m.name).is_absolute() and '..' not in pathlib.PurePosixPath(m.name).parts
    archive.extractall(source,filter='data')
for name,digest in manifest['files'].items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
for label,sanitize in [('debug','OFF'),('runtime-sanitize','ON')]:
    folder=out/label;folder.mkdir();build=folder/'build';env=dict(os.environ,ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_INPUT_TESTS=ON','-DSPATIAL_SANITIZERS='+sanitize]),('build',['cmake','--build',str(build),'--parallel','2','--target','spatial-participant-runtime-probe','identityd','spatial-issuer-client','issuer-python-protobuf','grants-python-protobuf','key-python-protobuf','python-protobuf','security-python-protobuf']),('runtime',['ctest','--test-dir',str(build),'-V','-R','^identity-participant-runtime$']),('regression',['ctest','--test-dir',str(build),'-V','-R','^identity-participant(s|-client|-session(-client)?|-barrier(-client)?)$'])]
    results=[]
    for name,command in commands:
        with (folder/(name+'.log')).open('wb') as log:r=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
        results.append(dict(name=name,command=command,exit_code=r.returncode));(folder/'run.json').write_text(json.dumps(dict(host=host,source_files=len(manifest['files']),results=results,ASAN_OPTIONS=env['ASAN_OPTIONS'],UBSAN_OPTIONS=env['UBSAN_OPTIONS']),indent=2)+'\n');print(host,label,name,r.returncode,flush=True)
        if r.returncode:print((folder/(name+'.log')).read_text()[-8000:],flush=True);raise SystemExit(r.returncode)
print(host,'both worker focused profiles complete',flush=True)
