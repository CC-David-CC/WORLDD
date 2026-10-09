import hashlib,json,os,pathlib,socket,subprocess,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
out=root/'local/m02-process-termination-draft-01';out.mkdir();source=out/'source';source.mkdir()
manifest=json.loads((root/'local/process-termination-candidate-01.json').read_text())
with tarfile.open(root/'local/process-termination-candidate-01.tar') as archive:
    for m in archive.getmembers():assert m.isfile() and not pathlib.PurePosixPath(m.name).is_absolute() and '..' not in pathlib.PurePosixPath(m.name).parts
    archive.extractall(source,filter='data')
for n,h in manifest['files'].items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
server=json.loads((root/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
assert hashlib.sha256(pathlib.Path(server['path']).read_bytes()).hexdigest()==server['sha256']
for label,sanitize in [('debug','OFF'),('runtime-sanitize','ON')]:
    folder=out/label;folder.mkdir();build=folder/'build'
    env=dict(os.environ,SPATIAL_TEST_XSERVER=server['path'],ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1',SPATIAL_TEST_XSERVER_LOG=str(folder/'xserver.log'))
    commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_INPUT_TESTS=ON','-DSPATIAL_SANITIZERS='+sanitize]),('build',['cmake','--build',str(build),'--parallel','2']),('termination',['ctest','--test-dir',str(build),'-V','-R','^process-termination$']),('regression',['ctest','--test-dir',str(build),'-V','-R','^(workspace-processes|workspace-context|workspace-authority)$'])]
    results=[]
    for name,command in commands:
        with (folder/(name+'.log')).open('wb') as log:r=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
        results.append(dict(name=name,command=command,exit_code=r.returncode));(folder/'run.json').write_text(json.dumps(dict(host=host,source_files=len(manifest['files']),results=results,server=server,ASAN_OPTIONS=env['ASAN_OPTIONS'],UBSAN_OPTIONS=env['UBSAN_OPTIONS']),indent=2)+'\n');print(host,label,name,r.returncode,flush=True)
        if r.returncode:print((folder/(name+'.log')).read_text()[-11000:],flush=True);raise SystemExit(r.returncode)
